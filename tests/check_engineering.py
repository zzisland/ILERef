"""Real downstream integration check using a new deterministic TEST-ONLY fixture."""
import json
import sys
from datetime import datetime,timezone
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'framework')]
from run_instance import rebuild,setup_environment,run_frontend,final_audit,persist_raw_evidence
from runtime_support import read_json,write_json,extract_project,file_sha,contained
from pipeline.contracts import make_agent_request
from pipeline.agents.risk_agent import RiskAssessmentAgent
from pipeline.contract_builders import ExecutableContractBuilder
from pipeline.agents.safety_contracts import apply_edits,grounded_message
from pipeline.agents.java_assertions import find_assertion_calls
from pipeline.ar_module2.output import compose
from pipeline.ar_module2.outcome import Module2FileGate
from pipeline.ar_module2.detector import TargetFileDetector
from pipeline.verification.engineering.workspace import project_input_sha256

def main():
    out=ROOT/'results/checks'/('engineering_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    out.mkdir(parents=True)
    report={'test_only':True,'candidate_origin':'New deterministic source quotation fixture; not an LLM response',
            'model_calls':0,'experiment_decision':'NOT_APPLICABLE_TEST_ONLY','status':'FAIL'}
    try:
        setup_environment(out)
        cfg=read_json(ROOT/'config/run_ar.json');prov=read_json(ROOT/cfg['provenance']);record=read_json(ROOT/cfg['detection'])[0]
        source=out/'source';extract_project(prov,source)
        instance,binding=rebuild(record,cfg,source,prov)
        write_json(out/'instance.json',instance)
        context={'test_class_source':Path(instance['test_file_path']).read_text(encoding='utf-8')}
        payload={'instance':instance,'context':context,'target_smells':[cfg['smell']]}
        def req(agent,data):return make_agent_request(run_id=out.name,instance_id=instance['instance_id'],agent=agent,agent_version='3.0',input_payload=data)
        risk=RiskAssessmentAgent(enabled=True,mode='rule_only').execute(req('risk_assessment',payload))
        contract=ExecutableContractBuilder().build(req('refactoring',{**payload,'risk_result':risk,'pipeline_state':'RISK_APPROVED'}))
        method=apply_edits(instance['test_code'],[(c['end']-1,c['end']-1,', '+grounded_message(c)) for c in find_assertion_calls(instance['test_code'])])
        write_json(out/'contract.json',contract)
        (out/'fixture_method.java').write_text(method,encoding='utf-8')
        bundle=compose(instance['test_file_path'],instance,method,cfg['project'])
        candidate=out/'fixture_candidate.java';candidate.write_bytes(bundle.candidate_source.encode('utf-8'))
        bundle=replace(bundle,candidate_file_sha256=file_sha(candidate))
        fingerprint=project_input_sha256(source,candidate)
        run_frontend(out)
        detector=TargetFileDetector(instance['test_relative_path'],out/'frontend',instance['production_context']['productionClassPath'],repo_root=ROOT/'framework')
        gate_executor=Module2FileGate(repo_root=ROOT/'framework',detector_runner=detector)
        gate_executor.detector_jar=ROOT/cfg['detector_jar']
        gate=gate_executor.execute(run_id=out.name,project_name=cfg['project'],bundle=bundle,targets=[instance],before_detection_ref=ROOT/cfg['detection'],candidate_path=candidate)
        for name,text in gate.pop('transient_artifacts',{}).items():
            p=contained(out/'validation'/name,out/'validation');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
        persist_raw_evidence(out)
        write_json(out/'file_gate.json',gate)
        verified,audit=final_audit(instance,contract,method,bundle,candidate,source,fingerprint,gate)
        write_json(out/'audit.json',audit)
        report.update(status='PASS' if verified else 'FAIL',checks=audit)
    except Exception as exc:
        report['error']=type(exc).__name__+':'+str(exc)
    write_json(out/'check_result.json',report)
    print(json.dumps({'output':str(out),**report},indent=2))
    return 0 if report['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
