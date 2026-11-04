"""AR generation facts and frozen sample loading; no applicability decisions."""
from pathlib import Path
import json
import re

from pipeline.resume_store import read_json, file_hash, digest


def load_manifest(root, manifest_path, variant):
    manifest_path = (root / manifest_path).resolve()
    manifest = read_json(manifest_path)
    rows = manifest.get('rows')
    if (manifest.get('schema_version') != 'ar-rq2-sample-1'
            or not manifest.get('seed') or not isinstance(rows, list)
            or len(rows) != manifest.get('sample_size')
            or digest(rows) != manifest.get('rows_sha256')
            or [r['instance_id'] for r in rows] != manifest.get('ordered_instance_ids')
            or len({r['instance_id'] for r in rows}) != len(rows)
            or any(r.get('smell') != 'Assertion_Roulette' for r in rows)):
        raise ValueError('INVALID_FROZEN_AR_MANIFEST')
    required = {'input_ref', 'input_sha256', 'context_ref', 'context_sha256',
                'source_metadata_ref', 'source_metadata_sha256', 'source_sha256',
                'method_sha256', 'project_revision', 'relative_path', 'project', 'smell'}
    if any(not required.issubset(row) for row in rows):
        raise ValueError('FROZEN_AR_INPUT_BINDINGS_REQUIRED')
    forbidden = {'admission', 'risk_decision', 'full_state', 'assessment', 'reason_code', 'stratum', 'inclusion_probability', 'sampling_rank'}
    if any(forbidden.intersection(row) for row in rows):
        raise ValueError('ASSESSMENT_IN_GENERATION_ROWS')
    # A1 never opens the assessment sidecar, including for cardinality/filtering.
    if variant == 'A1':
        return rows
    labels_path = (root / manifest['full_assessments_ref']).resolve()
    if file_hash(labels_path) != manifest['full_assessments_sha256']:
        raise ValueError('FULL_ASSESSMENT_CHANGED')
    labels = read_json(labels_path)
    if set(labels) != {r['instance_id'] for r in rows}:
        raise ValueError('FULL_ASSESSMENT_ID_MISMATCH')
    if variant == 'A3':
        return [dict(r, full_state=labels[r['instance_id']]['full_state']) for r in rows]
    if variant == 'A2':
        return [dict(r, admission='ADMITTED') for r in rows
                if labels[r['instance_id']]['risk_decision'] == 'REFACTOR_LOW_RISK']
    if variant == 'FULL_CONTROL':
        return [dict(r, admission='ADMITTED' if labels[r['instance_id']]['risk_decision']
                     == 'REFACTOR_LOW_RISK' else 'NON_ADMITTED') for r in rows]
    raise ValueError('AR_MANIFEST_UNSUPPORTED_VARIANT')


def verify_input(root, row):
    for ref, checksum in (('input_ref', 'input_sha256'), ('context_ref', 'context_sha256'),
                          ('source_metadata_ref', 'source_metadata_sha256')):
        if checksum in row and file_hash((root / row[ref]).resolve()) != row[checksum]:
            raise ValueError('FROZEN_AR_INPUT_CHANGED:' + ref)


def generation_facts(instance, context):
    # This extractor reports source facts only; unlike extract_rule_evidence it
    # never calls proof_evidence/analyze or obtains a risk decision.
    from pipeline.agents.risk_evidence import _assertion_evidence
    from pipeline.agents.safety_contracts import grounded_message
    try:
        facts = _assertion_evidence(instance, context)
    except ValueError:
        facts = [{'source_fact': 'ASSERTION_SYNTAX_UNAVAILABLE'}]
    targets = [dict(assertion_identity=f.get('assertion_identity'),
                    api=f.get('assertion_api'), ast_path=f.get('ast_path'),
                    parent_assertion_id=f.get('parent_assertion_id'),
                    nesting_depth=f.get('nesting_depth'), matched_code=f.get('matched_code'),
                    message=grounded_message({'code': f['matched_code']}))
               for f in facts if f.get('conclusion') == 'MISSING_MESSAGE']
    return {'allowed_changes': ['ADD_ASSERTION_MESSAGE'], 'targets': targets,
            'source_facts': facts, 'executable_enforcement': True}


