from __future__ import annotations

import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.contracts import make_agent_result, validate_contract
from pipeline.agents.base import AgentDisabledError, BaseAgent, load_revalidation_agent_config
from pipeline.agents.detection_result_adapter import load_schema_v41_records, normalized_record, load_phase1_detection_output
from pipeline.agents.detector_runner import Phase1DetectorRunner
from pipeline.agents.revalidation_policy import evaluate_policy
from pipeline.agents.smell_comparator import compare, match_record
from pipeline.agents.test_method_counter import compare_owned_test_files
from pipeline.agents.validation_contract import sha256_file
from pipeline.agents.refactoring_contract import sha256_text
from pipeline.agents.revalidation_types import REQUIRED_CHECKS, SUPPORTED_SMELLS, required_checks_for
from pipeline.agents.validation_workspace import IsolatedValidationWorkspace
from pipeline.agents.structure_validator import ValidationStructureValidator


class SmellRevalidationAgent(BaseAgent):
    agent_name = "smell_revalidation"
    agent_version = "5.0"

    def __init__(self, *, enabled: bool | None = None, detector_runner: Phase1DetectorRunner | None = None, config_path=None):
        config = load_revalidation_agent_config(config_path)
        super().__init__(enabled=config["enabled"] if enabled is None else enabled)
        self.config = config
        self.detector_runner = detector_runner or Phase1DetectorRunner(max_output_chars=config["max_output_chars"])

    @property
    def input_contract(self) -> tuple[str, ...]: return ("input/instance.json", "refactoring/result.json", "validation/result.json", "before schema 4.1 detection")
    @property
    def output_contract(self) -> str: return "revalidation/result.json"

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        progress={}
        try:
            return self._execute(request,progress)
        except AgentDisabledError:
            raise
        except Exception as exc:
            if not progress: raise
            p=request['input']; transient=progress['transient']
            transient['execution_error.txt']=f'{type(exc).__name__}: {exc}'
            category='TARGET_METHOD_ATTRIBUTION_UNCERTAIN' if 'TARGET_METHOD_ATTRIBUTION_UNCERTAIN' in str(exc) else 'REVALIDATION_EXECUTION_BLOCKED'
            result=self._result(p,'REVALIDATION_BLOCKED','REVALIDATION_BLOCKED',
                category,progress.get('detector',{}),
                progress.get('before',{}),progress.get('after',{}),progress.get('comparison',{}),
                progress.get('checks',[]),progress.get('integrity',{}),progress.get('manifest',{}),
                progress.get('structure',[]),progress['started'],progress['clock'],transient)
            return make_agent_result(run_id=request['run_id'],instance_id=request['instance_id'],
                agent=self.agent_name,agent_version=self.agent_version,status='FAILED',result=result,
                errors=[{'code':category,'message':transient['execution_error.txt']}])

    def _execute(self, request: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled: raise AgentDisabledError("Agent 'smell_revalidation' is disabled and was not executed.")
        self.validate_input(request); p=request["input"]
        if p.get("pipeline_state") != "VALIDATION_PASSED": raise ValueError("Only VALIDATION_PASSED instances may be revalidated.")
        chain=self._validate_trusted_contract(request)
        started=datetime.now(timezone.utc).isoformat(); clock=monotonic(); transient={}
        source=Path(p["source_test_file"]); candidate=Path(p["candidate_file"]); candidate_method=Path(p["candidate_method"])
        before_source_hash=sha256_file(source); before_candidate_hash=sha256_file(candidate)
        progress.update(started=started,clock=clock,transient=transient)
        before_records=[normalized_record(x) for x in load_schema_v41_records(Path(p["before_detection_ref"]))]
        before_match=match_record(before_records,p)
        from .method_identity import identify
        location=identify(before_match['test_code'],p['target_class'],p['target_package'],candidate=source.read_text(encoding='utf-8-sig'))
        if location is None: raise ValueError('TARGET_METHOD_ATTRIBUTION_UNCERTAIN:BEFORE_SOURCE_BINDING')
        before_match['method_identity_proof']=location[1]
        progress['before']={'instance_match':True,'target_smell_present':p['target_smell'] in before_match['smells'],'smells':before_match['smells'],'source_ref':p['before_detection_ref'],'method_identity_proof':before_match['method_identity_proof']}
        from functools import partial
        blocked=partial(self._blocked,progress=progress)
        method_inventory = compare_owned_test_files(source, candidate, p['target_package'], p['target_class'],
            source_sha256=before_source_hash, candidate_sha256=before_candidate_hash)
        if method_inventory['status'] == 'UNKNOWN':
            return blocked(request,p,started,clock,method_inventory['reason_code'],[],before_source_hash,before_candidate_hash,transient)
        before_count = method_inventory['before']['test_method_count']
        validation_payload={"pipeline_state":"CANDIDATE_GENERATED","source_test_file":str(source),"candidate_file_ref":str(candidate),
            "candidate_method_ref":str(candidate_method),"source_test_file_hash":p["source_file_hash"],"candidate_file_hash":p["candidate_file_hash"],
            "candidate_method_hash":p["candidate_method_hash"],"method_signature":p["method_signature"],"target_test_method":p["target_method"],
            "original_method_source":p["original_method_source"],"original_method_hash":p["original_method_hash"]}
        structure=ValidationStructureValidator().validate(validation_payload,p["refactoring_result"])
        progress['structure']=structure['checks']
        if not structure["passed"]:
            return blocked(request,p,started,clock,"CANDIDATE_SOURCE_MISMATCH",structure["checks"],before_source_hash,before_candidate_hash,transient)
        run_id=f"{request['run_id']}-detector"; project_name="phase5_"+request["instance_id"].split(":")[-1][:12]
        manifest={}
        with IsolatedValidationWorkspace(Path(p["source_project_root"]),Path(p["source_relative_path"]),candidate,preserve=self.config["preserve_failed_workspace"]) as workspace:
            manifest=workspace.manifest(); injected=workspace.root/Path(p["source_relative_path"])
            manifest["injected_candidate_hash"]=sha256_file(injected)
            if chain is not None:
                from pipeline.validation_evidence import observe
                manifest['hash_chain']=chain
                try:
                    observe(chain,'revalidation_injected_candidate_sha256',injected)
                    prepare=getattr(self.detector_runner,'prepare_input',Phase1DetectorRunner.prepare_input)
                    detector_input=Path(prepare(workspace=workspace.root,source_relative_path=Path(p['source_relative_path'])))
                    if detector_input.resolve()!=injected.resolve():
                        raise ValueError('MNT_DETECTOR_FILE_IDENTITY_MISMATCH')
                    observe(chain,'detector_input_sha256',detector_input)
                except (ValueError,OSError) as exc:
                    return blocked(request,p,started,clock,str(exc),structure['checks'],before_source_hash,before_candidate_hash,transient,manifest=manifest)
            detector,stdout,stderr,output_dir=self.detector_runner.run(workspace=workspace.root,run_id=run_id,project_name=project_name,
                detector_jar=Path(p["detector_jar"]),timeout_seconds=p["timeout_config"]["detector_seconds"])
            progress.update(detector=detector,manifest=manifest)
            transient["detector_stdout.txt"]=stdout; transient["detector_stderr.txt"]=stderr
            if chain is not None:
                try:
                    observe(chain,'post_detector_candidate_sha256',detector_input)
                    from pipeline.validation_evidence import require_chain
                    require_chain(chain,source=source,candidate=candidate,relative=p['source_relative_path'],instance_id=request['instance_id'],detector=True)
                    if p['target_smell']=='Magic_Number_Test':
                        from pipeline.agents.method_location import owned_method
                        full,method=owned_method(detector_input.read_text(encoding='utf8'),p['refactoring_result']['result']['risk_contract']['safety_instance'])
                        verified_method=full.text(method)
                except (ValueError,OSError) as exc:
                    return blocked(request,p,started,clock,str(exc),structure['checks'],before_source_hash,before_candidate_hash,transient,detector,manifest)
            if detector["status"] != "PASS" or output_dir is None:
                return blocked(request,p,started,clock,detector.get("failure_category") or "DETECTOR_EXECUTION_BLOCKED",structure["checks"],before_source_hash,before_candidate_hash,transient,detector,manifest)
            try: after_records,result_csv=load_phase1_detection_output(output_dir,p,execution_evidence=detector)
            except ValueError as exc:
                transient["raw_detection/identity_error.txt"]=str(exc)
                category="TARGET_METHOD_ATTRIBUTION_UNCERTAIN" if 'TARGET_METHOD_ATTRIBUTION_UNCERTAIN' in str(exc) else "DETECTOR_OUTPUT_INVALID"
                return blocked(request,p,started,clock,category,structure["checks"],before_source_hash,before_candidate_hash,transient,detector,manifest)
            raw=json.dumps(after_records,ensure_ascii=False,indent=2,default=str); transient["raw_detection/target_class.json"]=raw
            try: after_match=match_record(after_records,p)
            except ValueError:
                return blocked(request,p,started,clock,"TARGET_INSTANCE_NOT_OBSERVED",structure["checks"],before_source_hash,before_candidate_hash,transient,detector,manifest)
            progress['after']={'instance_match':True,'target_instance_observed':True,'target_smell_present':p['target_smell'] in after_match['smells'],'smells':after_match['smells']}
            after_count = method_inventory['after']['test_method_count']
            injected_after=sha256_file(injected)
            transient["raw_detection/result.csv"]=result_csv.read_text(encoding="utf-8-sig")
            transient["raw_detection/result_failures.csv"]=result_csv.with_name(result_csv.stem+"_failures.csv").read_text(encoding="utf-8-sig")
            input_csv=Path(detector["input_csv_ref"])
            transient["raw_detection/input.csv"]=input_csv.read_text(encoding="utf-8-sig")
        source_after=sha256_file(source); candidate_after=sha256_file(candidate)
        integrity={"source_hash_before":before_source_hash,"source_hash_after":source_after,"unchanged":before_source_hash==source_after,
                   "candidate_hash_before":before_candidate_hash,"candidate_hash_after":candidate_after,"candidate_unchanged":before_candidate_hash==candidate_after,
                   "injected_hash_after_detection":injected_after}
        if chain is not None: integrity['complete_file_chain_verified']=True
        progress['integrity']=integrity
        comparison=compare(before_match,after_match,p,method_counts=(before_count,after_count),non_target_changed=not structure["passed"])
        progress['comparison']=comparison
        if method_inventory is not None:
            comparison['method_inventory'] = method_inventory
            comparison['test_method_count_changed'] = method_inventory['test_method_set_changed']
            comparison['non_target_region_changed'] |= (
                not method_inventory['passed'] and not method_inventory['test_method_set_changed'])
            comparison['method_count_reason_code'] = method_inventory['reason_code']
        if p['target_smell'] == 'Magic_Number_Test':
            from pipeline.agents.safety_contracts import numeric_effect
            numeric=numeric_effect(p['refactoring_result']['result']['risk_contract'],
                verified_method, source.read_text(encoding='utf-8'),
                detector_positive='Magic_Number_Test' in after_match['smells'])
            comparison['numeric_target_effects']=numeric
            comparison['target_smell_removed']=comparison['target_smell_removed'] and numeric['all_targets_processed']
        before={"instance_match":True,"target_smell_present":p["target_smell"] in before_match["smells"],"smells":before_match["smells"],
                "test_method_count":before_count,"detector_name":p["detector_name"],"detector_version":p["detector_version"],"config_hash":p["detector_config_hash"],"source_ref":p["before_detection_ref"],"method_identity_proof":before_match['method_identity_proof']}
        after={"instance_match":True,"target_instance_observed":True,"target_smell_present":p["target_smell"] in after_match["smells"],"smells":after_match["smells"],
               "test_method_count":after_count,"detector_name":p["detector_name"],"detector_version":p["detector_version"],"config_hash":p["detector_config_hash"],"raw_result_ref":"revalidation/raw_detection/target_class.json","method_identity_proof":after_match['method_identity_proof']}
        decision,status,category,checks=evaluate_policy(payload=p,detector=detector,before=before,after=after,comparison=comparison,source_integrity=integrity,structure_passed=structure["passed"])
        progress.update(before=before,after=after,checks=checks)
        result=self._result(p,decision,status,category,detector,before,after,comparison,checks,integrity,manifest,structure["checks"],started,clock,transient)
        return make_agent_result(run_id=request["run_id"],instance_id=request["instance_id"],agent=self.agent_name,agent_version=self.agent_version,
                                 status="SUCCESS" if decision=="REVALIDATION_PASSED" else "FAILED",result=result)

    def _validate_trusted_contract(self, request: dict[str, Any]) -> dict[str, Any] | None:
        p = request["input"]
        if p.get("revalidation_contract_version") != "5.0": raise ValueError("Revalidation contract version 5.0 is required.")
        if tuple(p.get("requested_checks") or ()) != required_checks_for(p.get('target_smell')): raise ValueError("Mandatory Phase 5 checks cannot be changed.")
        if p.get("target_smell") not in SUPPORTED_SMELLS: raise ValueError("Unsupported Phase 5 target smell.")
        refactoring=p.get("refactoring_result"); validation=p.get("validation_result")
        if not isinstance(refactoring,dict) or not isinstance(validation,dict): raise ValueError("Validated Phase 3 and Phase 4A results are required.")
        validate_contract(refactoring,"agent-result"); validate_contract(validation,"agent-result")
        from pipeline.agents.safety_contracts import current_rule_version
        if (refactoring['result'].get('risk_contract') or {}).get('risk_rule_version') != current_rule_version(p.get('target_smell')):
            raise ValueError('RULE_VERSION_REAUDIT_REQUIRED')
        if refactoring.get("instance_id") != request["instance_id"] or validation.get("instance_id") != request["instance_id"]: raise ValueError("Upstream instance identity does not match the request.")
        if refactoring["result"].get("decision") != "CANDIDATE_GENERATED": raise ValueError("Phase 3 did not generate an approved candidate.")
        if validation["result"].get("decision") != "VALIDATION_PASSED" or not validation["result"].get("target_test_observed"): raise ValueError("Phase 4A validation gates did not pass.")
        project=Path(p["source_project_root"]).resolve(); source=Path(p["source_test_file"]).resolve()
        relative=Path(p["source_relative_path"])
        if not source.is_relative_to(project) or relative.is_absolute() or ".." in relative.parts or (project/relative).resolve()!=source:
            raise ValueError("Source path escapes or disagrees with the configured project root.")
        artifact_root=Path(p["phase3_result_ref"]).resolve().parent
        for key in ("candidate_file","candidate_method"):
            candidate=Path(p[key]).resolve()
            if not candidate.is_relative_to(artifact_root): raise ValueError("Candidate artifact escapes the Phase 3 refactoring directory.")
        if sha256_text(Path(p["candidate_file"]).read_text(encoding="utf-8")) != p["candidate_file_hash"]: raise ValueError("Candidate file hash mismatch.")
        if sha256_text(Path(p["candidate_method"]).read_text(encoding="utf-8")) != p["candidate_method_hash"]: raise ValueError("Candidate method hash mismatch.")
        repo=Path(__file__).resolve().parents[3]; detector=(repo/"detection"/"junit5"/"TestSmellDetector.jar").resolve(); detector_config=repo/"scripts"/"detect_only.ps1"
        if Path(p["detector_jar"]).resolve()!=detector or p["detector_version"]!="sha256:"+sha256_file(detector): raise ValueError("Detector identity or version does not match Phase 1.")
        import hashlib
        if p["detector_config_hash"]!=hashlib.sha256(detector_config.read_bytes()).hexdigest(): raise ValueError("Detector configuration hash does not match Phase 1.")
        from pipeline.validation_evidence import engineering_chain
        return engineering_chain(validation['result'],refactoring['result'],source=source,candidate=Path(p['candidate_file']),
                                 relative=p['source_relative_path'],instance_id=request['instance_id'])

    def _blocked(self,request,p,started,clock,category,structure,source_hash,candidate_hash,transient,detector=None,manifest=None,*,progress=None):
        integrity={"source_hash_before":source_hash,"source_hash_after":sha256_file(Path(p["source_test_file"])),"unchanged":source_hash==sha256_file(Path(p["source_test_file"])),
                   "candidate_hash_before":candidate_hash,"candidate_hash_after":sha256_file(Path(p["candidate_file"])),"candidate_unchanged":candidate_hash==sha256_file(Path(p["candidate_file"]))}
        observed=progress or {}
        result=self._result(p,"REVALIDATION_BLOCKED","REVALIDATION_BLOCKED",category,detector or {},observed.get('before',{}),observed.get('after',{}),observed.get('comparison',{}),observed.get('checks',[]),integrity,manifest or {},structure,started,clock,transient)
        return make_agent_result(run_id=request["run_id"],instance_id=request["instance_id"],agent=self.agent_name,agent_version=self.agent_version,status="FAILED",result=result,
                                 errors=[{"code":category,"message":"Smell revalidation was safely blocked."}])

    @staticmethod
    def _result(p,decision,status,category,detector,before,after,comparison,checks,integrity,manifest,structure,started,clock,transient):
        partial = (p.get('target_smell') == 'Magic_Number_Test' and decision == 'REVALIDATION_PASSED'
                   and (comparison.get('numeric_target_effects') or {}).get('all_targets_processed') is False)
        return {"decision":decision,"status":status,"effect_status":decision,"report_only":False,"failure_stage":"REVALIDATION" if category else None,"failure_category":category,
                **({'mnt_completion_status':'PARTIAL' if partial else 'COMPLETE' if decision=='REVALIDATION_PASSED' else 'NOT_ACCEPTED'}
                   if p.get('target_smell') == 'Magic_Number_Test' else {}),
                "reason":"Smell-effect comparison completed." if decision=="REVALIDATION_PASSED" else "Smell-effect comparison was negative or inconclusive; build/test validity is unchanged.",
                "detector_execution":detector,"before":before,"after":after,"comparison":comparison,"policy_checks":checks,
                "source_integrity":integrity,"workspace":manifest,"structure_checks":structure,"controlled_smells":p["controlled_smells"],
                'hash_chain':manifest.get('hash_chain'),
                "config_refs":p["config_refs"],"started_at":started,"completed_at":datetime.now(timezone.utc).isoformat(),
                "duration_ms":int((monotonic()-clock)*1000),"environment_summary":{"os":platform.system(),"python":platform.python_version()},
                "transient_artifacts":transient,"next_action":"ACCEPT_PARTIAL" if partial else "ACCEPT" if decision=="REVALIDATION_PASSED" else "STOP"}
