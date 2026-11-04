"""Experimental CTL v1: bounded, ordered unrolling of local fixture preparation.

This is a closed transformation language, not general loop equivalence analysis.
No user code runs during proof construction. Limits are readability limits, not
Java limits. Unsupported evidence is retained and never authorizes generation.
"""
from __future__ import annotations

import json
import re

CTL = 'Conditional_Test_Logic'
VERSION = '1.0-experimental'
MAX_ITERATIONS = 8
MAX_OPERATIONS = 16
MAX_EXPANSION_CHARS = 2048
CONTROL = {'if_statement', 'for_statement', 'enhanced_for_statement', 'while_statement',
           'do_statement', 'switch_expression', 'switch_statement', 'ternary_expression'}
BOUNDARIES = {'try_statement', 'try_with_resources_statement', 'synchronized_statement',
              'lambda_expression', 'class_declaration', 'break_statement', 'continue_statement',
              'return_statement', 'throw_statement', 'assert_statement', 'labeled_statement'}


class Unsupported(ValueError):
    pass


def _fail(reason):
    raise Unsupported(reason)


def _string(text):
    # Deliberately exclude Java-only octal/unicode/text-block forms; no eval.
    try:
        if not text.startswith('"') or text.startswith('"""') or '\\u' in text:
            _fail('CTL_LITERAL_VALUE_UNPROVEN')
        value = json.loads(text)
        if not isinstance(value, str):
            _fail('CTL_LITERAL_VALUE_UNPROVEN')
        return value
    except (ValueError, TypeError):
        _fail('CTL_LITERAL_VALUE_UNPROVEN')


def _length(text):
    return len(_string(text).encode('utf-16-le')) // 2


def _bound_type(owner, name, qualified):
    from .safety_contracts import Java, walk
    tree = Java(owner)
    simple_shadow = False
    # Reject local/nested/custom types and package-prefix shadowing. Explicit
    # java.util.ArrayList and java.lang.StringBuilder are the only receivers.
    for node in walk(tree.root):
        if node.type in {'class_declaration', 'interface_declaration', 'enum_declaration',
                         'record_declaration', 'type_parameter', 'variable_declarator', 'formal_parameter'}:
            bound_name = tree.text(node.child_by_field_name('name'))
            if bound_name == 'java':
                _fail('CTL_CONTAINER_TYPE_BINDING_UNPROVEN')
            if bound_name == name:
                simple_shadow = True
    imports = [re.sub(r'\s+', '', tree.text(n)) for n in tree.nodes('import_declaration')]
    conflicts = [i for i in imports if i.endswith('.' + name + ';') and i != 'import' + qualified + ';']
    # An implicit java.lang name or on-demand import alone cannot rule out a
    # same-package type in a different file. Explicit imports or FQ spellings
    # are required in this first closed subset. Inherited member types also
    # require hierarchy evidence before simple-name binding can be approved.
    inherited = any(n.type in {'superclass', 'super_interfaces'} for n in walk(tree.root))
    return not (inherited or simple_shadow or conflicts) and 'import' + qualified + ';' in imports