AR_RULE = ('Add meaningful diagnostic messages only to assertions lacking messages. '
           'Preserve assertion APIs, all non-message arguments, evaluation order, '
           'assertion count, nesting, control flow and existing messages. '
           'New messages must be ordinary string literals in the final JUnit 5 argument position.')



# Generation-only projection; the production builder and evaluator remain authoritative.
CONTRACT_BEGIN = '<BEGIN_AR_EXECUTABLE_CONTRACT_V2>\n'
CONTRACT_END = '\n<END_AR_EXECUTABLE_CONTRACT_V2>\n'
CONSTRAINT_FIELDS = frozenset({'target', 'allowed_edits', 'protected_elements',
                              'forbidden_transformations', 'validation_predicates'})


def shared_descriptive_context(instance, context):
    """Source-only knowledge, built before either variant's contract branch."""
    import copy
    import hashlib
    from tree_sitter import Language, Parser
    import tree_sitter_java
    source = instance['test_code'].encode('utf-8')
    tree = Parser(Language(tree_sitter_java.language())).parse(source)
    invocations = []
    def visit(node, ast_path):
        if node.type == 'method_invocation':
            name = node.child_by_field_name('name')
            receiver = node.child_by_field_name('object')
            arguments = node.child_by_field_name('arguments')
            text = lambda item: source[item.start_byte:item.end_byte].decode('utf-8') if item else None
            invocations.append({'name': text(name), 'receiver': text(receiver),
                'arguments': [text(item) for item in arguments.named_children] if arguments else [],
                'byte_range': [node.start_byte, node.end_byte], 'ast_path': ast_path})
        for index, child in enumerate(node.named_children):
            visit(child, ast_path + '/' + child.type + ':' + str(index))
    visit(tree.root_node, 'program:0')
    # All calls, including non-assertions, are included. No API support lookup,
    # message classification, approval, target selection or grounded message.
    return {
        'instance': copy.deepcopy(instance),
        'test_class_context': {
            'imports': re.findall(r'(?m)^\s*import\s+[^;]+;', context.get('test_class_source', '')),
            'context_json': context.get('context_json'),
            'source_owner_binding': copy.deepcopy(context.get('source_owner_binding') or {}),
            'source_sha256': hashlib.sha256(context.get('test_class_source', '').encode()).hexdigest()},
        'static_evidence': {
            'method_sha256': hashlib.sha256(source).hexdigest(),
            'ast_parse_status': 'PARSED' if not tree.root_node.has_error else 'PARSE_ERROR',
            'method_invocations': invocations}}


