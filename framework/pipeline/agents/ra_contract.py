"""RA v2: a source-bound, deletion-only exception-test subset.

No model, build, JVM, or service is used to establish this proof. The existing
straight-line RA contract remains in safety_contracts.analyze_ra.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .safety_contracts import (Java, apply_edits, children, digest,
                               call_node, resolve_api, walk, plain_string)

VERSION = '2.0'
RULE_VERSION = '4.2'
PROMPT_VERSION = 'ra-v2-local-deletion-1'
SAFE = 'SAFE_CONSTANT_ASSERTION_IN_CATCH'


def semantic_category(java, calls):
    """Review labels are conservative hints, never proof that an operand is wrong."""
    methods = java.nodes('method_declaration')
    name = java.text(methods[0].child_by_field_name('name')).lower() if methods else ''
    selfs = [c for c in calls if c['name'] in ('assertEquals', 'assertSame')
             and len(c['arguments']) == 2 and c['arguments'][0] == c['arguments'][1]]
    if 'equalsself' not in name:
        for a, b in zip(calls, calls[1:]):
            if a['name'] == b['name'] == 'assertEquals' and len(a['arguments']) == len(b['arguments']) == 2:
                x, y = a['arguments']; u, v = b['arguments']
                if (x != y and u == v == y) or (x == y == v and u != v):
                    return 'POSSIBLE_WRONG_OPERAND'
    if any(word in name for word in ('equals', 'hashcode', 'reflexiv', 'clone', 'copy')):
        return 'CONTRACT_OR_REFLEXIVITY_CHECK'
    return 'BINDING_UNPROVEN' if selfs else None


def _root(instance, owner):
    path = Path(instance.get('test_file_path') or '')
    relative = Path(instance.get('test_relative_path') or '')
    if not path.is_file() or relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('RA_V2_SOURCE_ROOT_UNPROVEN')
    path = path.resolve()
    root = path
    for _ in relative.parts:
        root = root.parent
    if (root / relative).resolve() != path or path.read_text(encoding='utf-8') != owner:
        raise ValueError('RA_V2_OWNER_SOURCE_MISMATCH')
    return root


def _owner_for_api(owner, instance):
    """Resolve just the audited listener interface; do not relax shared API rules."""
    from .api_scope import owner_scope, _declarations
    target = Java(instance['test_code'])
    methods = target.nodes('method_declaration')
    if len(methods) != 1:
        raise ValueError('RA_V2_API_OWNER_UNPROVEN')
    java, method, classes = owner_scope(target, methods[0], owner)
    clauses = [n for cls in classes for n in cls.named_children]
    if any(n.type == 'superclass' for n in clauses):
        raise ValueError('RA_V2_API_INHERITANCE_UNPROVEN')
    interfaces = [n for n in clauses if n.type in ('super_interfaces', 'extends_interfaces')]
    if not interfaces:
        return owner, []
    if len(interfaces) != 1 or java.text(interfaces[0]) != 'implements MarkerChangeListener':
        raise ValueError('RA_V2_API_INTERFACE_UNPROVEN')
    imports = [java.text(n).strip() for n in java.nodes('import_declaration')]
    if 'import org.jfree.chart.event.MarkerChangeListener;' not in imports:
        raise ValueError('RA_V2_API_INTERFACE_UNPROVEN')
    if any(java.text(n.child_by_field_name('name')) == 'MarkerChangeListener'
           for n in _declarations(java, method, classes) if n.type in ('class_declaration', 'interface_declaration')):
        raise ValueError('RA_V2_INTERFACE_SHADOWED')
    root = _root(instance, owner)
    path = root / 'src/main/java/org/jfree/chart/event/MarkerChangeListener.java'
    source = path.read_text(encoding='utf-8')
    # Entire declaration is checked, including ancestors and all members. The
    # terminal java.util.EventListener is the standard memberless JDK interface.
    from .safety_contracts import ast_tokens
    expected = ('package org.jfree.chart.event; import java.util.EventListener; '
                'import org.jfree.chart.plot.Marker; '
                'public interface MarkerChangeListener extends EventListener { '
                'void markerChanged(MarkerChangeEvent event); }')
    if ast_tokens(source) != ast_tokens(expected):
        raise ValueError('RA_V2_INTERFACE_MEMBERS_UNPROVEN')
    if (path.parent / 'EventListener.java').exists():
        raise ValueError('RA_V2_INTERFACE_ANCESTOR_UNPROVEN')
    evidence = [{'path': str(path.resolve()), 'sha256_utf8': digest(source),
                 'qualified_type': 'org.jfree.chart.event.MarkerChangeListener',
                 'ancestor': 'java.util.EventListener', 'assertion_members': []}]
    sanitized = apply_edits(owner, [(*java.span(interfaces[0]), '')])
    return sanitized, evidence


def _exception_binding(owner, instance, name):
    if name not in ('IllegalArgumentException', 'java.lang.IllegalArgumentException'):
        raise ValueError('RA_V2_EXCEPTION_TYPE_UNPROVEN')
    java = Java(owner)
    if any(java.text(n.child_by_field_name('name')) in ('IllegalArgumentException', 'java')
           for n in walk(java.root) if n.type in ('class_declaration', 'interface_declaration',
               'enum_declaration', 'record_declaration', 'type_parameter', 'variable_declarator', 'formal_parameter')):
        raise ValueError('RA_V2_EXCEPTION_TYPE_SHADOWED')
    for n in java.nodes('import_declaration'):
        imp = java.text(n)
        if imp.endswith('.IllegalArgumentException;') and imp != 'import java.lang.IllegalArgumentException;':
            raise ValueError('RA_V2_EXCEPTION_TYPE_UNPROVEN')
        if imp.endswith('.*;') and imp != 'import static org.junit.jupiter.api.Assertions.*;':
            raise ValueError('RA_V2_EXCEPTION_TYPE_UNPROVEN')
    # Resolve the package-level shadow boundary from the real test owner.
    if instance.get('test_file_path') and Path(instance['test_file_path']).is_file():
        root = _root(instance, owner)
        packages = java.nodes('package_declaration')
        package = java.text(packages[0])[len('package'):].strip().rstrip(';') if packages else ''
        for tree in ('src/main/java', 'src/test/java'):
            if (root / tree / package.replace('.', '/') / 'IllegalArgumentException.java').exists():
                raise ValueError('RA_V2_EXCEPTION_TYPE_SHADOWED')
    elif not any(java.text(n) == 'import java.lang.IllegalArgumentException;' for n in java.nodes('import_declaration')):
        raise ValueError('RA_V2_EXCEPTION_PACKAGE_UNPROVEN')
    return 'java.lang.IllegalArgumentException'


def analyze_catch(java, owner, calls, proof, finish, instance):
    """Return None for v1 methods; catch-containing methods use only this subset."""
    if not java.nodes('catch_clause'):
        return None
    proof['ra_contract_version'] = VERSION
    proof['ra_semantic_category'] = 'BINDING_UNPROVEN'

    def review(reason, category='BINDING_UNPROVEN'):
        proof['ra_semantic_category'] = category
        proof['targets'] = []
        return finish('REVIEW_INSUFFICIENT_CONTEXT', reason)

    category = semantic_category(java, calls)
    if category:
        return review('RA_V2_PROTECTED_SEMANTIC_CONTEXT', category)
    # Calls to copy/clone/equals/hashCode, even under an unrelated method name,
    # are outside this deliberately narrow setter/null-argument pattern.
    if any(re.fullmatch(r'equals|hashCode|clone|copy\w*', java.text(n.child_by_field_name('name')), re.I)
           for n in java.nodes('method_invocation')):
        return review('RA_V2_PROTECTED_SEMANTIC_CONTEXT', 'CONTRACT_OR_REFLEXIVITY_CHECK')
    try:
        api_owner, dependencies = _owner_for_api(owner, instance)
        if not calls or any(not resolve_api(java, call_node(java, c), api_owner) or c['unsupported_reasons'] for c in calls):
            return review('RA_V2_ASSERTION_API_UNPROVEN')
        methods = java.nodes('method_declaration')
        body = methods[0].child_by_field_name('body')
        statements = children(body)
        tries = java.nodes('try_statement')
        if (len(tries) != 1 or len(java.nodes('catch_clause')) != 1
                or tries[0].parent != body or statements[-1] != tries[0]
                or any(n.type in ('try_with_resources_statement', 'finally_clause', 'if_statement',
                    'switch_expression', 'for_statement', 'enhanced_for_statement', 'while_statement',
                    'do_statement', 'return_statement', 'throw_statement', 'lambda_expression',
                    'class_declaration', 'synchronized_statement') for n in walk(java.root))):
            return review('RA_V2_CONTROL_FLOW_UNPROVEN')
        tr = tries[0]; catch = java.nodes('catch_clause')[0]
        cb = catch.child_by_field_name('body'); cs = children(cb)
        if len(cs) != 1 or cs[0].type != 'expression_statement':
            return review('RA_V2_CATCH_NOT_SINGLE_CONSTANT_ASSERTION')
        target = next((c for c in calls if call_node(java, c).parent == cs[0]), None)
        if not target or (target['name'], target['arguments']) != ('assertTrue', ['true']):
            return review('RA_V2_CONSTANT_ASSERTION_UNPROVEN', 'ORACLE_LOSS_RISK')
        if java.text(cs[0]) != 'assertTrue(true);':
            return review('RA_V2_EXACT_LITERAL_STATEMENT_REQUIRED')
        parameter = next(n for n in children(catch) if n.type == 'catch_formal_parameter')
        typ = next(n for n in children(parameter) if n.type == 'catch_type')
        exception = _exception_binding(owner, instance, java.text(typ))
        tb = tr.child_by_field_name('body'); ts = children(tb)
        if len(ts) != 2 or any(n.type != 'expression_statement' for n in ts):
            return review('RA_V2_EXPECTED_EXCEPTION_ORACLE_UNPROVEN', 'ORACLE_LOSS_RISK')
        trigger, failure = (children(n)[0] for n in ts)
        if (trigger.type != 'method_invocation' or failure.type != 'method_invocation'
                or java.text(failure.child_by_field_name('name')) != 'fail'
                or not resolve_api(java, failure, api_owner, 'fail')):
            return review('RA_V2_EXPECTED_EXCEPTION_ORACLE_UNPROVEN', 'ORACLE_LOSS_RISK')
        fa = children(failure.child_by_field_name('arguments'))
        if len(fa) != 1 or not plain_string(java.text(fa[0])):
            return review('RA_V2_FAIL_MESSAGE_UNPROVEN')
        receiver = java.text(trigger.child_by_field_name('object'))
        setter = java.text(trigger.child_by_field_name('name'))
        if (not re.fullmatch(r'[A-Za-z_$][\w$]*', receiver) or not re.fullmatch(r'set[A-Z]\w*', setter)
                or java.text(trigger.child_by_field_name('arguments')) != '(null)'):
            return review('RA_V2_TRIGGER_NOT_NULL_SETTER')
        # Exactly the audited prefix: construct, listen, reset event, default
        # check, update, new-value check, event-source check. No hidden branch.
        if len(statements) != 8:
            return review('RA_V2_REMAINING_ORACLE_SHAPE_UNPROVEN', 'ORACLE_LOSS_RISK')
        prefix = [java.text(n) for n in statements[:7]]
        if not re.fullmatch(r'\w+\s+' + re.escape(receiver) + r'\s*=\s*new\s+\w+\([^;]*\);', prefix[0]):
            return review('RA_V2_RECEIVER_UNPROVEN')
        if prefix[1] != receiver + '.addChangeListener(this);' or prefix[2] != 'this.lastEvent = null;':
            return review('RA_V2_EVENT_ORACLE_UNPROVEN', 'ORACLE_LOSS_RISK')
        before = [c for c in calls if call_node(java, c).start_byte < tr.start_byte]
        getter = receiver + '.get' + setter[3:] + '()'
        if (len(before) != 3 or any(c['name'] != 'assertEquals' or len(c['arguments']) != 2 for c in before)
                or before[0]['arguments'][1] != getter or before[1]['arguments'][1] != getter
                or before[0]['arguments'][0] == before[1]['arguments'][0]
                or before[2]['arguments'] != [receiver, 'this.lastEvent.getMarker()']
                or any(c['arguments'][0] == c['arguments'][1] for c in before)
                or any(call_node(java, c).parent != statements[index] for c, index in zip(before, (3, 5, 6)))):
            return review('RA_V2_REMAINING_ORACLE_SHAPE_UNPROVEN', 'ORACLE_LOSS_RISK')
        if prefix[4] != receiver + '.' + setter + '(' + before[1]['arguments'][0] + ');':
            return review('RA_V2_PROPERTY_UPDATE_UNPROVEN', 'ORACLE_LOSS_RISK')
        if len(calls) != 4:  # three equality checks and one removable assertion; fail is checked separately
            return review('RA_V2_NON_TARGET_ASSERTION_UNPROVEN')
        start, end = java.span(cs[0])
        proof.update(ra_semantic_category=SAFE, ra_transform='CATCH_CONSTANT_DELETION',
                     ra_prompt_version=PROMPT_VERSION,
                     ra_dependency_sources=dependencies,
                     remaining_oracle_ids=[c['assertion_id'] for c in before],
                     targets=[{**target, 'statement_start': start, 'statement_end': end,
                               'conclusion': SAFE, 'catch_start': java.span(catch)[0],
                               'catch_end': java.span(catch)[1], 'exception_type': exception,
                               'protected_statement': java.text(ts[0]), 'failure_statement': java.text(ts[1]),
                               'catch_other_statements': [], 'replacement': ''}],
                     ra_exception_oracle={'trigger': java.text(ts[0]), 'fail': java.text(ts[1]),
                                          'caught_type': exception, 'fail_not_caught': True})
        return finish('REFACTOR_LOW_RISK', SAFE, ['REMOVE_TARGET_ASSERTION_STATEMENT'])
    except (ValueError, OSError, StopIteration, IndexError):
        return review('RA_V2_BINDING_UNPROVEN')


def expected_candidate(source, proof):
    return apply_edits(source, [(t['statement_start'], t['statement_end'], '') for t in proof['targets']])


def _format_insensitive_tree(source):
    """Keep syntax, token boundaries and comment ownership; omit layout gaps only.

    Comments and literals are atomic so whitespace inside them remains data.
    Unlike the shared ast_tokens helper, comments are retained in their exact
    tree position. Parsing also rejects malformed/ambiguous prelexical input.
    """
    from .refactoring_contract import normalized_source
    java = Java(normalized_source(source))

    def signature(node):
        if not node.children or node.type in {
            'line_comment', 'block_comment', 'string_literal', 'character_literal',
        }:
            return node.type, java.text(node)
        return node.type, tuple(
            (node.field_name_for_child(index), signature(child))
            for index, child in enumerate(node.children)
        )

    return signature(java.root)


def validate_candidate(original, candidate, proof):
    # The expected program is still derived ONLY from source-bound target ranges.
    # Permit formatting around tokens, never edits to other tokens or structure.
    expected = expected_candidate(original, proof)
    if _format_insensitive_tree(candidate) != _format_insensitive_tree(expected):
        return False, 'RA_V2_ONLY_EXACT_LOCAL_DELETION_ALLOWED'
    return True, 'RA_V2_TARGET_DELETED_AND_NON_TARGET_TOKENS_PRESERVED'


def materialize_file(owner, candidate, contract):
    """Preserve the real owner's first-line indent and trailing newline exactly."""
    from .refactoring_contract import normalized_source
    lines = owner.splitlines(keepends=True)
    start = sum(map(len, lines[:contract['method_start_line'] - 1]))
    end = sum(map(len, lines[:contract['method_end_line']]))
    region = owner[start:end]
    prefix = region[:len(region) - len(region.lstrip())]
    suffix = region[len(region.rstrip()):]
    method = prefix + normalized_source(candidate)
    return method, owner[:start] + method + suffix + owner[end:]


