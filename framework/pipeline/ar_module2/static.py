"""Variant/contract-blind AR safety. Coverage is a metric, not an admission gate."""
import re
from pipeline.agents.safety_contracts import Java, call_node, resolve_api
from pipeline.agents.method_location import single_method
from pipeline.ar_independent_static import source_assertions, source_overload, quality, stable_message
from pipeline.verification.candidate.validation import (
    java_semantic_tokens, _argument_without_nested, _replace_assertions_with_markers)
from pipeline.hashing import normalize_lf
from .output import parse

VERSION = 'ar-module2-independent-static-1'


def parent_indices(calls):
    ids = {c['assertion_id']: i for i, c in enumerate(calls)}
    return [ids.get(c['parent_assertion_id']) for c in calls]


def constant_diagnostics(before, after, candidate):
    """Prove added final String literals used exclusively as new diagnostics.

    No Java expressions are executed. Names must be absent from the original.
    Only otherwise-exact source projection permits these new declarations.
    """
    old_java = Java(before)
    old_names = {old_java.text(n) for n in old_java.nodes('identifier') + old_java.nodes('type_identifier')}
    definitions, spans = {}, []
    for node in after.nodes('local_variable_declaration'):
        if not re.match(r'\s*final\s+String\b', after.text(node)):
            continue
        variables = [n for n in node.named_children if n.type == 'variable_declarator']
        if len(variables) != 1:
            continue
        var = variables[0]
        name = after.text(var.child_by_field_name('name'))
        expression = after.text(var.child_by_field_name('value'))
        if name not in old_names and stable_message(expression) is not None:
            definitions[name] = expression
            spans.append(after.span(node))
    for name in list(definitions):
        # An ambiguous duplicate declaration cannot prove binding.
        if sum(after.text(n.child_by_field_name('name')) == name for n in after.nodes('variable_declarator')) != 1:
            return {}, candidate
    projected = candidate
    for start, end in sorted(spans, reverse=True):
        projected = projected[:start] + projected[end:]
    return definitions, projected


