"""Variant-blind source-only AR outcome policy. No executable contract input."""
import re
import hashlib
from pipeline.agents.java_assertions import find_assertion_calls, mask_nested_assertions
from pipeline.agents.java_assertions import CONTROL_FLOW_NODES, _named_child_index, _statement_ordinal
from pipeline.agents.safety_contracts import Java, overload, resolve_api, call_node
from pipeline.agents.method_location import single_method
from pipeline.verification.candidate.validation import (
    extract_single_method, java_semantic_tokens, _decode_java_string_literal,
    _assertion_structure_key, _parent_structure_key, _argument_without_nested,
    _replace_assertions_with_markers)

VERSION = 'ar-rule-only-independent-static-1'
GENERIC = {'assertion failed', 'test failed', 'check value', 'check failed',
           'message', 'error', 'failed', 'test', 'assertion', 'expected', 'actual',
           'hello world', 'foo bar', 'test message', 'some message', 'diagnostic message',
           'something went wrong', 'check this', 'assertion message', 'expected result',
           'should work', 'should pass', 'should be correct'}
PLACEHOLDER = re.compile(r'(?i)\b(?:todo|fixme|tbd|placeholder|your code here|lorem ipsum)\b')
STOP = set('a an the is are be should must expected actual check assertion test failed failure message value values'.split())
PREDICATE = set('null nonnull true false equal equals equality same identical different greater less empty size length count contains match matches throws exception throw present absent exists valid invalid'.split())

def source_assertions(source):
    """Source AST inventory includes JUnit fail, as the pinned detector does.

    No source substitution or candidate rewriting is performed.
    """
    java=Java(source); calls=[]
    def visit(node,path,parent,control):
        if node.type in CONTROL_FLOW_NODES: control=control+('/'.join(path),)
        current_parent=parent
        if node.type=='method_invocation':
            name=java.text(node.child_by_field_name('name'))
            if name.startswith('assert') or name=='fail':
                args=node.child_by_field_name('arguments')
                argument_nodes=list(args.named_children) if args else []
                start,end=java.span(node); ast_path='/'.join(path)
                identity='assertion:v1:'+hashlib.sha256(f"{name}\0{ast_path}\0{'|'.join(control)}".encode()).hexdigest()
                call=dict(name=name,arguments=[java.text(n).strip() for n in argument_nodes],
                    argument_ranges=[dict(start=java.span(n)[0],end=java.span(n)[1]) for n in argument_nodes],
                    start=start,end=end,code=source[start:end],ast_path=ast_path,control_flow_path=list(control),
                    parent_assertion_id=parent['assertion_id'] if parent else None,
                    nesting_depth=parent['nesting_depth']+1 if parent else 0,
                    statement_ordinal=_statement_ordinal(node),assertion_id=identity)
                calls.append(call); current_parent=call
        for index,child in enumerate(node.children):
            if child.is_named:
                field=node.field_name_for_child(index)
                visit(child,path+(f'{field or child.type}:{_named_child_index(node,child)}',),current_parent,control)
    visit(java.root,('program:0',),None,())
    calls.sort(key=lambda c:(c['start'],-c['end'],c['ast_path']))
    return calls

def source_overload(java,call):
    if call['name']!='fail': return overload(java,call)
    if not call['arguments']: return 'MISSING_MESSAGE'
    if len(call['arguments'])==1:
        expression=call['arguments'][0]
        # An existing toString() has Java's required String return type; preserve
        # it unchanged. Only NEW messages undergo stable-value quality checks.
        if stable_message(expression) is not None or re.fullmatch(r'.+\.toString\(\s*\)',expression):
            return 'MESSAGE_PRESENT'
    return 'UNCONFIRMED_FAIL_OVERLOAD'

