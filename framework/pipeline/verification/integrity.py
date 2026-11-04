"""One complete-file evidence chain shared by all validation consumers."""
import copy
from pathlib import Path
from pipeline.hashing import HASH_CHAIN_VERSION, sha256_file_bytes, file_hash_chain_failure

MNT_VALIDATION_VERSION = '1'
VALIDATION_EVIDENCE_VERSION = '2'


def new_chain(source, candidate, relative, instance_ids):
    from pipeline.agents.validation_contract import nearest_maven_root
    from pipeline.agents.validation_workspace import project_input_sha256
    return {'version':HASH_CHAIN_VERSION, 'mnt_validation_version':MNT_VALIDATION_VERSION,
            'validation_evidence_version':VALIDATION_EVIDENCE_VERSION,
            'project_inputs_sha256':project_input_sha256(nearest_maven_root(Path(source)),candidate),
            'status':'HASH_NOT_VERIFIED', 'source_file':str(Path(source).resolve()),
            'source_file_sha256':sha256_file_bytes(source), 'source_relative_path':str(relative).replace('\\','/'),
            'instance_ids':sorted(instance_ids), 'candidate_file_sha256':sha256_file_bytes(candidate),
            'candidate_file':str(Path(candidate).resolve()),
            'validation_input_sha256':sha256_file_bytes(candidate)}


def observe(chain, field, path):
    value=sha256_file_bytes(path)
    if field in chain:
        raise ValueError('MNT_VALIDATION_EVIDENCE_ALREADY_RECORDED')
    chain[field]=value
    if value!=chain['candidate_file_sha256']:
        chain['status']='HASH_MISMATCH'
        raise ValueError('MNT_COMPLETE_FILE_HASH_MISMATCH:'+field)


def require_chain(chain, *, source, candidate, relative, instance_id, detector=False):
    if not isinstance(chain,dict) or chain.get('mnt_validation_version')!=MNT_VALIDATION_VERSION or chain.get('version')!=HASH_CHAIN_VERSION:
        raise ValueError('MNT_VALIDATION_EVIDENCE_REAUDIT_REQUIRED')
    if chain.get('validation_evidence_version')!=VALIDATION_EVIDENCE_VERSION:
        raise ValueError('VALIDATION_EVIDENCE_REAUDIT_REQUIRED')
    ids=chain.get('instance_ids')
    if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):
        raise ValueError('MNT_VALIDATION_FILE_IDENTITY_MISMATCH')
    if (chain.get('source_file')!=str(Path(source).resolve()) or
            chain.get('candidate_file')!=str(Path(candidate).resolve()) or
            chain.get('source_relative_path')!=str(relative).replace('\\','/') or
            instance_id not in (chain.get('instance_ids') or [])):
        raise ValueError('MNT_VALIDATION_FILE_IDENTITY_MISMATCH')
    if chain.get('source_file_sha256')!=sha256_file_bytes(source):
        raise ValueError('MNT_VALIDATION_SOURCE_HASH_MISMATCH')
    from pipeline.agents.validation_contract import nearest_maven_root
    from pipeline.agents.validation_workspace import project_input_sha256
    if chain.get('project_inputs_sha256')!=project_input_sha256(nearest_maven_root(Path(source)),candidate):
        raise ValueError('VALIDATION_PROJECT_INPUTS_CHANGED')
    expected=chain.get('candidate_file_sha256')
    failure=file_hash_chain_failure(expected,chain.get('validation_input_sha256'),chain.get('injected_candidate_sha256'),
                                    chain.get('validated_candidate_sha256'),sha256_file_bytes(candidate))
    if failure or chain.get('compiled_candidate_sha256')!=expected or chain.get('status')!='VERIFIED':
        raise ValueError('MNT_VALIDATION_COMPLETE_FILE_CHAIN_INVALID')
    for key in ('target_test_candidate_sha256','fallback_test_candidate_sha256','module_test_candidate_sha256','revalidation_injected_candidate_sha256'):
        if key in chain and chain[key]!=expected:
            raise ValueError('MNT_VALIDATION_COMPLETE_FILE_CHAIN_INVALID:'+key)
    if detector and (chain.get('detector_input_sha256')!=expected or chain.get('post_detector_candidate_sha256')!=expected):
        raise ValueError('MNT_REVALIDATION_COMPLETE_FILE_CHAIN_INVALID')
    return True


