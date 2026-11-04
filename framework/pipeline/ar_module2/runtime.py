"""Frozen 120-input module comparison. Execution is opt-in and single-attempt."""
import copy
import difflib
from collections import Counter
from pathlib import Path
from pipeline.resume_store import atomic_json, read_json, file_hash, digest, checkpoint, read_checkpoint, exclusive_lock
from . import inputs, original, baseline
from .inputs import FULL, ORIGINAL, VARIANTS, VERSION

ROOT = inputs.ROOT
HERE = ROOT / 'analysis/module2_original_rule_ablation'
CONFIG = ROOT / 'config/ar_module2_original_pairing.json'
MANIFEST = HERE / 'manifest_120.json'


def runtime_files():
    files = list((ROOT / 'pipeline').rglob('*.py')) + list((ROOT / 'pipeline').rglob('*.json'))
    files += list((ROOT / 'refactoring').rglob('*.py')) + list((ROOT / 'refactoring/contracts').rglob('*.yaml'))
    files += list((ROOT / 'refactoring/contracts').rglob('*.md')) + list((ROOT / 'config').glob('*.yaml'))
    files += list((ROOT / 'src/main/java').rglob('*.java')) + list((ROOT / 'target/classes').rglob('*.class'))
    files += list(original.UPSTREAM.rglob('*.txt'))
    files += [ROOT / 'run_ar_module2.py', ROOT / 'pom.xml', ROOT / 'detection/junit5/TestSmellDetector.jar']
    return {str(p.relative_to(ROOT)): file_hash(p) for p in sorted(set(files)) if p.is_file() and 'tests' not in p.parts}


def guard_runtime(config):
    if config['identity']['model'] != inputs.model_parameters().identity() or config['identity']['runtime_files'] != runtime_files():
        raise ValueError('MID_RUN_FRAMEWORK_OR_MODEL_DRIFT')


def guard_project(config, project):
    from pipeline.verification.engineering.workspace import project_input_sha256
    if project_input_sha256(ROOT / 'datasets' / project, ROOT / 'data/__ablation_candidate__') != config['identity']['project_inputs'][project]:
        raise ValueError('MID_RUN_PROJECT_INPUT_DRIFT:' + project)


def identity(rows):
    from pipeline.ablation_runtime import runtime_identity
    value = runtime_identity(rows, manifest_path=inputs.PARENT)
    value.update(runtime=VERSION, model=inputs.model_parameters().identity(), original_commit=original.COMMIT,
                 original_manifest_sha256=file_hash(original.UPSTREAM / 'manifest.json'),
                 entrypoint_sha256=file_hash(ROOT / 'run_ar_module2.py'),
                 framework_classes_sha256=digest({p.relative_to(ROOT / 'target/classes').as_posix(): file_hash(p)
                    for p in sorted((ROOT / 'target/classes').rglob('*.class'))}),
                 framework_pom_sha256=file_hash(ROOT / 'pom.xml'),
                 baseline_sha256=file_hash(baseline.BASELINE),
                 shared_whitelist={'instance': list(inputs.INSTANCE_FIELDS), 'production': list(inputs.PRODUCTION_FIELDS)},
                 output_adapter='ar-module2-ast-output-lf-1', evaluator='ar-module2-two-layer-outcome-1',
                 independent_static='ar-module2-independent-static-1', runtime_files=runtime_files())
    return value


def save_once(path, value):
    if Path(path).exists() and read_json(path) != value:
        raise ValueError('CREATE_ONCE_FREEZE_DRIFT:' + str(path))
    if not Path(path).exists():
        atomic_json(path, value)


