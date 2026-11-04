from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pipeline.contracts import validate_contract
from pipeline.agents.revalidation_types import SUPPORTED_SMELLS, required_checks_for
from pipeline.agents.validation_contract import nearest_maven_root, sha256_file
from pipeline.agents.refactoring_contract import sha256_text


def _jar_version(path: Path) -> str:
    return "sha256:" + sha256_file(path)


def build_revalidation_payload(*, instance: dict[str, Any], refactoring_result: dict[str, Any],
                               validation_result: dict[str, Any], phase3_root: Path,
                               phase4_root: Path, before_detection_ref: Path,
                               config: dict[str, Any]) -> dict[str, Any]:
    from pipeline.instances import LegacyInstanceAdapter
    instance = LegacyInstanceAdapter().adapt(instance)
    validate_contract(instance, "pipeline-instance")
    validate_contract(refactoring_result, "agent-result")
    validate_contract(validation_result, "agent-result")
    from pipeline.agents.safety_contracts import current_rule_version
    if (refactoring_result['result'].get('risk_contract') or {}).get('risk_rule_version') != current_rule_version(instance.get('smell_type')):
        raise ValueError('RULE_VERSION_REAUDIT_REQUIRED')
    if refactoring_result["result"].get("decision") != "CANDIDATE_GENERATED":
        raise ValueError("Phase 5 requires a CANDIDATE_GENERATED Phase 3 result.")
    vr = validation_result["result"]
    import json
    marker=Path(phase4_root)/"validation"/"orchestration_error.json"
    if marker.is_file() and not json.loads(marker.read_text(encoding='utf-8')).get('resolved'):
        raise ValueError("ENGINEERING_EVIDENCE_PERSISTENCE_BLOCKED")
    if vr.get("decision") != "VALIDATION_PASSED" or not vr.get("target_test_observed"):
        raise ValueError("Phase 5 requires VALIDATION_PASSED with the target test observed.")
    if int((vr.get("test_summary") or {}).get("tests") or 0) < 1:
        raise ValueError("Phase 5 requires at least one executed target test.")
    required_levels = {"STRUCTURE", "COMPILE", "TARGET_TEST"}
    if not required_levels.issubset(set(vr.get("completed_levels") or [])):
        raise ValueError("Phase 4A structure, compile, and target-test gates must all complete.")
    smell = instance["smell_type"]
    if smell not in SUPPORTED_SMELLS:
        raise ValueError(f"Unsupported Phase 5 smell: {smell}")
    source = Path(instance["test_file_path"]).resolve()
    project_root = nearest_maven_root(source)
    candidate_file = (Path(phase3_root) / "refactoring" / "candidate_file.java").resolve()
    candidate_method = (Path(phase3_root) / "refactoring" / "candidate_method.java").resolve()
    allowed = (Path(phase3_root) / "refactoring").resolve()
    for candidate in (candidate_file, candidate_method):
        if not candidate.is_relative_to(allowed):
            raise ValueError("Candidate artifact escapes the Phase 3 refactoring directory.")
        if not candidate.is_file():
            raise FileNotFoundError(f"Candidate artifact is missing: {candidate.name}")
    rr = refactoring_result["result"]
    if sha256_text(candidate_file.read_text(encoding="utf-8")) != rr["candidate"]["file_sha256"]:
        raise ValueError("Candidate file hash does not match Phase 3.")
    if sha256_text(candidate_method.read_text(encoding="utf-8")) != rr["candidate"]["method_sha256"]:
        raise ValueError("Candidate method hash does not match Phase 3.")
    from pipeline.validation_evidence import engineering_chain
    engineering_chain(vr,rr,source=source,candidate=candidate_file,
                      relative=source.relative_to(project_root),instance_id=instance['instance_id'])
    detector = (Path(__file__).resolve().parents[3] / "detection" / "junit5" / "TestSmellDetector.jar").resolve()
    if not detector.is_file():
        raise FileNotFoundError("Phase 1 detector jar is unavailable.")
    before_ref = Path(before_detection_ref).resolve()
    if not before_ref.is_file():
        raise FileNotFoundError("Phase 1 before detection artifact is unavailable.")
    supported_controls = set(SUPPORTED_SMELLS)
    if smell != 'Conditional_Test_Logic':
        supported_controls.discard('Conditional_Test_Logic')
    controlled = sorted(set(instance.get("detected_smells") or []) | supported_controls)
    return {
        "revalidation_contract_version": "5.0", "pipeline_state": "VALIDATION_PASSED",
        "project_id": instance["project_version"], "source_project_root": str(project_root),
        "module": instance.get("module_path") or ".", "source_test_file": str(source),
        "source_relative_path": str(source.relative_to(project_root)).replace("\\", "/"),
        "source_file_hash": rr["source"]["sha256"], "original_method_hash": rr["risk_contract"]["original_method_hash"],
        "original_method_source": instance["test_code"],
        "candidate_file": str(candidate_file), "candidate_file_hash": rr["candidate"]["file_sha256"],
        "candidate_method": str(candidate_method), "candidate_method_hash": rr["candidate"]["method_sha256"],
        "target_package": instance.get("test_package_name") or "", "target_class": Path(instance["test_filename"]).stem,
        "target_method": instance["method_signature"].split("(", 1)[0], "method_signature": instance["method_signature"],
        "target_smell": smell, "controlled_smells": controlled,
        "before_detection_ref": str(before_ref),
        "phase3_result_ref": str((Path(phase3_root) / "refactoring" / "result.json").resolve()),
        "validation_result_ref": str((Path(phase4_root) / "validation" / "result.json").resolve()),
        "validation_workspace_manifest_ref": str((Path(phase4_root) / "validation" / "workspace_manifest.json").resolve()),
        "refactoring_result": refactoring_result, "validation_result": validation_result,
        "detector_name": "TestSmellDetector-junit5", "detector_version": _jar_version(detector),
        "detector_jar": str(detector), "detector_config_ref": "scripts/detect_only.ps1",
        "detector_config_hash": hashlib.sha256((Path(__file__).resolve().parents[3] / "scripts" / "detect_only.ps1").read_bytes()).hexdigest(),
        "detection_schema_version": "4.1", "requested_checks": list(required_checks_for(smell)),
        "matching_policy_version": "5.0", "revalidation_policy_version": "5.0",
        "timeout_config": config["timeouts"], "preserve_failed_workspace": config["preserve_failed_workspace"],
        "max_output_chars": config["max_output_chars"], "config_refs": ["config/agent_config.yaml", "scripts/detect_only.ps1"],
    }
