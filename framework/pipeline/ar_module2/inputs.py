"""Shared source facts and disjoint module generation branches."""
from dataclasses import replace
from pathlib import Path
import json
from pipeline.resume_store import read_json, file_hash, digest
from pipeline.hashing import normalize_lf, sha256_utf8_exact
from pipeline.instances.legacy_adapter import LegacyInstanceAdapter

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT / 'analysis/ar_rq2_frozen/ar_240_manifest.json'
FULL = 'FULL_RISK_AWARE_CONSTRAINED'
ORIGINAL = 'ORIGINAL_UTREFACTOR_RULE_GUIDED'
VARIANTS = (FULL, ORIGINAL)
VERSION = 'ar-module2-original-strict-1'
INSTANCE_FIELDS = ('instance_id', 'project_version', 'module_path', 'test_relative_path',
                   'test_filename', 'test_package_name', 'method_signature', 'line_range',
                   'method_source_hash', 'test_code')
PRODUCTION_FIELDS = ('available', 'reason', 'productionClassPath', 'productionPackageName',
                     'productionClassName', 'productionImports', 'productionFields',
                     'focalMethodNames', 'focalMethodSignatures', 'focalMethodInputs',
                     'focalMethodOutputs', 'focalMethodThrownExceptions', 'focalMethodCodeSnippets',
                     'focalMethodSummaries', 'directCallNames', 'dependencyMethodSignatures',
                     'dependencyMethodInputs', 'dependencyMethodOutputs', 'dependencyMethodSummaries',
                     'astNodeTypeSummaries', 'astMethodCalls', 'astObjectCreations', 'astControlFlow',
                     'compactAstTrees', 'focalMethodRefs', 'methodLibraryPath')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def model_parameters():
    from pipeline.model_config import resolve_role
    config = replace(resolve_role('refactoring', require=True), max_retries=0)
    if (config.model, config.temperature, config.top_p, config.max_tokens) != ('deepseek-v4-pro', 0.0, 1.0, 32768):
        raise ValueError('AR_MODULE2_MODEL_PARAMETERS_REQUIRED')
    return config


def model_input(prompt):
    config = model_parameters()
    return {'model': config.model, 'temperature': config.temperature, 'top_p': config.top_p,
            'max_tokens': config.max_tokens, 'messages': [{'role': 'user', 'content': prompt}]}


def load_rows():
    # A1 is the existing source manifest mode that NEVER reads the assessment sidecar.
    from pipeline.ar_ablation_inputs import load_manifest
    rows = [r for r in load_manifest(ROOT, PARENT, 'A1') if r['project'] in ('cli', 'compress')]
    if len(rows) != 120 or any(sum(r['project'] == p for r in rows) != 60 for p in ('cli', 'compress')):
        raise ValueError('FROZEN_CLI_COMPRESS_120_REQUIRED')
    return rows


def shared(row):
    from pipeline.ar_ablation_inputs import verify_input
    from pipeline.ar_independent_outcome import range_execution_identity
    verify_input(ROOT, row)
    instance = LegacyInstanceAdapter().adapt(read_json(ROOT / row['input_ref']))
    source = (ROOT / 'datasets' / row['project'] / row['relative_path']).resolve()
    if (Path(instance['test_file_path']).resolve() != source or file_hash(source) != row['source_sha256']
            or instance['instance_id'] != row['instance_id'] or instance['project_version'] != row['project_revision']
            or instance['smell_type'] != 'Assertion_Roulette'
            or instance['method_signature'] != row['method_signature']
            or sha256_utf8_exact(instance['test_code']) != row['method_sha256']):
        raise ValueError('FROZEN_SOURCE_OR_INSTANCE_DRIFT')
    owner = normalize_lf(source.read_bytes().decode('utf-8'))
    identity = range_execution_identity(source, {**instance, 'source_byte_sha256': row['source_sha256']})
    # The stored URI is intact. Its model view is an explicit, policy-free whitelist.
    description = {'instance': {k: instance[k] for k in INSTANCE_FIELDS},
                   'test_class_source': owner, 'source_byte_sha256': row['source_sha256'],
                   'production_context': {k: instance.get('production_context', {})[k] for k in PRODUCTION_FIELDS
                                          if k in (instance.get('production_context') or {})}}
    context = {'test_class_source': owner, 'source_owner_binding': instance.get('source_owner_binding') or {},
               'context_json': str((ROOT / row['context_ref']).resolve())}
    request = {'run_id': 'ar-module2-contract-v1', 'instance_id': instance['instance_id'],
               'input': {'instance': instance, 'context': context, 'target_smells': ['Assertion_Roulette']}}
    return {'instance': instance, 'request': request, 'original': owner,
            'context_path': str((ROOT / row['context_ref']).resolve()), 'source_byte_sha256': row['source_sha256'],
            'shared_context': description, 'shared_context_text': canonical(description),
            'execution_identity': {'owner': identity[0], 'report_method': identity[1]}}


