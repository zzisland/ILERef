"""Bounded declaration binding over the existing Java/tree-sitter AST.

No project-name rules, source scan, model, build or dependency download. Source
files are loaded by qualified type name from explicit engineering roots. JDK
signatures come from local javap, never from an application method-name map.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .safety_contracts import Java, children, digest, walk, PRIMITIVES, JUNIT

MNT_RULE_VERSION = '5.2'
ECT_TYPE_EVIDENCE_VERSION = '2.0'


class BindingUnknown(ValueError):
    pass


def resolve_ect_exception_type(instance, context, raw_type):
    """Bind one catch type and prove that it is an Exception descendant.

    ECT uses the same source roots, owner binding and local-JDK evidence as the
    existing declaration resolver.  Nested source types need a small adapter
    because a catch may name them as ``Outer.Inner`` rather than as a source
    file of their own.
    """
    if not isinstance(raw_type, str) or not re.fullmatch(r'[\w$]+(?:\.[\w$]+)*', raw_type):
        raise BindingUnknown('ECT_EXCEPTION_TYPE_SYNTAX_UNSUPPORTED')
    resolver = DeclarationResolver(instance, context)
    parts = raw_type.split('.')
    bound = None
    nested = []
    last_error = None
    for split in range(len(parts), 0, -1):
        try:
            candidate = resolver.type_name('.'.join(parts[:split]))
            bound, nested = candidate, parts[split:]
            break
        except BindingUnknown as exc:
            last_error = exc
    if bound is None:
        raise BindingUnknown('ECT_EXCEPTION_TYPE_BINDING_UNKNOWN') from last_error

    if not nested:
        if not resolver._assignable(bound, 'java.lang.Exception'):
            raise BindingUnknown('ECT_EXCEPTION_NOT_EXCEPTION_DESCENDANT')
        qualified = bound
    else:
        view = resolver._view(bound)
        if view is None:
            raise BindingUnknown('ECT_NESTED_EXCEPTION_SOURCE_UNAVAILABLE')
        for name in nested:
            body = view['node'].child_by_field_name('body')
            declarations = [
                node for node in children(body)
                if node.type in ('class_declaration', 'record_declaration', 'enum_declaration')
                and view['java'].text(node.child_by_field_name('name')) == name
            ]
            if len(declarations) != 1:
                raise BindingUnknown('ECT_NESTED_EXCEPTION_BINDING_UNKNOWN')
            view = {**view, 'name': view['name'] + '.' + name, 'node': declarations[0], 'bindings': {}}
        parents = []
        for clause in view['node'].named_children:
            if clause.type != 'superclass':
                continue
            nodes = children(clause)
            if len(nodes) != 1:
                raise BindingUnknown('ECT_EXCEPTION_HIERARCHY_UNKNOWN')
            parents.append(resolver.type_name(view['java'].text(nodes[0]), view))
        if len(parents) != 1 or not resolver._assignable(parents[0], 'java.lang.Exception'):
            raise BindingUnknown('ECT_EXCEPTION_HIERARCHY_UNKNOWN')
        qualified = view['name']

    return {
        'evidence_version': ECT_TYPE_EVIDENCE_VERSION,
        'source_type': raw_type,
        'qualified_type': qualified,
        'proven_supertype': 'java.lang.Exception',
        'evidence_sources': [resolver.used[key] for key in sorted(resolver.used)],
    }


def resolve_ect_checked_exception_type(instance, context, raw_type):
    """Bind a throws type and prove that it is checked.

    The no-throw path deliberately excludes RuntimeException descendants: a
    declaration of an unchecked exception is not evidence that the method is a
    checked-exception smoke test.
    """
    evidence = resolve_ect_exception_type(instance, context, raw_type)
    resolver = DeclarationResolver(instance, context)
    qualified = evidence['qualified_type']
    if resolver._assignable(qualified, 'java.lang.RuntimeException'):
        raise BindingUnknown('ECT_NOTHROW_UNCHECKED_THROWS_TYPE')
    return {**evidence, 'classification': 'CHECKED_EXCEPTION'}


def resolve_ect_assertion_api(instance, context, call, requested_name=None):
    """Prove one JUnit assertion binding without treating unrelated nested
    classes or source-available wildcard imports as automatic ambiguity.
    """
    resolver = DeclarationResolver(instance, context)
    name = requested_name or call['name']
    node = resolver.java.at(call['start'], call['end'])
    if node is None:
        raise BindingUnknown('ECT_ASSERTION_NODE_BINDING_UNKNOWN')
    receiver = resolver.java.text(node.child_by_field_name('object'))
    if receiver not in ('', 'Assertions', JUNIT):
        raise BindingUnknown('ECT_CUSTOM_ASSERTION_BINDING')

    hierarchy = list(resolver._hierarchy(resolver.owner_type, member_name=name))
    for typ in hierarchy:
        java = typ['java']
        if receiver == '' and any(java.text(n.child_by_field_name('name')) == name
                                  for n in resolver._members(typ, 'method_declaration')):
            raise BindingUnknown('ECT_CUSTOM_ASSERTION_BINDING')
        for field in resolver._members(typ, 'field_declaration'):
            for variable in field.named_children:
                if variable.type == 'variable_declarator' and java.text(variable.child_by_field_name('name')) in ('Assertions', 'org'):
                    raise BindingUnknown('ECT_ASSERTION_NAME_SHADOWED')

    imports = [resolver.owner.text(n).strip() for n in resolver.owner.nodes('import_declaration')]
    if receiver == JUNIT:
        return {'declaring_type': JUNIT, 'source': 'FULLY_QUALIFIED_JUNIT_API'}
    if receiver == 'Assertions':
        matches = [item[len('import '):-1].strip() for item in imports if item.endswith('.Assertions;')]
        if matches != [JUNIT]:
            raise BindingUnknown('ECT_ASSERTION_API_AMBIGUOUS')
        return {'declaring_type': JUNIT, 'source': 'EXACT_JUNIT_TYPE_IMPORT'}

    junit = False
    for imp in imports:
        match = re.fullmatch(r'import\s+static\s+([\w.]+)\.(\*|\w+)\s*;', imp)
        if not match or match[2] not in ('*', name):
            continue
        declaring, member = match.groups()
        if declaring == JUNIT:
            junit = True
            continue
        if member != '*':
            raise BindingUnknown('ECT_ASSERTION_API_AMBIGUOUS')
        imported = resolver._source(declaring)
        if imported is None:
            raise BindingUnknown('ECT_STATIC_WILDCARD_BINDING_UNKNOWN')
        imported_hierarchy = list(resolver._hierarchy(imported, member_name=name))
        if any(typ['java'].text(n.child_by_field_name('name')) == name
               for typ in imported_hierarchy
               for n in resolver._members(typ, 'method_declaration')):
            raise BindingUnknown('ECT_ASSERTION_API_AMBIGUOUS')
    if not junit:
        raise BindingUnknown('ECT_ASSERTION_API_INPUT_MISSING')
    return {'declaring_type': JUNIT, 'source': 'JUNIT_IMPORT_AND_SOURCE_CHECKED_WILDCARDS',
            'hierarchy': [typ['name'] for typ in hierarchy]}


def engineering_context(instance):
    """Derive conventional source roots from this exact Maven source location."""
    source = Path(instance.get('test_file_path') or '').resolve()
    relative = Path(instance.get('test_relative_path') or '')
    if not source.is_file() or relative.is_absolute() or '..' in relative.parts:
        return {}
    for parent in source.parents:
        if (parent / relative).resolve() == source and (parent / 'src').is_dir():
            return {'project_root': str(parent),
                    'source_roots': [str(parent/'src/main/java'), str(parent/'src/test/java')],
                    'origin': 'INSTANCE_SOURCE_LOCATION_CONVENTIONAL_ROOTS'}
    return {}


@lru_cache(maxsize=128)
def _jdk_declarations(executable, executable_stamp, name):
    # Boot-module classes only; never load arbitrary project bytecode.
    if not re.fullmatch(r'java\.(?:lang|util|io)\.[A-Za-z_$][\w.$]*', name):
        raise BindingUnknown('MNT_TYPE_SOURCE_MISSING')
    result = subprocess.run([executable, '-public', name], capture_output=True,
                            text=True, timeout=12, check=False)
    if result.returncode:
        raise BindingUnknown('MNT_TYPE_SOURCE_MISSING')
    return result.stdout


class DeclarationResolver:
    def __init__(self, instance, context):
        self.instance = instance
        self.java = Java(instance['test_code'])
        self.owner = Java(context.get('test_class_source') or '')
        self.environment = context.get('java_environment') or engineering_context(instance)
        self.sources = {}
        self.types = {}
        self.jdk = {}
        self.used = {}
        normalize = lambda s: s.replace('\r\n', '\n').strip()
        matches = [n for n in self.owner.nodes('method_declaration')
                   if normalize(self.owner.text(n)) == normalize(instance['test_code'])]
        if len(matches) != 1:
            raise BindingUnknown('MNT_SOURCE_METHOD_BINDING_MISSING')
        self.method = matches[0]
        declaration = self.method.parent.parent
        if declaration.type != 'class_declaration':
            raise BindingUnknown('MNT_OWNER_BINDING_UNSUPPORTED')
        self.owner_type = self._register(self.owner, declaration, instance.get('test_file_path'), owner=True)

    def _roots(self):
        root = self.environment.get('project_root')
        if not root:
            return []
        root = Path(root).resolve()
        paths = [Path(p).resolve() for p in self.environment.get('source_roots', [])]
        if any(not p.is_relative_to(root) for p in paths):
            raise BindingUnknown('MNT_ENGINEERING_CONTEXT_INVALID')
        return paths

    def _register(self, java, declaration, path, owner=False):
        package = java.nodes('package_declaration')
        package = re.sub(r'^package\s+|;$', '', java.text(package[0])).strip() if package else ''
        name = java.text(declaration.child_by_field_name('name'))
        # Inner/local classes need a separate enclosing-binding model.
        if declaration.parent.type != 'program':
            raise BindingUnknown('MNT_NESTED_TYPE_BINDING_UNSUPPORTED')
        qualified = package + '.' + name if package else name
        typ = {'name': qualified, 'package': package, 'java': java, 'node': declaration,
               'path': str(path or ''), 'owner': owner}
        self.types[qualified] = typ
        if path:
            self.used[str(path)] = {'path': str(path), 'sha256_utf8': digest(java.source),
                                    'source_kind': 'TEST_OWNER' if owner else 'PROJECT_SOURCE'}
        return typ

    def _source(self, name):
        if name in self.types:
            return self.types[name]
        relative = Path(*name.split('.')).with_suffix('.java')
        files = [(root/relative).resolve() for root in self._roots() if (root/relative).is_file()]
        if len(files) > 1:
            raise BindingUnknown('MNT_TYPE_BINDING_AMBIGUOUS')
        if not files:
            return None
        path = files[0]
        if not any(path.is_relative_to(root) for root in self._roots()):
            raise BindingUnknown('MNT_ENGINEERING_CONTEXT_INVALID')
        java = Java(path.read_text(encoding='utf-8'))
        matches = [n for n in java.root.named_children if n.type in ('class_declaration', 'interface_declaration')
                   and java.text(n.child_by_field_name('name')) == name.split('.')[-1]]
        if len(matches) != 1:
            raise BindingUnknown('MNT_TYPE_DECLARATION_UNSUPPORTED')
        typ = self._register(java, matches[0], path)
        if typ['name'] != name:
            raise BindingUnknown('MNT_TYPE_BINDING_AMBIGUOUS')
        return typ

    def _jdk(self, name):
        if name in self.jdk:
            return self.jdk[name]
        executable = shutil.which('javap')
        if not executable:
            raise BindingUnknown('MNT_JDK_DECLARATIONS_UNAVAILABLE')
        stat = Path(executable).stat()
        output = _jdk_declarations(executable, (stat.st_size, stat.st_mtime_ns), name)
        self.jdk[name] = output
        self.used['jdk:'+name] = {'type': name, 'source_kind': 'LOCAL_JDK_JAVAP',
                                 'declarations_sha256': digest(output), 'tool': executable}
        return output

    def type_name(self, raw, typ=None):
        typ = typ or self.owner_type
        raw = raw.strip()
        if raw in typ.get('bindings', {}):
            return typ['bindings'][raw]
        if '?' in raw or '...' in raw:
            raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
        if raw == 'var':
            raise BindingUnknown('MNT_VAR_BINDING_UNSUPPORTED')
        if '<' in raw:
            base, arguments = self._type_parts(raw)
            name = self.type_name(base, typ)
            bound = [self.type_name(arg, typ) for arg in arguments]
            parameters = self._parameters(name)
            if len(parameters) != len(bound):
                raise BindingUnknown('MNT_GENERIC_ARITY_MISMATCH')
            self._check_bounds(name, dict(zip(parameters,bound)))
            return name + '<' + ','.join(bound) + '>'
        if raw.endswith('[]'):
            return self.type_name(raw[:-2], typ) + '[]'
        if raw in PRIMITIVES or raw == 'void':
            return raw
        if not re.fullmatch(r'[\w$]+(?:\.[\w$]+)*', raw):
            raise BindingUnknown('MNT_TYPE_BINDING_UNSUPPORTED')
        if '.' in raw:
            if self._source(raw) is not None:
                return raw
            self._jdk(raw)
            return raw
        imports = [typ['java'].text(n) for n in typ['java'].nodes('import_declaration')]
        exact = [i[len('import '):-1].strip() for i in imports
                 if not i.startswith('import static ') and i.endswith('.'+raw+';')]
        same = typ['package']+'.'+raw if typ['package'] else raw
        candidates = set(exact)
        if self._source(same) is not None:
            candidates.add(same)
        if len(candidates) > 1:
            raise BindingUnknown('MNT_TYPE_BINDING_AMBIGUOUS')
        if candidates:
            return self.type_name(next(iter(candidates)), typ) if '.' in next(iter(candidates)) else same
        # Wildcard imports can introduce a competing type; do not guess.
        if any(i.endswith('.*;') and not i.startswith('import static ') for i in imports):
            raise BindingUnknown('MNT_WILDCARD_TYPE_BINDING_UNRESOLVED')
        self._jdk('java.lang.'+raw)
        return 'java.lang.'+raw

    @staticmethod
    def _type_parts(raw):
        """Parse a type reference with the existing AST, retaining arguments."""
        java = Java('class TypeReference { '+raw+' reference; }')
        fields = java.nodes('field_declaration')
        if len(fields) != 1:
            raise BindingUnknown('MNT_TYPE_BINDING_UNSUPPORTED')
        node = fields[0].child_by_field_name('type')
        if node.type != 'generic_type':
            if node.type not in ('type_identifier','scoped_type_identifier'):
                raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
            return java.text(node), []
        parts = node.named_children
        if len(parts) != 2 or parts[1].type != 'type_arguments':
            raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
        args = children(parts[1])
        if any(n.type not in ('type_identifier','scoped_type_identifier','generic_type') for n in args):
            raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
        return java.text(parts[0]), [java.text(n) for n in args]

    def _parameters(self, name):
        typ = self._source(name)
        if typ:
            java, node = typ['java'], typ['node']
        else:
            header = next((line for line in self._jdk(name).splitlines() if '{' in line), '')
            java = Java(header.replace(name, name.rsplit('.',1)[-1], 1) + '}')
            node = java.root.named_children[0]
        parameters = node.child_by_field_name('type_parameters')
        return [java.text(n.named_children[0]) for n in children(parameters)] if parameters else []

    def _check_bounds(self, name, bindings):
        typ = self._source(name)
        if typ is None:
            # JDK type-parameter bounds are read from the actual runtime header.
            header=next(line for line in self._jdk(name).splitlines() if '{' in line)
            java=Java(header.replace(name,name.rsplit('.',1)[-1],1)+'}')
            typ={'name':name,'java':java,'node':java.root.named_children[0], 'package':name.rsplit('.',1)[0]}
        view={**typ,'bindings':bindings}
        for param in children(typ['node'].child_by_field_name('type_parameters')):
            symbol=typ['java'].text(param.named_children[0])
            bounds=next((n for n in param.named_children if n.type=='type_bound'),None)
            for bound in children(bounds) if bounds else []:
                required=self.type_name(typ['java'].text(bound),view)
                if not self._assignable(bindings[symbol],required):
                    raise BindingUnknown('MNT_GENERIC_BOUND_UNPROVEN')

    def _assignable(self, actual, required, seen=()):
        if actual==required or required=='java.lang.Object':return True
        if actual in seen or len(seen)>8:return False
        base,args=self._type_parts(actual);view=self._view(actual)
        if view is None:
            header=next(line for line in self._jdk(base).splitlines() if '{' in line)
            java=Java(header.replace(base,base.rsplit('.',1)[-1],1)+'}')
            view={'name':base,'java':java,'node':java.root.named_children[0],
                  'package':base.rsplit('.',1)[0],'bindings':dict(zip(self._parameters(base),args))}
        for clause in view['node'].named_children:
            if clause.type not in ('superclass','super_interfaces','extends_interfaces'):continue
            nodes=children(clause)
            if len(nodes)==1 and nodes[0].type=='type_list':nodes=children(nodes[0])
            for node in nodes:
                # Bounds are checked recursively by assignability, not erased.
                raw=view['java'].text(node)
                raw_base,raw_args=self._type_parts(raw)
                candidate=self.type_name(raw_base,view)
                if raw_args:candidate+='<'+','.join(self.type_name(a,view) for a in raw_args)+'>'
                if self._assignable(candidate,required,seen+(actual,)):return True
        return False

    def _view(self, name):
        base, args = self._type_parts(name)
        typ = self._source(base)
        if typ is None:
            return None
        parameters = self._parameters(base)
        if parameters and len(args) != len(parameters):
            raise BindingUnknown('MNT_RAW_GENERIC_BINDING_UNSUPPORTED')
        return {**typ, 'bindings': dict(zip(parameters,args)), 'applied_type': name}

    def _hierarchy(self, typ, seen=(), member_name=None):
        if typ['name'] in seen or len(seen) > 8:
            raise BindingUnknown('MNT_INHERITANCE_BINDING_UNRESOLVED')
        yield {**typ, 'lineage': seen}
        for clause in typ['node'].named_children:
            if clause.type not in ('superclass', 'super_interfaces', 'extends_interfaces'):
                continue
            nodes = children(clause)
            if len(nodes) == 1 and nodes[0].type == 'type_list':
                nodes = children(nodes[0])
            for reference in nodes:
                raw = typ['java'].text(reference)
                base = self.type_name(raw.strip(), typ)
                if base == 'java.lang.Object':
                    continue
                source = self._view(base)
                if source is None:
                    metadata = self._jdk(self._type_parts(base)[0])
                    if not member_name or re.search(r'\b'+re.escape(member_name)+r'\s*(?:\(|;|=)', metadata):
                        raise BindingUnknown('MNT_INHERITANCE_BINDING_UNRESOLVED')
                    continue
                yield from self._hierarchy(source, seen+(typ['name'],), member_name)

    def _members(self, typ, kind):
        kinds = {kind,'constant_declaration'} if kind=='field_declaration' else {kind}
        return [n for n in typ['node'].child_by_field_name('body').named_children if n.type in kinds]

    def api(self, call):
        name = call['name']
        node = self.java.at(call['start'], call['end'])
        receiver = self.java.text(node.child_by_field_name('object'))
        if receiver not in ('', 'Assertions', JUNIT):
            raise BindingUnknown('MNT_CUSTOM_ASSERTION_BINDING')
        # Ignore unrelated nested implementations, but inspect actual enclosing
        # members and all relevant inherited members for shadowing.
        hierarchy = list(self._hierarchy(self.owner_type, member_name=name))
        for typ in hierarchy:
            java = typ['java']
            if receiver == '' and any(java.text(n.child_by_field_name('name')) == name
                                      for n in self._members(typ, 'method_declaration')):
                raise BindingUnknown('MNT_CUSTOM_ASSERTION_BINDING')
            for field in self._members(typ, 'field_declaration'):
                for variable in field.named_children:
                    if variable.type == 'variable_declarator' and java.text(variable.child_by_field_name('name')) in ('Assertions', 'org'):
                        raise BindingUnknown('MNT_ASSERTION_NAME_SHADOWED')
            if any(java.text(n.child_by_field_name('name')) in ('Assertions', 'org')
                   for n in typ['node'].child_by_field_name('body').named_children
                   if n.type in ('class_declaration', 'interface_declaration', 'enum_declaration')):
                raise BindingUnknown('MNT_ASSERTION_NAME_SHADOWED')
        if any(self.java.text(n.child_by_field_name('name')) in ('Assertions', 'org')
               for n in walk(self.java.root) if n.type in ('variable_declarator', 'formal_parameter')):
            raise BindingUnknown('MNT_ASSERTION_NAME_SHADOWED')
        imports = [self.owner.text(n).strip() for n in self.owner.nodes('import_declaration')]
        if receiver == JUNIT:
            return {'declaring_type': JUNIT, 'source': 'FULLY_QUALIFIED_JUNIT_API'}
        if receiver == 'Assertions':
            if 'import '+JUNIT+';' not in imports:
                raise BindingUnknown('MNT_ASSERTION_API_INPUT_MISSING')
            matches = [i for i in imports if i.endswith('.Assertions;')]
            if len(matches) != 1:
                raise BindingUnknown('MNT_ASSERTION_API_AMBIGUOUS')
        else:
            matching = []
            for imp in imports:
                match = re.fullmatch(r'import\s+static\s+([\w.]+)\.(\*|\w+)\s*;', imp)
                if match and match[2] in ('*', name): matching.append(match[1])
            if not matching:
                raise BindingUnknown('MNT_ASSERTION_API_INPUT_MISSING')
            if set(matching) != {JUNIT}:
                raise BindingUnknown('MNT_ASSERTION_API_AMBIGUOUS')
        return {'declaring_type': JUNIT, 'source': 'EXACT_IMPORT_AND_RELEVANT_HIERARCHY',
                'hierarchy': [t['name'] for t in hierarchy]}

    def variable(self, name, use, *, explicit_this=False):
        declarations = [n for n in walk(self.java.root) if n.type in ('variable_declarator', 'formal_parameter')
                        and self.java.text(n.child_by_field_name('name')) == name] if not explicit_this else []
        if len(declarations) > 1:
            raise BindingUnknown('MNT_VARIABLE_BINDING_AMBIGUOUS')
        if declarations:
            node = declarations[0]
            if node.start_byte >= use.start_byte:
                raise BindingUnknown('MNT_VARIABLE_BINDING_UNRESOLVED')
            parent = node.parent
            if node.type == 'formal_parameter':
                raw_type = self.java.text(node.child_by_field_name('type'))
            elif parent.type == 'local_variable_declaration':
                # A declaration is visible only inside its containing block.
                block = parent.parent
                if not (block.start_byte <= use.start_byte < block.end_byte):
                    raise BindingUnknown('MNT_VARIABLE_SCOPE_UNSUPPORTED')
                raw_type = self.java.text(parent.child_by_field_name('type'))
            else:
                raise BindingUnknown('MNT_VARIABLE_BINDING_UNRESOLVED')
            return {'type': self.type_name(raw_type), 'name': name, 'source': 'LOCAL_DECLARATION',
                    'initializer': self.java.text(node.child_by_field_name('value'))}
        fields = []
        for typ in self._hierarchy(self.owner_type, member_name=name):
            for field in self._members(typ, 'field_declaration'):
                for node in field.named_children:
                    if node.type == 'variable_declarator' and typ['java'].text(node.child_by_field_name('name')) == name:
                        fields.append((typ, field, node))
        # A field declared by a subtype hides its ancestor's declaration. Local
        # variables do NOT hide this.field. Unrelated interface fields remain unknown.
        fields = [entry for entry in fields if not any(
            other[0]['name'] in entry[0].get('lineage', ()) for other in fields if other is not entry)]
        if len(fields) != 1:
            raise BindingUnknown('MNT_VARIABLE_BINDING_UNRESOLVED')
        typ, field, node = fields[0]
        modifiers = next((typ['java'].text(n) for n in field.named_children if n.type == 'modifiers'), '')
        if typ['name'] != self.owner_type['name'] and 'private' in modifiers.split():
            raise BindingUnknown('MNT_FIELD_ACCESS_BINDING_UNSUPPORTED')
        if (typ['name'] != self.owner_type['name'] and typ['package'] != self.owner_type['package']
                and typ['node'].type != 'interface_declaration' and not {'public','protected'}.intersection(modifiers.split())):
            raise BindingUnknown('MNT_FIELD_ACCESS_BINDING_UNSUPPORTED')
        return {'type': self.type_name(typ['java'].text(field.child_by_field_name('type')), typ),
                'name': name, 'owner': typ['name'], 'source': 'FIELD_DECLARATION',
                'initializer': typ['java'].text(node.child_by_field_name('value'))}

    def methods(self, type_name, name):
        base, type_arguments = self._type_parts(type_name)
        typ = self._view(type_name)
        if typ is None:
            output = self._jdk(base)
            found = []
            for line in output.splitlines():
                match = re.match(r'\s*public\s+(.*?)\s+'+re.escape(name)+r'\((.*?)\)(?:\s+throws\s+.*?)?;', line)
                if not match: continue
                left = match[1].split()
                if '<' in line or '...' in line:
                    raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
                if type_arguments and left[-1] not in PRIMITIVES | {'void'}:
                    raise BindingUnknown('MNT_GENERIC_RETURN_UNSUPPORTED')
                found.append({'owner': base, 'return_type': left[-1],
                              'parameters': [p.strip() for p in match[2].split(',') if p.strip()],
                              'signature': line.strip(), 'source_kind': 'LOCAL_JDK_JAVAP', 'javadoc': ''})
            if not found:
                raise BindingUnknown('MNT_JDK_METHOD_BINDING_UNSUPPORTED')
            return found
        found = []
        for declaring in self._hierarchy(typ, member_name=name):
            java = declaring['java']
            for method in self._members(declaring, 'method_declaration'):
                if java.text(method.child_by_field_name('name')) != name:
                    continue
                modifiers = next((java.text(n) for n in method.named_children if n.type == 'modifiers'), '')
                if declaring['name'] != self.owner_type['name']:
                    if 'private' in modifiers.split():
                        continue
                    if ('public' not in modifiers.split() and declaring['node'].type != 'interface_declaration'
                            and declaring['package'] != self.owner_type['package']):
                        raise BindingUnknown('MNT_METHOD_ACCESS_BINDING_UNSUPPORTED')
                if method.child_by_field_name('type_parameters'):
                    raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
                parameters = children(method.child_by_field_name('parameters'))
                if any(p.type != 'formal_parameter' for p in parameters):
                    raise BindingUnknown('MNT_GENERIC_BINDING_UNSUPPORTED')
                previous = method.prev_named_sibling
                raw_return = java.text(method.child_by_field_name('type'))
                raw_parameters = [java.text(p.child_by_field_name('type')) for p in parameters]
                symbols = set(self._parameters(declaring['name']))
                if any(symbols.intersection(re.findall(r'\b\w+\b', raw)) for raw in [raw_return, *raw_parameters]):
                    raise BindingUnknown('MNT_GENERIC_DEPENDENT_SIGNATURE_UNSUPPORTED')
                if (type_arguments or declaring.get('bindings')) and raw_return not in PRIMITIVES | {'void'}:
                    raise BindingUnknown('MNT_GENERIC_RETURN_UNSUPPORTED')
                found.append({'owner': declaring['name'],
                    'return_type': self.type_name(raw_return, declaring),
                    'parameters': [self.type_name(p, declaring) for p in raw_parameters],
                    'type_bindings': declaring.get('bindings', {}), 'lineage': declaring.get('lineage', ()),
                    'signature': java.text(method).split('{', 1)[0].strip(), 'source_kind': 'PROJECT_SOURCE',
                    'path': declaring['path'], 'line': method.start_point.row+1,
                    'javadoc': java.text(previous) if previous and previous.type == 'block_comment' else ''})
        if not found:
            raise BindingUnknown('MNT_METHOD_DECLARATION_MISSING')
        # Override only the same bound signature on an actual ancestor path.
        # Covariance/default conflicts and unrelated-interface ambiguity fail closed.
        applications={}
        for m in found:
            application=tuple(sorted(m.get('type_bindings',{}).items()))
            if m['owner'] in applications and applications[m['owner']]!=application:
                raise BindingUnknown('MNT_GENERIC_INHERITANCE_CONFLICT')
            applications[m['owner']]=application
        unique = {(m['owner'],tuple(m['parameters']),m['return_type']):m for m in found}
        found = list(unique.values())
        found = [m for m in found if not any(other['owner'] in m.get('lineage', ())
                 and other['parameters'] == m['parameters'] and other['return_type'] == m['return_type']
                 for other in found)]
        return found

    def expression(self, node, use, depth=0):
        if node is None or depth > 3:
            raise BindingUnknown('MNT_EXPRESSION_BINDING_UNSUPPORTED')
        text = self.java.text(node)
        if node.type == 'identifier':
            return self.variable(text, use)
        if node.type == 'string_literal': return {'type': 'java.lang.String', 'source': 'LITERAL'}
        if node.type == 'character_literal': return {'type': 'char', 'source': 'LITERAL'}
        if node.type in ('true', 'false'): return {'type': 'boolean', 'source': 'LITERAL'}
        if node.type == 'null_literal': raise BindingUnknown('MNT_OVERLOAD_BINDING_UNRESOLVED')
        if 'integer_literal' in node.type or 'floating_point_literal' in node.type:
            from .safety_contracts import numeric_type
            return {'type': numeric_type(text), 'source': 'LITERAL'}
        if node.type == 'field_access':
            obj = node.child_by_field_name('object')
            if obj.type == 'this':
                return self.variable(self.java.text(node.child_by_field_name('field')), use, explicit_this=True)
            receiver = self.expression(node.child_by_field_name('object'), use, depth+1)
            name = self.java.text(node.child_by_field_name('field'))
            if name == 'length' and receiver['type'].endswith('[]'):
                return {'type': 'int', 'source': 'JAVA_ARRAY_LENGTH', 'semantic_kind': 'ARRAY_LENGTH', 'receiver': receiver}
            raise BindingUnknown('MNT_FIELD_BINDING_UNSUPPORTED')
        if node.type != 'method_invocation':
            raise BindingUnknown('MNT_EXPRESSION_BINDING_UNSUPPORTED')
        receiver_node = node.child_by_field_name('object')
        if receiver_node is None or receiver_node.type == 'this':
            receiver = {'type': self.owner_type['name'], 'source': 'ENCLOSING_CLASS'}
        else:
            receiver = self.expression(receiver_node, use, depth+1)
        name = self.java.text(node.child_by_field_name('name'))
        methods = self.methods(receiver['type'], name)
        args = children(node.child_by_field_name('arguments'))
        types = [self.expression(arg, use, depth+1)['type'] for arg in args]
        matching = [m for m in methods if m['parameters'] == types]
        if len(matching) != 1:
            raise BindingUnknown('MNT_OVERLOAD_BINDING_UNRESOLVED')
        method = matching[0]
        return {'type': method['return_type'], 'source': 'RESOLVED_METHOD_DECLARATION',
                'receiver': receiver, 'method': method, 'method_name': name}

    def manifest(self):
        return sorted(self.used.values(), key=lambda item: item.get('path') or item.get('type'))

    def empty_constructor_count(self, evidence, call):
        """Narrow no-benefit proof: fresh unescaped object, direct JDK size getter,
        and a constructor containing ONLY empty standard-collection assignments.
        No application-name map, operation counting, or production evaluation.
        """
        receiver=evidence.get('receiver') or {};method=evidence.get('method') or {}
        if receiver.get('source')!='LOCAL_DECLARATION' or method.get('parameters')!=[] or evidence.get('type')!='int':return None
        typ=self._view(receiver['type'])
        if not typ or typ['name']!=method.get('owner'):return None
        node=self.java.at(call['start'],call['end']);block=node.parent.parent
        declarations=[n for n in self.java.nodes('variable_declarator') if self.java.text(n.child_by_field_name('name'))==receiver['name']]
        if len(declarations)!=1:return None
        declaration=declarations[0];initializer=declaration.child_by_field_name('value')
        if declaration.parent.parent!=block or not initializer or initializer.type!='object_creation_expression':return None
        if children(initializer.child_by_field_name('arguments')) or any(n.type=='class_body' for n in initializer.named_children):return None
        creation_type=self.java.text(initializer.child_by_field_name('type'))
        if creation_type.endswith('<>'):creation_type=creation_type[:-2]
        created=self.type_name(creation_type)
        if created not in (typ['name'],receiver['type']):return None
        for statement in block.named_children:
            if declaration.end_byte<=statement.start_byte<node.parent.start_byte:
                if any(self.java.text(n)==receiver['name'] for n in walk(statement) if n.type=='identifier'):return None
        java=typ['java'];getters=[m for m in self._members(typ,'method_declaration')
            if m.start_point.row+1==method.get('line') and java.text(m.child_by_field_name('name'))==evidence.get('method_name')]
        if len(getters)!=1:return None
        body=children(getters[0].child_by_field_name('body'))
        if len(body)!=1 or body[0].type!='return_statement':return None
        values=children(body[0]);read=values[0] if len(values)==1 else None
        if read is None or read.type!='method_invocation' or java.text(read.child_by_field_name('name'))!='size' or children(read.child_by_field_name('arguments')):return None
        obj=read.child_by_field_name('object')
        if obj is None:return None
        if obj.type=='field_access' and obj.child_by_field_name('object').type=='this':field_name=java.text(obj.child_by_field_name('field'))
        elif obj.type=='identifier':field_name=java.text(obj)
        else:return None
        fields=[f for f in self._members(typ,'field_declaration') if any(v.type=='variable_declarator' and java.text(v.child_by_field_name('name'))==field_name for v in f.named_children)]
        if len(fields)!=1:return None
        mods=next((java.text(n) for n in fields[0].named_children if n.type=='modifiers'),'')
        if 'static' in mods.split():return None
        bound_field=self.type_name(java.text(fields[0].child_by_field_name('type')),typ)
        if self._type_parts(bound_field)[0] not in ('java.util.List','java.util.Collection','java.util.Set','java.util.Map'):return None
        constructors=[n for n in self._members(typ,'constructor_declaration') if not children(n.child_by_field_name('parameters'))]
        if len(constructors)!=1:return None
        initializations={}
        for s in children(constructors[0].child_by_field_name('body')):
            values=children(s)
            if s.type!='expression_statement' or len(values)!=1 or values[0].type!='assignment_expression':return None
            assignment=values[0];left=assignment.child_by_field_name('left');right=assignment.child_by_field_name('right')
            if left.type!='field_access' or left.child_by_field_name('object').type!='this':return None
            if (right.type!='object_creation_expression' or children(right.child_by_field_name('arguments'))
                    or any(n.type=='class_body' for n in right.named_children)):return None
            raw=java.text(right.child_by_field_name('type'))
            # A diamond is resolved by the assignment's declared field, not erased
            # to authorize a method binding; this check proves only empty allocation.
            if raw.endswith('<>'):raw=raw[:-2]
            bound=self.type_name(raw,typ)
            if bound not in ('java.util.ArrayList','java.util.LinkedList','java.util.HashSet','java.util.HashMap'):return None
            key=java.text(left.child_by_field_name('field'))
            if key in initializations:return None
            initializations[key]=bound
        if field_name not in initializations:return None
        return {'source':'BOUND_FRESH_EMPTY_JDK_COLLECTION_CONSTRUCTOR', 'constructor':java.text(constructors[0]),
            'getter':java.text(getters[0]),'field':field_name,'field_type':bound_field,'declaration_path':typ['path']}


def explained_zero(evidence, call):
    """No transformation benefit, proved by a bound API plus related context.

    The named JDK length operations describe their semantics, not guessed return
    types. Application names never select a branch. Application count semantics
    require the resolved declaration's @return contract AND its receiver's
    empty input fixture. Business codes without this evidence remain unknown.
    """
    if evidence.get('semantic_kind') == 'ARRAY_LENGTH':
        return 'MNT_EXPLAINED_EMPTY_ARRAY', 'BOUND_ARRAY_LENGTH_ZERO'
    method = evidence.get('method') or {}
    receiver = evidence.get('receiver') or {}
    if (method.get('source_kind') == 'LOCAL_JDK_JAVAP'
            and method.get('owner') in ('java.lang.StringBuilder', 'java.lang.StringBuffer', 'java.lang.String')
            and evidence.get('method_name') == 'length' and method.get('parameters') == []
            and evidence.get('type') == 'int' and len(call['arguments']) == 3):
        message = call['arguments'][-1]
        if re.search(r'(?i)\bempty\b|\b(?:not|no)\b.*\b(?:write|output|characters)\b', message):
            return 'MNT_EXPLAINED_EMPTY_OUTPUT', 'BOUND_JDK_LENGTH_AND_ORIGINAL_MESSAGE'
    doc = method.get('javadoc', '')
    if re.search(r'(?is)@return\s+(?:the\s+)?(?:number|count)\s+of\b', doc):
        initializer = receiver.get('initializer', '')
        if initializer:
            try:
                java = Java('Object fixture = '+initializer+';')
                if any(n.type == 'array_initializer' and not children(n) for n in walk(java.root)):
                    return 'MNT_EXPLAINED_ZERO_COUNT', 'BOUND_RETURN_COUNT_CONTRACT_AND_RECEIVER_EMPTY_FIXTURE'
            except ValueError:
                pass
    return None
