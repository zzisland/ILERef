from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.agents.base import load_revalidation_agent_config, load_validation_agent_config
from pipeline.agents.detection_result_adapter import load_phase1_detection_output, load_schema_v41_records, normalized_record
from pipeline.agents.detector_runner import Phase1DetectorRunner
from pipeline.agents.refactoring_contract import sha256_text
from pipeline.agents.test_report_parser import parse_maven_test_reports, snapshot_maven_test_reports, execution_confirmed
from pipeline.agents.validation_contract import nearest_maven_root, sha256_file
from pipeline.agents.validation_runner import MavenValidationRunner
from pipeline.agents.validation_types import BLOCKED_STATUSES
from pipeline.agents.validation_workspace import IsolatedValidationWorkspace, project_input_sha256
from pipeline.agents.risk_evidence import strip_comments_and_literals
from pipeline.agents.instance_revalidation import canonicalize_records, revalidate_instances
from pipeline.file_candidate import FileCandidateBundle
from pipeline.hashing import HASH_CHAIN_VERSION, sha256_file_bytes


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileGateExecutor:
    """Run Phase 4A and Phase 5 once for one fully assembled Java test file."""

    version = "file-gates-2.2"

    def __init__(self, *, repo_root: Path, config_path: Path | None = None,
                 validation_runner=None, detector_runner=None, workspace_factory=None):
        self.repo_root=Path(repo_root).resolve(); self.validation_config=load_validation_agent_config(config_path)
        self.revalidation_config=load_revalidation_agent_config(config_path)
        self.validation_runner=validation_runner or MavenValidationRunner(max_output_chars=self.validation_config["max_output_chars"])
        self.detector_runner=detector_runner or Phase1DetectorRunner(repo_root=self.repo_root,max_output_chars=self.revalidation_config["max_output_chars"])
        self.workspace_factory=workspace_factory or IsolatedValidationWorkspace

    def execute(self, *, run_id: str, project_name: str, bundle: FileCandidateBundle,
                targets: list[dict[str, Any]], before_detection_ref: Path,
                candidate_path: Path) -> dict[str, Any]:
        started,clock=_utcnow(),monotonic(); source=bundle.source_file
        source_before=sha256_file(source); candidate_before=sha256_file(candidate_path)
        expected_candidate=getattr(bundle,"candidate_file_sha256",None)
        project_root=nearest_maven_root(source); relative=source.relative_to(project_root)
        target_class=".".join(filter(None,[targets[0].get("test_package_name"),Path(targets[0]["test_filename"]).stem]))
        baseline_records=[normalized_record(x) for x in load_schema_v41_records(Path(before_detection_ref))]
        class_name=Path(targets[0]["test_filename"]).stem; package=targets[0].get("test_package_name") or ""
        baseline_class=[x for x in baseline_records if x["class"]==class_name and x["package"]==package]
        source_relative_path=str(relative).replace("\\","/")
        module_path=str(targets[0].get("module_path") or "")
        baseline_class=canonicalize_records(
            baseline_class, project_id=project_name, module_path=module_path,
            package_name=package, source_relative_path=source_relative_path, class_name=class_name,
        )
        artifacts: dict[str,str]={}; stages=[]; manifest={}
        project_inputs=project_input_sha256(project_root,candidate_path)
        structure_checks=self._structure_checks(bundle,candidate_path,targets)
        if not expected_candidate:
            return self._finish(started,clock,"VALIDATION_BLOCKED","HASH_NOT_VERIFIED",bundle,targets,source_before,candidate_before,
                                structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                                hash_status_override="HASH_NOT_VERIFIED",mismatch_stage="PRE_DETECTOR_MANIFEST")
        if candidate_before!=expected_candidate:
            return self._finish(started,clock,"VALIDATION_BLOCKED","HASH_MISMATCH",bundle,targets,source_before,candidate_before,
                                structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                                mismatch_stage="VALIDATION_INPUT",expected_sha256=expected_candidate,actual_sha256=candidate_before)
        if not all(x["status"]=="PASS" for x in structure_checks):
            return self._finish(started,clock,"VALIDATION_FAILED","STRUCTURE_FAILED",bundle,targets,source_before,candidate_before,
                                structure_checks,stages,manifest,artifacts,candidate_path=candidate_path)
        try:
            identities=self._execution_identities(source, targets)
        except ValueError as exc:
            artifacts['target_mapping_error.txt']=str(exc)
            return self._finish(started,clock,'VALIDATION_BLOCKED','TEST_SELECTION_BLOCKED',bundle,targets,source_before,candidate_before,
                                structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                                test_summary={'execution_status':str(exc)})
        with self.workspace_factory(project_root,relative,candidate_path,preserve=self.validation_config["preserve_failed_workspace"] or self.revalidation_config["preserve_failed_workspace"]) as workspace:
            from pipeline.verification.engineering.file_gate import execute_engineering, EngineeringCompletion
            engineering = execute_engineering(self,
                run_id=run_id,
                project_name=project_name,
                bundle=bundle,
                targets=targets,
                candidate_path=candidate_path,
                started=started,
                clock=clock,
                source=source,
                source_before=source_before,
                candidate_before=candidate_before,
                expected_candidate=expected_candidate,
                relative=relative,
                target_class=target_class,
                baseline_class=baseline_class,
                source_relative_path=source_relative_path,
                module_path=module_path,
                artifacts=artifacts,
                stages=stages,
                manifest=manifest,
                structure_checks=structure_checks,
                identities=identities,
                workspace=workspace,
                package=package,
                class_name=class_name,
                project_inputs=project_inputs,
            )
            if not isinstance(engineering, EngineeringCompletion):
                return engineering
            from pipeline.verification.outcome.file_gate import execute_outcome
            return execute_outcome(self,
                run_id=run_id,
                project_name=project_name,
                bundle=bundle,
                targets=targets,
                candidate_path=candidate_path,
                started=started,
                clock=clock,
                source=source,
                source_before=source_before,
                candidate_before=candidate_before,
                expected_candidate=expected_candidate,
                relative=relative,
                target_class=target_class,
                baseline_class=baseline_class,
                source_relative_path=source_relative_path,
                module_path=module_path,
                artifacts=artifacts,
                stages=stages,
                manifest=engineering.manifest,
                structure_checks=structure_checks,
                identities=identities,
                workspace=workspace,
                package=package,
                class_name=class_name,
                project_inputs=project_inputs,
                injected=engineering.injected,
                injected_hash=engineering.injected_hash,
                validated_hash=engineering.validated_hash,
                tests=engineering.tests,
                class_observed=engineering.class_observed,
                method_summaries=engineering.method_summaries,
            )

    @staticmethod
    def _execution_identities(source, targets):
        """Default formal identity policy; separate outcome namespaces may override."""
        from pipeline.experiment_infrastructure import verified_execution_identity
        return {t['instance_id']: verified_execution_identity(source, t) for t in targets}

    @staticmethod
    def _numeric_targets(bundle, targets):
        from pipeline.verification.outcome.numeric_targets import numeric_targets
        return numeric_targets(bundle, targets)

    @staticmethod
    def _pre_detector_hash_failure(expected, validation_input, injected, validated, current):
        from pipeline.verification.outcome.pre_detector_hash_failure import pre_detector_hash_failure
        return pre_detector_hash_failure(expected, validation_input, injected, validated, current)

    @staticmethod
    def _detector_failure_status(detector):
        from pipeline.verification.outcome.detector_failure_status import detector_failure_status
        return detector_failure_status(detector)

    @staticmethod
    def _capture_raw_detection(artifacts, output_dir):
        from pipeline.verification.outcome.capture_raw_detection import capture_raw_detection
        return capture_raw_detection(artifacts, output_dir)

    @staticmethod
    def _smell_counts(before_records, after_records):
        from pipeline.verification.outcome.smell_counts import smell_counts
        return smell_counts(before_records, after_records)

    @staticmethod
    def _structure_checks(bundle, candidate_path, targets):
        from pipeline.verification.reintegration.structure_checks import structure_checks
        return structure_checks(bundle, candidate_path, targets)

    def _finish(self,started,clock,decision,status,bundle,targets,source_before,candidate_before,checks,stages,manifest,artifacts,
                detector=None,test_summary=None,smell_count_status=None,smell_comparison=None,target_smell_comparison=None,
                candidate_path=None,injected_candidate_sha256=None,validated_candidate_sha256=None,
                mismatch_stage=None,expected_sha256=None,actual_sha256=None,detector_input_sha256=None,
                post_detector_candidate_sha256=None,hash_status_override=None,preserve_validation_on_hash_error=False,
                instance_revalidation_results=None):
        source_after=sha256_file(bundle.source_file)
        candidate_after=sha256_file(Path(candidate_path)) if candidate_path and Path(candidate_path).is_file() else None
        expected_candidate=getattr(bundle,"candidate_file_sha256",None)
        if candidate_path and not mismatch_stage and candidate_after != candidate_before:
            mismatch_stage="CANDIDATE_ARTIFACT_POST_VALIDATION"
            expected_sha256=candidate_before
            actual_sha256=candidate_after
        mismatch_stage=mismatch_stage or ("VALIDATION_INPUT" if candidate_before!=expected_candidate else None)
        expected_sha256=expected_sha256 or (expected_candidate if mismatch_stage else None)
        actual_sha256=actual_sha256 or (candidate_before if mismatch_stage=="VALIDATION_INPUT" else None)
        hash_status=hash_status_override or ("HASH_MISMATCH" if mismatch_stage else "VERIFIED")
        if source_after!=source_before:
            decision,status="VALIDATION_FAILED","SOURCE_HASH_MISMATCH"
        elif hash_status in {"HASH_MISMATCH","HASH_NOT_VERIFIED"} and not preserve_validation_on_hash_error:
            decision,status="VALIDATION_BLOCKED",hash_status
        validation_decision=decision if decision in {"VALIDATION_FAILED","VALIDATION_BLOCKED"} else "VALIDATION_PASSED"
        count_status="NOT_EXECUTED" if validation_decision!="VALIDATION_PASSED" else (smell_count_status or "NOT_EXECUTED")
        special={"HASH_MISMATCH","HASH_NOT_VERIFIED","DETECTOR_FAILED","DETECTOR_RESULT_INVALID","ENVIRONMENT_BLOCKED"}
        validation_status=status if validation_decision!="VALIDATION_PASSED" or status in special else "VALIDATION_PASSED"
        included=validation_decision=="VALIDATION_PASSED" and count_status=="COMPLETED" and hash_status=="VERIFIED"
        detector_execution_status=("PASS" if count_status=="COMPLETED" else
                                   "NOT_RUN" if count_status=="NOT_EXECUTED" else "FAILED")
        if instance_revalidation_results is None:
            recheck_status="RECHECK_BLOCKED" if validation_decision=="VALIDATION_PASSED" else "NOT_RUN"
            instance_revalidation_results=[{
                "instance_id":x["instance_id"],"smell_type":x.get("smell_type"),"method_signature":x.get("method_signature"),
                "canonical_identity":None,"before_mapping_status":None,"after_mapping_status":None,"mapping_confidence":"NONE",
                "target_smell_revalidation_status":recheck_status,"final_acceptance_status":"REJECTED",
                "reason":count_status,"evidence":None,"before_record":None,"after_record":None,"new_smells":[],
            } for x in targets]
        return {"schema_version":"1.0","pipeline_mode":"FILE_LEVEL_V1","hash_chain_version":HASH_CHAIN_VERSION,
                "contract_version":"1.0","execution_scope":"JAVA_TEST_FILE","decision":validation_decision,"status":validation_status,
                "validation_decision":validation_decision,"engineering_validation_status":validation_decision,
                "detector_execution_status":detector_execution_status,"smell_count_status":count_status,"revalidation_report_only":False,
                "file_id":bundle.file_id,"file_id_version":getattr(bundle,"file_id_version","1"),"source_file":str(bundle.source_file),"source_hash_before":source_before,"source_hash_after":source_after,
                "source_unchanged":source_before==source_after,"candidate_hash":candidate_before,"candidate_unchanged":candidate_after==candidate_before,
                "instance_ids":[x["instance_id"] for x in targets],"method_count":len(getattr(bundle,"methods",targets)),"structure_checks":checks,
                "stage_results":stages,"test_summary":test_summary or {},"detector_execution":detector or {},
                "smell_comparison":smell_comparison or [],"target_smell_comparison":target_smell_comparison or [],
                "instance_revalidation_results":instance_revalidation_results,
                "workspace":manifest,"hash_chain":{"version":HASH_CHAIN_VERSION,"status":hash_status,
                    **({'mnt_validation_version':__import__('pipeline.validation_evidence',fromlist=['MNT_VALIDATION_VERSION']).MNT_VALIDATION_VERSION,
                        'validation_evidence_version':__import__('pipeline.validation_evidence',fromlist=['VALIDATION_EVIDENCE_VERSION']).VALIDATION_EVIDENCE_VERSION,
                        'source_file':str(bundle.source_file.resolve()),'source_file_sha256':source_before,
                        'project_inputs_sha256':manifest.get('project_inputs_sha256'),
                        'candidate_file':str(Path(candidate_path).resolve()) if candidate_path else None,
                        'source_relative_path':manifest.get('injected_relative_path') or getattr(bundle,'source_relative_path',None) or bundle.source_file.name,
                        'instance_ids':[x['instance_id'] for x in targets],
                        'compiled_candidate_sha256':manifest.get('compiled_candidate_sha256')}
                       ),
                    "candidate_file_sha256":expected_candidate,"validation_input_sha256":candidate_before,
                    "injected_candidate_sha256":injected_candidate_sha256,"validated_candidate_sha256":validated_candidate_sha256,
                    "detector_input_sha256":detector_input_sha256,
                    "post_detector_candidate_sha256":post_detector_candidate_sha256,
                    "mismatch_stage":mismatch_stage,"expected_sha256":expected_sha256,"actual_sha256":actual_sha256},
                "included_in_statistics":included,"transient_artifacts":artifacts,"started_at":started,"completed_at":_utcnow(),"duration_ms":int((monotonic()-clock)*1000)}
