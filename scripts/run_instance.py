"""One fresh AR run from a preserved schema 4.1 record; no candidate replay."""
from __future__ import annotations
import argparse
import copy
import json
import os
import re
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'framework'))
from runtime_support import contained, extract_project, file_sha, read_json, write_json, require, tool_paths, isolated_environment

STAGES = ('INPUT', 'SOURCE_BINDING', 'APPLICABILITY', 'CONTRACT_PROMPT', 'MODEL',
          'CANDIDATE_CHECK', 'REINTEGRATION', 'ENGINEERING_OUTCOME', 'FINAL_AUDIT')

class StopRun(Exception):
    def __init__(self, decision, reason):
        self.decision, self.reason = decision, reason


def rebuild(record, config, source, provenance):
    from pipeline.contracts.adapter_v41 import adapt_schema_v41_record
    from pipeline.ar_independent_outcome import range_execution_identity
    instance = adapt_schema_v41_record(record, project_version=config['project_version'], smell_type=config['smell'])
    require(instance['test_relative_path'] == config['target_path'], 'DETECTION_TARGET_PATH_MISMATCH')
    require(instance['method_signature'] == config['method_signature'], 'DETECTION_METHOD_MISMATCH')
    require(config['smell'] == 'Assertion_Roulette' and config['smell'] in instance['detected_smells'], 'AR_NOT_DETECTED')
    require(config['project_version'] == provenance['project_revision'], 'PROJECT_VERSION_MISMATCH')
    target = contained(source / instance['test_relative_path'], source)
    require(file_sha(target) == provenance['source_sha256'], 'SOURCE_HASH_MISMATCH')
    historical = {'test_file_path': instance['test_file_path'], 'selected_smells': list(instance['selected_smells']),
                  'ignored_smells': list(instance['ignored_smells'])}
    # Explicit current selection only; immutable source_record remains unchanged.
    instance['test_file_path'] = str(target)
    instance['selected_smells'] = [config['smell']]
    instance['ignored_smells'] = [s for s in instance['detected_smells'] if s != config['smell']]
    instance['source_owner_binding'] = {'schema_version':'1.0','status':'BOUND','reason_code':'HASH_BOUND_PENDING_AST','source_file_sha256':file_sha(target)}
    owner, method = range_execution_identity(target, instance)
    binding = {'schema_version': '1.0', 'status': 'BOUND', 'reason_code': 'FRESH_AST_RANGE_SIGNATURE_SOURCE_OWNER_MATCH',
               'source_file_sha256': file_sha(target), 'resolved_source_path': str(target),
               'test_relative_path': instance['test_relative_path'], 'owner_class': owner, 'test_method': method}
    instance['source_owner_binding'] = binding
    # The historical production path is provenance only, never a lookup path.
    pc = copy.deepcopy(instance['production_context'])
    production_relative = 'src/main/java/' + pc['productionPackageName'].replace('.', '/') + '/' + pc['productionClassName'] + '.java'
    production = contained(source / production_relative, source)
    require(production.is_file(), 'PRODUCTION_SOURCE_MISSING')
    pc['productionClassPath'] = str(production)
    pc['focalMethodRefs'] = [str(production) + '#' + ref.split('#', 1)[1] for ref in pc.get('focalMethodRefs', []) if '#' in ref]
    instance['production_context'] = pc
    require(instance['source_record'] == record, 'HISTORICAL_RECORD_MUTATED')
    return instance, {'historical': historical, 'current_selection': config['smell'],
                      'selection_semantics': 'Explicit new request; not historical admission',
                      'binding': binding, 'production_source': production_relative}


