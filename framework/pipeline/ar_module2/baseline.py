"""Frozen before-detector evidence, source-only and shared by both variants."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import copy
from pipeline.resume_store import read_json, atomic_json, file_hash
from pipeline.agents.safety_contracts import Java
from pipeline.agents.method_location import signature
from pipeline.agents.detection_result_adapter import load_phase1_detection_output
from pipeline.agents.validation_contract import nearest_maven_root
from . import inputs
from .detector import TargetFileDetector

HERE = inputs.ROOT / 'analysis/module2_original_rule_ablation'
BASELINE = HERE / 'baseline_ast.json'


def check_file(rows):
    row = rows[0]
    prepared = inputs.shared(row)
    instance, source = prepared['instance'], Path(prepared['instance']['test_file_path'])
    folder = HERE / 'baseline' / row['source_sha256'][:16]
    folder.mkdir(parents=True, exist_ok=True)
    saved = folder / 'calibration.json'
    jar = inputs.ROOT / 'detection/junit5/TestSmellDetector.jar'
    if saved.exists():
        cached = read_json(saved)
        if (cached['source_sha256'] != file_hash(source) or cached['detector_jar_sha256'] != file_hash(jar)
                or cached['context_sha256'] != file_hash(folder / 'context.json')):
            raise ValueError('BASELINE_CACHE_DRIFT')
        return cached
    # Reuse completed byte-bound full-file detector observations, NOT candidates.
    old = inputs.ROOT / 'analysis/ar_a2_acceptance_decoupling/baseline_calibration_v3' / row['source_sha256'][:16]
    reused = False
    if (old / 'calibration.json').exists():
        metadata = read_json(old / 'calibration.json')
        det = metadata.get('detector') or {}
        if (metadata.get('passed') is True and metadata.get('source_sha256') == file_hash(source)
                and det.get('detector_identity', {}).get('detector_jar_sha256') == file_hash(jar)
                and det.get('actual_detector_source_sha256') == file_hash(source)
                and (old / 'result.csv').exists() and file_hash(old / 'result.csv') == det.get('output_hash')):
            records = read_json(old / 'records.json')
            # Save a new independent evidence copy, no historical writes.
            (folder / 'result.csv').write_bytes((old / 'result.csv').read_bytes())
            result, stdout, stderr, reused = det, '', '', True
    if not reused:
        relative = source.relative_to(nearest_maven_root(source))
        detector = TargetFileDetector(relative, folder / 'detector_runtime',
            (instance.get('production_context') or {}).get('productionClassPath'))
        result, stdout, stderr, directory = detector.run(workspace=nearest_maven_root(source),
            run_id='module2-baseline-' + row['source_sha256'], project_name=row['project'],
            detector_jar=jar, timeout_seconds=600)
        (folder / 'stdout.txt').write_text(stdout, encoding='utf-8')
        (folder / 'stderr.txt').write_text(stderr, encoding='utf-8')
        if result['status'] != 'PASS' or directory is None:
            raise ValueError('BASELINE_DETECTOR_NOT_SUCCESSFUL:' + str(result.get('failure_category')))
        payload = {'target_class': Path(instance['test_filename']).stem,
                   'target_package': instance.get('test_package_name') or '',
                   'source_relative_path': row['relative_path'], 'project_id': row['project'],
                   'module_path': instance.get('module_path') or ''}
        records, csv = load_phase1_detection_output(directory, payload)
        (folder / 'result.csv').write_bytes(csv.read_bytes())
        detector.cleanup_successful_output(directory, run_id='ignored', project_name='ignored')
    atomic_json(folder / 'records.json', records)
    java = Java(prepared['original'])
    by_signature = defaultdict(list)
    for node in java.nodes('method_declaration'):
        by_signature[signature(java, node)].append(node)
    contexts = []
    for record in records:
        matches = by_signature.get(record.get('method_signature'), [])
        if len(matches) != 1:
            # Explicit mapping unknown; no synthetic AR-positive row is inserted.
            continue
        node = matches[0]
        contexts.append({'schemaVersion': '4.1', 'projectName': row['project'], 'testContext': {
            'testCode': java.text(node), 'testFilename': instance['test_filename'],
            'testPackageName': instance.get('test_package_name') or '',
            'testRelativePath': row['relative_path'], 'splitFilename': record['split_filename'],
            'testCodeStartLine': node.start_point.row + 1, 'testCodeEndLine': node.end_point.row + 1,
            'smellList': record['smells']}})
    atomic_json(folder / 'context.json', contexts)
    truth = []
    for r in rows:
        matches = [c for c in contexts if signature(Java(c['testContext']['testCode']),
                    Java(c['testContext']['testCode']).nodes('method_declaration')[0]) == r['method_signature']]
        truth.append({'instance_id': r['instance_id'], 'before_ar': None if len(matches) != 1 else
                      'Assertion_Roulette' in matches[0]['testContext']['smellList'],
                      'mapping_status': 'EXACT_SOURCE_SIGNATURE' if len(matches) == 1 else 'INCONCLUSIVE'})
    value = {'project': row['project'], 'relative_path': row['relative_path'],
             'source_sha256': file_hash(source), 'detector_jar_sha256': file_hash(jar),
             'context_ref': str((folder / 'context.json').relative_to(inputs.ROOT)),
             'context_sha256': file_hash(folder / 'context.json'), 'records_sha256': file_hash(folder / 'records.json'),
             'result_csv_sha256': file_hash(folder / 'result.csv'), 'detector': result,
             'reused_read_only': reused, 'truth': truth, 'status': 'PASS'}
    atomic_json(saved, value)
    return value


def calibrate(progress=None):
    groups = defaultdict(list)
    for row in inputs.load_rows():
        groups[(row['project'], row['relative_path'])].append(row)
    records = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(check_file, groups.values()):
            records.append(result)
            if progress:
                progress({'baseline_files': len(records), 'total': len(groups), 'reused': result['reused_read_only']})
    payload = {'status': 'PASS', 'files': len(records), 'instances': 120, 'records': records,
               'model_calls': 0, 'new_detector_runs': sum(not r['reused_read_only'] for r in records)}
    legacy = HERE / 'baseline.json'
    if legacy.exists() and read_json(legacy) != payload:
        raise ValueError('EXISTING_BASELINE_FREEZE_DRIFT')
    atomic_json(legacy, payload)
    return remap()


def remap():
    """Reinterpret archived split ASTs; no detector invocation or old overwrites."""
    from .mapping import correct, VERSION
    payload = read_json(HERE / 'baseline.json')
    rows = inputs.load_rows()
    values = []
    for old in payload['records']:
        folder = (inputs.ROOT / old['context_ref']).parent
        if old['records_sha256'] != file_hash(folder / 'records.json'):
            raise ValueError('BASELINE_RAW_RECORDS_DRIFT')
        records = correct(read_json(folder / 'records.json'))
        selected = [r for r in rows if (r['project'], r['relative_path']) == (old['project'], old['relative_path'])]
        prepared = inputs.shared(selected[0])
        java = Java(prepared['original'])
        contexts = []
        for record in records:
            matches = [n for n in java.nodes('method_declaration') if signature(java, n) == record['method_signature']]
            if len(matches) != 1:
                raise ValueError('DETECTOR_SIGNATURE_NOT_UNIQUE_IN_SOURCE')
            node = matches[0]
            contexts.append({'schemaVersion': '4.1', 'projectName': old['project'], 'testContext': {
                'testCode': java.text(node), 'testFilename': prepared['instance']['test_filename'],
                'testPackageName': prepared['instance'].get('test_package_name') or '',
                'testRelativePath': old['relative_path'], 'splitFilename': record['split_filename'],
                'testCodeStartLine': node.start_point.row + 1, 'testCodeEndLine': node.end_point.row + 1,
                'smellList': record['smells']}})
        truth = []
        for row in selected:
            matches = [r for r in records if r['method_signature'] == row['method_signature']]
            truth.append({'instance_id': row['instance_id'], 'before_ar': None if len(matches) != 1 else
                          'Assertion_Roulette' in matches[0]['smells'],
                          'mapping_status': 'EXACT_SOURCE_SIGNATURE' if len(matches) == 1 else 'INCONCLUSIVE'})
        atomic_json(folder / 'records_ast.json', records)
        atomic_json(folder / 'context_ast.json', contexts)
        value = {**old, 'mapping_version': VERSION, 'truth': truth,
                 'context_ref': str((folder / 'context_ast.json').relative_to(inputs.ROOT)),
                 'context_sha256': file_hash(folder / 'context_ast.json'),
                 'records_ref': str((folder / 'records_ast.json').relative_to(inputs.ROOT)),
                 'records_sha256': file_hash(folder / 'records_ast.json')}
        atomic_json(folder / 'calibration_ast.json', value)
        values.append(value)
    result = {**payload, 'records': values, 'mapping_version': VERSION}
    if BASELINE.exists() and read_json(BASELINE) != result:
        raise ValueError('EXISTING_AST_BASELINE_FREEZE_DRIFT')
    atomic_json(BASELINE, result)
    return result


def verified():
    payload = read_json(BASELINE)
    if payload['status'] != 'PASS' or payload['instances'] != 120 or payload['files'] != 66:
        raise ValueError('MODULE2_SOURCE_BASELINE_REQUIRED')
    jar_hash = file_hash(inputs.ROOT / 'detection/junit5/TestSmellDetector.jar')
    for record in payload['records']:
        if (record['detector_jar_sha256'] != jar_hash
                or record['source_sha256'] != file_hash(inputs.ROOT / 'datasets' / record['project'] / record['relative_path'])
                or record['context_sha256'] != file_hash(inputs.ROOT / record['context_ref'])
                or record['records_sha256'] != file_hash(inputs.ROOT / record['records_ref'])
                or record['result_csv_sha256'] != file_hash((inputs.ROOT / record['context_ref']).parent / 'result.csv')):
            raise ValueError('MODULE2_SOURCE_BASELINE_DRIFT')
    return payload


def context_for(row, payload=None):
    payload = payload or verified()
    record = next(r for r in payload['records'] if (r['project'], r['relative_path']) == (row['project'], row['relative_path']))
    return inputs.ROOT / record['context_ref']