def engineering_chain(validation, refactoring, *, source, candidate, relative, instance_id):
    """Validate persisted Phase 4 evidence, not a freshly inferred replacement."""
    chain=validation.get('hash_chain')
    require_chain(chain,source=source,candidate=candidate,relative=relative,instance_id=instance_id)
    vc=validation.get('candidate') or {}; rc=refactoring.get('candidate') or {}
    if vc.get('file_hash')!=rc.get('file_sha256') or vc.get('method_hash')!=rc.get('method_sha256'):
        raise ValueError('MNT_ENGINEERING_CANDIDATE_MISMATCH')
    if Path(vc.get('file_ref') or '').resolve()!=Path(candidate).resolve():
        raise ValueError('MNT_ENGINEERING_CANDIDATE_MISMATCH')
    source_evidence=validation.get('source') or {}
    if (validation.get('instance_id')!=instance_id or Path(source_evidence.get('path') or '').resolve()!=Path(source).resolve() or
            source_evidence.get('hash_before')!=chain['source_file_sha256'] or
            source_evidence.get('hash_after')!=chain['source_file_sha256'] or source_evidence.get('unchanged') is not True):
        raise ValueError('MNT_ENGINEERING_SOURCE_IDENTITY_MISMATCH')
    summary=validation.get('test_summary') or {}
    from pipeline.agents.test_report_parser import execution_confirmed
    from pipeline.agents.method_location import test_execution_identity
    target_class,target_method=test_execution_identity(Path(source).read_text(encoding='utf8'),refactoring['risk_contract']['safety_instance'])
    if (not {'STRUCTURE','COMPILE','TARGET_TEST'}.issubset(validation.get('completed_levels') or []) or
            validation.get('target_test_observed') is not True or summary.get('target_method_observed') is not True or
            not execution_confirmed(summary,target_class,target_method)):
        raise ValueError('MNT_ENGINEERING_EXECUTION_NOT_CONFIRMED')
    if any((validation.get(k) or {}).get('status')!='PASS' or (validation.get(k) or {}).get('exit_code')!=0
           for k in ('compile_result','target_test_result')):
        raise ValueError('MNT_ENGINEERING_EXECUTION_NOT_CONFIRMED')
    return copy.deepcopy(chain)


def verify_cached_mnt(stage, result, payload):
    refactoring=payload['refactoring_result']['result']
    contract=refactoring['risk_contract'];instance=contract['safety_instance']
    source=Path(payload['source_test_file'])
    candidate=Path(payload['candidate_file_ref'] if stage=='validation' else payload['candidate_file'])
    method=Path(payload['candidate_method_ref'] if stage=='validation' else payload['candidate_method'])
    relative=payload['source_test_relative_path'] if stage=='validation' else payload['source_relative_path']
    validation=result['result'] if stage=='validation' else payload['validation_result']['result']
    upstream=engineering_chain(validation,refactoring,source=source,candidate=candidate,relative=relative,instance_id=instance['instance_id'])
    if stage=='revalidation':
        current=result['result'].get('hash_chain')
        require_chain(current,source=source,candidate=candidate,relative=relative,instance_id=instance['instance_id'],detector=True)
        if any(current.get(k)!=v for k,v in upstream.items()):
            raise ValueError('MNT_ENGINEERING_REVALIDATION_CHAIN_CONFLICT')
    from pipeline.agents.safety_contracts import replay_saved_contract
    if not replay_saved_contract(contract,method.read_text(encoding='utf8'),source.read_text(encoding='utf8'))[0]:
        raise ValueError('MNT_CACHED_METHOD_PROOF_INVALID')
    from pipeline.agents.refactoring_contract import replace_method_in_copy
    expected=replace_method_in_copy(source.read_text(encoding='utf8'),method.read_text(encoding='utf8'),
                                    contract['method_start_line'],contract['method_end_line'])
    if candidate.read_text(encoding='utf8')!=expected:
        raise ValueError('CACHED_COMPLETE_FILE_PROOF_INVALID')
