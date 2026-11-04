from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.contracts import validate_contract
from pipeline.hashing import sha256_file_bytes


JAVA_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
JAVA_CLASS = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$")


def sha256_file(path: Path) -> str:
    """Compatibility alias for exact file-byte integrity hashing."""
    return sha256_file_bytes(path)


def nearest_maven_root(source_file: Path) -> Path:
    for parent in (source_file.parent, *source_file.parents):
        if (parent / "pom.xml").is_file(): return parent.resolve()
    raise ValueError("No Maven pom.xml was found above the source test file.")


def build_validation_payload(*, instance: dict[str, Any], refactoring_result: dict[str, Any], instance_artifact_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    validate_contract(refactoring_result, "agent-result")
    rr = refactoring_result["result"]
    from pipeline.agents.safety_contracts import current_rule_version
    if (rr.get('risk_contract') or {}).get('risk_rule_version') != current_rule_version(instance.get('smell_type')):
        raise ValueError('RULE_VERSION_REAUDIT_REQUIRED: regenerate source-bound risk and candidate contracts.')
    if rr.get("decision") != "CANDIDATE_GENERATED": raise ValueError("Validation requires a CANDIDATE_GENERATED refactoring result.")
    source_file = Path(instance["test_file_path"]).resolve(); project_root = nearest_maven_root(source_file)
    relative_source = source_file.relative_to(project_root)
    candidate_method = (instance_artifact_root / "refactoring" / "candidate_method.java").resolve()
    candidate_file = (instance_artifact_root / "refactoring" / "candidate_file.java").resolve()
    allowed_root = (instance_artifact_root / "refactoring").resolve()
    for path in (candidate_method, candidate_file):
        if not path.is_relative_to(allowed_root): raise ValueError("Candidate artifact path escapes the instance refactoring stage.")
    method = instance["method_signature"].split("(", 1)[0]
    from pipeline.agents.method_location import test_execution_identity
    target_class, method = test_execution_identity(source_file.read_text(encoding='utf8'),instance)
    if not JAVA_NAME.fullmatch(method) or not JAVA_CLASS.fullmatch(target_class): raise ValueError("Unsafe Maven test selector metadata.")
    levels = [name.upper() for name, enabled in config["validation_levels"].items() if enabled]
    return {
        "validation_contract_version": "4A.0",
        "project_id": instance.get("project_version"), "pipeline_state": "CANDIDATE_GENERATED",
        "validation_mode": "maven_isolated", "source_project_root": str(project_root),
        "source_test_file": str(source_file), "source_test_relative_path": str(relative_source).replace("\\", "/"),
        "source_test_file_hash": rr["source"]["sha256"], "target_module": instance.get("module_path") or ".",
        "target_test_class": target_class, "target_test_method": method,
        "method_signature": instance["method_signature"], "original_method_hash": rr["risk_contract"]["original_method_hash"],
        "original_method_source": instance["test_code"],
        "refactoring_result_ref": str((instance_artifact_root / "refactoring" / "result.json").resolve()),
        "refactoring_result": refactoring_result,
        "candidate_method_ref": str(candidate_method), "candidate_file_ref": str(candidate_file),
        "candidate_method_hash": rr["candidate"]["method_sha256"], "candidate_file_hash": rr["candidate"]["file_sha256"],
        "build_system": "maven", "build_command": {"template_id": "maven-test-compile-v1"},
        "target_test_command": {"template_id": "maven-surefire-method-v1", "selector": f"{target_class}#{method}"},
        "module_test_command": {"template_id": "maven-module-test-v1"} if config["validation_levels"]["module_test"] else None,
        "validation_levels": levels, "timeout_config": config["timeouts"],
        "environment_snapshot_ref": "validation/environment.json", "config_refs": ["config/agent_config.yaml"],
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