def build_prompt(instance, proof):
    if proof.get('decision') != 'REFACTOR_LOW_RISK' or proof.get('ra_semantic_category') != SAFE:
        raise ValueError('RA_V2_APPROVED_CATCH_PROOF_REQUIRED')
    return (
        'RA v2 safety contract: SAFE_CONSTANT_ASSERTION_IN_CATCH.\n'
        'Return exactly one complete Java test method, without Markdown or explanations.\n'
        'DELETE_ONLY_APPROVED_CONSTANT_ASSERTION: delete only the exact target statement ranges.\n'
        'PRESERVE_TRY_CATCH_STRUCTURE: retain try/catch, exception type, variable and ordering; keep the empty catch.\n'
        'PRESERVE_OTHER_CATCH_STATEMENTS: do not change any non-target catch text or comment.\n'
        'PRESERVE_OTHER_ASSERTIONS: retain fail, property/event assertions and their operands/messages exactly.\n'
        'NO_UNRELATED_FORMATTING: preserve all non-target text, indentation, comments, variables, signatures and annotations.\n'
        'NO_OTHER_RA_REMOVAL: do not touch self-comparison, equals/hashCode/reflexivity, clone/copy or wrong operands.\n'
        'NO_CONTROL_FLOW_REWRITE: do not introduce assertThrows or alter the exception trigger or fail path.\n'
        'If these constraints cannot be met, return REFUSE_UNSAFE_REFACTORING.\n'
        'Source-bound proof: ' + json.dumps(proof, ensure_ascii=False) + '\n'
        'Original method:\n' + instance['test_code'] + '\n')