def preflight(*, freeze=False, progress=None):
    original.verify()
    rows = inputs.load_rows()
    truth = baseline.verified()
    results, errors = [], []
    for ordinal, row in enumerate(rows):
        try:
            ori = inputs.prepare(row, ORIGINAL)
            full = inputs.prepare(row, FULL)
            assert full['instance'] == ori['instance']
            assert full['shared_context_text'] == ori['shared_context_text']
            assert full['request']['input'] == ori['request']['input']
            assert ori['prompt'] == original.render(ori['instance']['test_code'], ori['shared_context_text'])
            assert ori['shared_context_text'] in ori['prompt']
            assert 'generation_contract' not in ori and 'risk_result' not in ori
            # Compare shared slots byte-for-byte, module-specific guidance intentionally differs.
            shared_input = {'instance_sha256': digest(ori['instance']), 'context': ori['shared_context_text'],
                            'test_code': ori['instance']['test_code'], 'parameters': {k:v for k,v in ori['model_input'].items() if k != 'messages'}}
            if full['generate']:
                assert full['shared_context_text'] in full['prompt']
                assert {k:v for k,v in full['model_input'].items() if k != 'messages'} == shared_input['parameters']
            artifact = HERE / 'prompts' / f'{ordinal:03d}'
            artifact.mkdir(parents=True, exist_ok=True)
            atomic_json(artifact / 'shared_slots.json', shared_input)
            for variant, prepared in ((FULL, full), (ORIGINAL, ori)):
                if prepared['generate']:
                    (artifact / (variant + '.txt')).write_bytes(prepared['prompt'].encode('utf-8'))
                    atomic_json(artifact / (variant + '.model_input.json'), prepared['model_input'])
                else:
                    atomic_json(artifact / (variant + '.abstention.json'), {
                        'decision': 'ABSTAINED', 'admission_decision': prepared['admission_decision'],
                        'risk_result': prepared['risk_result'], 'model_input': None})
            diff = ''.join(difflib.unified_diff((full['prompt'] or '').splitlines(True), ori['prompt'].splitlines(True),
                           fromfile=FULL if full['generate'] else 'FULL_ABSTENTION', tofile=ORIGINAL))
            (artifact / 'canonical.diff').write_bytes(diff.encode('utf-8'))
            audit = {'instance_id': row['instance_id'], 'project': row['project'], 'ordinal': ordinal,
                     'instance_sha256': digest(ori['instance']), 'shared_context_sha256': digest(ori['shared_context_text']),
                     'shared_slots_sha256': digest(shared_input), 'source_sha256': row['source_sha256'],
                     'method_sha256': row['method_sha256'], 'input_sha256': row['input_sha256'],
                     'full_admitted': full['generate'], 'admission_decision': full['admission_decision'],
                     'full_input_sha256': digest(full['model_input']) if full['generate'] else None,
                     'original_input_sha256': digest(ori['model_input']), 'same_shared_slots': True,
                     'original_render_exact': True, 'execution_identity': ori['execution_identity'],
                     'baseline_context_ref': str(baseline.context_for(row, truth).relative_to(ROOT)),
                     'baseline_context_sha256': file_hash(baseline.context_for(row, truth))}
            observed = next(x for r in truth['records'] for x in r['truth'] if x['instance_id'] == row['instance_id'])
            audit.update(before_ar=observed['before_ar'], before_mapping_status=observed['mapping_status'])
            atomic_json(artifact / 'pairing.json', audit)
            results.append(audit)
        except (ValueError, KeyError, OSError, AssertionError) as exc:
            errors.append({'instance_id': row['instance_id'], 'reason': type(exc).__name__ + ':' + str(exc)})
        if progress and ((ordinal + 1) % 10 == 0 or ordinal == len(rows) - 1):
            progress({'processed': ordinal + 1, 'passed': len(results), 'errors': len(errors)})
    admitted = [r['instance_id'] for r in results if r['full_admitted']]
    expected = [r['instance_id'] for r in read_json(ROOT / 'analysis/ar_a2_pairing_preflight/ar_admitted_160_manifest.json')['rows']
                if r['project'] in ('cli', 'compress')]
    if set(admitted) != set(expected) or len(admitted) != 80:
        errors.append({'reason': 'CURRENT_FULL_ADMISSION_NOT_FROZEN_80'})
    freeze_identity = identity(rows)
    value = {'schema_version': VERSION, 'status': 'READY_FOR_MODULE2_RUN' if len(results) == 120 and not errors else 'NOT_READY',
             'variants': list(VARIANTS), 'sample_size': 120, 'projects': {'cli': 60, 'compress': 60},
             'parent_manifest_sha256': file_hash(inputs.PARENT), 'identity': freeze_identity, 'rows': results,
             'bindings_sha256': digest(results), 'errors': errors, 'admitted_subanalysis_ids': admitted,
             'planned_model_calls': {FULL: 80, ORIGINAL: 120, 'total': 200}, 'actual_model_calls': 0,
             'baseline': {'files': truth['files'], 'new_detector_runs': truth['new_detector_runs']}}
    if freeze and value['status'] == 'READY_FOR_MODULE2_RUN':
        save_once(MANIFEST, {'parent_ref': str(inputs.PARENT.relative_to(ROOT)), 'parent_sha256': file_hash(inputs.PARENT),
                             'rows': rows, 'sample_size': 120, 'ordered_instance_ids': [r['instance_id'] for r in rows]})
        save_once(CONFIG, {k:v for k,v in value.items() if k not in ('status', 'errors', 'actual_model_calls', 'baseline')})
    atomic_json(HERE / 'preflight.json', value)
    return value


