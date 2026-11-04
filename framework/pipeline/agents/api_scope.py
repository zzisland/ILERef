"""Closed lexical API evidence, scoped to one uniquely bound source method.

This does not solve inherited members or expand the supported API subset.
"""
from functools import lru_cache
import re

API_BINDING_RULE_VERSION = 'lexical-api-scope-2'
TYPES = {'class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration'}


@lru_cache(maxsize=32)
def _owner_index(source):
    from .safety_contracts import Java, ast_node_tokens
    owner = Java(source)
    index = {}
    for method in owner.nodes('method_declaration'):
        index.setdefault(ast_node_tokens(owner, method), []).append(method)
    return owner, index


def owner_scope(java, node, class_source):
    """Map a detached method node into the full owner without guessing by name."""
    from .safety_contracts import ast_node_tokens
    if node is None or not class_source:
        raise ValueError('API_SOURCE_CONTEXT_MISSING')
    method = node
    route = []
    while method and method.type != 'method_declaration':
        parent = method.parent
        if parent is None:
            raise ValueError('API_TARGET_METHOD_UNKNOWN')
        siblings = [c for c in parent.named_children if c.type not in ('line_comment', 'block_comment')]
        route.append(siblings.index(method))
        method = parent
    if method is None:
        raise ValueError('API_TARGET_METHOD_UNKNOWN')
    owner, index = _owner_index(class_source)
    matches = index.get(ast_node_tokens(java, method), [])
    if len(matches) != 1:
        raise ValueError('API_TARGET_METHOD_NOT_UNIQUE')
    mapped = matches[0]
    for position in reversed(route):
        siblings = [c for c in mapped.named_children if c.type not in ('line_comment', 'block_comment')]
        mapped = siblings[position]
    if ast_node_tokens(java, node) != ast_node_tokens(owner, mapped):
        raise ValueError('API_CALL_SOURCE_MISMATCH')
    classes = []
    current = mapped
    while current is not None:
        if current.type == 'class_body' and current.parent.type not in TYPES:
            raise ValueError('API_ANONYMOUS_OWNER_UNPROVEN')
        if current.type in TYPES:
            # Named member types have an enclosing lexical class. Local types
            # require capture/type resolution outside this closed subset.
            if current.parent.type not in ('program', 'class_body', 'interface_body', 'enum_body'):
                raise ValueError('API_LOCAL_OWNER_UNPROVEN')
            classes.append(current)
        current = current.parent
    if not classes:
        raise ValueError('API_DECLARING_OWNER_UNKNOWN')
    return owner, mapped, classes


def _declarations(owner, call, classes):
    """Only members and locals visible on the call's enclosing scope chain."""
    from .safety_contracts import walk
    # Top-level type names are visible throughout the compilation unit; their
    # members and inheritance are not members of another top-level class.
    yield from (c for c in owner.root.named_children if c.type in TYPES)
    for cls in classes:
        yield cls
        members = list(cls.child_by_field_name('body').named_children)
        members = [nested for member in members for nested in
                   (member.named_children if member.type == 'enum_body_declarations' else [member])]
        for member in members:
            if member.type in TYPES or member.type == 'method_declaration':
                yield member
            elif member.type in ('field_declaration', 'constant_declaration'):
                yield from (n for n in member.named_children if n.type == 'variable_declarator')
            elif member.type == 'enum_constant':
                yield member
        record_parameters = cls.child_by_field_name('parameters')
        if record_parameters:
            yield from (n for n in walk(record_parameters) if n.type in ('formal_parameter', 'spread_parameter'))
        parameters = cls.child_by_field_name('type_parameters')
        if parameters:
            yield from (n for n in walk(parameters) if n.type == 'type_parameter')
    current = call.parent
    while current and current.type not in TYPES:
        if current.type in ('block', 'constructor_body', 'switch_block_statement_group'):
            for statement in current.named_children:
                if statement.start_byte > call.start_byte:
                    continue
                if statement.type == 'local_variable_declaration':
                    yield from (n for n in statement.named_children if n.type == 'variable_declarator' and n.start_byte <= call.start_byte)
                elif statement.type in TYPES:
                    yield statement
        elif current.type == 'switch_block':
            for group in current.named_children:
                if group.type != 'switch_block_statement_group':
                    continue
                for statement in group.named_children:
                    if statement.start_byte <= call.start_byte and statement.type == 'local_variable_declaration':
                        yield from (n for n in statement.named_children if n.type == 'variable_declarator' and n.start_byte <= call.start_byte)
        elif current.type in ('method_declaration', 'lambda_expression'):
            parameters = current.child_by_field_name('parameters')
            if parameters:
                if parameters.type == 'identifier':
                    yield parameters
                else:
                    yield from (n for n in walk(parameters) if n.type in ('formal_parameter', 'spread_parameter') or
                                (n.type == 'identifier' and n.parent.type == 'inferred_parameters'))
            type_parameters = current.child_by_field_name('type_parameters')
            if type_parameters:
                yield from (n for n in walk(type_parameters) if n.type == 'type_parameter')
            # Pattern variable visibility is flow-sensitive. Keep conflicting
            # preceding patterns in this method conservative; never consult
            # patterns in unrelated methods/classes.
            body = current.child_by_field_name('body')
            if body:
                for pattern in walk(body):
                    if pattern.type == 'instanceof_expression' and pattern.start_byte < call.start_byte:
                        variable = pattern.child_by_field_name('name')
                        if variable:
                            yield variable
        elif current.type == 'catch_clause':
            yield from (n for n in current.named_children if n.type == 'catch_formal_parameter')
        elif current.type == 'for_statement':
            for n in current.named_children:
                if n.type == 'local_variable_declaration':
                    yield from (c for c in n.named_children if c.type == 'variable_declarator' and c.start_byte <= call.start_byte)
        elif current.type == 'enhanced_for_statement':
            name = current.child_by_field_name('name')
            body = current.child_by_field_name('body')
            if name and body and body.start_byte <= call.start_byte <= body.end_byte:
                yield name
        elif current.type == 'try_with_resources_statement':
            resources = current.child_by_field_name('resources')
            if resources:
                yield from (n for n in walk(resources) if n.type == 'resource' and n.start_byte <= call.start_byte)
        current = current.parent


