"""Isolated, instance-unit ablations. Historical artifacts are read-only inputs.

Generation authorization is NOT a safety proof. All generated responses enter
the same CandidateValidator / FileGateExecutor / evidence-linked evaluator.
No Full response cache is used for A1/A2. Only A3 reuses historical evidence.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
import copy
import hashlib
import json
import subprocess

from pipeline.ar_ablation_inputs import AR_RULE
from pipeline.resume_store import (atomic_json, checkpoint, read_checkpoint,
                                   read_json, file_hash, digest, exclusive_lock)

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'analysis/ablation_readiness_audit_20260927'
VERSION = 'ablation-instance-runtime-5-api-failure-finalization'
PAIRED_PROFILE = 'ar-formal-contract-pair-2'
SMELLS = ('Assertion_Roulette', 'Redundant_Assertion', 'Exception_Catching_Throwing')
VARIANTS = {
    'A1': ('core', 'category', 'executable'),
    'A2': ('admitted', 'frozen_full', 'natural_language'),
    'A2_ECT': ('supplement', 'frozen_full', 'natural_language'),
    'A3': ('core', 'offline', 'none'),
    'FULL_CONTROL': ('core', 'frozen_full', 'executable'),
    'FULL_ECT_CONTROL': ('supplement', 'frozen_full', 'executable'),
}
RULES = {
    'Assertion_Roulette': AR_RULE,
    'Redundant_Assertion': 'Remove redundant assertions only when doing so preserves the test oracle and all behavior, side effects and exception observations. Preserve meaningful assertions and setup.',
    'Exception_Catching_Throwing': 'Express expected exceptions with assertThrows and no-exception expectations with assertDoesNotThrow where appropriate. Preserve exception type, executed operations, setup, verification and exception observations.',
}


def path(value):
    return (ROOT / value).resolve()


def category_authorization(instance):
    return {'kind': 'CATEGORY_GENERATION_AUTHORIZATION',
            'authorized': instance.get('smell_type') in SMELLS,
            'smell_type': instance.get('smell_type'),
            'instance_id': instance['instance_id'],
            'is_safety_proof': False, 'version': 'category-admission-1'}


def natural_language_input(request):
    instance = request['input']['instance']
    smell = instance['smell_type']
    if smell not in RULES:
        raise ValueError('UNSUPPORTED_ABLATION_CATEGORY')
    return {'kind': 'NATURAL_LANGUAGE_GENERATION', 'version': 'category-rule-prompt-1',
            'instance_id': instance['instance_id'], 'smell': smell, 'rule': RULES[smell],
            'executable_enforcement': False}


def natural_language_prompt(request):
    if request['input']['instance']['smell_type'] == 'Assertion_Roulette':
        from .ar_ablation_inputs import prompt_parts
        instance, context = request['input']['instance'], request['input']['context']
        parts = prompt_parts(instance, context, None, executable=False)
        return ''.join(parts.values())
    spec = natural_language_input(request)
    instance = request['input']['instance']
    # Use precisely the same context projection as Full; never include proof,
    # historical admission decisions, exact target replacements or RAG examples.
    import re
    context = request['input']['context']
    imports = re.findall(r'(?m)^\s*import\s+[^;]+;', context.get('test_class_source', ''))
    production = instance.get('production_context') or {}
    summary = {k: production.get(k) for k in (
        'available', 'productionClassName', 'focalMethodSignatures',
        'focalMethodSummaries', 'dependencyMethodSignatures',
        'dependencyMethodSummaries', 'astMethodCalls', 'astControlFlow')
        if production.get(k) not in (None, [], '')}
    return ('Conservative Java test-method refactoring.\nTarget smell: ' + spec['smell']
            + '\nRule: ' + spec['rule']
            + '\nReturn exactly one complete target test method. Preserve method identity, annotations, '
              'unrelated behavior and code outside the target method. No imports, helpers, class declarations, '
              'Markdown or explanation. If unsafe, return REFUSE_UNSAFE_REFACTORING.\n'
            + 'Available test-class imports: ' + json.dumps(imports, ensure_ascii=False)
            + '\nOriginal target method:\n' + instance['test_code']
            + '\nRelevant production context summary:\n' + json.dumps(summary, ensure_ascii=False))


def config_file(variant, config_path=None):
    if config_path is not None:
        return Path(config_path)
    ar_config = ROOT / 'config/ablation_variants/ar_rq2' / f'{variant}.json'
    return ar_config if ar_config.is_file() else ROOT / 'config/ablation_variants' / f'{variant}.json'


def sample_manifest(variant, config_path=None):
    config = config_file(variant, config_path)
    return read_json(config).get('sample_manifest_ref') if config.is_file() else None


def load_rows(variant, audit=AUDIT, manifest_path=None):
    if variant in {'A1', 'A2', 'A3', 'FULL_CONTROL'}:
        manifest_path = manifest_path or sample_manifest(variant)
        if not manifest_path:
            raise ValueError('FROZEN_AR_MANIFEST_REQUIRED')
    if manifest_path:
        from .ar_ablation_inputs import load_manifest
        return load_manifest(ROOT, manifest_path, variant)
    scope = VARIANTS[variant][0]
    rows = read_json(audit / 'instance_crosswalk.json')
    if scope == 'admitted':
        rows = [r for r in rows if r['admission'] == 'ADMITTED']
    elif scope == 'supplement':
        rows = read_json(audit / 'proposed_ect_supplement.json')
        rows = copy.deepcopy(rows)
        for row in rows:
            base = ROOT / 'data' / row['run'] / row['project'] / 'instances' / row['instance_id'].replace(':', '_')
            instance = read_json(base / 'input/instance.json')
            row.update(input_ref=str(base / 'input/instance.json'), admission='ADMITTED',
                       relative_path=instance['test_relative_path'],
                       project_revision=instance['project_version'],
                       source_sha256=instance['source_owner_binding']['source_file_sha256'],
                       method_sha256=instance['method_source_hash'])
    if len({r['instance_id'] for r in rows}) != len(rows):
        raise ValueError('FROZEN_SAMPLE_CARDINALITY_MISMATCH')
    return rows


def ar_evaluation_scaffold(prepared):
    """Source-bound common scoring contract; called only after generation for A2."""
    from pipeline.hashing import sha256_utf8_exact as sha256_text, sha256_method_text
    from pipeline.agents.safety_contracts import current_rule_version
    from pipeline.agents.risk_policy import COMMON_FORBIDDEN_CHANGES
    instance = prepared['instance']
    run_id = prepared['request']['run_id']
    source = Path(instance['test_file_path'])
    original = prepared['original']
    start, end = instance['line_range']['start'], instance['line_range']['end']
    current = ''.join(original.splitlines(keepends=True)[start-1:end])
    contract = dict(run_id=run_id, instance_id=instance['instance_id'], smell=instance['smell_type'],
        source_file=str(source), source_hash=sha256_text(original),
        method_signature=instance['method_signature'], method_start_line=start, method_end_line=end,
        original_method_hash=instance['method_source_hash'], source_baseline_hash=sha256_method_text(current),
        risk_decision='GENERATION_AUTHORIZED', risk_level='NOT_REASSESSED',
        risk_rule_version=current_rule_version(instance['smell_type']),
        allowed_changes=['ADD_ASSERTION_MESSAGE'], forbidden_changes=sorted(COMMON_FORBIDDEN_CHANGES),
        required_context=[], missing_context=[], target_scope='SINGLE_TEST_METHOD', max_target_methods=1,
        production_code_changes_allowed=False, helper_changes_allowed=False,
        class_level_changes_allowed=False, import_changes_allowed=False)
    return contract


def formal_ar_generation_contract(request):
    """Use the production builder with fresh, source-bound rule-only evidence."""
    from pipeline.agents.risk_agent import RiskAssessmentAgent
    from pipeline.contracts import make_agent_request
    from pipeline.contract_builders import ExecutableContractBuilder
    payload = copy.deepcopy(request['input'])
    # A stable contract run label prevents run namespace from changing model input.
    run_id = 'ar-paired-contract-v1'
    risk_request = make_agent_request(run_id=run_id, instance_id=request['instance_id'],
        agent='risk_assessment', agent_version='3.0',
        input_payload={**payload, 'delete_test_method': False})
    from pipeline.ablation_config import load_ablation_config
    risk = RiskAssessmentAgent(enabled=True, mode='rule_only',
        strategy_selection=load_ablation_config(profile='FULL')).execute(risk_request)
    contract_request = make_agent_request(run_id=run_id, instance_id=request['instance_id'],
        agent='refactoring', agent_version='3.0',
        input_payload={**payload, 'pipeline_state': 'RISK_APPROVED', 'risk_result': risk})
    return ExecutableContractBuilder().build(contract_request).to_versioned_dict()


def prepare(row, variant, run_id='ablation-plan', generation_profile=None):
    """Prepare bound input; A2 never consults executable constraints or a model."""
    from pipeline.instances.legacy_adapter import LegacyInstanceAdapter
    from pipeline.hashing import sha256_utf8_exact as sha256_text
    def normalized_source(value):
        return value.replace("\r\n", "\n").replace("\r", "\n").lstrip().rstrip()
    from pipeline.hashing import sha256_method_text
    from .ar_ablation_inputs import verify_input
    verify_input(ROOT, row)
    raw_instance = read_json(path(row['input_ref']))
    instance = LegacyInstanceAdapter().adapt(raw_instance)
    source = path(instance['test_file_path'])
    expected_source = ROOT / 'datasets' / row['project'] / row['relative_path']
    if source != expected_source.resolve() or file_hash(source) != row['source_sha256']:
        raise ValueError('FROZEN_SOURCE_CHANGED:' + row['instance_id'])
    if (instance['instance_id'] != row['instance_id'] or instance['smell_type'] != row['smell']
            or instance['project_version'] != row['project_revision']
            or instance['method_source_hash'] != row['method_sha256']
            or sha256_text(instance['test_code']) != row['method_sha256']):
        raise ValueError('FROZEN_INSTANCE_CHANGED')
    original = source.read_text(encoding='utf-8')
    start, end = instance['line_range']['start'], instance['line_range']['end']
    current = ''.join(original.splitlines(keepends=True)[start-1:end])
    if start < 1 or end < start or normalized_source(current) != normalized_source(instance['test_code']):
        raise ValueError('SOURCE_METHOD_RANGE_CHANGED')
    source_meta = read_json(path(row['input_ref']).parent / 'source.json')
    context_path = path(source_meta['context_json'])
    context = {'test_class_source': original, 'context_json': str(context_path),
               'source_owner_binding': instance.get('source_owner_binding') or {}}
    authorization = (category_authorization(instance) if variant == 'A1' else
        {'kind': 'FROZEN_FULL_ADMISSION', 'authorized': row['admission'] == 'ADMITTED',
         'is_safety_proof': False, 'instance_id': instance['instance_id']})
    if instance['smell_type'] == 'Assertion_Roulette':
        from .ar_ablation_inputs import prompt_parts
        request = {'run_id': run_id, 'instance_id': instance['instance_id'],
                   'input': {'instance': instance, 'context': context,
                             'target_smells': [instance['smell_type']]}}
        prepared = dict(instance=instance, request=request, authorization=authorization,
                        context_path=str(context_path), context_sha256=file_hash(context_path),
                        original=original, variant_id=variant, deferred_ar_evaluation=True)
        if variant == 'A2':
            # No executable evidence, facts, policy constants or scaffold are read here.
            facts = None
        elif generation_profile == PAIRED_PROFILE and variant == 'FULL_CONTROL':
            formal = formal_ar_generation_contract(request)
            if formal['safety_instance'] != instance:
                raise ValueError('FORMAL_SHARED_INSTANCE_MISMATCH')
            from .ar_ablation_inputs import executable_constraint_projection
            facts = executable_constraint_projection(formal)
            prepared['formal_contract'] = formal
            prepared['generation_contract'] = facts
        else:
            from .ar_ablation_inputs import generation_facts
            facts = generation_facts(instance, context)
            prepared['contract'] = ar_evaluation_scaffold(prepared)
            prepared['generation_facts'] = facts
        parts = prompt_parts(instance, context, facts, executable=variant != 'A2')
        prepared.update(prompt=''.join(parts.values()), prompt_parts=parts)
        return prepared
    from pipeline.agents.risk_evidence import extract_rule_evidence
    from pipeline.agents.safety_contracts import get_proof, current_rule_version
    from pipeline.agents.risk_policy import COMMON_FORBIDDEN_CHANGES
    from pipeline.contract_registry import contract_for
    # Legacy extractor includes proof decisions. Preserve them verbatim, but DO
    # NOT use them as A1 admission or replace an invalid proof with an approval.
    evidence = extract_rule_evidence(instance, context)
    proof = get_proof(evidence) or {}
    risk = {'evidence': evidence}
    contract = dict(run_id=run_id, instance_id=instance['instance_id'], smell=instance['smell_type'],
        source_file=str(source), source_hash=sha256_text(original),
        method_signature=instance['method_signature'], method_start_line=start, method_end_line=end,
        original_method_hash=instance['method_source_hash'], source_baseline_hash=sha256_method_text(current),
        risk_decision='GENERATION_AUTHORIZED',
        risk_level='NOT_REASSESSED', risk_rule_version=current_rule_version(instance['smell_type']),
        safety_evidence=evidence, safety_instance=instance,
        risk_evidence_ids=[e['evidence_id'] for e in evidence],
        allowed_changes=list(proof.get('allowed_changes') or []),
        forbidden_changes=sorted(COMMON_FORBIDDEN_CHANGES), required_context=[], missing_context=[],
        target_scope='SINGLE_TEST_METHOD', max_target_methods=1, production_code_changes_allowed=False,
        helper_changes_allowed=False, class_level_changes_allowed=False, import_changes_allowed=False)
    if instance['smell_type'] == 'Redundant_Assertion':
        contract.update(ra_contract_version=proof.get('binding', {}).get('ra_contract_version'),
                        ra_prompt_version=proof.get('ra_prompt_version'))
    if instance['smell_type'] == 'Exception_Catching_Throwing':
        contract.update({k: proof.get(k) for k in ('ect_contract_version', 'ect_path', 'ect_path_contract_version')})
        if (proof.get('ect_path') == 'NO_THROW_ASSERT_DOES_NOT_THROW'
                and 'REMOVE_APPROVED_THROWS_CLAUSE' in contract['allowed_changes']):
            contract['forbidden_changes'].remove('CHANGE_METHOD_SIGNATURE')
            contract['forbidden_changes'].append('CHANGE_METHOD_NAME_RETURN_PARAMETERS_ANNOTATIONS_OR_OTHER_MODIFIERS')
            contract['forbidden_changes'].sort()
    # Non-admitted ECT has no registered executable path. Preserve that fact,
    # do not invent targets or promote its proof. It is still an A1 attempt;
    # the unchanged evaluator decides whether a response satisfies a contract.
    metadata = (None if instance['smell_type']=='Exception_Catching_Throwing' and not proof.get('ect_path')
                else contract_for(instance['smell_type'], proof))
    if metadata:
        contract['refactoring_contract'] = metadata
    request = {'run_id': run_id, 'instance_id': instance['instance_id'],
               'input': {'instance': instance, 'context': context, 'risk_result': {'result': risk},
                         'target_smells': [instance['smell_type']]}}
    if VARIANTS[variant][2] == 'natural_language':
        prompt = natural_language_prompt(request)
    else:
        from pipeline.agents.prompt_service import RefactoringPromptService
        prompt, _ = RefactoringPromptService().build(request, contract, context, [])
    return dict(instance=instance, request=request, contract=contract, prompt=prompt,
                authorization=authorization, context_path=str(context_path),
                context_sha256=file_hash(context_path), original=original, variant_id=variant)


def runtime_identity(rows, audit=AUDIT, manifest_path=None):
    from pipeline.model_config import resolve_role
    model = replace(resolve_role('refactoring'), max_retries=0).identity()
    revisions = {}
    project_inputs = {}
    from pipeline.verification.engineering.workspace import project_input_sha256
    for project in sorted({r['project'] for r in rows}):
        revision = subprocess.check_output(['git', '-C', str(ROOT/'datasets'/project), 'rev-parse', 'HEAD'], text=True).strip()
        expected = {r['project_revision'] for r in rows if r['project'] == project}
        if expected != {revision}:
            raise ValueError('SOURCE_REVISION_CHANGED:' + project)
        revisions[project] = revision
        project_inputs[project] = project_input_sha256(ROOT/'datasets'/project, ROOT/'data/__ablation_candidate__')
    files = list((ROOT/'pipeline').rglob('*.py')) + list((ROOT/'refactoring/contracts').rglob('*.yaml'))
    files += list((ROOT/'pipeline').rglob('*.json'))
    files += list((ROOT/'refactoring').rglob('*.py'))
    files += list((ROOT/'src/main/java').rglob('*.java'))
    files += list((ROOT/'config').glob('*.yaml')) + [ROOT/'run_ablation.py', ROOT/'scripts/detect_only.ps1', ROOT/'detection/junit5/TestSmellDetector.jar']
    # Secret-bearing config content is never persisted, only its digest.
    frozen = ([path(manifest_path)] if manifest_path else [audit/'instance_crosswalk.json',
              audit/'proposed_ect_supplement.json',
              ROOT/'analysis/controlled_baseline_frozen_input_20260920/baseline_sample_manifest.json'])
    return dict(runtime=VERSION, model=model, rag=False, retry=False, repair=False,
        cache=False, cross_variant_reuse=False, generation_unit='CURRENT_TARGET_INSTANCE',
        revisions=revisions, project_inputs=project_inputs,
        framework_sha256=digest({str(p.relative_to(ROOT)):file_hash(p) for p in sorted(files) if p.is_file() and 'tests' not in p.parts}),
        frozen_inputs={p.name:file_hash(p) for p in frozen})


def plan(variant, audit=AUDIT, config_path=None):
    manifest_path = sample_manifest(variant, config_path)
    rows = load_rows(variant, audit, manifest_path=manifest_path)
    identity = runtime_identity(rows, audit, manifest_path=manifest_path)
    config = read_json(config_file(variant, config_path))
    profile = config.get('generation_profile')
    if profile not in (None, PAIRED_PROFILE):
        raise ValueError('UNKNOWN_GENERATION_PROFILE')
    calls = 0
    prompt_bytes = 0
    bound = []
    for row in rows:
        if variant == 'A3':
            from .ar_ablation_inputs import verify_input
            verify_input(ROOT, row)
            bound.append({'instance_id': row['instance_id'], 'row_sha256': digest(row)})
            if row.get('file_gate_ref') and row.get('file_candidate_artifact'):
                verify_a3_artifacts(row)  # Integrity only; no pseudo decisions.
            continue
        prepared = prepare(row, variant, generation_profile=profile)
        if prepared['authorization']['authorized']:
            calls += 1
            prompt_bytes += len(prepared['prompt'].encode('utf-8'))
        bound.append({'instance_id':row['instance_id'], 'row_sha256':digest(row), 'context_sha256':prepared['context_sha256'],
                      'input_sha256':file_hash(path(row['input_ref'])), 'contract_sha256':digest(prepared.get('generation_contract', prepared.get('contract'))),
                      'prompt_sha256':hashlib.sha256(prepared['prompt'].encode()).hexdigest()})
    evidence = sum(r.get('a3_evidence_status') == 'EVIDENCE_FIELDS_AVAILABLE' for r in rows) if variant == 'A3' else 0
    return {'variant_id':variant, 'sample_size':len(rows), 'planned_model_calls':calls,
            'planned_rag_calls':0, 'project_distribution':dict(Counter(r['project'] for r in rows)),
            'smell_distribution':dict(Counter(r['smell'] for r in rows)),
            'reused_evidence':evidence, 'reused_candidate_references':evidence, 'reused_generation_candidates':0,
            'not_evaluable_or_not_reached':len(rows)-evidence if variant=='A3' else None,
            'estimated_cost':{'currency_amount':0 if not calls else None,
                'reason':'OFFLINE' if not calls else 'Provider prices and actual token usage are not frozen; no invented currency estimate.',
                'prompt_utf8_bytes':prompt_bytes, 'maximum_output_tokens':calls * identity['model']['max_tokens'],
                'formula':'input_tokens * input_price_per_million / 1e6 + output_tokens * output_price_per_million / 1e6'},
            'identity':identity, 'generation_profile':profile, 'bindings_sha256':digest(bound), 'dry_run':True, 'actual_model_calls':0}


def compile_test_decision(gate, instance):
    from pipeline.verification.engineering.test_reports import execution_confirmed
    stages = {s['stage']:s.get('status') for s in gate.get('stage_results', [])}
    if not instance.get('instance_id') or instance['instance_id'] not in (gate.get('instance_ids') or []):
        return 'NOT_EVALUABLE', 'INSTANCE_NOT_IN_GATE'
    tests = (gate.get('test_summary') or {}).get('methods', {}).get(instance['instance_id'])
    if any(stages.get(k) in {'FAIL', 'FAILED', 'COMPILE_FAILED', 'TARGET_TEST_FAILED', 'TEST_FAILED'} for k in ('COMPILE','TARGET_TEST')):
        return 'PSEUDO_REJECT', 'COMPILE_OR_TARGET_TEST_FAILED'
    if not tests or any(stages.get(k) != 'PASS' for k in ('COMPILE','TARGET_TEST')):
        return 'NOT_EVALUABLE', 'COMPILE_OR_EXACT_TEST_EVIDENCE_MISSING'
    target_class = '.'.join(filter(None, (instance.get('test_package_name'), Path(instance['test_filename']).stem)))
    target_method = instance['method_signature'].split('(')[0].split()[-1]
    if tests.get('target_failures',0) or tests.get('target_errors',0):
        return 'PSEUDO_REJECT', 'EXACT_TARGET_FAILED'
    if tests.get('target_skipped',0):
        return 'NOT_EVALUABLE', 'EXACT_TARGET_SKIPPED'
    if not execution_confirmed(tests, target_class=target_class, target_method=target_method):
        return 'NOT_EVALUABLE', 'EXACT_TARGET_EXECUTION_UNCONFIRMED'
    return 'PSEUDO_ACCEPT', 'COMPILE_PASS_AND_EXACT_TARGET_PASS'


class CompileTestOnlyAcceptancePolicy:
    """Separate ablation policy; the frozen Full evaluator stays byte-identical."""
    version = 'compile-test-only-acceptance-1'

    def evaluate(self, evidence, *, accept_on_pass=True):
        from pipeline.verification.acceptance.policy import AcceptanceDecision
        gate = {**dict(evidence.engineering), **dict(evidence.file_composition)}
        decision, reason = compile_test_decision(gate, evidence.instance)
        return AcceptanceDecision(decision, full=decision=='PSEUDO_ACCEPT' and accept_on_pass, status=reason)


def verify_a3_artifacts(row):
    from .ar_ablation_inputs import verify_input
    verify_input(ROOT, row)
    gate_path = path(row['file_gate_ref'])
    if file_hash(gate_path) != row['file_gate_sha256']:
        raise ValueError('FULL_EVIDENCE_CHANGED')
    gate = read_json(gate_path)
    expected = gate.get('candidate_file_sha256') or gate.get('candidate_hash')
    if (row.get('file_candidate_sha256', expected) != expected
            or file_hash(path(row['file_candidate_artifact'])) != expected):
        raise ValueError('FULL_CANDIDATE_HASH_MISMATCH')
    instance = read_json(path(row['input_ref']))
    if (instance['instance_id'] != row['instance_id'] or instance['smell_type'] != row['smell']
            or instance['method_source_hash'] != row['method_sha256']
            or instance['project_version'] != row['project_revision']):
        raise ValueError('FULL_INPUT_BINDING_MISMATCH')
    chain = gate.get('hash_chain') or {}
    if (row['instance_id'] not in gate.get('instance_ids', [])
            or gate.get('source_hash_before') != row['source_sha256']
            or any((chain.get(k) != expected if row.get('input_sha256') else chain.get(k) not in (None, expected)) for k in
                ('compiled_candidate_sha256','validation_input_sha256','injected_candidate_sha256','validated_candidate_sha256'))):
        raise ValueError('FULL_EVIDENCE_BINDING_MISMATCH')
    return gate, expected


def verify_config(variant, planned, config_path=None):
    config_path = config_file(variant, config_path)
    if not config_path.is_file():
        raise ValueError('FROZEN_VARIANT_CONFIG_REQUIRED')
    config = read_json(config_path)
    if (config.get('variant_id') != variant or config.get('identity') != planned['identity']
            or config.get('bindings_sha256') != planned['bindings_sha256']
            or config.get('sample_size') != planned['sample_size']
            or config.get('generation_profile') != planned.get('generation_profile')):
        raise ValueError('FROZEN_VARIANT_CONFIG_DRIFT')
    return config


def offline_a3(row):
    result = {'instance_id':row['instance_id'], 'project':row['project'], 'smell':row['smell'],
              'full_decision':row['full_state'], 'variant_id':'A3'}
    if not row.get('file_gate_ref') or not row.get('file_candidate_artifact'):
        decision, reason = 'NOT_EVALUABLE', 'FULL_DID_NOT_REACH_BOUND_FILE_EVIDENCE'
    else:
        gate_path = path(row['file_gate_ref'])
        gate, expected = verify_a3_artifacts(row)
        decision, reason = compile_test_decision(gate, read_json(path(row['input_ref'])))
        result.update(evidence_ref=str(gate_path), evidence_sha256=file_hash(gate_path), candidate_sha256=expected)
        result['full_evidence_reasons'] = {
            'file_gate_status':gate.get('status'),
            'instance_effect':next((x for x in gate.get('instance_revalidation_results',[])
                                    if x.get('instance_id')==row['instance_id']),None)}
    result.update(pseudo_decision=decision, reason=reason,
        decision_differs=None if decision=='NOT_EVALUABLE' else (decision=='PSEUDO_ACCEPT') != (row['full_state']=='ACCEPTED'))
    if result['decision_differs']:
        result['difference_reason'] = 'COMPILE_TEST_ONLY_OMITS_FULL_STATIC_SMELL_AND_EVIDENCE_LINKED_ACCEPTANCE'
    return result


def evaluate_generated(prepared, raw, output, project, run_id, gate_executor=None):
    """Common evaluator for FULL_CONTROL, A1 and A2; no weakened checks."""
    from pipeline.verification.candidate.validation import CandidateValidator
    from pipeline.file_candidate import assemble_method_candidates
    from pipeline.hashing import sha256_utf8_lf
    from pipeline.verification.file_gate import FileGateExecutor
    from pipeline.verification.evidence import ValidationEvidenceBundle
    from pipeline.verification.acceptance.policy import EvidenceLinkedAcceptancePolicy
    instance = prepared['instance']
    c = (ar_evaluation_scaffold(prepared) if prepared.get('deferred_ar_evaluation')
         else copy.deepcopy(prepared['contract']))
    request = copy.deepcopy(prepared['request'])
    # Recreate current scoring evidence after generation, for every variant.
    # Never read a saved Full assessment or relax the shared validator.
    if prepared.get('deferred_ar_evaluation'):
        from .agents.risk_evidence import extract_rule_evidence
        from .agents.safety_contracts import get_proof
        from .contract_registry import contract_for
        evidence = extract_rule_evidence(instance, request['input']['context'])
        proof = get_proof(evidence) or {}
        request['input']['risk_result'] = {'result': {'evidence': evidence}}
        c.update(safety_evidence=evidence, safety_instance=instance,
                 risk_evidence_ids=[e['evidence_id'] for e in evidence],
                 allowed_changes=list(proof.get('allowed_changes') or []),
                 refactoring_contract=contract_for(instance['smell_type'], proof))
    variant = prepared.get('variant_id', 'UNSPECIFIED')
    atomic_json(output/'evaluation_input.json', {'variant_id': variant, 'risk_contract': c})
    validation = CandidateValidator().validate(raw_response=raw, request=request,
        contract=c, original_file=prepared['original'])
    atomic_json(output/'static_evaluation.json', dict(validation, variant_id=variant))
    if not validation['passed']:
        return {'decision':'REFACTORING_REJECTED', 'static_passed':False, 'validated_files':0,
                'failure_checks':[x['check_id'] for x in validation['checks'] if x['status']=='FAIL']}
    method = validation['candidate_method']
    item = dict(instance_id=instance['instance_id'], source_file=c['source_file'], source_hash=c['source_hash'],
        start_line=c['method_start_line'], end_line=c['method_end_line'], original_method=instance['test_code'],
        candidate_method=method, candidate_method_sha256_lf=sha256_utf8_lf(method),
        method_signature=instance['method_signature'], smell_type=instance['smell_type'])
    bundle = assemble_method_candidates(Path(c['source_file']), [item], project_id=project,
        module_path=instance.get('module_path') or '', source_relative_path=instance['test_relative_path'])
    candidate = output/'candidate.java'
    candidate.write_text(bundle.candidate_source, encoding='utf-8')
    atomic_json(output/'candidate_meta.json', {'variant_id':variant,
        'instance_id':instance['instance_id'], 'candidate_sha256':file_hash(candidate),
        'source_sha256':c['source_hash']})
    bundle = replace(bundle, candidate_file_sha256=file_hash(candidate))
    executor = gate_executor or FileGateExecutor(repo_root=ROOT)
    # Operational owner mapping follows a statically valid candidate. It is
    # separate from the immutable input and never fabricates a Risk decision.
    from pipeline.experiment_infrastructure import bind_source_owner
    binding = bind_source_owner(instance, frozen_source_root=ROOT/'datasets'/project,
                                previous_binding=instance.get('source_owner_binding'))
    mapping = {'status':'MAPPED' if binding['status']=='BOUND' else 'BLOCKED',
               'reason_code':binding['reason_code'], 'test_class':binding.get('owner_class'),
               'test_method':binding.get('test_method'), 'source_file_sha256':binding.get('source_file_sha256')}
    gate = executor.execute(run_id=run_id, project_name=project, bundle=bundle,
        targets=[{**instance, 'risk_contract':c, 'contract_candidate':method,
                  'source_owner_binding':binding, 'execution_owner_mapping':mapping}],
        before_detection_ref=Path(prepared['context_path']), candidate_path=candidate)
    atomic_json(output/'file_gate.json', dict(gate, variant_id=variant))
    decision = EvidenceLinkedAcceptancePolicy().evaluate(ValidationEvidenceBundle.from_file_gate(instance, gate))
    return {'decision':'ACCEPTED' if decision.full else decision.decision, 'static_passed':True,
            'validated_files':1, 'acceptance':asdict(decision)}


class GenerationTransportFailure(RuntimeError):
    """A completed client attempt with no usable model response; never retry."""

    def __init__(self, kind, *, status_code=None):
        super().__init__(kind)
        self.kind = kind
        self.status_code = status_code


def _complete_instance(state_path, state, item_dir, iid, variant, result):
    result.update(variant_id=variant, instance_id=iid)
    result_path = item_dir/'result.json'
    atomic_json(result_path, result)
    state['instances'][iid] = {
        'status': 'DONE', 'variant_id': variant, 'result_ref': str(result_path),
        'result_sha256': file_hash(result_path)}
    checkpoint(state_path, state)


def production_generate(prompt, run_id):
    from openai import APITimeoutError, APIConnectionError, APIStatusError
    from pipeline.model_config import resolve_role, chat_client, safe_failure
    from pipeline.agents.refactoring_providers import _response_text
    from .resume_transport import ACTIVE
    if ACTIVE.get() is not None:
        raise ValueError('EXTERNAL_CACHE_REPLAY_FORBIDDEN_IN_ABLATION')
    config = replace(resolve_role('refactoring', require=True), max_retries=0)
    try:
        client = chat_client('refactoring', config=config, session_id=run_id)
        client.cache = False  # Also disable any process-global LangChain cache.
        return _response_text(client.invoke(prompt))
    except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
        kind = ('API_TIMEOUT' if isinstance(exc, APITimeoutError)
                else 'API_CONNECTION_ERROR' if isinstance(exc, APIConnectionError)
                else 'API_HTTP_ERROR')
        raise GenerationTransportFailure(
            kind, status_code=getattr(exc, 'status_code', None)) from None
    except Exception as exc:
        raise RuntimeError(safe_failure(exc)) from None


def run(variant, output, *, allow_model_calls=False, audit=AUDIT, generate=None, evaluator=None, config_path=None):
    """One bounded batch. Unknown in-flight calls block replay instead of retry."""
    if variant != 'A3' and not allow_model_calls:
        raise ValueError('REAL_MODEL_CALLS_DISABLED: use --dry-run for planning')
    output = Path(output).resolve()
    data_root = (ROOT/'data').resolve()
    if not output.is_relative_to(data_root) or output == data_root:
        raise ValueError('NEW_DATA_RUN_REQUIRED')
    # A run must be its own new top-level data directory; never nest in Full.
    if output.parent != data_root:
        raise ValueError('TOP_LEVEL_NEW_RUN_REQUIRED')
    if output.exists() and not (output/'ablation_manifest.json').is_file():
        raise ValueError('REFUSE_EXISTING_NON_ABLATION_DIRECTORY')
    rows = load_rows(variant, audit, manifest_path=sample_manifest(variant, config_path))
    planned = plan(variant, audit, config_path=config_path)
    verify_config(variant, planned, config_path)
    manifest = {'variant_id':variant, 'identity':planned['identity'], 'generation_profile':planned.get('generation_profile'),
                'cache_namespace':variant + '/' + output.name, 'bindings_sha256':planned['bindings_sha256'],
                'rows_sha256':digest(rows), 'limits':{'instances':225, 'model_calls':120, 'validation_files':30}}
    with exclusive_lock(output):
        manifest_path = output/'ablation_manifest.json'
        if manifest_path.exists():
            if read_json(manifest_path) != manifest:
                raise ValueError('ABLATION_RESUME_IDENTITY_CHANGED')
        else:
            atomic_json(manifest_path, manifest)
        state_path = output/'state.json'
        state = read_checkpoint(state_path) if state_path.exists() else {'instances':{}, 'batches':0}
        stats = dict(variant_id=variant, batch_id=state['batches']+1, completed_instances=0,
                     actual_model_calls=0, generation_api_failures=0, validated_files=0, stop_reason='QUEUE_EXHAUSTED')
        try:
            for row in rows:
                iid = row['instance_id']
                old = state['instances'].get(iid, {})
                if old.get('status')=='DONE':
                    expected_result=output/'instances'/hashlib.sha256(iid.encode()).hexdigest()/'result.json'
                    if Path(old['result_ref']).resolve() != expected_result.resolve():
                        raise ValueError('CROSS_RUN_RESULT_REFUSED')
                    if old.get('variant_id') != variant:
                        raise ValueError('CROSS_VARIANT_RESULT_REFUSED')
                    if file_hash(old['result_ref']) != old['result_sha256']:
                        raise ValueError('COMPLETED_RESULT_CHANGED')
                    continue
                if old.get('status')=='CALL_STARTED':
                    stats['stop_reason']='MODEL_OUTCOME_UNKNOWN'; break
                if stats['completed_instances']>=225:
                    stats['stop_reason']='MAX_INSTANCES'; break
                if stats['validated_files']>=30:
                    stats['stop_reason']='MAX_VALIDATION_FILES'; break
                item_dir=output/'instances'/hashlib.sha256(iid.encode()).hexdigest()
                item_dir.mkdir(parents=True, exist_ok=True)
                if variant=='A3':
                    result=offline_a3(row)
                else:
                    prepared=prepare(row,variant,output.name, generation_profile=planned.get('generation_profile'))
                    if not prepared['authorization']['authorized']:
                        result={'decision':'PRESERVE_FULL_NON_ADMISSION'}
                    else:
                        response_binding=digest({'variant_id':variant, 'instance_id':iid,
                                                 'row_sha256':digest(row), 'prompt_sha256':digest(prepared['prompt'])})
                        if old.get('status')=='RESPONSE_SAVED':
                            if Path(old['response_ref']).resolve() != (item_dir/'response.txt').resolve():
                                raise ValueError('CROSS_RUN_RESPONSE_REFUSED')
                            meta=read_json(item_dir/'response_meta.json')
                            if (meta.get('variant_id') != variant or meta.get('response_binding') != response_binding
                                    or meta.get('response_sha256') != old.get('response_sha256')):
                                raise ValueError('CROSS_VARIANT_RESPONSE_REFUSED')
                            if old.get('variant_id') != variant or old.get('response_binding') != response_binding:
                                raise ValueError('CROSS_VARIANT_RESPONSE_REFUSED')
                            if file_hash(old['response_ref']) != old['response_sha256']:
                                raise ValueError('SAVED_RESPONSE_CHANGED')
                            raw=Path(old['response_ref']).read_text(encoding='utf-8')
                        else:
                            if stats['actual_model_calls']>=120:
                                stats['stop_reason']='MAX_MODEL_CALLS'; break
                            atomic_json(item_dir/'generation_input.json', {
                                'variant_id':variant, 'cache_namespace':variant + '/' + output.name,
                                'authorization':prepared['authorization'],
                                'prompt':prepared['prompt'], 'generation_unit':'CURRENT_TARGET_INSTANCE',
                                'source_sha256':row['source_sha256'], 'instance_id':iid,
                                'response_binding':response_binding, 'rag':False, 'retry':False, 'repair':False})
                            state['instances'][iid]={'status':'CALL_STARTED','variant_id':variant,'response_binding':response_binding}
                            checkpoint(state_path,state)  # durable before any external operation
                            stats['actual_model_calls']+=1
                            try:
                                raw=(generate or production_generate)(prepared['prompt'],output.name)
                            except GenerationTransportFailure as exc:
                                # Finalize this one attempted instance, preserving an
                                # unknown server outcome. Never classify it as a
                                # candidate rejection or invoke its evaluator.
                                failure = {
                                    'variant_id': variant, 'instance_id': iid,
                                    'cache_namespace': variant + '/' + output.name,
                                    'response_binding': response_binding,
                                    'source_sha256': row['source_sha256'],
                                    'failure_domain': 'API_ENVIRONMENT',
                                    'failure_kind': exc.kind,
                                    'http_status': exc.status_code,
                                    'attempt_count': 1, 'retry': False,
                                    'usable_response_received': False,
                                    'server_outcome': 'UNKNOWN',
                                    'terminal_for_run': True}
                                failure_path = item_dir/'generation_failure.json'
                                atomic_json(failure_path, failure)
                                result = {
                                    'decision': 'GENERATION_API_FAILED',
                                    'generation_success': False,
                                    'candidate_available': False,
                                    'failure_domain': 'API_ENVIRONMENT',
                                    'failure_kind': exc.kind,
                                    'model_outcome_unknown': True,
                                    'generation_failure_ref': str(failure_path),
                                    'generation_failure_sha256': file_hash(failure_path),
                                    'static_passed': None, 'compilation': 'NOT_RUN',
                                    'exact_target_test': 'NOT_RUN',
                                    'post_refactoring_ar': 'NOT_RUN',
                                    'final_acceptance': None, 'validated_files': 0}
                                _complete_instance(state_path, state, item_dir, iid, variant, result)
                                stats['completed_instances'] += 1
                                stats['generation_api_failures'] += 1
                                print(json.dumps({'event': 'GENERATION_API_FAILED',
                                    'variant_id': variant, 'instance_id': iid,
                                    'failure_kind': exc.kind, 'retry': False}), flush=True)
                                continue
                            response_path=item_dir/'response.txt'
                            response_path.write_text(raw,encoding='utf-8')
                            state['instances'][iid]={'status':'RESPONSE_SAVED','variant_id':variant,'response_binding':response_binding,
                                 'response_ref':str(response_path),
                                'response_sha256':file_hash(response_path)}
                            atomic_json(item_dir/'response_meta.json', {'variant_id':variant,
                                'instance_id':iid,'response_binding':response_binding,
                                'response_sha256':file_hash(response_path)})
                            checkpoint(state_path,state)
                        # Neutral raw response always survives an evaluator rejection.
                        result=(evaluator or evaluate_generated)(prepared,raw,item_dir,row['project'],output.name)
                        stats['validated_files']+=result.get('validated_files',0)
                _complete_instance(state_path, state, item_dir, iid, variant, result)
                stats['completed_instances']+=1
        except BaseException:
            stats['stop_reason']='INTERRUPTED_OR_ERROR'
            raise
        finally:
            state['batches']+=1
            checkpoint(state_path,state)
            stats['remaining_instances']=sum(state['instances'].get(r['instance_id'],{}).get('status')!='DONE' for r in rows)
            atomic_json(output/'batches'/f"{state['batches']:04d}.json",stats)
        return stats