def _receiver(java, loop, name, owner):
    from .safety_contracts import children, walk
    variables = [n for n in java.nodes('variable_declarator') if java.text(n.child_by_field_name('name')) == name]
    if len(variables) != 1:
        _fail('CTL_RECEIVER_BINDING_UNPROVEN')
    var = variables[0]
    decl = var.parent
    if decl.type != 'local_variable_declaration' or decl.parent != loop.parent or decl.end_byte >= loop.start_byte:
        _fail('CTL_RECEIVER_NOT_DIRECT_LOCAL')
    if len([n for n in children(decl) if n.type == 'variable_declarator']) != 1:
        _fail('CTL_RECEIVER_BINDING_UNPROVEN')
    value = var.child_by_field_name('value')
    if value is None or value.type != 'object_creation_expression' or any(n.type == 'class_body' for n in value.named_children):
        _fail('CTL_RECEIVER_CONSTRUCTOR_UNSUPPORTED')
    declared = re.sub(r'\s+', '', java.text(decl.child_by_field_name('type')))
    created = re.sub(r'\s+', '', java.text(value.child_by_field_name('type')))
    base = declared.split('<')[0]
    if base in {'StringBuilder', 'java.lang.StringBuilder'}:
        kind, qualified = 'StringBuilder', 'java.lang.StringBuilder'
        if declared != base or created not in {kind, qualified}:
            _fail('CTL_CONTAINER_TYPE_BINDING_UNPROVEN')
    elif base in {'ArrayList', 'java.util.ArrayList', 'List', 'java.util.List'}:
        kind, qualified = 'ArrayList', 'java.util.ArrayList'
        if declared[len(base):] not in {'<Integer>', '<String>', '<java.lang.Integer>', '<java.lang.String>'}:
            _fail('CTL_CONTAINER_ELEMENT_TYPE_UNSUPPORTED')
        if created.split('<')[0] not in {kind, qualified} or created[len(created.split('<')[0]):] not in {'<>', declared[len(base):]}:
            _fail('CTL_CONTAINER_TYPE_BINDING_UNPROVEN')
        if base in {'List', 'java.util.List'}:
            list_import = _bound_type(owner, 'List', 'java.util.List')
            if base == 'List' and not list_import:
                _fail('CTL_CONTAINER_TYPE_BINDING_UNPROVEN')
    else:
        _fail('CTL_CONTAINER_TYPE_BINDING_UNPROVEN')
    implicit_ok = _bound_type(owner, kind, qualified)
    if (base == kind or created.split('<')[0] == kind) and not implicit_ok:
        _fail('CTL_CONTAINER_TYPE_BINDING_UNPROVEN')
    # No reads, aliases, reassignment, calls, capture or escape between new and
    # the loop. Escape after the loop is allowed: its complete state is equal.
    args = children(value.child_by_field_name('arguments'))
    if kind == 'ArrayList' and args:
        _fail('CTL_RECEIVER_CONSTRUCTOR_UNSUPPORTED')
    if kind == 'StringBuilder' and (len(args) > 1 or args and args[0].type != 'string_literal'):
        _fail('CTL_RECEIVER_CONSTRUCTOR_UNSUPPORTED')
    initial_length = _length(java.text(args[0])) if args else 0
    permitted = set()
    for n in walk(java.root):
        if n.type != 'identifier' or java.text(n) != name or not decl.end_byte <= n.start_byte < loop.start_byte:
            continue
        call=n.parent
        if (kind != 'StringBuilder' or call.type != 'method_invocation' or
                call.child_by_field_name('object') != n or java.text(call.child_by_field_name('name')) != 'append' or
                call.parent.type != 'expression_statement' or call.parent.parent != loop.parent):
            _fail('CTL_RECEIVER_ESCAPE_OR_INTERVENING_USE')
        call_args=children(call.child_by_field_name('arguments'))
        if len(call_args) != 1 or call_args[0].type != 'string_literal':
            _fail('CTL_RECEIVER_ESCAPE_OR_INTERVENING_USE')
        initial_length += _length(java.text(call_args[0]))
        permitted.add(call.start_byte)
    return kind, declared, initial_length, permitted