def _name(owner, declaration):
    if declaration.type == 'identifier':
        return owner.text(declaration)
    node = declaration.child_by_field_name('name')
    # Varargs and type parameters have grammar-specific name nodes.
    if node is None and declaration.type in ('spread_parameter', 'type_parameter'):
        kinds = ('variable_declarator',) if declaration.type == 'spread_parameter' else ('identifier', 'type_identifier')
        node = next((n for n in declaration.named_children if n.type in kinds), None)
        if node is not None and node.type == 'variable_declarator':
            node = node.child_by_field_name('name')
    return owner.text(node)


def resolve_api_evidence(java, node, class_source, name=None):
    from .safety_contracts import JUNIT
    evidence = {'api_binding_rule_version': API_BINDING_RULE_VERSION, 'bound': False,
                'declaring_api_owner': None, 'source_owner': None, 'scope_chain': [], 'import_sources': [],
                'reason': 'API_SOURCE_CONTEXT_MISSING', 'rejected_node': None}
    if node is None:
        return evidence
    name = name or java.text(node.child_by_field_name('name'))
    receiver = java.text(node.child_by_field_name('object'))
    evidence.update(query_name=name, receiver=receiver)
    def reject(reason, rejected=None):
        evidence['reason'] = reason
        if rejected is not None:
            evidence['rejected_node'] = {'kind': rejected.type, 'line': rejected.start_point.row+1, 'text': owner.text(rejected)}
        return evidence
    if receiver not in ('', 'Assertions', JUNIT):
        return reject('API_RECEIVER_UNSUPPORTED')
    try:
        owner, call, classes = owner_scope(java, node, class_source)
    except (ValueError, IndexError) as exc:
        evidence['scope_failure'] = str(exc)
        return reject('API_SCOPE_UNPROVEN')
    evidence['scope_chain'] = [{'class': _name(owner, cls), 'line': cls.start_point.row+1} for cls in classes]
    packages = owner.nodes('package_declaration')
    package = re.sub(r'^package\s+|;$', '', owner.text(packages[0])).strip() if packages else ''
    names = '.'.join(_name(owner, cls) for cls in reversed(classes))
    evidence['source_owner'] = (package+'.' if package else '') + names
    evidence['call_line'] = call.start_point.row+1
    # Preserve unknown actual inheritance. Fully qualified receivers keep the
    # former inheritance-independent path, with lexical 'org' checks scoped.
    if receiver != JUNIT:
        for cls in classes:
            for clause in cls.named_children:
                if clause.type in ('superclass', 'super_interfaces', 'extends_interfaces'):
                    return reject('API_ACTUAL_INHERITANCE_UNPROVEN', clause)
    conflicts = {'org'} if receiver == JUNIT else {'Assertions', name}
    for declaration in _declarations(owner, call, classes):
        if declaration.type == 'method_declaration':
            # Retain the existing conservative same-name member restriction.
            if receiver != JUNIT and _name(owner, declaration) == name:
                return reject('API_VISIBLE_METHOD_CONFLICT', declaration)
        elif _name(owner, declaration) in conflicts:
            return reject('API_VISIBLE_NAME_CONFLICT', declaration)
    imports = [owner.text(n).strip() for n in owner.root.named_children if n.type == 'import_declaration']
    evidence['import_sources'] = imports
    if receiver == JUNIT:
        source = 'FULLY_QUALIFIED_JUNIT_API'
    elif receiver == 'Assertions':
        if 'import org.junit.jupiter.api.Assertions;' not in imports or any(
                i.endswith('.Assertions;') and i != 'import org.junit.jupiter.api.Assertions;' for i in imports):
            return reject('API_TYPE_IMPORT_UNPROVEN')
        source = 'EXACT_JUNIT_TYPE_IMPORT'
    else:
        matching = []
        for imp in imports:
            m = re.fullmatch(r'import\s+static\s+([\w.]+)\.(\*|\w+)\s*;', imp)
            if m and m[2] in ('*', name):
                matching.append(m[1])
        if not matching or set(matching) != {JUNIT}:
            return reject('API_STATIC_IMPORT_UNPROVEN')
        source = 'EXACT_JUNIT_STATIC_IMPORT'
    evidence.update(bound=True, declaring_api_owner=JUNIT, reason=source)
    return evidence
