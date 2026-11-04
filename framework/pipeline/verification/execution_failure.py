"""Keep completed observations while closing admission after execution errors."""
import json
import os
from pathlib import Path
from tempfile import mkdtemp


def require_short_evidence_paths(store, stage):
    longest = store.root/stage/('raw_detection/target_class.json' if stage=='revalidation' else 'test_reports/report-999.xml')
    if os.name=='nt' and len(str(longest.resolve()))>=240:
        raise ValueError('VERIFICATION_EVIDENCE_PATH_TOO_LONG: use a shorter output root/run ID')


def preserve_failure(store, stage, result, *, error, phase, agent_called, observed):
    metadata = {'error': error, 'failure_phase': phase, 'agent_called': agent_called,
                'observed_decision': result['result'].get('decision') if observed else None,
                'persistence_complete': False, 'acceptance_allowed': False,
                'result_ref': None, 'persistence_errors': []}
    # A secondary persistence failure must not replace or discard the observation.
    for name, value in (('result.json', result), ('orchestration_error.json', metadata.copy())):
        try:
            path = store.write_json(stage, name, value)
            if name == 'result.json': metadata['result_ref'] = str(path)
        except Exception as exc:
            metadata['persistence_errors'].append(f'{name}: {type(exc).__name__}: {exc}')
    if metadata['persistence_errors']:
        try:
            directory = Path(mkdtemp(prefix='vf-'))
            path = directory/'result.json'
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            metadata['fallback_result_ref'] = str(path)
            (directory/'error.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as exc:
            metadata['persistence_errors'].append(f'fallback: {type(exc).__name__}: {exc}')
            # The caller still receives the complete in-memory result and errors.
    if not metadata.get('result_ref') and not metadata.get('fallback_result_ref'):
        from copy import deepcopy
        metadata['in_memory_observation'] = deepcopy(result)
    return metadata