def _target(java, loop, owner):
    from .safety_contracts import children, walk, COMMENTS, apply_edits
    method = java.nodes('method_declaration')[0]
    if loop.parent != method.child_by_field_name('body'):
        _fail('CTL_LOOP_NOT_DIRECT_PREPARATION')
    body = loop.child_by_field_name('body')
    if body is None or body.type != 'block':
        _fail('CTL_LOOP_BODY_BLOCK_REQUIRED')
    if any(n.type in CONTROL | BOUNDARIES for n in walk(body)):
        _fail('CTL_BODY_CONTROL_OR_EXCEPTION_BOUNDARY')
    statements = children(body)
    if not statements or any(s.type != 'expression_statement' for s in statements):
        _fail('CTL_BODY_OPERATION_UNSUPPORTED')
    calls = [children(s)[0] for s in statements]
    if any(c.type != 'method_invocation' for c in calls):
        _fail('CTL_BODY_OPERATION_UNSUPPORTED')
    receiver = java.text(calls[0].child_by_field_name('object'))
    if not re.fullmatch(r'[A-Za-z_$][\w$]*', receiver):
        _fail('CTL_RECEIVER_BINDING_UNPROVEN')
    kind, declared, initial_length, preparation_calls = _receiver(java, loop, receiver, owner)
    index = None
    if loop.type == 'for_statement':
        init = java.text(loop.child_by_field_name('init')).strip().rstrip(';').strip()
        match = re.fullmatch(r'int\s+([A-Za-z_$][\w$]*)\s*=\s*(0|[1-9][0-9]*)', init)
        if not match:
            _fail('CTL_ITERATION_INITIALIZER_UNSUPPORTED')
        index, start = match[1], int(match[2])
        condition = java.text(loop.child_by_field_name('condition'))
        bound = re.fullmatch(r'\s*' + re.escape(index) + r'\s*(<|<=)\s*(0|[1-9][0-9]*)\s*', condition)
        update = re.sub(r'\s+', '', java.text(loop.child_by_field_name('update')))
        if not bound or update not in {index + '++', '++' + index}:
            _fail('CTL_DYNAMIC_BOUND_OR_UPDATE_UNSUPPORTED')
        end = int(bound[2]) + (bound[1] == '<=')
        if not 0 <= start <= end <= 2147483647 or end == start:
            _fail('CTL_EMPTY_OR_OVERFLOW_ITERATION_UNSUPPORTED')
        iterations = end - start
        if index == receiver:
            _fail('CTL_ITERATION_BINDING_UNPROVEN')
    elif loop.type == 'while_statement':
        condition = re.sub(r'\s+', '', java.text(loop.child_by_field_name('condition')))
        match = re.fullmatch(r'\(' + re.escape(receiver) + r'\.length\(\)<([0-9]+)\)', condition)
        if kind != 'StringBuilder' or not match or len(calls) != 1:
            _fail('CTL_DYNAMIC_BOUND_OR_UPDATE_UNSUPPORTED')
        arg_nodes = children(calls[0].child_by_field_name('arguments'))
        if len(arg_nodes) != 1 or arg_nodes[0].type != 'string_literal':
            _fail('CTL_LITERAL_VALUE_UNPROVEN')
        chunk = _length(java.text(arg_nodes[0]))
        limit = int(match[1])
        if chunk <= 0 or not initial_length < limit <= 2147483647:
            _fail('CTL_EMPTY_OR_OVERFLOW_ITERATION_UNSUPPORTED')
        iterations = (limit - initial_length + chunk - 1) // chunk
        if initial_length + iterations * chunk > 2147483647:
            _fail('CTL_EMPTY_OR_OVERFLOW_ITERATION_UNSUPPORTED')
        start = 0
    else:
        _fail('CTL_TRANSFORMATION_UNSUPPORTED')
    operations = []
    index_uses = []
    for call in calls:
        api = java.text(call.child_by_field_name('name'))
        if java.text(call.child_by_field_name('object')) != receiver or api != ('add' if kind == 'ArrayList' else 'append'):
            _fail('CTL_CALL_BEHAVIOR_UNPROVEN')
        args = children(call.child_by_field_name('arguments'))
        if len(args) != 1:
            _fail('CTL_CALL_OVERLOAD_UNSUPPORTED')
        arg = args[0]
        integer = (arg.type == 'identifier' and java.text(arg) == index) or (
            arg.type == 'decimal_integer_literal' and re.fullmatch(r'0|[1-9][0-9]*', java.text(arg)) and int(java.text(arg)) <= 2147483647)
        string = arg.type == 'string_literal'
        if string:
            _string(java.text(arg))
        if not (integer or string) or (kind == 'ArrayList' and (('String>' in declared) != bool(string))):
            _fail('CTL_ARGUMENT_EVALUATION_UNPROVEN')
        if arg.type == 'identifier':
            index_uses.append(java.span(arg))
        operations.append(java.text(call))
    # Calls prior to this preparation loop are unknown observer/capture points
    # unless they are the proven receiver construction (a constructor AST).
    if any(n.start_byte < loop.start_byte and n.start_byte not in preparation_calls for n in java.nodes('method_invocation')):
        _fail('CTL_PREPARATION_ORDER_UNPROVEN')
    if iterations > MAX_ITERATIONS or iterations * len(calls) > MAX_OPERATIONS:
        _fail('CTL_EXPANSION_NOT_BENEFICIAL')
    inner = java.text(body)[1:-1]
    inner_start = java.span(body)[0] + 1
    # The closed body has no declarations, captures or writes. Its only index
    # uses are direct int arguments. Substitute AST-bound argument ranges only;
    # do not rewrite matching text in comments, method names or string literals.
    replacement = '\n'.join(apply_edits(inner, [(a-inner_start,b-inner_start,str(v)) for a,b in index_uses])
                            for v in range(start,start+iterations))
    if len(replacement) > MAX_EXPANSION_CHARS:
        _fail('CTL_EXPANSION_NOT_BENEFICIAL')
    # Loop-header comments cannot be discarded or duplicated accidentally.
    if any(n.type in COMMENTS and n.start_byte < body.start_byte for n in walk(loop)):
        _fail('CTL_HEADER_COMMENT_PRESERVATION_UNSUPPORTED')
    a, b = java.span(loop)
    return {'start': a, 'end': b, 'replacement': replacement, 'iterations': iterations,
            'iteration_values': list(range(start, start + iterations)) if index else [],
            'receiver': receiver, 'receiver_type': declared, 'operations_per_iteration': operations,
            'expanded_operations': iterations * len(calls), 'action': 'ORDERED_UNROLL_LOCAL_PREPARATION'}