def setup_environment(out):
    tools = tool_paths()
    mirror_id = os.environ.get('ILEREF_MAVEN_MIRROR_ID', 'central')
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]+',mirror_id)), 'Invalid Maven mirror id')
    local_repository = os.environ.get('ILEREF_MAVEN_REPOSITORY')
    offline = os.environ.get('ILEREF_MAVEN_OFFLINE') == '1'
    env = isolated_environment(out, tools)
    # Preserve model credential only in Python; existing Java runners strip secrets.
    key = os.environ.get('ILEREF_API_KEY')
    for name in ('home', 'temp', 'config'):
        (out / name).mkdir(exist_ok=True)
    os.environ.clear()
    os.environ.update(env)
    if key:
        os.environ['ILEREF_API_KEY'] = key
    tempfile.tempdir = str(out / 'temp')
    os.environ['FULL_DETECTOR_TMP_ROOT'] = str(out / 'temp')
    repository = Path(local_repository).resolve() if local_repository else ROOT / 'results/maven_repository'
    repository.mkdir(parents=True, exist_ok=True)
    settings = out / 'config/settings.xml'
    settings.write_text(f'<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"><mirrors><mirror><id>{mirror_id}</id><mirrorOf>*</mirrorOf><url>https://repo.maven.apache.org/maven2/</url></mirror></mirrors></settings>\n', encoding='utf-8')
    globals_ = out / 'config/global-settings.xml'
    globals_.write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"/>\n', encoding='utf-8')
    os.environ['MAVEN_ARGS'] = f'-B -ntp -s "{settings}" -gs "{globals_}" -Dmaven.repo.local="{repository}" -DfailIfNoTests=true -Dsurefire.failIfNoSpecifiedTests=true'
    if offline:
        os.environ['MAVEN_ARGS'] += ' -o'
    return tools


def run_frontend(out, timeout=600):
    from pipeline.verification.engineering.runner import MavenValidationRunner
    result, stdout, stderr = MavenValidationRunner().run(stage='FRONTEND_COMPILE', args=['-q', '-DskipTests', 'compile'], cwd=ROOT / 'framework', timeout_seconds=timeout)
    write_json(out / 'frontend_compile.json', result)
    (out / 'frontend.stdout.log').write_text(stdout, encoding='utf-8')
    (out / 'frontend.stderr.log').write_text(stderr, encoding='utf-8')
    if result['status'] != 'PASS':
        raise StopRun('INCONCLUSIVE', 'FRONTEND_BUILD_FAILED')


def persist_raw_evidence(out):
    """Retain byte-exact fresh XML/CSV independently of temporary workspaces."""
    records=[]
    for source in sorted((out/'temp').rglob('*')):
        if not source.is_file() or not (source.name.startswith('TEST-') and source.suffix=='.xml' or source.suffix=='.csv'):
            continue
        source=contained(source,out)
        digest=file_sha(source)
        target=out/'validation/raw_bytes'/(digest[:16]+'-'+source.name)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes())
        require(file_sha(target)==digest,'RAW_EVIDENCE_COPY_MISMATCH')
        records.append({'path':target.relative_to(out).as_posix(),'sha256':digest,'source':source.relative_to(out).as_posix()})
    write_json(out/'raw_evidence.json',records)
    return records


def final_audit(instance, contract, candidate_method, bundle, candidate, source_root, source_fingerprint, gate):
    from pipeline.agents.safety_contracts import replay_saved_contract
    from pipeline.ar_module2.outcome import Module2FileGate
    from pipeline.ar_independent_outcome import summarize_gate, range_execution_identity
    from pipeline.verification.engineering.workspace import project_input_sha256
    from pipeline.verification.candidate.validation import CandidateValidator
    owner = Path(instance['test_file_path']).read_text(encoding='utf-8')
    identity = range_execution_identity(instance['test_file_path'], instance)
    request = {'input': {'instance': instance, 'context': {'test_class_source': owner},
                         'risk_result': {'result': {'evidence': contract['safety_evidence']}}}}
    current = CandidateValidator().validate(raw_response=candidate_method, request=request, contract=contract, original_file=owner)
    ok, reason = replay_saved_contract(contract, candidate_method, owner)
    structure = Module2FileGate._structure_checks(bundle, candidate, [instance])
    hashes = (file_sha(candidate) == bundle.candidate_file_sha256
              and file_sha(instance['test_file_path']) == instance['source_owner_binding']['source_file_sha256']
              and project_input_sha256(source_root, candidate) == source_fingerprint)
    from pipeline.verification.integrity import require_chain
    chain_error = None
    try:
        require_chain(gate.get('hash_chain'), source=instance['test_file_path'], candidate=candidate,
                      relative=instance['test_relative_path'], instance_id=instance['instance_id'], detector=True)
    except ValueError as exc:
        chain_error = str(exc)
    summary = summarize_gate(instance, gate)
    summary['current_hash_chain_error'] = chain_error
    summary.update(current_contract_passed=current['passed'] and ok, current_structure_passed=all(x['status']=='PASS' for x in structure),
                   current_inputs_unchanged=hashes, target_identity=identity, contract_reason=reason)
    accepted = summary['independent_accepted'] and summary['current_contract_passed'] and summary['current_structure_passed'] and hashes and chain_error is None
    return bool(accepted), summary


