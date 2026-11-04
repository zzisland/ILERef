from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.agents.base import load_revalidation_agent_config, load_validation_agent_config
from pipeline.ar_module2.mapping import load_phase1_detection_output
from pipeline.agents.detection_result_adapter import load_schema_v41_records, normalized_record
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


def execute_outcome(self, *, run_id, project_name, bundle, targets, candidate_path, started, clock, source, source_before, candidate_before, expected_candidate, relative, target_class, baseline_class, source_relative_path, module_path, artifacts, stages, manifest, structure_checks, identities, workspace, package, class_name, project_inputs, injected, injected_hash, validated_hash, tests, class_observed, method_summaries):
    pre_detector_current=sha256_file_bytes(candidate_path) if candidate_path.is_file() else None
    pre_failure=self._pre_detector_hash_failure(expected_candidate,candidate_before,injected_hash,
                                                 validated_hash,pre_detector_current)
    if pre_failure:
        workspace.preserve=True; manifest["preserved"]=True; manifest["retained_reason"]=pre_failure["status"]
        return self._finish(started,clock,"VALIDATION_PASSED",pre_failure["status"],bundle,targets,
                            source_before,candidate_before,structure_checks,stages,manifest,artifacts,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            smell_count_status=pre_failure["status"],candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            mismatch_stage=pre_failure["stage"],expected_sha256=pre_failure["expected"],
                            actual_sha256=pre_failure["actual"],hash_status_override=pre_failure["status"],
                            preserve_validation_on_hash_error=True)
    try:
        detector_input_path=(self.detector_runner.prepare_input(workspace=workspace.root,source_relative_path=relative)
                             if hasattr(self.detector_runner,"prepare_input") else injected)
        if Path(detector_input_path).resolve()!=injected.resolve():
            raise ValueError('DETECTOR_FILE_IDENTITY_MISMATCH')
        detector_input_hash=sha256_file_bytes(detector_input_path)
    except (OSError,ValueError) as exc:
        workspace.preserve=True; manifest["preserved"]=True; manifest["retained_reason"]="HASH_NOT_VERIFIED"
        artifacts["detector_stderr.txt"]=str(exc)
        return self._finish(started,clock,"VALIDATION_PASSED","HASH_NOT_VERIFIED",bundle,targets,source_before,
                            candidate_before,structure_checks,stages,manifest,artifacts,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            smell_count_status="HASH_NOT_VERIFIED",candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            mismatch_stage="DETECTOR_INJECTION",expected_sha256=expected_candidate,
                            hash_status_override="HASH_NOT_VERIFIED",preserve_validation_on_hash_error=True)
    if detector_input_hash!=expected_candidate:
        workspace.preserve=True; manifest["preserved"]=True; manifest["retained_reason"]="HASH_MISMATCH"
        return self._finish(started,clock,"VALIDATION_PASSED","HASH_MISMATCH",bundle,targets,source_before,
                            candidate_before,structure_checks,stages,manifest,artifacts,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            smell_count_status="HASH_MISMATCH",candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            detector_input_sha256=detector_input_hash,mismatch_stage="DETECTOR_INJECTION",
                            expected_sha256=expected_candidate,actual_sha256=detector_input_hash,
                            preserve_validation_on_hash_error=True)
    detector_run_id=f"{run_id}-detector"
    detector_project="phase5_file_"+bundle.file_id.rsplit(":",1)[-1][:16]
    detector,stdout,stderr,output_dir=self.detector_runner.run(workspace=workspace.root,run_id=detector_run_id,
        project_name=detector_project,detector_jar=getattr(self, "detector_jar", self.repo_root/"detection"/"junit5"/"TestSmellDetector.jar"),
        timeout_seconds=self.revalidation_config["timeouts"]["detector_seconds"])
    artifacts["detector_stdout.txt"]=stdout; artifacts["detector_stderr.txt"]=stderr
    post_detector_hash=sha256_file_bytes(detector_input_path) if detector_input_path.is_file() else None
    if post_detector_hash!=detector_input_hash or post_detector_hash!=expected_candidate:
        workspace.preserve=True; manifest["preserved"]=True; manifest["retained_reason"]="HASH_MISMATCH"
        self._capture_raw_detection(artifacts,output_dir)
        return self._finish(started,clock,"VALIDATION_PASSED","HASH_MISMATCH",bundle,targets,source_before,
                            candidate_before,structure_checks,stages,manifest,artifacts,detector=detector,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            smell_count_status="HASH_MISMATCH",candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            detector_input_sha256=detector_input_hash,post_detector_candidate_sha256=post_detector_hash,
                            mismatch_stage="POST_DETECTOR_INPUT",expected_sha256=detector_input_hash,
                            actual_sha256=post_detector_hash,preserve_validation_on_hash_error=True)
    if detector["status"]!="PASS" or output_dir is None:
        detector_status=self._detector_failure_status(detector)
        return self._finish(started,clock,"VALIDATION_PASSED",detector_status,bundle,targets,source_before,candidate_before,
                            structure_checks,stages,manifest,artifacts,detector=detector,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            smell_count_status=detector_status,candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            detector_input_sha256=detector_input_hash,post_detector_candidate_sha256=post_detector_hash)
    payload={"target_class":Path(targets[0]["test_filename"]).stem,"target_package":targets[0].get("test_package_name") or "",
             "source_relative_path":source_relative_path,"project_id":project_name,"module_path":module_path,
             "expected_methods":{Path(str(x.get("split_filename") or "")).stem:{"method_signature":x.get("method_signature"),"instance_id":x.get("instance_id")} for x in targets}}
    try:
        after_records,result_csv=load_phase1_detection_output(output_dir,payload)
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        workspace.preserve=True; manifest["preserved"]=True; manifest["retained_reason"]="DETECTOR_RESULT_INVALID"
        artifacts["detector_parse_error.txt"]=str(exc); self._capture_raw_detection(artifacts,output_dir)
        return self._finish(started,clock,"VALIDATION_PASSED","DETECTOR_RESULT_INVALID",bundle,targets,
                            source_before,candidate_before,structure_checks,stages,manifest,artifacts,detector=detector,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            smell_count_status="DETECTOR_RESULT_INVALID",candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            detector_input_sha256=detector_input_hash,post_detector_candidate_sha256=post_detector_hash)
    artifacts["raw_detection/result.csv"]=result_csv.read_text(encoding="utf-8-sig")
    artifacts["raw_detection/target_class.json"]=json.dumps(after_records,ensure_ascii=False,indent=2,default=str)
    if hasattr(self.detector_runner,"cleanup_successful_output"):
        detector["workspace_cleanup"]=self.detector_runner.cleanup_successful_output(
            output_dir,run_id=detector_run_id,project_name=detector_project)
    from pipeline.agents.test_method_counter import compare_owned_test_files
    method_inventory = compare_owned_test_files(
        source, candidate_path, package, class_name,
        source_sha256=source_before, candidate_sha256=expected_candidate)
    method_count_guard = method_inventory['passed'] is True
    before_method_count = (method_inventory.get('before') or {}).get('test_method_count')
    after_method_count = (method_inventory.get('after') or {}).get('test_method_count')
    smell_comparison=self._smell_counts(baseline_class,after_records)
    target_smells=sorted({x["smell_type"] for x in targets})
    target_comparison=[x for x in smell_comparison if x["smell_type"] in target_smells]
    instance_results=revalidate_instances(
        project_id=project_name, source_relative_path=source_relative_path,
        targets=self._numeric_targets(bundle,targets), before_records=baseline_class, after_records=after_records,
        detector_execution_status="PASS", method_count_guard_passed=method_count_guard,
        method_inventory=method_inventory,
    )
    return self._finish(started,clock,"VALIDATION_PASSED","VALIDATION_PASSED",bundle,targets,source_before,candidate_before,
                        structure_checks,stages,manifest,artifacts,detector=detector,
                        test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries,
                        "test_method_count_before":before_method_count,"test_method_count_after":after_method_count,
                        "method_inventory":method_inventory},
                         smell_count_status="COMPLETED",smell_comparison=smell_comparison,
                         target_smell_comparison=target_comparison,instance_revalidation_results=instance_results,candidate_path=candidate_path,
                        injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                        detector_input_sha256=detector_input_hash,post_detector_candidate_sha256=post_detector_hash)
