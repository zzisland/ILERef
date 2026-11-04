"""Local regression tests. All model/build/detector doubles below are test-only."""
import copy
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'framework')]
from runtime_support import extract_project,read_json,file_sha
from run_instance import rebuild,final_audit
from pipeline.contracts import make_agent_request,validate_contract
from pipeline.agents.risk_agent import RiskAssessmentAgent
from pipeline.agents.refactoring_agent import RefactoringAgent
from pipeline.agents.refactoring_providers import DeterministicTestRefactoringProvider
from pipeline.contract_builders import ExecutableContractBuilder
from pipeline.verification.candidate.validation import CandidateValidator
from pipeline.ar_module2.output import compose
from pipeline.ar_module2.outcome import Module2FileGate
from pipeline.ar_independent_outcome import summarize_gate,range_execution_identity
from pipeline.verification.engineering.workspace import project_input_sha256

class RunnerDouble:
    def __init__(self, mode, identity): self.mode,self.identity=mode,identity
    def run(self, *,stage,args,cwd,timeout_seconds):
        status='COMPILE_FAILED' if stage=='COMPILE' and self.mode=='compile_fail' else 'PASS'
        if stage=='TARGET_TEST' and self.mode not in {'empty','compile_fail'}:
            owner,method=self.identity
            skipped='<skipped/>' if self.mode=='skipped' else ''
            folder=Path(cwd)/'target/surefire-reports';folder.mkdir(parents=True,exist_ok=True)
            (folder/'TEST-fixture.xml').write_text(f'<testsuite tests="1" failures="0" errors="0" skipped="{int(bool(skipped))}"><testcase classname="{owner}" name="{method}[1]">{skipped}</testcase></testsuite>',encoding='utf-8')
        return {'stage':stage,'status':status,'exit_code':0 if status=='PASS' else 1},'TEST DOUBLE',''

