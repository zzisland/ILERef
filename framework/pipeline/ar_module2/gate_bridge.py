"""Module-local execution bridge; shared engineering is unchanged."""
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


def _utcnow():
    return datetime.now(timezone.utc).isoformat()

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
        from pipeline.ar_module2.redetection import execute_outcome
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