def verify_frozen():
    config = read_json(CONFIG)
    rows = inputs.load_rows()
    baseline.verified()
    if (config['schema_version'] != VERSION or config['variants'] != list(VARIANTS)
            or config['sample_size'] != 120 or config['parent_manifest_sha256'] != file_hash(inputs.PARENT)
            or config['identity'] != identity(rows) or config['bindings_sha256'] != digest(config['rows'])
            or sum(r['full_admitted'] for r in config['rows']) != 80
            or config['planned_model_calls'] != {FULL: 80, ORIGINAL: 120, 'total': 200}
            or [r['instance_id'] for r in config['rows']] != [r['instance_id'] for r in rows]
            or read_json(MANIFEST)['rows'] != rows):
        raise ValueError('MODULE2_FREEZE_DRIFT')
    for pinned in config['rows']:
        if file_hash(ROOT / pinned['baseline_context_ref']) != pinned['baseline_context_sha256']:
            raise ValueError('MODULE2_BASELINE_CONTEXT_DRIFT')
    return config, rows


def run(variant, run_label, *, allow_model_calls=False, generate=None, evaluator=None, progress=None):
    if not allow_model_calls:
        raise ValueError('MODEL_CALLS_DISABLED')
    if variant not in VARIANTS or not run_label or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_label):
        raise ValueError('MODULE2_VARIANT_AND_SAFE_LABEL_REQUIRED')
    config, rows = verify_frozen()
    output = ROOT / 'data/ar_module2_runs' / run_label / variant
    from pipeline.ablation_runtime import production_generate, GenerationTransportFailure
    from .outcome import evaluate_generated
    with exclusive_lock(output):
        state_path = output / 'checkpoint.json'
        state = read_checkpoint(state_path) if state_path.exists() else {
            'variant': variant, 'run_label': run_label, 'config_sha256': file_hash(CONFIG), 'instances': {}}
        if (state['variant'], state['run_label'], state['config_sha256']) != (variant, run_label, file_hash(CONFIG)):
            raise ValueError('RUN_CONFIG_OR_LABEL_DRIFT')
        for ordinal, (row, pinned) in enumerate(zip(rows, config['rows'])):
            guard_runtime(config)
            guard_project(config, row['project'])
            if file_hash(CONFIG) != state['config_sha256']:
                raise ValueError('MID_RUN_CONFIG_DRIFT')
            iid = row['instance_id']
            previous = state['instances'].get(iid)
            if previous:
                if previous['status'] != 'DONE':
                    return {'status': 'INTERRUPTED_REQUIRES_AUDIT', 'instance_id': iid, 'automatic_retry': False}
                folder = output / 'instances' / f'{ordinal:03d}'
                if file_hash(folder / 'result.json') != previous['result_sha256']:
                    raise ValueError('COMPLETED_RESULT_DRIFT')
                for relative, expected_hash in previous.get('artifacts', {}).items():
                    if file_hash(folder / relative) != expected_hash:
                        raise ValueError('COMPLETED_ARTIFACT_DRIFT')
                continue
            prepared = inputs.prepare(row, variant)
            if (digest(prepared['instance']) != pinned['instance_sha256'] or digest(prepared['shared_context_text']) != pinned['shared_context_sha256']
                    or prepared['generate'] != (pinned['full_admitted'] if variant == FULL else True)):
                raise ValueError('FROZEN_GENERATION_CONDITIONS_DRIFT')
            folder = output / 'instances' / f'{ordinal:03d}'
            folder.mkdir(parents=True, exist_ok=False)
            atomic_json(folder / 'shared_instance.json', prepared['instance'])
            atomic_json(folder / 'shared_context.json', prepared['shared_context'])
            if not prepared['generate']:
                atomic_json(folder / 'abstention.json', prepared['risk_result'])
                result = {'decision': 'ABSTAINED', 'admission_decision': prepared['admission_decision'],
                          'generated': False, 'independent_accepted': False, 'contract_compliance': 'NOT_APPLICABLE'}
            else:
                key = 'full_input_sha256' if variant == FULL else 'original_input_sha256'
                if digest(prepared['model_input']) != pinned[key]:
                    raise ValueError('FROZEN_MODEL_INPUT_DRIFT')
                atomic_json(folder / 'generation_input.json', prepared['model_input'])
                if variant == FULL:
                    atomic_json(folder / 'generation_contract.json', prepared['generation_contract'])
                state['instances'][iid] = {'status': 'ATTEMPT_STARTED', 'ordinal': ordinal}
                checkpoint(state_path, state)
                try:
                    raw = (generate or production_generate)(prepared['prompt'], run_label)
                except GenerationTransportFailure as exc:
                    result = {'decision': 'GENERATION_API_FAILED', 'generated': False, 'api_attempted': True,
                              'kind': exc.kind, 'independent_accepted': False, 'contract_compliance': 'NOT_RUN'}
                else:
                    (folder / 'response.txt').write_bytes(raw.encode('utf-8'))
                    atomic_json(folder / 'response_meta.json', {'response_sha256': file_hash(folder / 'response.txt'),
                                                              'model_input_sha256': digest(prepared['model_input'])})
                    state['instances'][iid] = {'status': 'RESPONSE_SAVED', 'ordinal': ordinal}
                    checkpoint(state_path, state)
                    result = (evaluator or evaluate_generated)(prepared, raw, folder / 'evaluation', row['project'],
                        run_label + '-' + f'{ordinal:03d}', baseline_context=ROOT / pinned['baseline_context_ref'])
                    result['generated'] = True
            atomic_json(folder / 'result.json', {**result, 'instance_id': iid, 'project': row['project'], 'variant': variant})
            artifacts = {p.relative_to(folder).as_posix(): file_hash(p) for p in folder.rglob('*')
                         if p.is_file() and 'detector_runtime' not in p.parts}
            state['instances'][iid] = {'status': 'DONE', 'ordinal': ordinal,
                'result_sha256': file_hash(folder / 'result.json'), 'artifacts': artifacts}
            checkpoint(state_path, state)
            if progress:
                progress({'variant': variant, 'completed': ordinal + 1, 'total': 120, 'decision': result['decision']})
    guard_runtime(config)
    for project in ('cli', 'compress'):
        guard_project(config, project)
    return {'status': 'DONE', 'variant': variant, 'inputs': 120,
            'expected_model_calls': 80 if variant == FULL else 120, 'automatic_retry': False}