def prepare(row, variant, run_id=None):
    if variant not in VARIANTS:
        raise ValueError('MODULE2_VARIANT_REQUIRED')
    prepared = shared(row)
    prepared.update(variant_id=variant, prompt=None, model_input=None, generate=False)
    if variant == ORIGINAL:
        from .original import render
        prompt = render(prepared['instance']['test_code'], prepared['shared_context_text'])
        prepared.update(prompt=prompt, model_input=model_input(prompt), generate=True)
        return prepared
    # This branch alone executes the current applicability/evidence/contract chain.
    from pipeline.agents.risk_agent import RiskAssessmentAgent
    from pipeline.contracts import make_agent_request
    from pipeline.contract_builders import ExecutableContractBuilder
    from pipeline.ablation_config import load_ablation_config
    from pipeline.ar_ablation_inputs import AR_RULE, executable_constraint_projection, CONTRACT_BEGIN, CONTRACT_END
    payload = prepared['request']['input']
    risk = RiskAssessmentAgent(enabled=True, mode='rule_only',
                              strategy_selection=load_ablation_config(profile='FULL')).execute(
        make_agent_request(run_id='ar-module2-contract-v1', instance_id=row['instance_id'],
                           agent='risk_assessment', agent_version='3.0',
                           input_payload={**payload, 'delete_test_method': False}))
    prepared['risk_result'] = risk
    decision = risk['result']['decision']
    prepared['admission_decision'] = decision
    if decision != 'REFACTOR_LOW_RISK':
        return prepared
    contract = ExecutableContractBuilder().build(make_agent_request(
        run_id='ar-module2-contract-v1', instance_id=row['instance_id'], agent='refactoring', agent_version='3.0',
        input_payload={**payload, 'pipeline_state': 'RISK_APPROVED', 'risk_result': risk})).to_versioned_dict()
    constraints = executable_constraint_projection(contract)
    constraints['validation_predicates'].update({
        'OUTPUT_REINTEGRATION': 'Return only the complete source-bound target method; preserve imports, helpers and other methods.',
        'BEHAVIOR_PRESERVATION': 'Change only approved diagnostic arguments; preserve API/receiver, non-message arguments, evaluation and side effects.'})
    prompt = ('You are performing one conservative Java test-method refactoring.\n'
              'Target smell: Assertion_Roulette\n'
              'Scope: one current target test method, with no imports or helpers.\n'
              'Return exactly one complete method, preserving identity and annotations. No class declarations, Markdown or explanation. '
              'If unsafe, return exactly REFUSE_UNSAFE_REFACTORING.\nCommon AR invariants: ' + AR_RULE + '\n'
              'Original target method:\n' + prepared['instance']['test_code'] + '\n'
              'Shared descriptive source/context:\n' + prepared['shared_context_text'] + '\n'
              + CONTRACT_BEGIN + canonical(constraints) + CONTRACT_END)
    prepared.update(prompt=prompt, model_input=model_input(prompt), generate=True,
                    generation_contract=contract, generation_constraints=constraints)
    return prepared