def evaluate(original, raw, owner):
    original, owner = normalize_lf(original), normalize_lf(owner)
    extraction = parse(raw, original, owner)
    result = {'version': VERSION, 'output': extraction, 'candidate_method': extraction['candidate_method'],
              'structurally_valid': False, 'assertions_preserved': False,
              'assertion_semantics_preserved': False, 'unrelated_edits_absent': False,
              'message_added': False, 'all_messages_added': False, 'generic_message_quality_pass': None,
              'messages': [], 'unsupported': [], 'checks': {}, 'status': extraction['status'],
              'engineering_eligible': False, 'independent_static_passed': False}
    if extraction['status'] != 'PASS':
        result['unsupported'] = [extraction['reason']] if extraction['status'] == 'INCONCLUSIVE' else []
        return result
    candidate = extraction['candidate_method']
    checks = result['checks']
    before, old_method = single_method(original)
    after, new_method = single_method(candidate)
    header = lambda java, node: java_semantic_tokens(java.source[:java.span(node.child_by_field_name('body'))[0]])
    checks['method_header_preserved'] = header(before, old_method) == header(after, new_method)
    old, new = source_assertions(original), source_assertions(candidate)
    checks['assertion_count_preserved'] = len(old) == len(new)
    checks['assertion_api_order_preserved'] = [c['name'] for c in old] == [c['name'] for c in new]
    checks['assertion_parent_preserved'] = parent_indices(old) == parent_indices(new)
    result['structurally_valid'] = all(checks.values())
    result['assertions_preserved'] = result['structurally_valid']
    if not result['structurally_valid']:
        result['status'] = 'FAIL'
        return result
    constants, projected = constant_diagnostics(original, after, candidate)
    projected_calls = source_assertions(projected)
    checks['non_assertion_code_preserved'] = (
        java_semantic_tokens(_replace_assertions_with_markers(original, old)) ==
        java_semantic_tokens(_replace_assertions_with_markers(projected, projected_calls)))
    comments = lambda java: [java.text(n) for n in sorted(java.nodes('line_comment') + java.nodes('block_comment'), key=lambda n: n.start_byte)]
    checks['comments_preserved'] = comments(before) == comments(after)
    semantics = True
    covered, missing, comparisons = 0, 0, []
    for index, (a, b) in enumerate(zip(old, new)):
        api_known = resolve_api(before, call_node(before, a), owner)
        overload = source_overload(before, a)
        known = api_known and overload in ('MISSING_MESSAGE', 'MESSAGE_PRESENT')
        if not known:
            result['unsupported'].append({'ordinal': index, 'reason': 'API_BINDING_OR_OVERLOAD_UNSUPPORTED',
                                          'api': a['name'], 'bound': api_known, 'overload': overload})
        receiver = lambda x: java_semantic_tokens(x['code'].split('(', 1)[0])
        receiver_equal = receiver(a) == receiver(b)
        arguments = lambda source, call, calls: [java_semantic_tokens(_argument_without_nested(source, call, i, calls))
                                                for i in range(len(call['arguments']))]
        aa, bb = arguments(original, a, old), arguments(candidate, b, new)
        unchanged = aa == bb
        new_message = None
        preserved = unchanged
        if known and overload == 'MISSING_MESSAGE':
            missing += 1
            if len(bb) == len(aa) + 1 and bb[:-1] == aa:
                preserved, new_message = True, b['arguments'][-1]
                covered += 1
        elif known and overload == 'MESSAGE_PRESENT' and len(aa) == len(bb) and aa[:-1] == bb[:-1] and not unchanged:
            # Rewording a diagnostic is not an assertion-oracle change.
            preserved, new_message = True, b['arguments'][-1]
        elif not known and len(bb) == len(aa) + 1:
            # Observe an insertion without pretending to know overload binding.
            if bb[:-1] == aa:
                preserved, new_message = True, b['arguments'][-1]
            elif bb[1:] == aa:
                preserved, new_message = True, b['arguments'][0]
        if new_message is not None:
            expression = constants.get(new_message.strip(), new_message)
            expression_java = Java('String diagnostic = ' + expression + ';')
            variables = expression_java.nodes('variable_declarator')
            value = variables[0].child_by_field_name('value') if len(variables) == 1 else None
            if value is not None and value.type == 'lambda_expression':
                parameters = value.child_by_field_name('parameters')
                body = value.child_by_field_name('body')
                if expression_java.text(parameters).strip() == '()' and body.type != 'block':
                    expression = expression_java.text(body)
            diagnostic = quality(expression, a, before.text(old_method.child_by_field_name('name')))
            diagnostic.update(ordinal=index, source_expression=new_message)
            result['messages'].append(diagnostic)
            if diagnostic['status'] == 'INCONCLUSIVE':
                result['unsupported'].append({'ordinal': index, 'reason': 'MESSAGE_STABILITY_UNPROVEN'})
        semantics = semantics and receiver_equal and preserved
        comparisons.append({'ordinal': index, 'binding_known': known, 'arguments_preserved': preserved,
                            'receiver_preserved': receiver_equal, 'message_inserted': new_message is not None})
    checks['original_assertion_arguments_preserved'] = semantics
    # New constants must be used only in added diagnostic slots. Any non-assertion
    # use survives projection and fails the source equality check above.
    for name in constants:
        def uses_identifier(argument):
            argument_java = Java('Object __ar_module2_probe__ = ' + argument + ';')
            return any(argument_java.text(n) == name for n in argument_java.nodes('identifier'))
        old_arg_use = any(uses_identifier(arg) for c in old for arg in c['arguments'])
        new_non_message_use = any(uses_identifier(arg)
                                  for c in new for arg in c['arguments'][:-1])
        diagnostic_use = any(m['source_expression'].strip() == name for m in result['messages'])
        if old_arg_use or new_non_message_use or not diagnostic_use:
            checks['diagnostic_constant_binding_safe'] = False
    quality_failed = any(m['status'] == 'FAIL' for m in result['messages'])
    result.update(assertion_semantics_preserved=(None if semantics and result['unsupported'] else semantics),
                  unrelated_edits_absent=checks['non_assertion_code_preserved'] and checks['comments_preserved'],
                  message_added=bool(result['messages']), all_messages_added=missing > 0 and covered == missing,
                  missing_message_count=missing, covered_message_count=covered, comparisons=comparisons,
                  generic_message_quality_pass=(all(m['status'] == 'PASS' for m in result['messages'])
                                                if result['messages'] else None))
    # Missing messages (including unchanged assertThrows) do not block engineering.
    # Unknown APIs/messages are measured through engineering but never accepted as proven safe.
    result['status'] = 'FAIL' if not all(checks.values()) or quality_failed else 'INCONCLUSIVE' if result['unsupported'] else 'PASS'
    result['engineering_eligible'] = result['status'] != 'FAIL'
    result['independent_static_passed'] = result['status'] == 'PASS'
    return result
