from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.contracts import make_agent_result, validate_contract

from pipeline.agents.base import AgentDisabledError, BaseAgent, load_validation_agent_config
from pipeline.agents.structure_validator import ValidationStructureValidator
from pipeline.agents.test_report_parser import parse_maven_test_reports, snapshot_maven_test_reports, execution_confirmed
from pipeline.agents.validation_contract import sha256_file
from pipeline.agents.refactoring_contract import sha256_text
from pipeline.agents.validation_runner import MavenValidationRunner
from pipeline.agents.validation_types import BLOCKED_STATUSES
from pipeline.agents.validation_workspace import IsolatedValidationWorkspace


def now() -> str: return datetime.now(timezone.utc).isoformat()


class BuildTestValidationAgent(BaseAgent):
    agent_name = "build_test_validation"
    agent_version = "4A.0"

    def __init__(self, *, enabled: bool | None = None, config_path=None, runner=None, structure_validator=None, workspace_factory=None):
        config=load_validation_agent_config(config_path); super().__init__(enabled=config["enabled"] if enabled is None else enabled)
        self.config=config; self.runner=runner or MavenValidationRunner(max_output_chars=config["max_output_chars"])
        self.structure_validator=structure_validator or ValidationStructureValidator(); self.workspace_factory=workspace_factory or IsolatedValidationWorkspace

    @property
    def input_contract(self): return ("refactoring/result.json", "refactoring/candidate_method.java", "refactoring/candidate_file.java", "source_project_root")
    @property
    def output_contract(self): return "validation/result.json"

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled: raise AgentDisabledError("Agent 'build_test_validation' is disabled and was not executed.")
        self.validate_input(request); payload=request["input"]
        if payload.get("validation_contract_version") != "4A.0": raise ValueError("Validation contract version 4A.0 is required.")
        if payload.get("pipeline_state")!="CANDIDATE_GENERATED": raise ValueError("Validation Agent requires CANDIDATE_GENERATED state.")
        self._validate_trusted_contract(payload)
        ref_result=payload.get("refactoring_result")
        if not isinstance(ref_result,dict): raise ValueError("A Phase 3 refactoring result is required.")
        validate_contract(ref_result,"agent-result")
        started,clock=now(),monotonic(); before_hash=sha256_file(Path(payload["source_test_file"])) if Path(payload["source_test_file"]).is_file() else None
        structure=self.structure_validator.validate(payload,ref_result)
        transient={}; stages=[]; completed=["STRUCTURE"]
        if not structure["passed"]:
            return self._finish(request,payload,ref_result,started,clock,"VALIDATION_FAILED","STRUCTURE_FAILED","INVALID_CANDIDATE_ARTIFACT",structure,stages,completed,before_hash,before_hash,{},transient,"Static candidate structure validation failed.")
        relative=Path(payload["source_test_relative_path"]); workspace=None; manifest={}
        try:
            from pipeline.validation_evidence import new_chain, observe
            chain=new_chain(payload['source_test_file'],payload['candidate_file_ref'],relative,[request['instance_id']])
            manager=self.workspace_factory(Path(payload["source_project_root"]),relative,Path(payload["candidate_file_ref"]),preserve=self.config["preserve_failed_workspace"])
            with manager as workspace:
                injected=workspace.root/relative
                injected_hash=sha256_text(injected.read_text(encoding="utf-8"))
                if injected_hash!=payload["candidate_file_hash"]: raise ValueError("Injected candidate hash mismatch.")
                manifest=workspace.manifest(); manifest["injected_candidate_hash"]=injected_hash
                manifest['hash_chain']=chain
                observe(chain,'injected_candidate_sha256',injected)
                compile_result,out,err=self.runner.run(stage="COMPILE",args=["-q","-DskipTests","test-compile"],cwd=workspace.root,timeout_seconds=payload["timeout_config"]["compile_seconds"])
                transient["compile_stdout.txt"]=out; transient["compile_stderr.txt"]=err; stages.append(compile_result); completed.append("COMPILE")
                observe(chain,'compiled_candidate_sha256',injected)
                if compile_result["status"]!="PASS":
                    decision="VALIDATION_BLOCKED" if compile_result["status"] in BLOCKED_STATUSES else "VALIDATION_FAILED"
                    return self._finish(request,payload,ref_result,started,clock,decision,compile_result["status"],compile_result["failure_category"],structure,stages,completed,before_hash,sha256_file(Path(payload["source_test_file"])),manifest,transient,"Compilation did not pass.")
                selector=payload["target_test_command"]["selector"]
                before_reports=snapshot_maven_test_reports(workspace.root)
                target_result,out,err=self.runner.run(stage="TARGET_TEST",args=["-q",f"-Dtest={selector}","test"],cwd=workspace.root,timeout_seconds=payload["timeout_config"]["target_test_seconds"])
                target_result["requested_scope"]="METHOD"; target_result["actual_scope"]="METHOD"; target_result["fallback_reason"]=None
                transient["target_test_stdout.txt"]=out; transient["target_test_stderr.txt"]=err; stages.append(target_result); completed.append("TARGET_TEST")
                observe(chain,'target_test_candidate_sha256',injected)
                summary=parse_maven_test_reports(workspace.root,payload["target_test_class"],payload["target_test_method"],before_reports=before_reports)
                report_payloads=summary.pop("_report_payloads", {})
                for index, (name, content) in enumerate(sorted(report_payloads.items()), 1):
                    artifact_name=f"test_reports/report-{index:03d}.xml"
                    transient[artifact_name]=content
                    for report in summary["reports"]:
                        if report["path"]==name: report["artifact_ref"]="validation/"+artifact_name
                fallback_needed = not summary['target_method_observed'] and summary['execution_status'] not in ('TARGET_FAILED','TEST_RUN_FAILED')
                if fallback_needed and self.config["allow_class_level_test_fallback"]:
                    target_result["stage"]="TARGET_TEST_METHOD"
                    target_result["requested_scope"]="METHOD"; target_result["actual_scope"]="METHOD"
                    before_reports=snapshot_maven_test_reports(workspace.root)
                    fallback,out,err=self.runner.run(stage="TARGET_TEST",args=["-q",f"-Dtest={payload['target_test_class']}","test"],cwd=workspace.root,timeout_seconds=payload["timeout_config"]["target_test_seconds"])
                    fallback["requested_scope"]="METHOD"; fallback["actual_scope"]="CLASS"; fallback["fallback_reason"]="Method-level test execution was not observed."
                    transient["target_test_class_stdout.txt"]=out; transient["target_test_class_stderr.txt"]=err; stages.append(fallback); target_result=fallback
                    observe(chain,'fallback_test_candidate_sha256',injected)
                    summary=parse_maven_test_reports(workspace.root,payload["target_test_class"],payload["target_test_method"],before_reports=before_reports)
                    report_payloads=summary.pop("_report_payloads", {})
                    for index,(name,content) in enumerate(sorted(report_payloads.items()),1): transient[f"test_reports/report-{index:03d}.xml"]=content
                if target_result["status"]=="PASS" and not execution_confirmed(summary):
                    target_result["status"]="TARGET_TEST_FAILED" if summary['execution_status'] in ('TARGET_FAILED','TEST_RUN_FAILED') else "TARGET_TEST_NOT_EXECUTED"
                    target_result["failure_category"]=summary["execution_status"]
                if target_result["status"]!="PASS":
                    decision="VALIDATION_BLOCKED" if target_result["status"] in BLOCKED_STATUSES else "VALIDATION_FAILED"
                    return self._finish(request,payload,ref_result,started,clock,decision,target_result["status"],target_result["failure_category"],structure,stages,completed,before_hash,sha256_file(Path(payload["source_test_file"])),manifest,transient,"Target test did not pass or was not observed.",summary)
                module_result=_not_configured("MODULE_TEST")
                if "MODULE_TEST" in payload["validation_levels"]:
                    module_result,out,err=self.runner.run(stage="MODULE_TEST",args=["-q","test"],cwd=workspace.root,timeout_seconds=payload["timeout_config"]["module_test_seconds"])
                    transient["module_test_stdout.txt"]=out; transient["module_test_stderr.txt"]=err; stages.append(module_result); completed.append("MODULE_TEST")
                    observe(chain,'module_test_candidate_sha256',injected)
                    if module_result["status"]!="PASS":
                        decision="VALIDATION_BLOCKED" if module_result["status"] in BLOCKED_STATUSES else "VALIDATION_FAILED"
                        return self._finish(request,payload,ref_result,started,clock,decision,module_result["status"],module_result["failure_category"],structure,stages,completed,before_hash,sha256_file(Path(payload["source_test_file"])),manifest,transient,"Module tests did not pass.",summary,module_result)
                observe(chain,'validated_candidate_sha256',injected)
                chain['status']='VERIFIED'
                from pipeline.validation_evidence import require_chain
                require_chain(chain,source=payload['source_test_file'],candidate=payload['candidate_file_ref'],relative=relative,instance_id=request['instance_id'])
                return self._finish(request,payload,ref_result,started,clock,"VALIDATION_PASSED","VALIDATION_PASSED",None,structure,stages,completed,before_hash,sha256_file(Path(payload["source_test_file"])),manifest,transient,"Configured structure, compile, and target-test gates passed.",summary,module_result)
        except Exception as exc:
            after=sha256_file(Path(payload["source_test_file"])) if Path(payload["source_test_file"]).is_file() else None
            category=str(exc).split(':',1)[0] if str(exc).startswith('MNT_') else 'WORKSPACE_PREPARATION_ERROR'
            return self._finish(request,payload,ref_result,started,clock,"VALIDATION_BLOCKED","ARTIFACT_BLOCKED",category,structure,stages,completed,before_hash,after,manifest,transient,f"Isolation or validation execution failed: {type(exc).__name__}: {exc}")

    def _finish(self,request,payload,ref_result,started,clock,decision,status,category,structure,stages,completed,before,after,manifest,transient,reason,summary=None,module_result=None):
        source_unchanged=before is not None and before==after
        if not source_unchanged and decision=="VALIDATION_PASSED": decision,status,category="VALIDATION_FAILED","STRUCTURE_FAILED","SOURCE_HASH_MISMATCH"
        result={"decision":decision,"status":status,"run_id":request["run_id"],"instance_id":request["instance_id"],"project_id":payload["project_id"],"agent_name":self.agent_name,"agent_version":self.agent_version,"validation_mode":payload["validation_mode"],
                "source":{"path":payload["source_test_file"],"hash_before":before,"hash_after":after,"unchanged":source_unchanged},"candidate":{"file_ref":payload["candidate_file_ref"],"file_hash":payload["candidate_file_hash"],"method_ref":payload["candidate_method_ref"],"method_hash":payload["candidate_method_hash"]},
                "isolation":manifest,"requested_levels":payload["validation_levels"],"completed_levels":completed,"stage_results":stages,"structure_checks":structure["checks"],
                "compile_result":next((x for x in stages if x["stage"]=="COMPILE"),_not_configured("COMPILE")),"target_test_result":next((x for x in stages if x["stage"]=="TARGET_TEST"),_not_configured("TARGET_TEST")),"module_test_result":module_result or _not_configured("MODULE_TEST"),
                "test_summary":summary or _empty_summary(),"target_test_observed":bool((summary or {}).get("target_method_observed")),"baseline_comparison":{"available":False,"reason":"No reliable Phase 3 baseline test count is available."},
                "failure_category":category,"failure_stage":next((x["stage"] for x in reversed(stages) if x["status"]!="PASS"),"STRUCTURE" if not structure["passed"] else None),"reason":reason,"next_action":"STOP","validation_errors":[],"started_at":started,"completed_at":now(),"duration_ms":int((monotonic()-clock)*1000),
                "environment_summary":{"os":platform.system(),"os_release":platform.release(),"python":platform.python_version(),"build_system":"maven"},"config_refs":payload["config_refs"],"transient_artifacts":transient}
        result['hash_chain']=manifest.get('hash_chain')
        envelope=make_agent_result(run_id=request["run_id"],instance_id=request["instance_id"],agent=self.agent_name,agent_version=self.agent_version,status="SUCCESS" if decision=="VALIDATION_PASSED" else "FAILED",result=result)
        validate_contract(envelope,"agent-result"); return envelope

    @staticmethod
    def _validate_trusted_contract(payload):
        from pipeline.agents.safety_contracts import current_rule_version
        contract = ((payload.get('refactoring_result') or {}).get('result') or {}).get('risk_contract') or {}
        if contract.get('risk_rule_version') != current_rule_version(contract.get('smell')):
            raise ValueError('RULE_VERSION_REAUDIT_REQUIRED')
        if payload.get("build_system")!="maven" or (payload.get("build_command") or {}).get("template_id")!="maven-test-compile-v1": raise ValueError("Only the trusted Maven compile template is allowed.")
        from pipeline.agents.method_location import test_execution_identity
        owner,method=test_execution_identity(Path(payload['source_test_file']).read_text(encoding='utf8'),contract['safety_instance'])
        if (payload.get('target_test_class'),payload.get('target_test_method'))!=(owner,method):
            raise ValueError('TARGET_TEST_IDENTITY_MISMATCH')
        expected=f"{payload.get('target_test_class')}#{payload.get('target_test_method')}"
        if (payload.get("target_test_command") or {}).get("template_id")!="maven-surefire-method-v1" or payload["target_test_command"].get("selector")!=expected: raise ValueError("Target test selector does not match trusted metadata.")
        if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*#[A-Za-z_$][\w$]*",expected): raise ValueError("Unsafe target test selector.")
        project=Path(payload["source_project_root"]).resolve(); source=Path(payload["source_test_file"]).resolve()
        if not source.is_relative_to(project) or Path(payload["source_test_relative_path"]).is_absolute() or ".." in Path(payload["source_test_relative_path"]).parts: raise ValueError("Source test path escapes the configured project root.")
        artifact_root=Path(payload["refactoring_result_ref"]).resolve().parent
        for key in ("candidate_method_ref","candidate_file_ref"):
            if not Path(payload[key]).resolve().is_relative_to(artifact_root): raise ValueError("Candidate artifact path escapes the Phase 3 refactoring directory.")


def _not_configured(stage): return {"stage":stage,"status":"NOT_CONFIGURED","command_ref":None,"sanitized_command":[],"started_at":None,"completed_at":None,"duration_ms":0,"exit_code":None,"timed_out":False,"summary":"Stage was not configured or not reached.","failure_category":None,"artifacts":[]}
def _empty_summary(): return {"tests":0,"failures":0,"errors":0,"skipped":0,"elapsed_seconds":0.0,"target_class_observed":False,"target_method_observed":False,"invocations":0,"reports":[],"parse_errors":[],"report_count":0}
