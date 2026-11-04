"""Executable, source-bound contracts. Confidence is rule certainty, not a probability.

This is deliberately a closed subset of Java/JUnit, not a whole-program symbol
solver. Unknown bindings remain unknown. Offsets are Unicode character offsets.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tree_sitter import Parser
from .java_assertions import JAVA_LANGUAGE, SUPPORTED_ASSERTIONS, find_assertion_calls
from .api_scope import API_BINDING_RULE_VERSION, resolve_api_evidence

VERSION = '4.1'
ECT_RULE_VERSION = '4.2'
ECT_DISPATCH_CONTRACT_VERSION = '3.0'
ECT_MUST_THROW_CONTRACT_VERSION = '2.0'
ECT_NOTHROW_CONTRACT_VERSION = '1.0'
# Compatibility name used by persisted contracts and callers.  From v3 this
# identifies the dispatcher; each proof also carries an explicit path version.
ECT_CONTRACT_VERSION = ECT_DISPATCH_CONTRACT_VERSION
ECT_MUST_THROW_PATH = 'MUST_THROW_ASSERT_THROWS'
ECT_NOTHROW_PATH = 'NO_THROW_ASSERT_DOES_NOT_THROW'
SMELLS = {'Assertion_Roulette', 'Redundant_Assertion', 'Exception_Catching_Throwing', 'Magic_Number_Test', 'Conditional_Test_Logic'}
JUNIT = 'org.junit.jupiter.api.Assertions'
COMMENTS = {'line_comment', 'block_comment'}
PRIMITIVES = {'byte', 'short', 'int', 'long', 'float', 'double', 'boolean', 'char'}


def current_rule_version(smell):
    if smell == 'Redundant_Assertion':
        from .ra_contract import RULE_VERSION
        return RULE_VERSION
    if smell == 'Exception_Catching_Throwing':
        return ECT_RULE_VERSION
    return VERSION


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


class Java:
    def __init__(self, source):
        self.source = source
        self.data = source.encode('utf-8')
        self.root = Parser(JAVA_LANGUAGE).parse(self.data).root_node
        if self.root.has_error:
            raise ValueError('JAVA_AST_PARSE_ERROR')
        # Java translates Unicode escapes before lexing, including in comments.
        # The tree-sitter AST cannot prove a binding if that changes tokens.
        literal_ranges = [self.span(n) for n in walk(self.root) if n.type in ('string_literal', 'character_literal')]
        for match in re.finditer(r'\\u+([0-9a-fA-F]{4})', source):
            decoded = chr(int(match[1], 16))
            if decoded in '\r\n"\'\\' or not any(start <= match.start() < end for start, end in literal_ranges):
                raise ValueError('JAVA_PRELEXICAL_UNICODE_BINDING_UNKNOWN')

    def text(self, node):
        return self.data[node.start_byte:node.end_byte].decode('utf-8') if node else ''

    def span(self, node):
        return (len(self.data[:node.start_byte].decode('utf-8')), len(self.data[:node.end_byte].decode('utf-8')))

    def nodes(self, kind):
        return [n for n in walk(self.root) if n.type == kind]

    def at(self, start, end):
        return next((n for n in walk(self.root) if self.span(n) == (start, end)), None)


def children(node):
    return [n for n in node.named_children if n.type not in COMMENTS]


def ast_tokens(source):
    """AST leaves with literal spelling preserved; whitespace/comments ignored."""
    java = Java(source)
    def leaves(n):
        if n.type in COMMENTS:
            return []
        if not n.children:
            return [(n.type, java.text(n))]
        return [item for child in n.children for item in leaves(child)]
    return tuple(leaves(java.root))


def ast_node_tokens(java, node):
    """AST leaves for an already parsed node, preserving literal spelling."""
    def leaves(current):
        if current.type in COMMENTS:
            return []
        if not current.children:
            return [(current.type, java.text(current))]
        return [item for child in current.children for item in leaves(child)]
    return tuple(leaves(node))


def comment_inventory(source):
    """Return layout-insensitive comment text at its AST structural position.

    Tree-sitter includes the carriage return in a ``//`` node for CRLF input.
    Comparing raw node text therefore rejects an otherwise identical LF
    candidate.  Comment contents are normalized by delimiter and whitespace
    runs, while their parent path and slot among non-comment siblings retain
    deletion, modification, reordering, and movement detection.
    """
    java = Java(source)

    def parent_path(node):
        path = []
        current = node.parent
        while current is not None and current.parent is not None:
            siblings = [child for child in current.parent.named_children
                        if child.type not in COMMENTS]
            path.append((current.type, siblings.index(current)))
            current = current.parent
        return tuple(reversed(path))

    inventory = []
    for node in (item for item in walk(java.root) if item.type in COMMENTS):
        siblings = list(node.parent.named_children)
        index = siblings.index(node)
        slot = sum(child.type not in COMMENTS for child in siblings[:index])
        ordinal = 0
        for previous in reversed(siblings[:index]):
            if previous.type not in COMMENTS:
                break
            ordinal += 1
        raw = java.text(node).replace('\r\n', '\n').replace('\r', '\n')
        if node.type == 'line_comment':
            delimiter, body = '//', raw[2:]
        else:
            delimiter, body = '/* */', raw[2:-2] if raw.endswith('*/') else raw[2:]
        content = ' '.join(body.split())
        inventory.append((parent_path(node), slot, ordinal, node.type, delimiter, content))
    return tuple(inventory)


def apply_edits(source, edits):
    previous = len(source) + 1
    for start, end, replacement in sorted(edits, reverse=True):
        if not 0 <= start <= end < previous:
            raise ValueError('OVERLAPPING_OR_INVALID_TARGET')
        source = source[:start] + replacement + source[end:]
        previous = start
    return source


def plain_string(value):
    # No text blocks, concatenation, dynamic supplier, or pre-lexical escapes.
    return bool(re.fullmatch(r'"(?:\\[btnfr"\x27\\]|\\[0-7]{1,3}|[^"\\\r\n])*"', value))


def resolve_api(java, node, class_source, name=None):
    """Closed lexical proof using only declarations visible to this call."""
    return resolve_api_evidence(java, node, class_source, name)['bound']


def call_node(java, call):
    return java.at(call['start'], call['end'])


def local_binding(java, name, use_node):
    """A unique preceding direct local declaration in the very same block."""
    if not re.fullmatch(r'[A-Za-z_$][\w$]*', name):
        return None
    declarations = [n for n in java.nodes('variable_declarator') if java.text(n.child_by_field_name('name')) == name]
    if len(declarations) != 1:
        return None
    var = declarations[0]
    declaration = var.parent
    if declaration.type != 'local_variable_declaration' or var.start_byte >= use_node.start_byte:
        return None
    block = use_node.parent
    while block and block.type != 'block':
        block = block.parent
    if declaration.parent != block:
        return None
    typ = java.text(declaration.child_by_field_name('type'))
    value = var.child_by_field_name('value')
    final = any(n.type == 'modifiers' and java.text(n) == 'final' for n in declaration.named_children)
    return {'type': typ, 'value': java.text(value), 'final': final,
            'declaration_start': java.span(declaration)[0], 'declaration_end': java.span(declaration)[1]}


def numeric_type(text):
    raw = text.lstrip('+-').replace('_', '')
    if not re.fullmatch(r'(?:0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?|0[bB][01]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)[fFdDlL]?', raw):
        return None
    # Hex integer F/D are digits, not floating suffixes.
    floating = '.' in raw or bool(re.search(r'[pP]', raw)) or (not raw.lower().startswith('0x') and bool(re.search(r'[eE]', raw)))
    if raw[-1:] in ('l', 'L'):
        return 'long'
    if floating or (not raw.lower().startswith(('0x', '0b')) and raw[-1:] in 'fFdD'):
        return 'float' if raw[-1:] in ('f', 'F') else 'double'
    return 'int'


def argument_type(java, value, node):
    if plain_string(value): return 'String'
    if value in ('true', 'false'): return 'boolean'
    if value == 'null': return 'null'
    numeric = numeric_type(value)
    if numeric: return numeric
    binding = local_binding(java, value, node)
    return binding['type'] if binding else None


def overload(java, call):
    name, args = call['name'], call['arguments']
    count = SUPPORTED_ASSERTIONS.get(name)
    if count is None: return 'UNSUPPORTED_ASSERTION_API'
    if len(args) == count:
        return 'MISSING_MESSAGE'
    if len(args) == count + 1:
        if plain_string(args[-1]) or re.fullmatch(r'\(\s*\)\s*->\s*.+', args[-1], re.S):
            return 'MESSAGE_PRESENT'
        typ = argument_type(java, args[-1], call_node(java, call))
        if typ in ('String', 'Supplier<String>', 'java.util.function.Supplier<String>'):
            return 'MESSAGE_PRESENT'
        # Delta overloads are deliberately not transformed in v4.
        if name in ('assertEquals', 'assertNotEquals', 'assertArrayEquals') and typ in PRIMITIVES - {'boolean', 'char'}:
            return 'UNSUPPORTED_DELTA_OVERLOAD'
    return 'UNCONFIRMED_MESSAGE_POSITION'


def grounded_message(call):
    """A source quotation locates the failed check without inventing domain facts."""
    quoted = call['code'].strip()
    return json.dumps('Check failed: ' + quoted, ensure_ascii=False)


def source_binding(instance, context):
    source = instance.get('test_code') or ''
    owner = context.get('test_class_source') or ''
    binding = {'instance_id': instance.get('instance_id'), 'method_signature': instance.get('method_signature'),
            'source_file': instance.get('test_relative_path'), 'method_hash': digest(source),
            'class_hash': digest(owner), 'line_range': instance.get('line_range'), 'rule_version': current_rule_version(instance.get('smell_type'))}
    if instance.get('smell_type') == 'Redundant_Assertion':
        binding['ra_oracle_version'] = '2'
        from .ra_contract import VERSION as ra_version
        binding['ra_contract_version'] = ra_version
    if instance.get('smell_type') == 'Magic_Number_Test':
        from .java_type_evidence import MNT_RULE_VERSION, engineering_context
        binding['mnt_rule_version'] = MNT_RULE_VERSION
        environment = context.get('java_environment') or engineering_context(instance)
        binding['engineering_context_hash'] = digest(json.dumps(environment, sort_keys=True))
    if instance.get('smell_type') == 'Exception_Catching_Throwing':
        from .java_type_evidence import ECT_TYPE_EVIDENCE_VERSION, engineering_context
        binding['ect_contract_version'] = ECT_CONTRACT_VERSION
        binding['ect_dispatch_contract_version'] = ECT_DISPATCH_CONTRACT_VERSION
        binding['ect_type_evidence_version'] = ECT_TYPE_EVIDENCE_VERSION
        environment = context.get('java_environment') or engineering_context(instance)
        binding['engineering_context_hash'] = digest(json.dumps(environment, sort_keys=True))
    return binding


def analyze(instance, context):
    source = instance.get('test_code') or ''
    proof = {'binding': source_binding(instance, context), 'targets': [], 'retained_targets': [],
             'decision': 'REVIEW_INSUFFICIENT_CONTEXT', 'reason_code': 'NOT_IMPLEMENTED',
             'allowed_changes': [], 'confidence_semantics': 'DETERMINISTIC_RULE_CERTAINTY_NOT_SAFETY_PROBABILITY'}
    def finish(decision, reason, allowed=()):
        if decision == 'REFACTOR_LOW_RISK':
            binding = proof['binding']; lines = binding.get('line_range') or {}
            if (not all(binding.get(key) for key in ('instance_id', 'method_signature', 'source_file'))
                    or not isinstance(lines.get('start'), int) or not isinstance(lines.get('end'), int)
                    or lines['start'] < 1 or lines['end'] < lines['start']):
                decision, reason, allowed = 'REVIEW_INSUFFICIENT_CONTEXT', 'SOURCE_IDENTITY_OR_LOCATION_MISSING', ()
            else:
                from .refactoring_contract import normalized_source
                try:
                    parsed_owner = Java(context.get('test_class_source') or '')
                    matches = [n for n in parsed_owner.nodes('method_declaration') if normalized_source(parsed_owner.text(n)) == normalized_source(source)]
                    if len(matches) != 1:
                        decision, reason, allowed = 'REVIEW_INSUFFICIENT_CONTEXT', 'SOURCE_METHOD_OWNERSHIP_UNPROVEN', ()
                except ValueError:
                    decision, reason, allowed = 'REVIEW_INSUFFICIENT_CONTEXT', 'SOURCE_CLASS_PARSE_UNKNOWN', ()
        if instance.get('smell_type') == 'Redundant_Assertion':
            from .ra_contract import semantic_category
            if 'ra_semantic_category' not in proof:
                try:
                    parsed = Java(source)
                    category = semantic_category(parsed, find_assertion_calls(source))
                except ValueError:
                    category = None
                if decision == 'REFACTOR_LOW_RISK' and category in ('CONTRACT_OR_REFLEXIVITY_CHECK', 'POSSIBLE_WRONG_OPERAND'):
                    decision, reason, allowed = 'REVIEW_HIGH_RISK', 'RA_PROTECTED_SEMANTIC_CONTEXT', ()
                if decision == 'REFACTOR_LOW_RISK':
                    category = 'LEGACY_V1_SAFE_TAUTOLOGY'
                proof['ra_semantic_category'] = category or ('ORACLE_LOSS_RISK' if reason == 'NO_CONFIRMED_REMAINING_ORACLE'
                    else 'LEGACY_V1_SAFE_TAUTOLOGY' if decision == 'REFACTOR_LOW_RISK' else 'BINDING_UNPROVEN')
            if decision != 'REFACTOR_LOW_RISK' and proof.get('ra_semantic_category') == 'SAFE_CONSTANT_ASSERTION_IN_CATCH':
                proof['ra_semantic_category'] = 'BINDING_UNPROVEN'
        proof.update(decision=decision, reason_code=reason, allowed_changes=list(allowed))
        return proof
    smell = instance.get('smell_type')
    if smell not in SMELLS:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'NOT_IMPLEMENTED')
    if instance.get('method_source_hash') and instance['method_source_hash'] != digest(source):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'STALE_METHOD_SOURCE')
    try:
        java = Java(source)
        methods = java.nodes('method_declaration')
        if smell == 'Magic_Number_Test':
            methods = [n for n in java.root.named_children if n.type == 'method_declaration']
        if len(methods) != 1: return finish('REVIEW_INSUFFICIENT_CONTEXT', 'SINGLE_METHOD_REQUIRED')
        calls = find_assertion_calls(source)
    except ValueError:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'JAVA_AST_PARSE_ERROR')
    owner = context.get('test_class_source') or ''
    if smell == 'Conditional_Test_Logic':
        from .ctl_contract import analyze_ctl
        return analyze_ctl(java, owner, proof, finish)
    if smell == 'Exception_Catching_Throwing':
        return analyze_ect(java, owner, proof, finish, instance=instance, context=context)
    if not calls:
        return finish('SKIP_FALSE_POSITIVE', 'NO_TARGET_ASSERTION')
    if smell == 'Magic_Number_Test':
        return analyze_mnt(java, owner, calls, proof, finish, instance=instance, context=context)
    if smell == 'Redundant_Assertion':
        from .ra_contract import analyze_catch
        result = analyze_catch(java, owner, calls, proof, finish, instance)
        if result is not None:
            return result
    for call in calls:
        node = call_node(java, call)
        if not resolve_api(java, node, owner):
            return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ASSERTION_API_BINDING_UNKNOWN')
        if call['unsupported_reasons']:
            return finish('REVIEW_HIGH_RISK', call['unsupported_reasons'][0])
    if smell == 'Assertion_Roulette':
        for call in calls:
            status = overload(java, call)
            if status not in ('MISSING_MESSAGE', 'MESSAGE_PRESENT'):
                return finish('REVIEW_INSUFFICIENT_CONTEXT', status)
            if status == 'MISSING_MESSAGE':
                proof['targets'].append({**call, 'conclusion': status, 'message': grounded_message(call)})
        if not proof['targets']:
            return finish('SKIP_FALSE_POSITIVE', 'ALL_MESSAGES_PRESENT')
        # Current JAR: missing-message count >= 1, with a separate exclusion
        # when the entire method has exactly one assertion (verified bytecode).
        if len(calls) == 1:
            return finish('SKIP_FALSE_POSITIVE', 'AR_SINGLE_ASSERTION_NOT_ROULETTE')
        reason = 'AR_MULTIPLE_ASSERTIONS_MISSING_MESSAGE' if len(proof['targets']) > 1 else 'AR_DETECTOR_ONE_MISSING_AMONG_MULTIPLE'
        return finish('REFACTOR_LOW_RISK', reason, ['ADD_ASSERTION_MESSAGE'])
    return analyze_ra(java, calls, proof, finish)


def analyze_ra(java, calls, proof, finish):
    remaining = []
    oracle_barrier = False
    for call in calls:
        node = call_node(java, call)
        name, args = call['name'], call['arguments']
        tautology = (name, args) in [('assertTrue', ['true']), ('assertFalse', ['false']), ('assertNull', ['null'])]
        if name == 'assertNotNull' and args and args[0].startswith('new '):
            return finish('REVIEW_HIGH_RISK', 'CONSTRUCTOR_EXECUTION_MUST_REMAIN')
        if name in ('assertEquals', 'assertSame') and len(args) == 2 and args[0] == args[1]:
            binding = local_binding(java, args[0], node)
            if not binding or binding['type'] not in PRIMITIVES or not binding['final']:
                return finish('REVIEW_HIGH_RISK' if '.' in args[0] or '(' in args[0] else 'REVIEW_INSUFFICIENT_CONTEXT', 'SELF_COMPARISON_BINDING_UNPROVEN')
            if name != 'assertEquals' or binding['type'] in ('float', 'double'):
                return finish('REVIEW_INSUFFICIENT_CONTEXT', 'SELF_COMPARISON_OVERLOAD_UNPROVEN')
            tautology = True
        if tautology:
            # Deleting a nested call can remove an operand rather than a statement.
            if node.parent.type != 'expression_statement' or node.parent.parent.type != 'block' or call['control_flow_path']:
                return finish('REVIEW_HIGH_RISK', 'RA_TARGET_CONTROL_FLOW_UNSUPPORTED')
            start, end = java.span(node.parent)
            proof['targets'].append({**call, 'statement_start': start, 'statement_end': end, 'conclusion': 'CONFIRMED_REDUNDANT'})
        elif call['control_flow_path'] or call['nesting_depth'] or overload(java, call) not in ('MESSAGE_PRESENT', 'MISSING_MESSAGE'):
            return finish('REVIEW_INSUFFICIENT_CONTEXT', 'REMAINING_ORACLE_REACHABILITY_UNKNOWN')
        else:
            # Standard, unconditional assertion. Constants that always fail are
            # not removable and do not prove a meaningful remaining oracle.
            if (name, args) in [('assertTrue', ['false']), ('assertFalse', ['true']), ('assertNotNull', ['null'])]:
                oracle_barrier = True
                continue
            if confirmed_oracle(java, call):
                if not oracle_barrier:
                    remaining.append(call['assertion_id'])
            else:
                # An unsupported/predetermined check may fail before a later
                # observation. Its existence cannot establish reachability.
                oracle_barrier = True
    proof['remaining_oracle_ids'] = remaining
    if not proof['targets']: return finish('SKIP_NOT_APPLICABLE', 'NO_PROVEN_REDUNDANT_ASSERTION')
    # Calls in helpers and branching before/after an oracle are not proven by
    # call-counting. Straight-line top-level execution is the supported subset.
    if any(n.type in ('if_statement', 'switch_expression', 'for_statement', 'enhanced_for_statement', 'while_statement', 'do_statement', 'return_statement', 'throw_statement', 'try_statement') for n in walk(java.root)):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'REMAINING_ORACLE_REACHABILITY_UNKNOWN')
    if not remaining: return finish('REVIEW_HIGH_RISK', 'NO_CONFIRMED_REMAINING_ORACLE')
    return finish('REFACTOR_LOW_RISK', 'PURE_TAUTOLOGY_WITH_REMAINING_ORACLE', ['REMOVE_TARGET_ASSERTION_STATEMENT'])


def confirmed_oracle(java, call):
    """A supported, bound value check, not merely an assert-prefixed call."""
    args, name = call['arguments'], call['name']
    node = call_node(java, call)
    # A lambda/helper/nested block is not evidence of straight-line execution.
    if (node.parent.type != 'expression_statement' or node.parent.parent.type != 'block'
            or node.parent.parent.parent.type != 'method_declaration'):
        return False
    actual_index = 1 if name in ('assertEquals', 'assertNotEquals') else 0
    actual = local_binding(java, args[actual_index], node)
    if not actual:
        return False
    declarations = [n for n in java.nodes('variable_declarator')
                    if java.text(n.child_by_field_name('name')) == args[actual_index]]
    value = declarations[0].child_by_field_name('value')
    # Support a direct runtime observation, not constants, aliases or unknown
    # expressions. This is a bounded local proof, not an interprocedural claim.
    if value is None or value.type != 'method_invocation':
        return False
    for write in java.nodes('assignment_expression') + java.nodes('update_expression'):
        if actual['declaration_start'] < java.span(write)[0] < java.span(node)[0]:
            if any(n.type == 'identifier' and java.text(n) == args[actual_index] for n in walk(write)):
                return False
    if name in ('assertTrue', 'assertFalse'):
        binding = local_binding(java, args[0], node)
        return bool(binding and binding['type'] == 'boolean')
    if name in ('assertEquals', 'assertNotEquals'):
        expected = args[0]
        fixed = plain_string(expected) or numeric_type(expected) or expected in ('true', 'false')
        binding = local_binding(java, expected, node)
        if binding:
            fixed = binding['final'] and (plain_string(binding['value']) or numeric_type(binding['value'])
                                         or binding['value'] in ('true', 'false'))
        if not fixed or expected == args[1]:
            return False
        left = argument_type(java, args[0], node)
        right = argument_type(java, args[1], node)
        return bool(left and left == right and local_binding(java, args[1], node))
    if name in ('assertNull', 'assertNotNull'):
        binding = local_binding(java, args[0], node)
        return bool(binding and binding['type'] not in PRIMITIVES)
    return False


ECT_V2_ASSERTIONS = {
    'assertEquals', 'assertNotEquals', 'assertTrue', 'assertFalse',
    'assertNull', 'assertNotNull', 'assertSame', 'assertNotSame',
    'assertArrayEquals', 'assertInstanceOf',
}
ECT_V2_OBSERVATION_METHODS = {
    'getMessage', 'getLocalizedMessage', 'getCause', 'getSuppressed', 'getClass',
    'contains', 'startsWith', 'endsWith', 'equals', 'isEmpty', 'length', 'toString',
}


def _ect_direct_call(java, statement):
    if statement.type != 'expression_statement':
        return None
    direct = children(statement)
    return direct[0] if len(direct) == 1 and direct[0].type == 'method_invocation' else None


def _ect_v2_api(java, call, owner, instance, context, name):
    if resolve_api(java, call, owner, name):
        return True
    from .java_type_evidence import BindingUnknown, resolve_ect_assertion_api
    start, end = java.span(call)
    try:
        resolve_ect_assertion_api(instance, context, {'start': start, 'end': end, 'name': java.text(call.child_by_field_name('name'))}, name)
        return True
    except BindingUnknown:
        return False


def _ect_v2_marker_assertion(java, statement, flag, owner, instance, context):
    call = _ect_direct_call(java, statement)
    if call is None:
        return None
    name = java.text(call.child_by_field_name('name'))
    args = children(call.child_by_field_name('arguments'))
    values = [java.text(arg) for arg in args]
    supported = (name == 'assertTrue' and values == [flag]) or (
        name == 'assertEquals' and len(values) == 2
        and ((values[0] == 'true' and values[1] == flag) or (values[0] == flag and values[1] == 'true'))
    )
    if (not supported or not _ect_v2_api(java, call, owner, instance, context, name)
            or not _ect_v2_api(java, call, owner, instance, context, 'assertThrows')):
        return None
    flag_arg = next(arg for arg in args if java.text(arg) == flag)
    receiver = java.text(call.child_by_field_name('object'))
    return {'call': call, 'flag_arg': flag_arg, 'api': (receiver + '.' if receiver else '') + 'assertThrows',
            'kind': name, 'source': java.text(statement)}


def _ect_v2_catch_observations(java, statements, catch_name, owner, instance, context):
    observations = []
    cause_names = set()
    for statement in statements:
        if statement.type == 'local_variable_declaration':
            typ = java.text(statement.child_by_field_name('type'))
            variables = [n for n in statement.named_children if n.type == 'variable_declarator']
            if typ not in ('Throwable', 'java.lang.Throwable') or len(variables) != 1:
                return None, 'ECT_V2_CATCH_LOCAL_UNSUPPORTED'
            variable = variables[0]
            name = java.text(variable.child_by_field_name('name'))
            value = variable.child_by_field_name('value')
            if (not name or value is None or value.type != 'method_invocation'
                    or java.text(value.child_by_field_name('object')) != catch_name
                    or java.text(value.child_by_field_name('name')) != 'getCause'
                    or children(value.child_by_field_name('arguments'))):
                return None, 'ECT_V2_CAUSE_BINDING_UNSUPPORTED'
            if any(java.text(n.child_by_field_name('name')) == name for n in java.nodes('variable_declarator') if n != variable):
                return None, 'ECT_V2_CAUSE_SCOPE_COLLISION'
            cause_names.add(name)
            observations.append(statement)
            continue

        call = _ect_direct_call(java, statement)
        if call is None:
            return None, 'ECT_V2_CATCH_STATEMENT_UNSUPPORTED'
        assertion_name = java.text(call.child_by_field_name('name'))
        if (assertion_name not in ECT_V2_ASSERTIONS
                or not _ect_v2_api(java, call, owner, instance, context, assertion_name)):
            return None, 'ECT_V2_EXCEPTION_ASSERTION_API_UNPROVEN'
        allowed_roots = {catch_name} | cause_names
        identifiers = {java.text(n) for n in walk(call) if n.type == 'identifier'}
        if not identifiers.intersection(allowed_roots):
            return None, 'ECT_V2_ASSERTION_NOT_EXCEPTION_RELATED'
        for nested in (n for n in walk(call) if n.type == 'method_invocation' and n != call):
            name = java.text(nested.child_by_field_name('name'))
            receiver = java.text(nested.child_by_field_name('object'))
            if name not in ECT_V2_OBSERVATION_METHODS or not any(
                    re.search(r'(?<![\w$])' + re.escape(root) + r'(?![\w$])', receiver)
                    for root in allowed_roots):
                return None, 'ECT_V2_NON_OBSERVATION_CALL_IN_CATCH'
        if any(n.type in ('assignment_expression', 'update_expression', 'object_creation_expression',
                          'lambda_expression', 'anonymous_class_body', 'class_body') for n in walk(call)):
            return None, 'ECT_V2_CATCH_SIDE_EFFECT_OR_SCOPE_UNSUPPORTED'
        observations.append(statement)
    return observations, None


def _analyze_ect_v2(java, owner, proof, finish, *, instance, context, target, catch, exception):
    block = target.parent
    siblings = children(block)
    try:
        index = siblings.index(target)
    except ValueError:
        return None
    if index == 0 or index + 1 >= len(siblings):
        return None
    declaration, post = siblings[index - 1], siblings[index + 1]
    if declaration.type != 'local_variable_declaration' or java.text(declaration.child_by_field_name('type')) != 'boolean':
        return None
    variables = [n for n in declaration.named_children if n.type == 'variable_declarator']
    if len(variables) != 1:
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_FLAG_DECLARATION_UNSUPPORTED')
    variable = variables[0]
    flag = java.text(variable.child_by_field_name('name'))
    initial = variable.child_by_field_name('value')
    if not flag or initial is None or java.text(initial) != 'false':
        return None
    marker = _ect_v2_marker_assertion(java, post, flag, owner, instance, context)
    if marker is None:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_V2_MUST_THROW_ASSERTION_UNPROVEN')

    parameter = next((n for n in catch.named_children if n.type == 'catch_formal_parameter'), None)
    catch_name_node = parameter.child_by_field_name('name') if parameter else None
    catch_name = java.text(catch_name_node)
    if not catch_name:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_V2_CATCH_BINDING_UNKNOWN')
    if any(java.text(n.child_by_field_name('name')) == catch_name for n in java.nodes('variable_declarator')):
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_EXCEPTION_SCOPE_COLLISION')
    if any(java.text(n.child_by_field_name('name')) == catch_name for n in java.nodes('formal_parameter')):
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_EXCEPTION_SCOPE_COLLISION')

    catch_body = catch.child_by_field_name('body')
    catch_statements = children(catch_body) if catch_body is not None else []
    marker_assignments = []
    remaining = []
    for statement in catch_statements:
        direct = children(statement) if statement.type == 'expression_statement' else []
        expression = direct[0] if len(direct) == 1 else None
        if expression is not None and expression.type == 'assignment_expression':
            left = expression.child_by_field_name('left')
            right = expression.child_by_field_name('right')
            if (left is not None and right is not None and java.text(left) == flag and java.text(right) == 'true'
                    and re.fullmatch(re.escape(flag) + r'\s*=\s*true', java.text(expression))):
                marker_assignments.append((statement, left))
                continue
        remaining.append(statement)
    if len(marker_assignments) != 1:
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_FLAG_ASSIGNMENT_NOT_UNIQUE')

    allowed_flag_spans = {java.span(variable.child_by_field_name('name')), java.span(marker_assignments[0][1]),
                          java.span(marker['flag_arg'])}
    actual_flag_spans = {java.span(n) for n in java.nodes('identifier') if java.text(n) == flag}
    if actual_flag_spans != allowed_flag_spans:
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_FLAG_HAS_OTHER_USE')

    observations, observation_error = _ect_v2_catch_observations(
        java, remaining, catch_name, owner, instance, context)
    if observation_error:
        return finish('REVIEW_HIGH_RISK' if 'BINDING' not in observation_error and 'API' not in observation_error
                      else 'REVIEW_INSUFFICIENT_CONTEXT', observation_error)

    try_body = target.child_by_field_name('body')
    protected = children(try_body) if try_body is not None else []
    if len(protected) != 1:
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_PROTECTED_REGION_NOT_SINGLE_STATEMENT')
    statement = protected[0]
    if statement.type == 'expression_statement':
        direct = children(statement)
        if len(direct) != 1 or direct[0].type not in ('method_invocation', 'object_creation_expression'):
            return finish('REVIEW_HIGH_RISK', 'ECT_V2_PROTECTED_STATEMENT_UNSUPPORTED')
        executable = '() -> ' + java.text(direct[0])
    elif statement.type == 'local_variable_declaration':
        declared = [n for n in statement.named_children if n.type == 'variable_declarator']
        value = declared[0].child_by_field_name('value') if len(declared) == 1 else None
        if value is None or value.type not in ('method_invocation', 'object_creation_expression'):
            return finish('REVIEW_HIGH_RISK', 'ECT_V2_PROTECTED_DECLARATION_UNSUPPORTED')
        if java.text(declared[0].child_by_field_name('name')) == catch_name:
            return finish('REVIEW_HIGH_RISK', 'ECT_V2_EXCEPTION_SCOPE_COLLISION')
        executable = '() -> { ' + java.text(statement) + ' }'
    else:
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_PROTECTED_STATEMENT_UNSUPPORTED')
    if any(n.type in ('assignment_expression', 'update_expression', 'lambda_expression',
                      'anonymous_class_body', 'class_body') for n in walk(statement)):
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_PROTECTED_REGION_SIDE_EFFECT_BOUNDARY_UNSUPPORTED')

    region_start, region_end = java.span(declaration)[0], java.span(post)[1]
    if any(region_start <= java.span(n)[0] and java.span(n)[1] <= region_end
           for n in java.nodes('line_comment') + java.nodes('block_comment')):
        return finish('REVIEW_HIGH_RISK', 'ECT_V2_COMMENT_IN_REWRITE_REGION_UNSUPPORTED')
    other_writes = [n for n in java.nodes('assignment_expression') + java.nodes('update_expression')
                    if java.span(n) != java.span(marker_assignments[0][0].named_children[0])]
    if other_writes:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_V2_EFFECTIVELY_FINAL_CAPTURE_UNKNOWN')

    from .java_type_evidence import BindingUnknown, resolve_ect_exception_type
    try:
        exception_binding = resolve_ect_exception_type(instance, context, exception)
    except BindingUnknown as exc:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', str(exc))

    replacement = f'{exception} {catch_name} = {marker["api"]}({exception}.class, {executable});'
    observation_text = [java.text(statement) for statement in observations]
    if observation_text:
        replacement += '\n' + '\n'.join(observation_text)
    target_id = 'ect:v2:' + digest(str(region_start) + ':' + java.source[region_start:region_end])
    proof['ect_contract_version'] = ECT_DISPATCH_CONTRACT_VERSION
    proof['ect_path'] = ECT_MUST_THROW_PATH
    proof['ect_path_contract_version'] = ECT_MUST_THROW_CONTRACT_VERSION
    proof['targets'] = [{
        'target_id': target_id, 'start': region_start, 'end': region_end,
        'conclusion': 'ECT_V2_MUST_THROW_FLAG_REGION', 'exception_type': exception,
        'exception_binding': exception_binding, 'catch_variable': catch_name,
        'flag': flag, 'flag_initial_value': 'false', 'flag_assignment': java.text(marker_assignments[0][0]),
        'must_throw_assertion': marker['source'], 'protected_statement': java.text(statement),
        'protected_statement_kind': statement.type, 'exception_observations': observation_text,
        'replacement': replacement,
        'safety_conditions': [
            'SINGLE_TRY_SINGLE_CATCH_NO_FINALLY_OR_RESOURCES', 'BOUND_EXCEPTION_DESCENDS_FROM_EXCEPTION',
            'FALSE_TO_TRUE_FLAG_WITH_UNCONDITIONAL_POST_ASSERTION', 'FLAG_HAS_NO_OTHER_USE',
            'ONE_COMPLETE_PROTECTED_STATEMENT', 'EXCEPTION_OBSERVATIONS_PRESERVED',
            'NO_OTHER_WRITES_OR_CONTROL_IN_REWRITE_REGION',
        ],
    }]
    return finish('REFACTOR_LOW_RISK', 'ECT_V2_MUST_THROW_FLAG_REGION_PROVEN',
                  ['REWRITE_MUST_THROW_FLAG_ORACLE_WITH_ASSERT_THROWS'])


ECT_NOTHROW_FORBIDDEN_NODES = {
    'try_statement', 'try_with_resources_statement', 'catch_clause', 'finally_clause',
    'return_statement', 'break_statement', 'continue_statement', 'yield_statement',
    'throw_statement', 'if_statement', 'switch_expression', 'switch_statement',
    'switch_block_statement_group', 'for_statement', 'enhanced_for_statement',
    'while_statement', 'do_statement', 'synchronized_statement',
    'lambda_expression', 'anonymous_class_body', 'class_declaration',
    'record_declaration', 'enum_declaration', 'assignment_expression',
    'update_expression',
}
ECT_NOTHROW_ORACLE_NAMES = set(SUPPORTED_ASSERTIONS) | {
    'fail', 'assertAll', 'assumeTrue', 'assumeFalse', 'assumingThat',
    'assumeThat', 'assumeNoException', 'dynamicTest', 'dynamicContainer',
}


def _ect_nothrow_annotation_binding(java, method, owner):
    modifiers = next((node for node in method.named_children if node.type == 'modifiers'), None)
    annotations = [n for n in walk(modifiers) if n.type in ('annotation', 'marker_annotation')] if modifiers else []
    if len(annotations) != 1:
        return None
    raw = java.text(annotations[0]).strip()
    name = java.text(annotations[0].child_by_field_name('name'))
    if raw == '@org.junit.jupiter.api.Test' or name == 'org.junit.jupiter.api.Test':
        return {'annotation': raw, 'qualified_type': 'org.junit.jupiter.api.Test', 'source': 'FULLY_QUALIFIED'}
    if name != 'Test':
        return None
    try:
        owner_java = Java(owner)
    except ValueError:
        return None
    imports = [owner_java.text(n).strip() for n in owner_java.nodes('import_declaration')]
    exact = [item for item in imports if item.endswith('.Test;') and not item.startswith('import static ')]
    if exact != ['import org.junit.jupiter.api.Test;']:
        return None
    return {'annotation': raw, 'qualified_type': 'org.junit.jupiter.api.Test', 'source': 'EXACT_IMPORT'}


def _ect_nothrow_call_arity(java, call):
    arguments = call.child_by_field_name('arguments')
    return len(children(arguments)) if arguments is not None else 0


def _ect_nothrow_helper_closure(instance, context):
    """Prove that source-resolved test helpers contain no hidden oracle.

    Only unqualified, ``this`` and ``super`` calls can bind to the test class
    hierarchy. Explicit external receivers remain ordinary calls whose exact
    syntax is preserved and whose legality is checked by compilation.
    """
    from .java_type_evidence import BindingUnknown, DeclarationResolver
    resolver = DeclarationResolver(instance, context)
    visited = set()
    evidence = []

    def resolve_helper(name, arity, receiver):
        hierarchy = list(resolver._hierarchy(resolver.owner_type, member_name=name))
        if receiver == 'super':
            hierarchy = hierarchy[1:]
        for typ in hierarchy:
            matches = []
            for declaration in resolver._members(typ, 'method_declaration'):
                if typ['java'].text(declaration.child_by_field_name('name')) != name:
                    continue
                parameters = declaration.child_by_field_name('parameters')
                if len(children(parameters)) == arity:
                    matches.append(declaration)
            if matches:
                if len(matches) != 1:
                    raise BindingUnknown('ECT_NOTHROW_HELPER_OVERLOAD_AMBIGUOUS')
                return typ, matches[0]
        raise BindingUnknown('ECT_NOTHROW_HELPER_BINDING_UNKNOWN')

    def scan(java_view, method, depth):
        if depth > 8:
            raise BindingUnknown('ECT_NOTHROW_HELPER_CLOSURE_DEPTH_EXCEEDED')
        body = method.child_by_field_name('body')
        if body is None:
            raise BindingUnknown('ECT_NOTHROW_HELPER_BODY_MISSING')
        for call in (n for n in walk(body) if n.type == 'method_invocation'):
            name = java_view.text(call.child_by_field_name('name'))
            if name in ECT_NOTHROW_ORACLE_NAMES or name.startswith(('assert', 'assume')):
                raise BindingUnknown('ECT_NOTHROW_REACHABLE_ASSERTION_OR_ASSUMPTION')
            receiver = java_view.text(call.child_by_field_name('object'))
            if receiver not in ('', 'this', 'super'):
                continue
            typ, declaration = resolve_helper(name, _ect_nothrow_call_arity(java_view, call), receiver)
            key = (typ['name'], *typ['java'].span(declaration))
            if key in visited:
                continue
            visited.add(key)
            evidence.append({
                'declaring_type': typ['name'], 'method': name,
                'arity': _ect_nothrow_call_arity(java_view, call),
                'source_hash': digest(typ['java'].text(declaration)),
                'source_file': typ.get('path') or None,
            })
            scan(typ['java'], declaration, depth + 1)

    scan(resolver.owner, resolver.method, 0)
    return {
        'status': 'COMPLETE',
        'methods': evidence,
        'closure_hash': digest(json.dumps(evidence, sort_keys=True)),
        'evidence_sources': [resolver.used[key] for key in sorted(resolver.used)],
    }


def _ect_nothrow_body_replacement(java, body):
    start, end = java.span(body)
    newline = '\r\n' if '\r\n' in java.source else '\n'
    raw_inner = java.source[start + 1:end - 1].replace('\r\n', '\n').replace('\r', '\n')
    lines = raw_inner.split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    line_start = java.source.rfind('\n', 0, end - 1) + 1
    close_indent = re.match(r'[ \t]*', java.source[line_start:end - 1]).group(0)
    statement_indent = close_indent + '    '
    shifted = newline.join(('    ' + line if line.strip() else line) for line in lines)
    return (
        '{' + newline + statement_indent
        + 'org.junit.jupiter.api.Assertions.assertDoesNotThrow(() -> {' + newline
        + shifted + newline + statement_indent + '});' + newline + close_indent + '}'
    )


def materialize_ect_nothrow(source, proof):
    target = proof['targets'][0]
    return apply_edits(source, [
        (target['body_start'], target['body_end'], target['body_replacement']),
        (target['throws_remove_start'], target['throws_end'], ''),
    ])


def _analyze_ect_nothrow_v1(java, owner, proof, finish, *, instance, context):
    method = java.nodes('method_declaration')[0]
    throws_nodes = java.nodes('throws')
    if len(throws_nodes) != 1:
        return finish('REVIEW_HIGH_RISK', 'ECT_NOTHROW_SINGLE_THROWS_CLAUSE_REQUIRED')
    throws = throws_nodes[0]
    types = [node for node in children(throws)
             if node.type in ('type_identifier', 'scoped_type_identifier', 'generic_type')]
    if len(types) != 1:
        return finish('REVIEW_HIGH_RISK', 'ECT_NOTHROW_SINGLE_CHECKED_EXCEPTION_REQUIRED')
    raw_type = java.text(types[0])

    binding = _ect_nothrow_annotation_binding(java, method, owner)
    if binding is None:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_NOTHROW_JUPITER_TEST_BINDING_UNKNOWN')
    parameters = method.child_by_field_name('parameters')
    return_type = method.child_by_field_name('type')
    modifier_node = next((node for node in method.named_children if node.type == 'modifiers'), None)
    modifiers = java.text(modifier_node)
    if (parameters is None or children(parameters) or return_type is None or java.text(return_type) != 'void'
            or re.search(r'\b(?:abstract|native|static)\b', modifiers)):
        return finish('REVIEW_HIGH_RISK', 'ECT_NOTHROW_ORDINARY_VOID_ZERO_ARG_TEST_REQUIRED')
    body = method.child_by_field_name('body')
    statements = children(body) if body is not None else []
    if not statements:
        return finish('SKIP_NOT_APPLICABLE', 'ECT_NOTHROW_NO_ORACLE_BENEFIT')
    if any(statement.type not in ('local_variable_declaration', 'expression_statement') for statement in statements):
        return finish('REVIEW_HIGH_RISK', 'ECT_NOTHROW_SEQUENTIAL_STATEMENTS_REQUIRED')
    forbidden = [node for node in walk(body) if node.type in ECT_NOTHROW_FORBIDDEN_NODES]
    if forbidden:
        reason = ('ECT_NOTHROW_EXPLICIT_THROW_INTENT' if any(n.type == 'throw_statement' for n in forbidden)
                  else 'ECT_NOTHROW_EXCEPTION_BOUNDARY_PRESENT' if any(n.type.startswith('try') or n.type in ('catch_clause', 'finally_clause') for n in forbidden)
                  else 'ECT_NOTHROW_CONTROL_OR_CAPTURE_UNSUPPORTED')
        return finish('REVIEW_HIGH_RISK', reason)
    calls = [node for node in walk(body) if node.type == 'method_invocation']
    direct_oracles = [java.text(call.child_by_field_name('name')) for call in calls
                      if java.text(call.child_by_field_name('name')) in ECT_NOTHROW_ORACLE_NAMES
                      or java.text(call.child_by_field_name('name')).startswith(('assert', 'assume'))]
    if direct_oracles:
        reason = ('ECT_NOTHROW_ASSUMPTION_OUTCOME_CHANGED' if any(name.startswith('assume') for name in direct_oracles)
                  else 'ECT_NOTHROW_EXISTING_EXCEPTION_ASSERTION' if any(name in ('assertThrows', 'assertThrowsExactly', 'assertDoesNotThrow') for name in direct_oracles)
                  else 'ECT_NOTHROW_ASSERTION_DIAGNOSTIC_WRAPPED')
        return finish('REVIEW_HIGH_RISK', reason)
    if not calls and not any(node.type == 'object_creation_expression' for node in walk(body)):
        return finish('SKIP_NOT_APPLICABLE', 'ECT_NOTHROW_NO_ORACLE_BENEFIT')
    if re.search(r'@(?:ExtendWith|RegisterExtension|TestTemplate)\b|\bTestExecutionExceptionHandler\b', owner):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_NOTHROW_EXTERNAL_EXCEPTION_OBSERVER_UNKNOWN')

    from .java_type_evidence import BindingUnknown, resolve_ect_checked_exception_type
    try:
        exception_binding = resolve_ect_checked_exception_type(instance, context, raw_type)
        helper_closure = _ect_nothrow_helper_closure(instance, context)
    except BindingUnknown as exc:
        return finish('REVIEW_INSUFFICIENT_CONTEXT', str(exc))

    params_end = java.span(parameters)[1]
    throws_start, throws_end = java.span(throws)
    if java.source[params_end:throws_start].strip():
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_NOTHROW_THROWS_RANGE_UNPROVEN')
    body_start, body_end = java.span(body)
    replacement = _ect_nothrow_body_replacement(java, body)
    statement_evidence = [{
        'index': index, 'type': statement.type,
        'start': java.span(statement)[0], 'end': java.span(statement)[1],
        'ast_hash': digest(json.dumps(ast_node_tokens(java, statement), ensure_ascii=False)),
    } for index, statement in enumerate(statements)]
    target_id = 'ect:nothrow:v1:' + digest(str(throws_start) + ':' + java.text(throws) + ':' + java.text(body))
    proof.update({
        'ect_contract_version': ECT_DISPATCH_CONTRACT_VERSION,
        'ect_path': ECT_NOTHROW_PATH,
        'ect_path_contract_version': ECT_NOTHROW_CONTRACT_VERSION,
        'junit_test_binding': binding,
        'assert_does_not_throw_binding': {
            'qualified_api': 'org.junit.jupiter.api.Assertions.assertDoesNotThrow',
            'overload': 'Executable', 'execution': 'SYNCHRONOUS',
        },
        'diagnostic_policy': 'ASSERTION_FAILED_ERROR_WITH_ORIGINAL_CAUSE',
        'helper_closure': helper_closure,
        'direct_oracle_census': [],
        'reachable_oracle_census': [],
        'forbidden_nodes': [],
        'import_plan': 'USE_FQN',
        'targets': [{
            'target_id': target_id,
            'conclusion': 'ECT_NOTHROW_V1_COMPLETE_BODY',
            'throws_text': java.text(throws),
            'throws_type': raw_type,
            'throws_binding': exception_binding,
            'throws_start': throws_start,
            'throws_remove_start': params_end,
            'throws_end': throws_end,
            'body_start': body_start,
            'body_end': body_end,
            'body_hash': digest(java.text(body)),
            'body_ast_fingerprint': digest(json.dumps(ast_node_tokens(java, body), ensure_ascii=False)),
            'statement_count': len(statements),
            'statements': statement_evidence,
            'body_replacement': replacement,
            'wrapper_api': 'org.junit.jupiter.api.Assertions.assertDoesNotThrow',
        }],
    })
    expected = materialize_ect_nothrow(java.source, proof)
    proof['expected_candidate_hash'] = digest(expected)
    return finish('REFACTOR_LOW_RISK', 'ECT_NOTHROW_V1_NO_THROW_SMOKE_TEST_PROVEN', [
        'REMOVE_APPROVED_THROWS_CLAUSE',
        'WRAP_COMPLETE_METHOD_BODY_WITH_ASSERT_DOES_NOT_THROW',
    ])


def analyze_ect(java, owner, proof, finish, *, instance=None, context=None):
    tries = java.nodes('try_statement') + java.nodes('try_with_resources_statement')
    if not tries:
        if java.nodes('throws'):
            return _analyze_ect_nothrow_v1(java, owner, proof, finish,
                                           instance=instance or {}, context=context or {})
        if java.nodes('throw_statement'): return finish('SKIP_NOT_APPLICABLE', 'EXPLICIT_THROW_OUTSIDE_SUPPORTED_PATTERN')
        return finish('SKIP_FALSE_POSITIVE', 'NO_MANUAL_EXCEPTION_TARGET')
    if len(tries) != 1: return finish('REVIEW_HIGH_RISK', 'ECT_MULTIPLE_TARGETS_UNSUPPORTED')
    target = tries[0]
    catches = [n for n in target.named_children if n.type == 'catch_clause']
    if target.type != 'try_statement' or len(catches) != 1 or any(n.type == 'finally_clause' for n in target.named_children):
        return finish('REVIEW_HIGH_RISK', 'ECT_CATCH_OR_RESOURCE_BOUNDARY_UNSUPPORTED')
    if target.parent.type != 'block' or target.parent.parent.type != 'method_declaration':
        return finish('REVIEW_HIGH_RISK', 'ECT_NESTED_CONTROL_FLOW_UNSUPPORTED')
    catch = catches[0]
    parameter = next((n for n in catch.named_children if n.type == 'catch_formal_parameter'), None)
    types = [n for n in walk(parameter) if n.type == 'catch_type'] if parameter else []
    exception = java.text(types[0]) if len(types) == 1 else ''
    if '|' in exception or not re.fullmatch(r'[\w.]+', exception):
        return finish('REVIEW_HIGH_RISK', 'ECT_MULTICATCH_UNSUPPORTED')
    short = exception.rsplit('.', 1)[-1]
    if short in ('Throwable', 'Error', 'AssertionError', 'AssertionFailedError', 'MultipleFailuresError'):
        return finish('REVIEW_HIGH_RISK', 'ECT_CATCH_CAN_CAPTURE_FAIL')
    v2 = _analyze_ect_v2(java, owner, proof, finish, instance=instance or {}, context=context or {},
                         target=target, catch=catch, exception=exception)
    if v2 is not None:
        return v2
    # Known java.lang Exception descendants cannot cover AssertionFailedError.
    safe_types = {'Exception', 'RuntimeException', 'IllegalArgumentException', 'IllegalStateException', 'NullPointerException', 'IndexOutOfBoundsException', 'UnsupportedOperationException', 'NumberFormatException'}
    if short not in safe_types or exception not in (short, 'java.lang.' + short):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'EXCEPTION_HIERARCHY_UNKNOWN')
    if exception == short and not re.search(r'\bimport\s+java\.lang\.' + re.escape(short) + r'\s*;', owner):
        # Implicit java.lang lookup may be shadowed by an unseen same-package
        # type. Without a project symbol solver require an explicit binding.
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'EXCEPTION_TYPE_BINDING_UNKNOWN')
    if re.search(r'\b(?:class|interface)\s+' + re.escape(short) + r'\b|import\s+(?!java\.lang\.)[\w.]+\.' + re.escape(short) + r'\s*;', owner):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'EXCEPTION_TYPE_SHADOWED')
    catch_body = catch.child_by_field_name('body')
    if catch_body is None or children(catch_body):
        return finish('REVIEW_HIGH_RISK', 'ECT_CATCH_HAS_BEHAVIOR')
    body = target.child_by_field_name('body')
    statements = children(body)
    if len(statements) != 2 or any(n.type != 'expression_statement' for n in statements):
        return finish('REVIEW_HIGH_RISK', 'ECT_TRIGGER_FAIL_SEQUENCE_REQUIRED')
    trigger, fail = (children(n)[0] for n in statements)
    if trigger.type not in ('method_invocation', 'object_creation_expression') or fail.type != 'method_invocation' or java.text(fail.child_by_field_name('name')) != 'fail':
        return finish('REVIEW_HIGH_RISK', 'ECT_TRIGGER_FAIL_SEQUENCE_REQUIRED')
    if java.text(trigger.child_by_field_name('name')) == 'fail':
        return finish('REVIEW_HIGH_RISK', 'ECT_FAIL_IS_NOT_TRIGGER')
    if not resolve_api(java, fail, owner, 'fail') or not resolve_api(java, fail, owner, 'assertThrows'):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_JUNIT_API_UNPROVEN')
    fail_args = children(fail.child_by_field_name('arguments'))
    if len(fail_args) > 1 or (fail_args and not plain_string(java.text(fail_args[0]))):
        return finish('REVIEW_HIGH_RISK', 'ECT_DYNAMIC_FAIL_MESSAGE')
    # Moving to a lambda may capture mutable locals, or assignments may change
    # scope. Accept a single invocation with no assignments/update/declarations.
    if any(n.type in ('assignment_expression', 'update_expression', 'lambda_expression', 'anonymous_class_body', 'class_body') for n in walk(trigger)):
        return finish('REVIEW_HIGH_RISK', 'ECT_TRIGGER_CAPTURE_UNSUPPORTED')
    if java.nodes('assignment_expression') or java.nodes('update_expression'):
        return finish('REVIEW_INSUFFICIENT_CONTEXT', 'ECT_EFFECTIVELY_FINAL_CAPTURE_UNKNOWN')
    start, end = java.span(target)
    receiver = java.text(fail.child_by_field_name('object'))
    api = (receiver + '.' if receiver else '') + 'assertThrows'
    message = java.text(fail_args[0]) if fail_args else None
    trigger_text = java.text(trigger)
    replacement = f'{api}({exception}.class, () -> {trigger_text}' + (', ' + message if message else '') + ');'
    target_id = 'ect:v4:' + digest(str(start) + ':' + java.text(target))
    proof['ect_contract_version'] = ECT_DISPATCH_CONTRACT_VERSION
    proof['ect_path'] = ECT_MUST_THROW_PATH
    proof['ect_path_contract_version'] = '1.0'
    proof['targets'] = [{'target_id': target_id, 'start': start, 'end': end,
                         'trigger': trigger_text, 'exception_type': exception, 'message': message,
                         'replacement': replacement, 'conclusion': 'ECT_SAFE_REGION'}]
    return finish('REFACTOR_LOW_RISK', 'ECT_SINGLE_TARGET_REGION_PROVEN', ['REWRITE_TARGET_EXCEPTION_ORACLE_WITH_ASSERT_THROWS'])


def numeric_argument_nodes(java, args_node):
    for index, arg in enumerate(children(args_node)):
        for node in walk(arg):
            if 'integer_literal' in node.type or 'floating_point_literal' in node.type:
                literal = node.parent if node.parent.type == 'unary_expression' and java.text(node.parent).startswith(('-', '+')) else node
                yield index, arg, literal


def analyze_mnt(java, owner, calls, proof, finish, *, instance=None, context=None):
    """Explicit specifications or candidate-grounded ordinary test context.

    Immediately preceding comment syntax:
      // @test-constant retryLimit = 3 : maximum allowed retry attempts
    Existing local reuse:
      // @test-expected retryLimit
    A normal explanatory comment alone never proves a numeric role or name.
    Ordinary-context risk approval authorizes generation, not name acceptance.
    """
    from .java_type_evidence import DeclarationResolver, BindingUnknown, MNT_RULE_VERSION, explained_zero
    proof['mnt_rule_version'] = MNT_RULE_VERSION
    resolver = None
    resolver_error = None
    try:
        resolver = DeclarationResolver(instance or {}, context or {})
    except (ValueError, OSError, KeyError) as exc:
        resolver_error = str(exc) if str(exc).startswith('MNT_') else 'MNT_SOURCE_PARSE_UNSUPPORTED'
    targets = []
    for call in calls:
        node = call_node(java, call)
        args_node = node.child_by_field_name('arguments')
        for index, arg, literal_node in numeric_argument_nodes(java, args_node):
            raw = java.text(literal_node)
            nums = [n for n in walk(arg) if 'integer_literal' in n.type or 'floating_point_literal' in n.type]
            if not nums: continue
            start, end = java.span(literal_node)
            role = ('EXPECTED' if index == 0 else 'ACTUAL' if index == 1 else 'MESSAGE_OR_DELTA') if call['name'] in ('assertEquals', 'assertNotEquals', 'assertArrayEquals') else 'OTHER'
            item = {'target_id': 'mnt:v4:' + digest(proof['binding']['instance_id'] + ':' + call['assertion_id'] + ':' + str(index) + ':' + str(start)),
                    'instance_id': proof['binding']['instance_id'], 'assertion_identity': call['assertion_id'],
                    'assertion_api': call['name'], 'parameter_index': index, 'role': role, 'argument_source': java.text(arg),
                    'ast_path': call['ast_path'], 'start': start, 'end': end, 'literal': raw,
                    'literal_type': numeric_type(raw), 'binding_status': 'UNRESOLVED',
                    'context_explanation': None, 'evidence_source': None, 'idiomatic_value': 'UNKNOWN',
                    'explicit_example': False, 'boundary_input': 'UNKNOWN', 'suggested_name': None,
                    'benefit': 'UNKNOWN', 'decision': 'REVIEW_INSUFFICIENT_CONTEXT', 'reason_code': 'MNT_SEMANTIC_EVIDENCE_MISSING'}
            targets.append(item)
            item['evaluated_gates'] = ['SOURCE_METHOD']
            item['numeric_role'] = ('DIRECT_ASSERTION_'+role if literal_node == arg else
                                    'ARRAY_INDEX' if literal_node.parent.type == 'array_access' else
                                    'NESTED_CALL_ARGUMENT' if literal_node.parent.type == 'argument_list' else 'NESTED_EXPRESSION')
            item['reason_category'] = 'INSUFFICIENT_EVIDENCE'
            if resolver_error:
                item['reason_code'] = resolver_error; continue
            enclosing = node.parent
            while enclosing and enclosing.type != 'method_declaration':
                enclosing = enclosing.parent
            if not enclosing or enclosing.parent.type != 'program':
                item['reason_code'] = 'MNT_NESTED_METHOD_TARGET_UNSUPPORTED'; continue
            item['evaluated_gates'].append('ASSERTION_API')
            try:
                item['api_binding'] = resolver.api(call)
            except (BindingUnknown, OSError, ValueError) as exc:
                item['reason_code'] = str(exc) if str(exc).startswith('MNT_') else 'MNT_API_PARSING_UNSUPPORTED'
                continue
            item['evaluated_gates'].append('OVERLOAD')
            if overload(java, call) not in ('MISSING_MESSAGE', 'MESSAGE_PRESENT'):
                item.update(reason_code='MNT_UNSUPPORTED_ASSERTION_OVERLOAD', reason_category='UNSUPPORTED_TRANSFORM', decision='SKIP_NOT_APPLICABLE'); continue
            item['evaluated_gates'].append('CONTROL_AND_SCOPE')
            if call['control_flow_path'] or call['nesting_depth'] or call['unsupported_reasons'] or node.parent.type != 'expression_statement' or node.parent.parent.type != 'block':
                item.update(reason_code='MNT_UNSUPPORTED_CONTROL_FLOW', reason_category='UNSUPPORTED_TRANSFORM', decision='SKIP_NOT_APPLICABLE'); continue
            statement = node.parent
            # Obtain the immediately preceding comment, not arbitrary nearby prose.
            previous = [n for n in statement.parent.named_children if n.end_byte <= statement.start_byte]
            comment = previous[-1] if previous and previous[-1].type in COMMENTS else None
            note = java.text(comment).strip() if comment else ''
            if note.startswith('// @test-example:'):
                item.update(explicit_example=True, context_explanation=note, evidence_source='ADJACENT_TEST_EXAMPLE',
                            benefit='INSUFFICIENT', decision='SKIP_NOT_APPLICABLE', reason_code='MNT_EXPLICIT_EXAMPLE_RETAINED')
                continue
            item['evaluated_gates'].append('NUMERIC_POSITION')
            if call['name'] not in ('assertEquals', 'assertNotEquals') or index != 0 or not item['literal_type'] or len(nums) != 1 or literal_node != arg:
                item.update(reason_code='MNT_UNSUPPORTED_NUMERIC_POSITION', reason_category='UNSUPPORTED_TRANSFORM', decision='SKIP_NOT_APPLICABLE'); continue
            actual = call['arguments'][1]
            from .mnt_naming import unconfirmed_target_intent, annotation_purpose
            uncertain = unconfirmed_target_intent(java, call, statement)
            if uncertain:
                item.update(reason_code='MNT_EXPECTED_VALUE_UNCONFIRMED',
                            reason_category='SEMANTIC_EVIDENCE_MISSING', intent_evidence=uncertain,
                            context_explanation='Original target expectation is explicitly unconfirmed; retain the original explanation.')
                continue
            if note.startswith(('// @test-constant', '// @test-expected')) and annotation_purpose(note) != 'EXPLANATORY_CANDIDATE':
                item.update(reason_code='MNT_NAMING_EVIDENCE_NON_EXPLANATORY', reason_category='SEMANTIC_EVIDENCE_MISSING')
                continue
            item['evaluated_gates'].append('ACTUAL_TYPE')
            actual_node = children(args_node)[1]
            if raw == '0' and call['name'] == 'assertEquals' and actual_node.type == 'method_invocation':
                receiver = java.text(actual_node.child_by_field_name('object'))
                receiver_binding = local_binding(java, receiver, node)
                collection_type = re.sub(r'<.*>', '', (receiver_binding or {}).get('type', ''))
                if (java.text(actual_node.child_by_field_name('name')) == 'size' and not children(actual_node.child_by_field_name('arguments'))
                        and collection_type in ('java.util.Collection', 'java.util.List', 'java.util.Set', 'java.util.Map')):
                    item.update(idiomatic_value=True, context_explanation='A zero-element collection is explicitly checked through the bound JDK size API.',
                        binding_status='FULLY_QUALIFIED_JDK_COLLECTION_LOCAL', evidence_source='TEST_LOCAL_TYPE_AND_SIZE_CONTRACT',
                        benefit='INSUFFICIENT', decision='SKIP_NOT_APPLICABLE', reason_code='MNT_EMPTY_COLLECTION_CHECK_RETAINED')
                    continue
            try:
                actual_evidence = resolver.expression(actual_node, node)
            except (BindingUnknown, OSError, ValueError) as exc:
                item['reason_code'] = str(exc) if str(exc).startswith('MNT_') else 'MNT_TYPE_PARSING_UNSUPPORTED'
                continue
            item.update(actual_type=actual_evidence['type'], actual_type_evidence=actual_evidence,
                        binding_status='DECLARATION_BOUND_ACTUAL_TYPE')
            item['evaluated_gates'].append('TYPE_COMPATIBILITY')
            if actual_evidence['type'] != item['literal_type']:
                item.update(reason_code='MNT_NUMERIC_OVERLOAD_CONVERSION_UNSUPPORTED', reason_category='UNSUPPORTED_TRANSFORM', decision='SKIP_NOT_APPLICABLE'); continue
            item['evaluated_gates'].append('BENEFIT')
            if raw == '0' and call['name'] == 'assertEquals':
                try:
                    empty_evidence = resolver.empty_constructor_count(actual_evidence, call)
                except (ValueError,OSError):
                    empty_evidence = None
                if empty_evidence:
                    item.update(reason_code='MNT_BOUND_EMPTY_CONSTRUCTOR_RETAINED',reason_category='NO_EXPLANATORY_BENEFIT',
                        decision='SKIP_NOT_APPLICABLE',benefit='INSUFFICIENT',evidence_source=empty_evidence['source'],
                        no_benefit_evidence=empty_evidence)
                    continue
                explanation = explained_zero(actual_evidence, call)
                if explanation:
                    item.update(reason_code=explanation[0], reason_category='NO_EXPLANATORY_BENEFIT',
                        decision='SKIP_NOT_APPLICABLE', benefit='INSUFFICIENT', idiomatic_value=True,
                        evidence_source=explanation[1], context_explanation=explanation[1])
                    continue
            # An explicit immutable test-side sample already exposes the exact
            # literal. Retaining it is not a detector false positive or RA deletion.
            sample_binding = local_binding(java, actual, node)
            if (sample_binding and sample_binding['final'] and sample_binding['type'] == item['literal_type']
                    and sample_binding['value'] == raw):
                item.update(reason_code='MNT_EXPLICIT_IMMUTABLE_SAMPLE_RETAINED', reason_category='NO_EXPLANATORY_BENEFIT',
                    decision='SKIP_NOT_APPLICABLE', benefit='INSUFFICIENT', evidence_source='BOUND_TEST_LOCAL_LITERAL')
                continue
            item['reason_code'] = 'MNT_NAMING_EVIDENCE_MISSING'
            item['evaluated_gates'].append('NAMING_EVIDENCE')
            spec = re.fullmatch(r'//\s*@test-constant\s+([a-zA-Z_$][\w$]*)\s*=\s*(\S+)\s*:\s*(.+)', note)
            reuse = re.fullmatch(r'//\s*@test-expected\s+([a-zA-Z_$][\w$]*)', note)
            name = spec[1] if spec else reuse[1] if reuse else None
            if not name:
                from .mnt_naming import naming_context
                try:
                    naming = naming_context(java, call, actual_evidence, statement, item['target_id'], resolver=resolver)
                except (ValueError, OSError) as exc:
                    detail = str(exc)
                    item['context_trace_error'] = detail
                    item['reason_code'] = detail if detail.startswith('MNT_CONTEXT_') else 'MNT_CONTEXT_DECLARATION_BINDING_UNRESOLVED'
                    continue
                if naming:
                    item.update(action='PROPOSE_LOCAL_NAME', naming_context=naming,
                                insert_at=java.span(statement)[0], benefit='PENDING_CANDIDATE_GROUNDING',
                                reason_category='NAMING_CANDIDATE_PENDING',
                                evidence_source='BOUND_TYPE_AND_ORIGINAL_TEST_PREPARATION',
                                decision='REFACTOR_LOW_RISK', reason_code='MNT_CONTEXT_ALLOWS_NAMING_ATTEMPT')
                continue
            if name in ('expectedValue', 'magicNumber', 'number', 'value', 'expected') or re.fullmatch(r'number\d+', name) or len(re.findall(r'[A-Z]?[a-z]+|[A-Z]+', name)) < 2:
                item['reason_code'] = 'MNT_NAME_HAS_NO_EXPLANATORY_BENEFIT'; continue
            if spec:
                if spec[2] != raw:
                    item['decision'] = 'REVIEW_HIGH_RISK'
                    item['reason_code'] = 'MNT_SPEC_CONFLICT'; continue
                if len(spec[3].split()) < 3:
                    item['reason_code'] = 'MNT_SPEC_CONFLICT'; continue
                # Include all original class identifiers, local declarations and
                # unresolved references. No new name may capture any old binding.
                # The metadata comment is the sole permitted occurrence.
                parsed_owner = Java(owner) if owner else java
                identifiers = [parsed_owner.text(n) for n in walk(parsed_owner.root) if n.type in ('identifier', 'type_identifier')]
                identifiers += [java.text(n) for n in walk(java.root) if n.type in ('identifier', 'type_identifier')]
                if name in identifiers:
                    item['decision'] = 'REVIEW_HIGH_RISK'
                    item['reason_code'] = 'MNT_NAME_COLLISION'; continue
                item.update(action='DECLARE_LOCAL', context_explanation=spec[3], evidence_source='ADJACENT_EXPLICIT_TEST_SPEC',
                            declaration=f'final {item["literal_type"]} {name} = {raw};',
                            insert_at=java.span(statement)[0])
            else:
                constant = local_binding(java, name, node)
                if not constant or not constant['final'] or constant['type'] != item['literal_type'] or constant['value'] != raw:
                    if constant and (constant['type'] != item['literal_type'] or constant['value'] != raw):
                        item['decision'] = 'REVIEW_HIGH_RISK'
                    item['reason_code'] = 'MNT_REUSE_BINDING_OR_VALUE_MISMATCH'; continue
                item.update(action='REUSE_TEST_LOCAL', context_explanation=note, evidence_source='EXPLICIT_TEST_LOCAL_REFERENCE',
                            constant_binding=constant)
            item.update(suggested_name=name, benefit='EXPLICIT_TEST_SEMANTIC_NAME', decision='REFACTOR_LOW_RISK', reason_code='MNT_TEST_SPEC_AND_BINDING_PROVEN')
    proof['numeric_targets'] = targets
    proof['engineering_sources'] = resolver.manifest() if resolver else []
    for item in targets:
        item['unassessed_conditions'] = [gate for gate in ('ASSERTION_API','OVERLOAD','CONTROL_AND_SCOPE',
            'NUMERIC_POSITION','ACTUAL_TYPE','TYPE_COMPATIBILITY','BENEFIT','NAMING_EVIDENCE')
            if gate not in item['evaluated_gates']] + ['CANDIDATE_NAME_AND_STATIC_CONTRACT','COMPILE','TARGET_TEST','REVALIDATION']
        if item['reason_code'].endswith(('_MISSING', '_UNAVAILABLE')) and item['decision'] == 'REVIEW_INSUFFICIENT_CONTEXT':
            item['reason_category'] = 'INPUT_MISSING' if item['reason_code'] != 'MNT_NAMING_EVIDENCE_MISSING' else 'SEMANTIC_EVIDENCE_MISSING'
        elif item['decision'] == 'REVIEW_INSUFFICIENT_CONTEXT' and item['reason_code'] not in (
                'MNT_NAMING_EVIDENCE_MISSING', 'MNT_EXPECTED_VALUE_UNCONFIRMED', 'MNT_NAMING_EVIDENCE_NON_EXPLANATORY'):
            item['reason_category'] = 'BINDING_CAPABILITY_LIMIT'
    proof['targets'] = [x for x in targets if x['decision'] == 'REFACTOR_LOW_RISK']
    proof['retained_targets'] = [x for x in targets if x['decision'] == 'SKIP_NOT_APPLICABLE']
    if not targets: return finish('SKIP_FALSE_POSITIVE', 'MNT_NO_NUMERIC_ASSERTION_TARGET')
    if all(x['decision'] == 'SKIP_NOT_APPLICABLE' for x in targets):
        return finish('SKIP_NOT_APPLICABLE', 'MNT_NO_SUPPORTED_BENEFICIAL_TARGET' if any(x['reason_category'] == 'UNSUPPORTED_TRANSFORM' for x in targets) else 'MNT_NO_EXPLANATORY_BENEFIT')
    if not proof['targets']:
        return finish('REVIEW_HIGH_RISK' if any(x['decision']=='REVIEW_HIGH_RISK' for x in targets) else 'REVIEW_INSUFFICIENT_CONTEXT', 'MNT_NO_SAFE_GENERATION_TARGET')
    # Permission concerns a safe candidate set, not every numeric AST node and
    # not an assertion that any proposed name has already passed acceptance.
    proof['selection_contract_version'] = '1'
    proof['generation_permission'] = 'CANDIDATE_GENERATION_ONLY'
    proof['final_acceptance_requires'] = ['NONEMPTY_SAFE_SELECTION', 'GROUNDED_NAMES', 'UNCHANGED_RETAINED_TARGETS',
        'EXACT_STATIC_CONTRACT', 'ISOLATED_BUILD', 'TARGET_TESTS', 'FINAL_COMBINED_TARGET_REVALIDATION']
    return finish('REFACTOR_LOW_RISK', 'MNT_SAFE_TARGET_SET_GENERATION_ONLY', ['EXTRACT_APPROVED_NUMERIC_ARGUMENTS'])


def proof_evidence(instance, context):
    proof = analyze(instance, context)
    lines = instance.get('line_range') or {}
    return {'evidence_id': 'CONTRACT-V4', 'evidence_type': 'COMPLETE_SAFETY_CONTRACT',
            'source_file': instance.get('test_relative_path'), 'method_signature': instance.get('method_signature'),
            'start_line': lines.get('start') if (lines.get('start') or 0) > 0 else None,
            'end_line': lines.get('end') if (lines.get('start') or 0) > 0 else None, 'matched_code': instance.get('test_code'),
            'rule_id': 'ALL_NECESSARY_CONDITIONS', 'rule_version': current_rule_version(instance.get('smell_type')), 'conclusion': proof['reason_code'],
            'confidence': 1.0, 'source_available': True, 'contract': proof}


def get_proof(evidence):
    if not isinstance(evidence, list) or any(not isinstance(e, dict) for e in evidence):
        return None
    matches = [e.get('contract') for e in evidence if e.get('evidence_id') == 'CONTRACT-V4' and isinstance(e.get('contract'), dict)]
    return matches[0] if len(matches) == 1 else None


def mnt_evidence_equal(left, right):
    """Compare the complete JSON-domain evidence, preserving every key/value.

    Only mapping order and tuple/list representation are normalized. Do not use
    cache canonical_payload here: it deliberately omits some fields. Unsupported
    values/non-string keys/non-finite numbers fail closed, never stringify them.
    """
    def normalize(value):
        if type(value) is dict:
            if any(type(k) is not str for k in value):
                raise ValueError('Evidence keys must be strings')
            return {k: normalize(v) for k,v in value.items()}
        if type(value) in (list, tuple):
            return [normalize(v) for v in value]
        if value is None or type(value) in (str, bool, int, float):
            return value
        raise ValueError('Evidence value is outside the JSON domain')
    try:
        encode = lambda v: json.dumps(normalize(v), ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':'), allow_nan=False)
        return encode(left) == encode(right)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def verify_proof(evidence, instance, context):
    from .risk_evidence import extract_rule_evidence
    proof = get_proof(evidence)
    # AR/RA/ECT retain their existing comparison semantics.
    same = mnt_evidence_equal if instance.get('smell_type') == 'Magic_Number_Test' else lambda a,b: a == b
    if not proof or not same(proof.get('binding'), source_binding(instance, context)):
        return False, 'STALE_MISSING_OR_CROSS_INSTANCE_EVIDENCE'
    # Replay all positive, negative and exclusion facts; dropping one record,
    # moving a range, or changing an allowed action is not accepted.
    if not same(evidence, extract_rule_evidence(instance, context)):
        return False, 'INCOMPLETE_OR_CONFLICTING_EVIDENCE'
    if proof.get('decision') != 'REFACTOR_LOW_RISK':
        return False, proof.get('reason_code', 'NO_PROOF')
    return True, 'COMPLETE_SOURCE_BOUND_PROOF_REPLAYED'


def replay_saved_contract(contract, candidate, owner):
    """Recheck persisted approvals at validation/resume; a version label is not proof."""
    if contract.get('risk_rule_version') != current_rule_version(contract.get('smell')):
        return False, 'RULE_VERSION_REAUDIT_REQUIRED'
    if contract.get('smell') == 'Redundant_Assertion':
        from .ra_contract import VERSION as ra_version
        if contract.get('ra_contract_version') != ra_version:
            return False, 'RA_CONTRACT_VERSION_REAUDIT_REQUIRED'
    if contract.get('smell') == 'Conditional_Test_Logic':
        from .ctl_contract import VERSION as ctl_version
        if contract.get('ctl_rule_version') != ctl_version:
            return False, 'CTL_RULE_VERSION_REAUDIT_REQUIRED'
    if contract.get('smell') == 'Exception_Catching_Throwing':
        proof = get_proof(contract.get('safety_evidence') or []) or {}
        if proof.get('ect_contract_version') == ECT_DISPATCH_CONTRACT_VERSION:
            if contract.get('ect_contract_version') != ECT_DISPATCH_CONTRACT_VERSION:
                return False, 'ECT_CONTRACT_VERSION_REAUDIT_REQUIRED'
            if contract.get('ect_path') != proof.get('ect_path') or contract.get('ect_path_contract_version') != proof.get('ect_path_contract_version'):
                return False, 'ECT_PATH_CONTRACT_VERSION_REAUDIT_REQUIRED'
    instance = contract.get('safety_instance') or {}
    if contract.get('smell') not in SMELLS or contract.get('smell') != instance.get('smell_type'):
        return False, 'SAVED_CONTRACT_IDENTITY_MISMATCH'
    request = {'input': {'instance': instance, 'context': {'test_class_source': owner},
                        'risk_result': {'result': {'evidence': contract.get('safety_evidence') or []}}}}
    if contract.get('smell') == 'Magic_Number_Test' and contract.get('safety_java_environment'):
        request['input']['context']['java_environment'] = contract['safety_java_environment']
    try:
        from .candidate_validation import CandidateValidator
        result = CandidateValidator().validate(raw_response=candidate, request=request, contract=contract, original_file=owner)
        return result['passed'], 'CURRENT_CANDIDATE_CONTRACT_REPLAYED' if result['passed'] else 'CURRENT_CANDIDATE_CONTRACT_FAILED'
    except (ValueError, KeyError, TypeError, IndexError):
        return False, 'INVALID_SAVED_CONTRACT'


def replay_method_chain(method, targets, owner):
    """Compose individually proven single-smell steps; never infer a missing step."""
    from .refactoring_contract import normalized_source, replace_method_in_copy
    current = method.get('original_method') or ''
    start, end = method['start_line'], method['end_line']
    pending = [t for t in targets if t.get('risk_contract')]
    if not pending:
        return False
    while pending:
        matched = [t for t in pending if normalized_source((t['risk_contract'].get('safety_instance') or {}).get('test_code') or '') == normalized_source(current)]
        if len(matched) != 1:
            return False
        step = matched[0]; contract = step['risk_contract']
        next_code = step.get('contract_candidate') or method['candidate_method']
        # Preserve the exact real-file prefix/suffix and current method's
        # indentation. The evidence itself binds the resulting class digest.
        baseline = (contract.get('safety_instance') or {}).get('test_code') or ''
        if normalized_source(current) == normalized_source(method.get('original_method') or ''):
            step_owner = owner
        else:
            step_owner = replace_method_in_copy(owner, baseline, start, end)
        if contract.get('safety_instance', {}).get('instance_id') != step.get('instance_id'):
            return False
        if not replay_saved_contract(contract, next_code, step_owner)[0]:
            return False
        current = next_code; pending.remove(step)
    return normalized_source(current) == normalized_source(method['candidate_method'])


def expected_mnt(source, proof):
    edits = []
    for item in proof['targets']:
        edits.append((item['start'], item['end'], item['suggested_name']))
        if item['action'] == 'DECLARE_LOCAL':
            edits.append((item['insert_at'], item['insert_at'], item['declaration'] + '\n'))
    return apply_edits(source, edits)


def validate_exact(smell, original, candidate, request):
    payload = request.get('input') or {}
    evidence = ((payload.get('risk_result') or {}).get('result') or {}).get('evidence') or []
    if not evidence: return False, 'CURRENT_RISK_CONTRACT_REQUIRED'
    instance = request['input'].get('instance') or {}
    context = request['input'].get('context') or {}
    valid, reason = verify_proof(evidence, instance, context)
    if not valid: return False, reason
    proof = get_proof(evidence)
    if original != instance.get('test_code'): return False, 'ORIGINAL_SOURCE_MISMATCH'
    try:
        if smell == 'Conditional_Test_Logic':
            from .ctl_contract import validate_candidate
            return validate_candidate(original, candidate, proof)
        if smell == 'Redundant_Assertion':
            if proof.get('ra_transform') == 'CATCH_CONSTANT_DELETION':
                from .ra_contract import validate_candidate
                return validate_candidate(original, candidate, proof)
            expected = apply_edits(original, [(t['statement_start'], t['statement_end'], '') for t in proof['targets']])
            if ast_tokens(expected) != ast_tokens(candidate):
                return False, 'RA_ONLY_APPROVED_STATEMENT_DELETIONS_ALLOWED'
            removed = {t['assertion_id'] for t in proof['targets']}
            remaining = [c for c in find_assertion_calls(original) if c['assertion_id'] not in removed]
            candidate_calls = find_assertion_calls(candidate)
            candidate_java = Java(candidate)
            oracle_ids = set(proof.get('remaining_oracle_ids') or [])
            if len(remaining) != len(candidate_calls) or not oracle_ids:
                return False, 'RA_REMAINING_ORACLE_SET_MISMATCH'
            for before, after in zip(remaining, candidate_calls):
                if before['assertion_id'] in oracle_ids and not confirmed_oracle(candidate_java, after):
                    return False, 'RA_REMAINING_ORACLE_BINDING_UNPROVEN'
            return True, 'RA_APPROVED_REMAINING_ORACLES_RECHECKED'
        elif smell == 'Exception_Catching_Throwing':
            path = proof.get('ect_path')
            target = proof['targets'][0]
            if path == ECT_NOTHROW_PATH:
                if proof.get('ect_contract_version') != ECT_DISPATCH_CONTRACT_VERSION or proof.get('ect_path_contract_version') != ECT_NOTHROW_CONTRACT_VERSION:
                    return False, 'ECT_NOTHROW_CONTRACT_VERSION_MISMATCH'
                expected = materialize_ect_nothrow(original, proof)
                if digest(expected) != proof.get('expected_candidate_hash'):
                    return False, 'ECT_NOTHROW_EXPECTED_MATERIALIZATION_MISMATCH'
                if comment_inventory(expected) != comment_inventory(candidate):
                    return False, 'ECT_NOTHROW_COMMENTS_CHANGED'
                if ast_tokens(expected) != ast_tokens(candidate):
                    return False, 'ECT_NOTHROW_EXACT_COMPLETE_BODY_WRAPPER_REQUIRED'
                candidate_java = Java(candidate)
                methods = candidate_java.nodes('method_declaration')
                wrappers = [n for n in candidate_java.nodes('method_invocation')
                            if candidate_java.text(n.child_by_field_name('object')) == JUNIT
                            and candidate_java.text(n.child_by_field_name('name')) == 'assertDoesNotThrow']
                if len(methods) != 1 or len(wrappers) != 1 or candidate_java.nodes('throws'):
                    return False, 'ECT_NOTHROW_SIGNATURE_OR_WRAPPER_MISMATCH'
                return True, 'ECT_NOTHROW_V1_EXACT_TRANSFORMATION'
            if path not in (None, ECT_MUST_THROW_PATH):
                return False, 'ECT_PATH_MISSING_OR_UNKNOWN'
            expected = apply_edits(original, [(target['start'], target['end'], target['replacement'])])
            if ((proof.get('ect_contract_version') == ECT_MUST_THROW_CONTRACT_VERSION
                 or proof.get('ect_path_contract_version') == ECT_MUST_THROW_CONTRACT_VERSION)
                    and comment_inventory(expected) != comment_inventory(candidate)):
                return False, 'ECT_V2_NON_TARGET_COMMENT_CHANGED'
            # Normalize only the new lambda's one-expression block, never any
            # original surrounding code or other assertThrows call.
            java = Java(candidate)
            for n in java.nodes('lambda_expression'):
                body = n.child_by_field_name('body')
                statements = children(body) if body.type == 'block' else []
                if len(statements) == 1 and statements[0].type == 'expression_statement':
                    expression = children(statements[0])[0]
                    normalized = apply_edits(candidate, [(*java.span(body), java.text(expression))])
                    if ast_tokens(expected) == ast_tokens(normalized): return True, 'EXACT_APPROVED_REGION_BLOCK_LAMBDA'
        elif smell == 'Magic_Number_Test':
            from .mnt_naming import materialize_names, naming_review_reason
            # Collect unknown naming outcomes only to finish the technical
            # diagnostics. They can NEVER make this acceptance check return True.
            proof = materialize_names(proof, payload.get('mnt_naming_proposals'), original, context.get('test_class_source') or '', _collect_review=True)
            expected = expected_mnt(original, proof)
            if ast_tokens(expected) != ast_tokens(candidate): return False, 'MNT_EXACT_DECLARATIONS_AND_REPLACEMENTS_REQUIRED'
            java = Java(candidate)
            from .mnt_comments import check_comments
            comment_result = check_comments(expected, candidate)
            if comment_result['decision'] != 'ALLOW':
                return False, comment_result['reason_code']
            calls = find_assertion_calls(candidate)
            reverse = []
            for target in proof['targets']:
                # Assertion order/API unchanged by exact expected AST above;
                # newly inserted declarations can change AST ordinal IDs.
                original_calls = find_assertion_calls(original)
                ordinal = next(i for i,c in enumerate(original_calls) if c['assertion_id'] == target['assertion_identity'])
                call = calls[ordinal]
                node = call_node(java, call)
                binding = local_binding(java, target['suggested_name'], node)
                if not binding or not binding['final'] or binding['type'] != target['literal_type'] or binding['value'] != target['literal']:
                    return False, 'MNT_REVERSE_BINDING_MISMATCH'
                span = call['argument_ranges'][target['parameter_index']]
                reverse.append((span['start'], span['end'], target['literal']))
                if target['action'] == 'DECLARE_LOCAL':
                    reverse.append((binding['declaration_start'], binding['declaration_end'], ''))
            if ast_tokens(apply_edits(candidate, reverse)) != ast_tokens(original):
                return False, 'MNT_REVERSE_INLINE_MISMATCH'
            review = naming_review_reason(proof)
            if review:
                return False, review
        else:
            return False, 'NOT_IMPLEMENTED'
        return ast_tokens(expected) == ast_tokens(candidate), 'EXACT_APPROVED_TRANSFORMATION'
    except ValueError as exc:
        return False, str(exc) if str(exc).startswith('MNT_') else 'INVALID_OR_MISALIGNED_TARGET_AST'
    except (KeyError, StopIteration, IndexError, TypeError):
        return False, 'INVALID_OR_MISALIGNED_TARGET_AST'


def numeric_effect(contract, candidate, owner, *, detector_positive=None):
    """Target details never replace the detector's method-level outcome."""
    evidence = contract.get('safety_evidence') or []
    instance = contract.get('safety_instance') or {}
    proof = get_proof(evidence) or {}
    targets = proof.get('numeric_targets') or []
    req = {'input': {'instance': instance, 'context': {'test_class_source': owner},
                     'risk_result': {'result': {'evidence': evidence}}}}
    if contract.get('safety_java_environment'):
        req['input']['context']['java_environment'] = contract['safety_java_environment']
    from .mnt_naming import materialize_names, saved_selection
    try:
        selection = saved_selection(contract)
    except ValueError:
        selection = {'invalid_saved_selection': True}
    req['input']['mnt_naming_proposals'] = selection
    ok, reason = validate_exact('Magic_Number_Test', instance.get('test_code') or '', candidate, req)
    approved = [t['target_id'] for t in targets if t['decision'] == 'REFACTOR_LOW_RISK']
    selected = []; retention = []
    if ok:
        resolved = materialize_names(proof, selection, instance['test_code'], owner)
        selected = resolved['selected_target_ids']; retention = resolved['selection_retained_targets']
    retained = [t['target_id'] for t in targets if t['target_id'] not in selected]
    pending = [t['target_id'] for t in targets if t['decision'] not in ('REFACTOR_LOW_RISK','SKIP_NOT_APPLICABLE')]
    return {'schema_version': '2', 'unit': 'NUMERIC_TARGET_WITHIN_METHOD_INSTANCE',
            'mnt_rule_version': proof.get('mnt_rule_version'), 'candidate_method_hash': digest(candidate),
            'observed_target_ids': [t['target_id'] for t in targets], 'selected_target_ids': selected,
            'retention_suggestions': retention, 'retention_suggestions_are_proof': False,
            'approved_target_ids': approved, 'processed_target_ids': selected if ok else [],
            'retained_target_ids': retained, 'pending_target_ids': pending,
            'unprocessed_target_ids': [tid for tid in approved if tid not in selected],
            'static_contract_passed': ok, 'selected_targets_processed': bool(selected) and ok,
            'all_targets_processed': bool(selected) and ok and not retained,
            'detector_method_positive': detector_positive, 'reason_code': reason}


def selected_numeric_effect_valid(effect):
    """Shared typed effect gate for MNT only; legacy flags cannot authorize partials."""
    from .java_type_evidence import MNT_RULE_VERSION
    from pipeline.contracts import validate_contract
    try:
        validate_contract(effect or {}, 'mnt-target-effect')
        if effect['schema_version'] != '2' or effect['mnt_rule_version'] != MNT_RULE_VERSION:
            return False
        selected = set(effect['selected_target_ids']); observed = set(effect['observed_target_ids'])
        retained = set(effect['retained_target_ids'])
        return bool(selected) and effect['static_contract_passed'] and effect['selected_targets_processed'] and (
            selected <= set(effect['approved_target_ids']) and selected == set(effect['processed_target_ids']) and
            not selected & retained and selected | retained == observed and
            effect['all_targets_processed'] == (not retained))
    except (ValueError, KeyError, TypeError):
        return False