def words(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return set(re.findall(r'[a-z]+|\d+', text.lower()))

def stable_message(expression):
    """Only compile-time String literals/parentheses/+ of such literals.

    Calls, state reads and suppliers are unknown, never evaluated by Python.
    """
    java = Java('String __diagnostic = ' + expression + ';')
    variables = java.nodes('variable_declarator')
    if len(variables) != 1:
        return None
    def evaluate(node):
        if node.type == 'string_literal':
            return _decode_java_string_literal(java.text(node))
        if node.type == 'parenthesized_expression':
            return evaluate(node.named_children[0])
        if node.type == 'binary_expression' and java.text(node.child_by_field_name('operator')) == '+':
            left = evaluate(node.child_by_field_name('left'))
            right = evaluate(node.child_by_field_name('right'))
            return left + right if left is not None and right is not None else None
        return None
    return evaluate(variables[0].child_by_field_name('value'))

def quality(expression, assertion, method_name):
    try:
        value = stable_message(expression)
    except ValueError:
        value = None
    reasons = []
    if value is None:
        return {'expression': expression, 'value': None, 'status': 'INCONCLUSIVE',
                'reasons': ['MESSAGE_NOT_PROVEN_CONSTANT']}
    normalized = ' '.join(value.strip().lower().split()).rstrip('.!:')
    tokens = words(value)
    if not normalized: reasons.append('EMPTY')
    if PLACEHOLDER.search(value): reasons.append('PLACEHOLDER')
    if normalized in GENERIC: reasons.append('GENERIC_ONLY')
    if len(tokens) < 2 or not (tokens - STOP): reasons.append('NO_DESCRIPTIVE_CONTENT')
    if re.fullmatch(r'[\W_]+|(.)\1{3,}', value.strip()): reasons.append('MEANINGLESS_SYMBOLS_OR_REPETITION')
    # Topic overlap is recorded, not a gate. Requiring matching source words
    # would systematically prefer messages that quote source over paraphrases.
    relevant = (words(assertion['code']) | words(method_name) | PREDICATE) - STOP
    return {'expression': expression, 'value': value, 'status': 'FAIL' if reasons else 'PASS',
            'reasons': reasons, 'topic_overlap': sorted(tokens & relevant)}

def evaluate(original, raw, owner):
    """Inputs contain no variant, contract, risk evidence or prescribed message."""
    candidate, extraction = extract_single_method(raw)
    result = dict(version=VERSION, candidate_method=candidate, checks={}, messages=[],
                  structurally_valid=False, target_assertions_preserved=False,
                  assertion_semantics_preserved=False, message_added=False,
                  generic_message_quality_pass=False, unrelated_modification=False,
                  static_passed=False, inconclusive=False)
    checks = result['checks']
    checks['single_complete_method'] = candidate is not None
    if candidate is None:
        result['extraction'] = extraction
        return result
    try:
        before_java, after_java = Java(original), Java(candidate)
        _, before_method = single_method(original)
        _, after_method = single_method(candidate)
        # Full declaration prefix: annotations, visibility, return, name, parameters, throws.
        def header(java, method):
            return java_semantic_tokens(java.source[:java.span(method.child_by_field_name('body'))[0]])
        checks['method_header_unchanged'] = header(before_java,before_method) == header(after_java,after_method)
        old, new = source_assertions(original), source_assertions(candidate)
        checks['java_ast_parse'] = True
        checks['assertion_count'] = len(old) == len(new)
        checks['assertion_order'] = [x['name'] for x in old] == [x['name'] for x in new]
        checks['assertion_identity'] = [_assertion_structure_key(x) for x in old] == [_assertion_structure_key(x) for x in new]
        checks['assertion_parent_relation'] = [_parent_structure_key(x,old) for x in old] == [_parent_structure_key(x,new) for x in new]
        structural = all(checks.values())
        result['structurally_valid'] = structural
        targets, unsupported = [], []
        for i, call in enumerate(old):
            node = call_node(before_java, call)
            status = source_overload(before_java,call)
            bound = resolve_api(before_java,node,owner)
            if bound and status == 'MISSING_MESSAGE': targets.append(i)
            elif not bound or status not in ('MISSING_MESSAGE','MESSAGE_PRESENT'):
                unsupported.append({'ordinal':i,'api':call['name'],'overload':status,'bound':bound})
        result['targets'] = [{'ordinal':i,'assertion_id':old[i]['assertion_id'],
                              'code':old[i]['code'],'ast_path':old[i]['ast_path']} for i in targets]
        result['target_count'] = len(targets)
        result['unsupported_source_calls'] = unsupported
        result['inconclusive'] = bool(unsupported) or not targets
        checks['source_targets_resolved'] = bool(targets) and not unsupported
        result['target_assertions_preserved'] = structural and bool(targets)
        semantics, added = structural, structural and bool(targets)
        comparisons = []
        if structural:
            for i,(before,after) in enumerate(zip(old,new)):
                receiver_equal = java_semantic_tokens(before['code'].split('(',1)[0]) == java_semantic_tokens(after['code'].split('(',1)[0])
                args_equal = len(after['arguments']) >= len(before['arguments']) and all(
                    java_semantic_tokens(_argument_without_nested(original,before,j,old)) ==
                    java_semantic_tokens(_argument_without_nested(candidate,after,j,new))
                    for j in range(len(before['arguments'])))
                if i in targets:
                    has_added = len(after['arguments']) == len(before['arguments']) + 1
                    added = added and has_added
                    if has_added:
                        diagnostic = quality(after['arguments'][-1],before,before_java.text(before_method.child_by_field_name('name')))
                        diagnostic['ordinal'] = i
                        result['messages'].append(diagnostic)
                        if diagnostic['status'] == 'INCONCLUSIVE': result['inconclusive'] = True
                    semantics = semantics and receiver_equal and args_equal
                else:
                    same = java_semantic_tokens(mask_nested_assertions(original,start=before['start'],end=before['end'],calls=[x for x in old if x is not before])) == java_semantic_tokens(mask_nested_assertions(candidate,start=after['start'],end=after['end'],calls=[x for x in new if x is not after]))
                    semantics = semantics and same
                comparisons.append(dict(ordinal=i,target=i in targets,receiver_equal=receiver_equal,original_arguments_equal=args_equal))
        non_assertion_equal = java_semantic_tokens(_replace_assertions_with_markers(original,old)) == java_semantic_tokens(_replace_assertions_with_markers(candidate,new))
        checks['non_assertion_code_unchanged'] = non_assertion_equal
        # Comments are information outside the permitted diagnostic argument edit.
        def comments(java):
            nodes = sorted(java.nodes('line_comment') + java.nodes('block_comment'),
                           key=lambda node: node.start_byte)
            return [java.text(node) for node in nodes]
        checks['comments_unchanged'] = comments(before_java) == comments(after_java)
        non_assertion_equal = non_assertion_equal and checks['comments_unchanged']
        checks['assertion_semantics_preserved'] = semantics
        checks['all_target_messages_added'] = added
        checks['generic_message_quality'] = bool(result['messages']) and len(result['messages']) == len(targets) and all(x['status']=='PASS' for x in result['messages'])
        result.update(assertion_semantics_preserved=semantics, message_added=added,
                      generic_message_quality_pass=checks['generic_message_quality'],
                      unrelated_modification=not non_assertion_equal, comparisons=comparisons,
                      static_passed=all(checks.values()))
    except (ValueError,KeyError,IndexError,TypeError) as exc:
        checks['java_or_mapping_error'] = False
        result['error'] = str(exc)
        result['inconclusive'] = True
    return result