def executable_constraint_projection(contract):
    """Full-only render adapter for the current formal AR v4.1 contract."""
    import copy
    from pipeline.agents.safety_contracts import get_proof
    if (contract.get('refactoring_contract') != {'contract_id': 'ar.grounded_message', 'version': '4.1'}
            or contract.get('risk_decision') != 'REFACTOR_LOW_RISK'):
        raise ValueError('FORMAL_AR_V41_CONTRACT_REQUIRED')
    proof = get_proof(contract['safety_evidence'])
    if not proof or not proof.get('targets'):
        raise ValueError('FORMAL_AR_TARGETS_REQUIRED')
    # No safety_instance, source_record, production_context or assessment envelope.
    # Selectors and exact replacement messages are executable constraints.
    target = {
        'source_binding': copy.deepcopy(proof['binding']),
        'assertions': [{k: copy.deepcopy(t[k]) for k in (
            'assertion_id', 'ast_path', 'start', 'end', 'parent_assertion_id', 'message')}
            for t in proof['targets']]}
    return {
        'target': target,
        'allowed_edits': copy.deepcopy(contract['allowed_changes']),
        'protected_elements': [
            'Assertion APIs/receivers, count, order, AST identity and parent nesting.',
            'All original non-message arguments and existing messages.',
            'Non-target calls, non-assertion code, control flow and evaluation order.',
            'Method identity/annotations and all code outside the target method.'],
        'forbidden_transformations': {
            'formal_forbidden_changes': copy.deepcopy(contract['forbidden_changes']),
            'message_forms': ['supplier', 'call', 'concatenation', 'state_read', 'invented_claim'],
            'scope': {k: contract[k] for k in (
                'target_scope', 'max_target_methods', 'production_code_changes_allowed',
                'helper_changes_allowed', 'class_level_changes_allowed', 'import_changes_allowed')}},
        # Check IDs/semantics from the unchanged production AR candidate validator.
        'validation_predicates': {
            'CURRENT_RISK_CONTRACT': 'Original source and source-bound proof must still agree.',
            'ASSERTION_AST_PARSE': 'Original and candidate must parse as Java.',
            'ASSERTION_COUNT_UNCHANGED': 'Preserve the original assertion count.',
            'ASSERTION_ORDER_UNCHANGED': 'Preserve assertion APIs and order.',
            'ASSERTION_AST_IDENTITY_UNCHANGED': 'Preserve AST paths and control-flow identity.',
            'ASSERTION_PARENT_RELATION_UNCHANGED': 'Preserve parent/child assertion nesting.',
            'NON_ASSERTION_CODE_UNCHANGED': 'Preserve semantic tokens outside assertions.',
            'ONLY_TARGET_MESSAGES_ADDED': 'Only selected assertions gain one final plain string literal; preserve receiver and non-message semantic arguments.',
            'TARGET_ASSERTION_MESSAGE_MISSING': 'Every selected assertion must receive its message.',
            'NESTED_ASSERTION_NOT_REFACTORED': 'Every selected nested assertion must receive its message.',
            'MESSAGES_MEANINGFUL': 'Each decoded new message must equal the corresponding target.message, which quotes the original assertion source.'}}


def strip_executable_contract(prompt):
    """Remove exactly the final delimited contract; retain every shared byte."""
    if not prompt.endswith(CONTRACT_END) or prompt.count(CONTRACT_BEGIN) != 1:
        raise ValueError('ONE_FINAL_EXECUTABLE_BLOCK_REQUIRED')
    position = prompt.index(CONTRACT_BEGIN)
    value = json.loads(prompt[position + len(CONTRACT_BEGIN):-len(CONTRACT_END)])
    if set(value) != CONSTRAINT_FIELDS:
        raise ValueError('EXECUTABLE_BLOCK_HAS_NON_CONSTRAINT_FIELDS')
    return prompt[:position]


def prompt_parts(instance, context, facts, *, executable):
    prefix = ('You are performing one conservative Java test-method refactoring.\n'
              'Target smell: Assertion_Roulette\n'
              'Scope: one current target test method, with no imports or helpers.\n'
              'Return exactly one complete method, preserving identity and annotations. '
              'No class declarations, Markdown or explanation. '
              'If unsafe, return exactly REFUSE_UNSAFE_REFACTORING.\n'
              'Common AR invariants: ' + AR_RULE + '\n')
    shared = shared_descriptive_context(instance, context)
    suffix = ('Original target method:\n' + instance['test_code']
              + '\nShared descriptive instance/context/static evidence (facts, not permissions):\n'
              + json.dumps(shared, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n')
    # A2 has no additional guidance: AR_RULE occurs only in the common prefix.
    guidance = (CONTRACT_BEGIN + json.dumps(facts, sort_keys=True, ensure_ascii=False,
                                           separators=(',', ':')) + CONTRACT_END
                if executable else '')
    # Put the entire differential block at the end for exact, non-normalizing removal.
    return {'prefix': prefix, 'suffix': suffix, 'guidance': guidance}