def analyze_ctl(java, owner, proof, finish):
    from .safety_contracts import walk
    proof['ctl_rule_version'] = VERSION
    proof['expansion_limits'] = {'iterations': MAX_ITERATIONS, 'operations': MAX_OPERATIONS, 'characters': MAX_EXPANSION_CHARS}
    nodes = [n for n in walk(java.root) if n.type in CONTROL]
    if not nodes:
        return finish('SKIP_FALSE_POSITIVE', 'CTL_NO_CONTROL_FLOW')
    # Every control target is recorded; no MNT-style partial acceptance.
    for n in nodes:
        a, b = java.span(n)
        try:
            target = _target(java, n, owner)
            proof['targets'].append(target)
        except Unsupported as exc:
            proof['retained_targets'].append({'start': a, 'end': b, 'kind': n.type, 'reason_code': str(exc)})
    if any(n.type in BOUNDARIES for n in walk(java.root)):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'CTL_METHOD_BOUNDARY_UNSUPPORTED')
    if proof['retained_targets']:
        reason = proof['retained_targets'][0]['reason_code']
        return finish('SKIP_NOT_APPLICABLE' if reason == 'CTL_EXPANSION_NOT_BENEFICIAL' else 'REVIEW_INSUFFICIENT_CONTEXT', reason)
    if not proof['targets']:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'CTL_NO_SUPPORTED_TARGET')
    return finish('REFACTOR_LOW_RISK', 'CTL_COMPLETE_BOUNDED_PREPARATION_PROVEN', ['ORDERED_UNROLL_LOCAL_PREPARATION'])


def expected_source(source, proof):
    from .safety_contracts import apply_edits
    return apply_edits(source, [(t['start'], t['end'], t['replacement']) for t in proof['targets']])


def validate_candidate(original, candidate, proof):
    from .safety_contracts import Java, walk, COMMENTS
    if proof.get('ctl_rule_version') != VERSION or proof.get('retained_targets') or not proof.get('targets'):
        return False, 'CTL_COMPLETE_CURRENT_CONTRACT_REQUIRED'
    expected = expected_source(original, proof)
    tree = Java(candidate)
    if any(n.type in CONTROL for n in walk(tree.root)):
        return False, 'CTL_REMAINING_CONTROL_FLOW'
    def syntax(source):
        j = Java(source)
        def leaves(n):
            if not n.children or n.type in COMMENTS:
                return [(n.type,j.text(n))]
            return [token for child in n.children for token in leaves(child)]
        return leaves(j.root)
    passed = syntax(expected) == syntax(candidate)
    return passed, 'CTL_EXACT_ORDERED_EXPANSION' if passed else 'CTL_EXPANSION_OR_NON_TARGET_CHANGED'


def build_prompt(request, contract, cases):
    from .safety_contracts import get_proof
    proof = get_proof(request['input']['risk_result']['result']['evidence'])
    return ('Perform experimental CTL ordered unrolling only. Return exactly one complete Java test method. '
            'Apply every approved replacement exactly, using the proven int literals for bound index arguments and preserving calls, '
            'argument evaluation order, comments, annotations and all other code. No Stream, helper, import, '
            'new assertion, alternate loop or explanation. Refuse if impossible. RAG cases are advisory only.\n'
            + 'Contract: ' + json.dumps(proof, ensure_ascii=False) + '\n'
            + 'Optional cases: ' + json.dumps(cases or [], ensure_ascii=False) + '\n'
            + 'Original method:\n' + request['input']['instance']['test_code'])