def run(args):
    config_path = contained(ROOT / args.config)
    config = read_json(config_path)
    name = args.name or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    require(bool(re.fullmatch(r'[A-Za-z0-9_-]+', name)), 'Invalid run name')
    out = contained(ROOT / 'results/runs' / name)
    out.mkdir(parents=True, exist_ok=False)
    result = {'decision': 'INCONCLUSIVE', 'run_id': name, 'model_calls': 0,
              'scope': 'Fresh AR candidate from historical detection; no initial detection',
              'stages': {s: 'NOT_RUN' for s in STAGES}}
    stage = 'INPUT'
    try:
        write_json(out / 'run_config.json', config)
        tools = setup_environment(out)
        write_json(out / 'environment.json', {'python': sys.version, 'tools': tools})
        provenance = read_json(contained(ROOT / config['provenance']))
        detection_path = contained(ROOT / config['detection'])
        records = read_json(detection_path)
        require(isinstance(records, list) and len(records) == 1, 'Exactly one historical record required')
        (out / 'detection.original.json').write_bytes(detection_path.read_bytes())
        write_json(out / 'provenance.json', provenance)
        source = out / 'source'
        extract_project(provenance, source)
        result['stages'][stage] = 'PASS'
        stage = 'SOURCE_BINDING'
        instance, binding = rebuild(records[0], config, source, provenance)
        write_json(out / 'instance.json', instance)
        write_json(out / 'source_binding.json', binding)
        result['instance_id'] = instance['instance_id']
        result['stages'][stage] = 'PASS'
        from pipeline.contracts import make_agent_request
        from pipeline.agents.risk_agent import RiskAssessmentAgent
        from pipeline.agents.refactoring_agent import RefactoringAgent
        from pipeline.verification.engineering.workspace import project_input_sha256
        from pipeline.agents.prompt_service import RefactoringPromptService
        from pipeline.contract_builders import ExecutableContractBuilder
        from pipeline import model_config
        model_config.DEFAULT_MODEL_CONFIG = contained(ROOT / config['model_config'])
        require(model_config.DEFAULT_MODEL_CONFIG.is_file(), 'MODEL_CONFIG_MISSING')
        agent_config = contained(ROOT / config['agent_config'])
        context = {'test_class_source': Path(instance['test_file_path']).read_text(encoding='utf-8')}
        payload = {'instance': instance, 'context': context, 'target_smells': [config['smell']]}
        def request(agent, data):
            return make_agent_request(run_id=name, instance_id=instance['instance_id'], agent=agent, agent_version='3.0', input_payload=data)
        stage = 'APPLICABILITY'
        risk = RiskAssessmentAgent(enabled=True, mode='rule_only', config_path=agent_config).execute(request('risk_assessment', payload))
        write_json(out / 'risk.json', risk)
        if risk['result']['decision'] != 'REFACTOR_LOW_RISK':
            raise StopRun('REJECTED' if risk['status']=='SUCCESS' else 'INCONCLUSIVE', 'NOT_ADMITTED:' + risk['result']['decision'])
        result['stages'][stage] = 'PASS'
        stage = 'CONTRACT_PROMPT'
        req = request('refactoring', {**payload, 'risk_result': risk, 'pipeline_state': 'RISK_APPROVED'})
        contract = ExecutableContractBuilder().build(req)
        prompt, prompt_hash = RefactoringPromptService().build(req, contract, context, [])
        write_json(out / 'request.json', req)
        write_json(out / 'contract.json', contract)
        (out / 'prompt.txt').write_text(prompt, encoding='utf-8', newline='\n')
        write_json(out / 'prompt_identity.json', {'sha256': prompt_hash, 'rag': 'DISABLED', 'strategy': 'FULL', 'risk_mode': 'rule_only'})
        result['stages'][stage] = 'PASS'
        if args.prepare_only:
            raise StopRun('INCONCLUSIVE', 'PREPARATION_ONLY_MODEL_AND_DOWNSTREAM_NOT_RUN')
        if args.check_environment:
            run_frontend(out)
            from pipeline.verification.engineering.runner import MavenValidationRunner
            from pipeline.verification.engineering.test_reports import snapshot_maven_test_reports, parse_maven_test_reports, execution_confirmed
            before = snapshot_maven_test_reports(source)
            owner, method = binding['binding']['owner_class'], binding['binding']['test_method']
            for label, cmd in [('BASELINE_COMPILE', ['-q','-DskipTests','test-compile']), ('BASELINE_TARGET_TEST', ['-q','-Dtest='+owner+'#'+method.split('(')[0], 'test'])]:
                execution, stdout, stderr = MavenValidationRunner().run(stage=label,args=cmd,cwd=source,timeout_seconds=600)
                write_json(out / (label+'.json'), execution)
                (out / (label+'.log')).write_text(stdout+'\n'+stderr,encoding='utf-8')
                if execution['status'] != 'PASS':
                    raise StopRun('INCONCLUSIVE', label+'_FAILED')
            tests = parse_maven_test_reports(source, owner, method, before_reports=before)
            write_json(out / 'baseline_tests.json', tests)
            require(execution_confirmed(tests, owner, method), 'BASELINE_TARGET_NOT_EXECUTED')
            raise StopRun('INCONCLUSIVE', 'ENVIRONMENT_CHECK_PASSED_NO_CANDIDATE_GENERATED')
        stage = 'MODEL'
        identity = model_config.resolve_role('refactoring').identity()
        write_json(out / 'model_identity.json', identity)
        model_config.resolve_role('refactoring', require=True)
        # No saved_result_path, historical response or test provider is accepted by this CLI.
        agent = RefactoringAgent(enabled=True, config_path=agent_config)
        def request_started():
            result['model_calls'] += 1
        def response_received(text, usage):
            (out / 'response.txt').write_text(text, encoding='utf-8', newline='\n')
            write_json(out / 'model_response.json', {'content': text, 'usage': usage})
        agent.provider.on_request = request_started
        agent.provider.on_response = response_received
        generation = agent.execute(req)
        write_json(out / 'generation.json', generation)
        gr = generation['result']
        if not (out / 'response.txt').exists():
            (out / 'response.txt').write_text(gr.get('raw_response',''), encoding='utf-8', newline='\n')
        require(gr.get('prompt') == prompt, 'PROMPT_BUILD_DRIFT')
        if gr['decision'] == 'REFACTORING_FAILED':
            raise StopRun('INCONCLUSIVE', 'MODEL_OR_GENERATION_FAILED')
        result['stages'][stage] = 'PASS'
        stage = 'CANDIDATE_CHECK'
        write_json(out / 'candidate_checks.json', {'validation':gr['validation_checks'], 'policy':gr['policy_checks']})
        if gr['decision'] != 'CANDIDATE_GENERATED':
            raise StopRun('REJECTED', 'CANDIDATE_CONTRACT_REJECTED')
        method = gr['candidate']['method_code']
        (out / 'candidate_method.java').write_text(method, encoding='utf-8', newline='\n')
        result['stages'][stage] = 'PASS'
        stage = 'REINTEGRATION'
        from pipeline.ar_module2.output import compose
        from pipeline.ar_module2.outcome import Module2FileGate
        from pipeline.ar_module2.detector import TargetFileDetector
        bundle = compose(instance['test_file_path'], instance, method, config['project'])
        candidate = out / 'candidate.java'
        candidate.write_bytes(bundle.candidate_source.encode('utf-8'))
        bundle = replace(bundle, candidate_file_sha256=file_sha(candidate))
        checks = Module2FileGate._structure_checks(bundle, candidate, [instance])
        write_json(out / 'structure.json', checks)
        if not checks or not all(c['status']=='PASS' for c in checks):
            raise StopRun('REJECTED', 'STRUCTURE_FAILED')
        fingerprint = project_input_sha256(source, candidate)
        result['stages'][stage] = 'PASS'
        stage = 'ENGINEERING_OUTCOME'
        run_frontend(out)
        detector = TargetFileDetector(instance['test_relative_path'], out / 'frontend',
                                      instance['production_context']['productionClassPath'], repo_root=ROOT / 'framework')
        gate_executor = Module2FileGate(repo_root=ROOT / 'framework', config_path=agent_config, detector_runner=detector)
        gate_executor.detector_jar = contained(ROOT / config['detector_jar'])
        gate = gate_executor.execute(run_id=name,project_name=config['project'],bundle=bundle,targets=[instance],
                                     before_detection_ref=out / 'detection.original.json',candidate_path=candidate)
        for name_, text in gate.pop('transient_artifacts', {}).items():
            p = contained(out / 'validation' / name_, out / 'validation'); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text,encoding='utf-8')
        persist_raw_evidence(out)
        write_json(out / 'file_gate.json', gate)
        result['stages'][stage] = gate['status']
        stage = 'FINAL_AUDIT'
        accepted, summary = final_audit(instance, contract, method, bundle, candidate, source, fingerprint, gate)
        write_json(out / 'final_audit.json', summary)
        result['stages'][stage] = 'PASS' if accepted else 'NOT_ACCEPTED'
        if accepted:
            result.update(decision='ACCEPTED', reason='FRESH_CONTRACT_ENGINEERING_TARGET_EFFECT_AND_CURRENT_FILE_AUDIT_PASSED')
        else:
            known_failure = gate.get('validation_decision') == 'VALIDATION_FAILED' or any(x.get('target_smell_revalidation_status') in {'TARGET_SMELL_REMAINS','NEW_SMELL_INTRODUCED','METHOD_COUNT_VIOLATION'} for x in gate.get('instance_revalidation_results',[]))
            result.update(decision='REJECTED' if known_failure else 'INCONCLUSIVE', reason='CURRENT_EVIDENCE_DOES_NOT_SUPPORT_ACCEPTANCE')
    except StopRun as exc:
        result.update(decision=exc.decision,reason=exc.reason)
        if result['stages'][stage] == 'NOT_RUN': result['stages'][stage] = exc.decision
    except Exception as exc:
        from pipeline.model_config import safe_failure, ModelConfigurationError
        result.update(decision='INCONCLUSIVE', reason=safe_failure(exc) if stage=='MODEL' else type(exc).__name__+':'+str(exc))
        result['stages'][stage] = 'BLOCKED'
    finally:
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
        write_json(out / 'result.json',result)
        manifest = {p.relative_to(out).as_posix():file_sha(p) for p in out.rglob('*') if p.is_file() and 'temp' not in p.relative_to(out).parts and 'home' not in p.relative_to(out).parts}
        write_json(out / 'evidence_manifest.json',manifest)
    print(json.dumps({'output':str(out),'decision':result['decision'],'reason':result.get('reason'),'stages':result['stages']},indent=2))
    return {'ACCEPTED':0,'REJECTED':1,'INCONCLUSIVE':2}[result['decision']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config/run_ar.json')
    parser.add_argument('--name')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only',action='store_true',help='Build current instance, risk, contract and prompt; never claim acceptance.')
    mode.add_argument('--check-environment',action='store_true',help='Also build frontend and compile/test original source; no detector/model or candidate acceptance.')
    return run(parser.parse_args())

if __name__ == '__main__':
    raise SystemExit(main())
