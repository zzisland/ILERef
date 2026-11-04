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




from dataclasses import dataclass

@dataclass
class EngineeringCompletion:
    manifest: object
    injected: object
    injected_hash: object
    validated_hash: object
    tests: object
    class_observed: object
    method_summaries: object


def execute_engineering(self, *, run_id, project_name, bundle, targets, candidate_path, started, clock, source, source_before, candidate_before, expected_candidate, relative, target_class, baseline_class, source_relative_path, module_path, artifacts, stages, manifest, structure_checks, identities, workspace, package, class_name, project_inputs):
    manifest=workspace.manifest(); manifest["project_inputs_sha256"]=project_inputs; injected=workspace.root/relative
    injected_hash=sha256_file_bytes(injected)
    manifest["injected_candidate_sha256"]=injected_hash
    if injected_hash!=expected_candidate:
        return self._finish(started,clock,"VALIDATION_BLOCKED","HASH_MISMATCH",bundle,targets,source_before,candidate_before,
                            structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,mismatch_stage="WORKSPACE_INJECTION",
                            expected_sha256=expected_candidate,actual_sha256=injected_hash)
    compile_result,out,err=self.validation_runner.run(stage="COMPILE",args=["-q","-DskipTests","test-compile"],cwd=workspace.root,
                                                       timeout_seconds=self.validation_config["timeouts"]["compile_seconds"])
    artifacts["compile_stdout.txt"]=out; artifacts["compile_stderr.txt"]=err; stages.append(compile_result)
    validated_hash=sha256_file_bytes(injected)
    manifest['compiled_candidate_sha256']=validated_hash
    if validated_hash!=expected_candidate:
        return self._finish(started,clock,"VALIDATION_BLOCKED","HASH_MISMATCH",bundle,targets,source_before,candidate_before,
                            structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            mismatch_stage="POST_COMPILE",expected_sha256=expected_candidate,actual_sha256=validated_hash)
    if compile_result["status"]!="PASS":
        decision="VALIDATION_BLOCKED" if compile_result["status"] in BLOCKED_STATUSES else "VALIDATION_FAILED"
        return self._finish(started,clock,decision,compile_result["status"],bundle,targets,source_before,candidate_before,
                            structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash)
    before_reports=snapshot_maven_test_reports(workspace.root)
    test_result,out,err=self.validation_runner.run(stage="TARGET_TEST",args=["-q",f"-Dtest={target_class}","test"],cwd=workspace.root,
                                                    timeout_seconds=self.validation_config["timeouts"]["target_test_seconds"])
    artifacts["target_test_stdout.txt"]=out; artifacts["target_test_stderr.txt"]=err; stages.append(test_result)
    validated_hash=sha256_file_bytes(injected)
    if validated_hash!=expected_candidate:
        return self._finish(started,clock,"VALIDATION_BLOCKED","HASH_MISMATCH",bundle,targets,source_before,candidate_before,
                            structure_checks,stages,manifest,artifacts,candidate_path=candidate_path,
                            injected_candidate_sha256=injected_hash,validated_candidate_sha256=validated_hash,
                            mismatch_stage="POST_TARGET_TEST",expected_sha256=expected_candidate,actual_sha256=validated_hash)
    method_summaries={}
    for target in targets:
        owner,method=identities[target["instance_id"]]
        method_summaries[target["instance_id"]]=parse_maven_test_reports(workspace.root,owner,method,before_reports=before_reports)
    class_observed=any(x.get("target_class_observed") for x in method_summaries.values())
    tests=max((int(x.get("tests") or 0) for x in method_summaries.values()),default=0)
    if test_result["status"]!="PASS" or not all(execution_confirmed(x) for x in method_summaries.values()):
        status=test_result["status"] if test_result["status"]!="PASS" else (
            'TARGET_TEST_FAILED' if any(s['execution_status'] in ('TARGET_FAILED','TEST_RUN_FAILED') for s in method_summaries.values()) else 'TARGET_TEST_NOT_EXECUTED')
        return self._finish(started,clock,"VALIDATION_BLOCKED" if status in BLOCKED_STATUSES else "VALIDATION_FAILED",status,
                            bundle,targets,source_before,candidate_before,structure_checks,stages,manifest,artifacts,
                            test_summary={"tests":tests,"target_class_observed":class_observed,"methods":method_summaries},
                            candidate_path=candidate_path,injected_candidate_sha256=injected_hash,
                            validated_candidate_sha256=validated_hash)
    return EngineeringCompletion(manifest, injected, injected_hash, validated_hash, tests, class_observed, method_summaries)