class DetectorDouble:
    called=False
    def run(self, **kwargs):
        self.called=True
        assert kwargs["detector_jar"] == ROOT/"tools/detector/TestSmellDetector.jar", "CONFIGURED_JAR_NOT_PROPAGATED"
        return {'status':'BLOCKED','failure_category':'DETECTOR_EXECUTION_BLOCKED'},'', 'TEST DOUBLE DETECTOR FAILURE',None

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT/'results/test_checks').mkdir(parents=True,exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(dir=ROOT/'results/test_checks')
        cls.base=Path(cls.temp.name);cls.source=cls.base/'source'
        cls.config=read_json(ROOT/'config/run_ar.json');cls.prov=read_json(ROOT/cls.config['provenance'])
        cls.record=read_json(ROOT/cls.config['detection'])[0]
        extract_project(cls.prov,cls.source)
        cls.instance,cls.binding=rebuild(cls.record,cls.config,cls.source,cls.prov)
        cls.context={'test_class_source':Path(cls.instance['test_file_path']).read_text(encoding='utf-8')}
        payload={'instance':cls.instance,'context':cls.context,'target_smells':['Assertion_Roulette']}
        def req(agent,payload):return make_agent_request(run_id='test-only',instance_id=cls.instance['instance_id'],agent=agent,agent_version='3.0',input_payload=payload)
        risk=RiskAssessmentAgent(enabled=True,mode='rule_only').execute(req('risk_assessment',payload))
        cls.request=req('refactoring',{**payload,'risk_result':risk,'pipeline_state':'RISK_APPROVED'})
        cls.contract=ExecutableContractBuilder().build(cls.request)
        from pipeline.agents.safety_contracts import grounded_message,apply_edits
        from pipeline.agents.java_assertions import find_assertion_calls
        cls.method=apply_edits(cls.instance['test_code'],[(c['end']-1,c['end']-1,', '+grounded_message(c)) for c in find_assertion_calls(cls.instance['test_code'])])
        cls.bundle=compose(cls.instance['test_file_path'],cls.instance,cls.method,'cli')
        cls.candidate=cls.base/'candidate.java';cls.candidate.write_bytes(cls.bundle.candidate_source.encode())
        cls.bundle=replace(cls.bundle,candidate_file_sha256=file_sha(cls.candidate))
        cls.fingerprint=project_input_sha256(cls.source,cls.candidate)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_rebuild_preserves_history_and_reselects(self):
        self.assertEqual(self.instance['source_record'],self.record)
        self.assertEqual(self.record['testContext']['selectedSmells'],[])
        self.assertIn('Assertion_Roulette',self.record['testContext']['ignoredSmells'])
        self.assertEqual(self.instance['selected_smells'],['Assertion_Roulette'])
        validate_contract(self.instance,'pipeline-instance')
    def test_wrong_path_and_method_rejected(self):
        for field,value in [('target_path','../outside.java'),('method_signature','other()')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                rebuild(self.record,{**self.config,field:value},self.source,self.prov)
    def test_changed_source_rejected(self):
        p=Path(self.instance['test_file_path']);before=p.read_bytes()
        try:
            p.write_bytes(before+b'\n// changed\n')
            with self.assertRaises(ValueError):rebuild(self.record,self.config,self.source,self.prov)
        finally:p.write_bytes(before)
    def test_fresh_test_fixture_and_assertion_mutation(self):
        valid=CandidateValidator().validate(raw_response=self.method,request=self.request,contract=self.contract,original_file=self.context['test_class_source'])
        self.assertTrue(valid['passed'],valid['checks'])
        bad=self.method.replace('isWhitespace ? 1 : 0','isWhitespace ? 2 : 0')
        invalid=CandidateValidator().validate(raw_response=bad,request=self.request,contract=self.contract,original_file=self.context['test_class_source'])
        self.assertFalse(invalid['passed'])
    def test_provider_failure_never_candidate(self):
        result=RefactoringAgent(enabled=True,provider=DeterministicTestRefactoringProvider(error=RuntimeError('test-only failure'))).execute(self.request)
        self.assertEqual(result['result']['decision'],'REFACTORING_FAILED')
        self.assertIsNone(result['result']['candidate']['method_code'])
    def test_engineering_failures_never_accepted(self):
        identity=range_execution_identity(self.instance['test_file_path'],self.instance)
        for mode in ['compile_fail','empty','skipped','detector_fail']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=self.base) as scratch,patch.object(tempfile,'tempdir',scratch):
                detector=DetectorDouble()
                executor=Module2FileGate(repo_root=ROOT/'framework',validation_runner=RunnerDouble(mode,identity),detector_runner=detector)
                executor.detector_jar=ROOT/'tools/detector/TestSmellDetector.jar'
                gate=executor.execute(run_id='test-only',project_name='cli',bundle=self.bundle,targets=[self.instance],before_detection_ref=ROOT/self.config['detection'],candidate_path=self.candidate)
                self.assertFalse(summarize_gate(self.instance,gate)['independent_accepted'])
                self.assertEqual(detector.called,mode=='detector_fail',gate)
                accepted,audit=final_audit(self.instance,self.contract,self.method,self.bundle,self.candidate,self.source,self.fingerprint,gate)
                self.assertFalse(accepted,audit)
    def test_serialized_success_without_current_chain_rejected(self):
        gate={'decision':'VALIDATION_PASSED','included_in_statistics':True,'source_unchanged':True,'candidate_unchanged':True,
              'hash_chain':{'status':'VERIFIED'},'stage_results':[{'stage':'COMPILE','status':'PASS'},{'stage':'TARGET_TEST','status':'PASS'}],
              'instance_revalidation_results':[{'instance_id':self.instance['instance_id'],'target_smell_revalidation_status':'TARGET_SMELL_REMOVED','final_acceptance_status':'ACCEPTED'}]}
        accepted,audit=final_audit(self.instance,self.contract,self.method,self.bundle,self.candidate,self.source,self.fingerprint,gate)
        self.assertFalse(accepted)
        self.assertIsNotNone(audit['current_hash_chain_error'])

if __name__=='__main__': unittest.main(verbosity=2)
