from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .agents.method_location import test_execution_identity
from .hashing import sha256_utf8_lf
from .stage_cache import file_sha256, stable_sha256


OWNER_EVIDENCE_SCHEMA_VERSION = "1.0"
EXPERIMENT_MANIFEST_VERSION = "1.0"
CONFIRMED_MAPPING_SCHEMA_VERSION = "1.0"
CONFIRMED_MAPPING_REASON = "UNIQUE_PATH_PACKAGE_OWNER_SIGNATURE_AND_SOURCE_HASH"

PREFLIGHT_STAGES = (
    "PROJECT_LOAD",
    "CONTEXT_LOAD",
    "CANONICAL_BUILD",
    "SOURCE_OWNER_BINDING",
    "RISK",
)
FORMAL_STAGES = PREFLIGHT_STAGES + (
    "CANDIDATE_GENERATION",
    "STATIC_CONTRACT",
    "FILE_CANDIDATE_BUILD",
    "COMPILE",
    "TARGET_TEST",
    "SMELL_REVALIDATION",
    "FINAL_ACCEPTANCE",
)


def load_confirmed_method_mapping(evidence_path: Path, mapping_id: str) -> dict[str, Any]:
    """Load one deterministic, read-only method mapping overlay entry.

    Loading evidence never changes a smell outcome.  The returned record still
    has to pass :func:`confirmed_mapping_identity` against the current source
    bytes and the target instance before it can be used for test selection.
    """
    path = Path(evidence_path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("CONFIRMED_METHOD_MAPPING_UNAVAILABLE") from exc
    if (
        payload.get("schema_version") != CONFIRMED_MAPPING_SCHEMA_VERSION
        or payload.get("artifact_type") != "READ_ONLY_PROPOSED_MAPPING_OVERLAY"
        or payload.get("deterministic_evidence_only") is not True
        or not isinstance(payload.get("mappings"), list)
    ):
        raise ValueError("CONFIRMED_METHOD_MAPPING_SCHEMA_INVALID")
    matches = [item for item in payload["mappings"] if item.get("mapping_id") == mapping_id]
    if len(matches) != 1:
        raise ValueError("CONFIRMED_METHOD_MAPPING_NOT_UNIQUE")
    return matches[0]


def confirmed_mapping_identity(source_path: Path, instance: dict[str, Any]) -> tuple[str, str]:
    """Verify confirmed AST-owner evidence without weakening Java safety parsing.

    This is an identity-only compatibility boundary for an already audited
    source method.  It cannot establish detector attribution or smell removal.
    """
    evidence = instance.get("confirmed_method_mapping")
    if not isinstance(evidence, dict):
        raise ValueError("CONFIRMED_METHOD_MAPPING_MISSING")
    mapping_id = str(instance.get("mapping_id") or "")
    if not mapping_id or evidence.get("mapping_id") != mapping_id:
        raise ValueError("CONFIRMED_METHOD_MAPPING_ID_MISMATCH")
    if evidence.get("audit_reason_code") != CONFIRMED_MAPPING_REASON:
        raise ValueError("CONFIRMED_METHOD_MAPPING_REASON_UNSUPPORTED")
    mapping = evidence.get("mapping")
    if not isinstance(mapping, dict):
        raise ValueError("CONFIRMED_METHOD_MAPPING_RECORD_INVALID")

    source = Path(source_path).resolve()
    expected_source_hash = str(mapping.get("source_file_sha256") or "")
    if not source.is_file() or not expected_source_hash or file_sha256(source) != expected_source_hash:
        raise ValueError("CONFIRMED_METHOD_MAPPING_SOURCE_HASH_MISMATCH")
    relative = str(instance.get("test_relative_path") or "").replace("\\", "/")
    if not relative or str(mapping.get("relative_path") or "").replace("\\", "/") != relative:
        raise ValueError("CONFIRMED_METHOD_MAPPING_SOURCE_PATH_MISMATCH")

    package = str(instance.get("test_package_name") or "")
    signature = str(instance.get("method_signature") or "")
    if mapping.get("package") != package or mapping.get("method_signature") != signature:
        raise ValueError("CONFIRMED_METHOD_MAPPING_SIGNATURE_MISMATCH")
    method_name = signature.split("(", 1)[0]
    if not method_name or mapping.get("method_name") != method_name:
        raise ValueError("CONFIRMED_METHOD_MAPPING_SIGNATURE_MISMATCH")

    owner_chain = mapping.get("owner_chain")
    if not isinstance(owner_chain, list) or not owner_chain or not all(
        isinstance(owner, str) and owner for owner in owner_chain
    ):
        raise ValueError("CONFIRMED_METHOD_MAPPING_OWNER_INVALID")
    top_level = Path(str(instance.get("test_filename") or "")).stem
    expected_owner = ".".join(filter(None, (package, "$".join(owner_chain))))
    if owner_chain[0] != top_level or mapping.get("class_name") != expected_owner:
        raise ValueError("CONFIRMED_METHOD_MAPPING_OWNER_MISMATCH")

    expected_method_hash = str(mapping.get("method_source_sha256") or "")
    declared_method_hash = str(instance.get("method_source_hash") or "")
    if (
        not expected_method_hash
        or declared_method_hash != expected_method_hash
        or sha256_utf8_lf(str(instance.get("test_code") or "")) != expected_method_hash
    ):
        raise ValueError("CONFIRMED_METHOD_MAPPING_METHOD_HASH_MISMATCH")
    lines = instance.get("line_range") or {}
    if (
        mapping.get("ast_method_start_line") != lines.get("start")
        or mapping.get("ast_method_end_line") != lines.get("end")
    ):
        raise ValueError("CONFIRMED_METHOD_MAPPING_RANGE_MISMATCH")
    if (
        mapping.get("signature_candidate_count_in_file") != 1
        or mapping.get("exact_signature_and_hash_candidate_count") != 1
        or mapping.get("same_signature_method_count_in_owner") != 1
        or mapping.get("ast_root_has_error") is not False
    ):
        raise ValueError("CONFIRMED_METHOD_MAPPING_NOT_UNIQUE")
    unicode_evidence = mapping.get("unicode_evidence") or {}
    if (
        unicode_evidence.get("all_owner_lexically_stable") is not True
        or list(unicode_evidence.get("unsafe_escapes") or [])
    ):
        raise ValueError("CONFIRMED_METHOD_MAPPING_UNSAFE_UNICODE")
    return expected_owner, method_name


def bind_source_owner(
    instance: dict[str, Any],
    *,
    frozen_source_root: Path,
    previous_binding: dict[str, Any] | None = None,
    allow_recorded_path_compatibility: bool = False,
) -> dict[str, Any]:
    """Bind one Canonical instance to an exact file and AST-owned test method.

    Resolution is intentionally limited to ``frozen_source_root / test_relative_path``.
    Relocation is accepted only when an earlier binding supplies the expected file hash.
    """
    root = Path(frozen_source_root).resolve()
    relative_text = str(instance.get("test_relative_path") or "").replace("\\", "/")
    base = {
        "schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
        "status": "INSUFFICIENT_EVIDENCE",
        "reason_code": "SOURCE_RELATIVE_PATH_REQUIRED",
        "frozen_source_root": root.as_posix(),
        "test_relative_path": relative_text,
        "resolved_source_path": None,
        "source_file_sha256": None,
        "package_name": str(instance.get("test_package_name") or ""),
        "owner_class": None,
        "method_signature": str(instance.get("method_signature") or ""),
        "method_source_hash": str(instance.get("method_source_hash") or ""),
    }
    if not relative_text:
        return base
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        return {**base, "status": "IDENTITY_MISMATCH", "reason_code": "SOURCE_RELATIVE_PATH_UNSAFE"}
    candidate = (root / relative).resolve()
    compatibility_used = False
    recorded = Path(str(instance.get("test_file_path") or ""))
    if allow_recorded_path_compatibility and not candidate.is_file() and recorded.is_file():
        recorded = recorded.resolve()
        if recorded.is_relative_to(root):
            candidate = recorded
            compatibility_used = True
    if not candidate.is_relative_to(root):
        return {**base, "status": "IDENTITY_MISMATCH", "reason_code": "SOURCE_OUTSIDE_FROZEN_ROOT"}
    base["resolved_source_path"] = candidate.as_posix()
    if not candidate.is_file():
        return {**base, "status": "MISSING", "reason_code": "SOURCE_RELATIVE_PATH_NOT_FOUND"}

    actual_hash = file_sha256(candidate)
    base["source_file_sha256"] = actual_hash
    original_text = str(instance.get("test_file_path") or "").strip()
    original_matches = False
    if original_text:
        try:
            original_matches = Path(original_text).resolve() == candidate
        except OSError:
            original_matches = False

    reason = "LEGACY_EXACT_ABSOLUTE_COMPATIBILITY" if compatibility_used else "FRESH_SOURCE_BINDING"
    if previous_binding is not None:
        expected_hash = previous_binding.get("source_file_sha256")
        if not expected_hash:
            return {**base, "status": "INSUFFICIENT_EVIDENCE", "reason_code": "SOURCE_FILE_HASH_MISSING"}
        if expected_hash != actual_hash:
            return {**base, "status": "STALE", "reason_code": "SOURCE_FILE_HASH_MISMATCH"}
        if previous_binding.get("test_relative_path") not in (None, relative_text):
            return {**base, "status": "IDENTITY_MISMATCH", "reason_code": "SOURCE_RELATIVE_PATH_MISMATCH"}
        previous_path = str(previous_binding.get("resolved_source_path") or "")
        reason = (
            str(previous_binding.get("reason_code") or "HASH_VERIFIED_REUSE")
            if previous_path and Path(previous_path).resolve() == candidate
            else "HASH_VERIFIED_PATH_MIGRATION"
        )
    elif original_text and not original_matches:
        return {**base, "status": "INSUFFICIENT_EVIDENCE", "reason_code": "SOURCE_FILE_HASH_MISSING"}

    try:
        source = candidate.read_text(encoding="utf-8")
        owner, method = test_execution_identity(source, instance)
    except UnicodeDecodeError:
        return {**base, "status": "INSUFFICIENT_EVIDENCE", "reason_code": "SOURCE_ENCODING_UNSUPPORTED"}
    except ValueError as exc:
        code = str(exc)
        if code == "JAVA_PRELEXICAL_UNICODE_BINDING_UNKNOWN" and instance.get("confirmed_method_mapping"):
            try:
                owner, method = confirmed_mapping_identity(candidate, instance)
            except ValueError as evidence_exc:
                code = str(evidence_exc)
            else:
                return {
                    **base,
                    "status": "BOUND",
                    "reason_code": "CONFIRMED_METHOD_MAPPING_EVIDENCE",
                    "owner_class": owner,
                    "test_method": method,
                    "confirmed_mapping_id": instance.get("mapping_id"),
                }
        status = "AMBIGUOUS" if "AMBIGUOUS" in code else "IDENTITY_MISMATCH"
        reason_code = (
            "SOURCE_METHOD_OWNER_AMBIGUOUS"
            if status == "AMBIGUOUS"
            else f"SOURCE_OWNER_{code}"
        )
        return {**base, "status": status, "reason_code": reason_code}
    return {
        **base,
        "status": "BOUND",
        "reason_code": reason,
        "owner_class": owner,
        "test_method": method,
    }


def build_execution_owner_mapping(
    instance: dict[str, Any],
    source_owner_binding: dict[str, Any],
    *,
    risk_decision: str | None,
) -> dict[str, Any]:
    base = {
        "schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
        "status": "NOT_RUN",
        "reason_code": "RISK_NOT_APPROVED",
        "test_class": None,
        "test_method": None,
        "source_file_sha256": source_owner_binding.get("source_file_sha256"),
    }
    if risk_decision != "REFACTOR_LOW_RISK":
        return base
    if source_owner_binding.get("status") != "BOUND":
        return {**base, "status": "BLOCKED", "reason_code": "SOURCE_OWNER_NOT_BOUND"}
    source_path = Path(str(source_owner_binding.get("resolved_source_path") or ""))
    expected_hash = source_owner_binding.get("source_file_sha256")
    if not source_path.is_file() or not expected_hash:
        return {**base, "status": "BLOCKED", "reason_code": "SOURCE_OWNER_EVIDENCE_INCOMPLETE"}
    if file_sha256(source_path) != expected_hash:
        return {**base, "status": "BLOCKED", "reason_code": "SOURCE_OWNER_STALE"}
    try:
        if source_owner_binding.get("reason_code") == "CONFIRMED_METHOD_MAPPING_EVIDENCE":
            owner, method = confirmed_mapping_identity(source_path, instance)
        else:
            owner, method = test_execution_identity(source_path.read_text(encoding="utf-8"), instance)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        code = str(exc) or type(exc).__name__
        return {**base, "status": "BLOCKED", "reason_code": f"EXECUTION_OWNER_{code}"}
    if owner != source_owner_binding.get("owner_class") or method != source_owner_binding.get("test_method"):
        return {**base, "status": "BLOCKED", "reason_code": "EXECUTION_OWNER_IDENTITY_MISMATCH"}
    return {
        **base,
        "status": "MAPPED",
        "reason_code": (
            "CONFIRMED_METHOD_MAPPING_EVIDENCE"
            if source_owner_binding.get("reason_code") == "CONFIRMED_METHOD_MAPPING_EVIDENCE"
            else "EXACT_SOURCE_OWNER_MATCH"
        ),
        "test_class": owner,
        "test_method": method,
        **({"confirmed_mapping_id": instance.get("mapping_id")}
           if source_owner_binding.get("reason_code") == "CONFIRMED_METHOD_MAPPING_EVIDENCE" else {}),
    }


def normalize_owner_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Read current and legacy reports without assigning meaning to legacy nulls."""
    source = record.get("source_owner_binding")
    execution = record.get("execution_owner_mapping")
    if isinstance(source, dict) and isinstance(execution, dict):
        return {"source_owner_binding": source, "execution_owner_mapping": execution}
    legacy_present = "mapping" in record
    legacy = record.get("mapping")
    source = source if isinstance(source, dict) else {
        "schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
        "status": "UNKNOWN_LEGACY",
        "reason_code": "LEGACY_SOURCE_OWNER_NOT_RECORDED",
    }
    if isinstance(execution, dict):
        pass
    elif legacy_present and legacy is None:
        execution = {
            "schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
            "status": "UNKNOWN_LEGACY",
            "reason_code": "LEGACY_MAPPING_NULL_SEMANTICS_UNKNOWN",
        }
    elif legacy_present and isinstance(legacy, dict):
        execution = {
            "schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
            "status": "MAPPED_LEGACY",
            "reason_code": "LEGACY_MAPPING_PRESENT",
            "legacy_mapping": legacy,
        }
    else:
        execution = {
            "schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
            "status": "UNKNOWN_LEGACY",
            "reason_code": "OWNER_FIELDS_NOT_RECORDED",
        }
    return {"source_owner_binding": source, "execution_owner_mapping": execution}


def infer_frozen_source_root(instance: dict[str, Any]) -> Path:
    """Derive a root only from an exact recorded absolute/relative path pair."""
    source_text = str(instance.get("test_file_path") or "").strip()
    relative_text = str(instance.get("test_relative_path") or "").strip()
    if not source_text or not relative_text:
        raise ValueError("FROZEN_SOURCE_ROOT_REQUIRED")
    source = Path(source_text).resolve()
    relative = Path(relative_text.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("SOURCE_RELATIVE_PATH_UNSAFE")
    root = source
    for _ in relative.parts:
        root = root.parent
    if (root / relative).resolve() != source:
        raise ValueError("FROZEN_SOURCE_ROOT_CANNOT_BE_DERIVED")
    return root


def verified_execution_identity(source_path: Path, instance: dict[str, Any]) -> tuple[str, str]:
    """Consume the approved execution mapping and reject drift before test selection."""
    mapping = instance.get("execution_owner_mapping") or {}
    binding = instance.get("source_owner_binding") or {}
    if mapping.get("status") != "MAPPED":
        raise ValueError(f"EXECUTION_OWNER_MAPPING_{mapping.get('status') or 'MISSING'}")
    source = Path(source_path).resolve()
    bound_path = Path(str(binding.get("resolved_source_path") or "")).resolve()
    if source != bound_path:
        raise ValueError("EXECUTION_OWNER_SOURCE_PATH_MISMATCH")
    expected_hash = mapping.get("source_file_sha256")
    if not expected_hash or file_sha256(source) != expected_hash:
        raise ValueError("EXECUTION_OWNER_SOURCE_HASH_MISMATCH")
    if (
        mapping.get("reason_code") == "CONFIRMED_METHOD_MAPPING_EVIDENCE"
        and binding.get("reason_code") == "CONFIRMED_METHOD_MAPPING_EVIDENCE"
    ):
        actual = confirmed_mapping_identity(source, instance)
    else:
        actual = test_execution_identity(source.read_text(encoding="utf-8"), instance)
    expected = (mapping.get("test_class"), mapping.get("test_method"))
    if actual != expected:
        raise ValueError("EXECUTION_OWNER_IDENTITY_MISMATCH")
    return actual


def build_execution_plan(
    instances: Iterable[dict[str, Any]], *, execution_mode: str
) -> dict[str, Any]:
    mode = execution_mode.upper()
    if mode not in {"PREFLIGHT", "FORMAL"}:
        raise ValueError("execution_mode must be PREFLIGHT or FORMAL")
    items = []
    for instance in instances:
        binding = instance.get("source_owner_binding") or {}
        items.append({
            "instance_id": instance["instance_id"],
            "smell_type": instance["smell_type"],
            "project_version": instance["project_version"],
            "test_relative_path": instance["test_relative_path"],
            "method_signature": instance["method_signature"],
            "method_source_hash": instance["method_source_hash"],
            "source_owner_status": binding.get("status"),
            "source_file_sha256": binding.get("source_file_sha256"),
        })
    items.sort(key=lambda item: (item["instance_id"], item["smell_type"]))
    input_hash = stable_sha256(items)
    complete_plan = {"input_identity_hash": input_hash, "stages": list(FORMAL_STAGES), "instances": items}
    return {
        "plan_schema_version": "1.0",
        "execution_mode": mode,
        "input_identity_hash": input_hash,
        "execution_plan_hash": stable_sha256(complete_plan),
        "active_stages": list(PREFLIGHT_STAGES if mode == "PREFLIGHT" else FORMAL_STAGES),
        "complete_plan": complete_plan,
    }


def git_workspace_identity(repo_root: Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    return {
        "head": _git(root, "rev-parse", "HEAD"),
        "status_sha256": stable_sha256(_git(root, "status", "--porcelain=v1", "--untracked-files=all")),
        "tracked_diff_sha256": stable_sha256(_git(root, "diff", "--binary", "--no-ext-diff")),
        "staged_diff_sha256": stable_sha256(_git(root, "diff", "--cached", "--binary", "--no-ext-diff")),
    }


def framework_source_identity(repo_root: Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    files: list[Path] = []
    for directory, suffixes in (
        (root / "pipeline", {".py", ".json"}),
        (root / "refactoring", {".py"}),
        (root / "src" / "main" / "java", {".java"}),
    ):
        if directory.is_dir():
            files.extend(
                path for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() in suffixes and "__pycache__" not in path.parts
            )
    entry = root / "run_refactoring_pipeline.py"
    if entry.is_file():
        files.append(entry)
    identities = {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(set(files), key=lambda value: value.as_posix().lower())
    }
    return {
        "file_count": len(identities),
        "tree_sha256": stable_sha256(identities),
        "files": identities,
    }


def build_reproducibility_manifest(
    *,
    repo_root: Path,
    project_name: str,
    project_version: str,
    frozen_source_root: Path,
    context_files: Iterable[Path],
    execution_plan: dict[str, Any],
    config_identity: dict[str, Any],
    detector_identity: dict[str, Any],
    rule_identity: dict[str, Any],
    model_identity: dict[str, Any],
    call_budget: dict[str, Any],
) -> dict[str, Any]:
    contexts = [
        {"path": Path(path).resolve().as_posix(), "sha256": file_sha256(Path(path))}
        for path in sorted(context_files, key=lambda value: Path(value).as_posix().lower())
    ]
    payload = {
        "manifest_schema_version": EXPERIMENT_MANIFEST_VERSION,
        "execution_mode": execution_plan["execution_mode"],
        "framework": {
            "git_workspace": git_workspace_identity(repo_root),
            "source_tree": framework_source_identity(repo_root),
        },
        "schema_identity": {
            "context_schema_version": "4.1",
            "canonical_contract_version": "1.0",
            "owner_evidence_schema_version": OWNER_EVIDENCE_SCHEMA_VERSION,
        },
        "detector_identity": detector_identity,
        "rule_identity": rule_identity,
        "project": {
            "name": project_name,
            "source_commit": project_version,
            "frozen_source_root": Path(frozen_source_root).resolve().as_posix(),
        },
        "configuration": config_identity,
        "model": model_identity,
        "call_budget": call_budget,
        "inputs": {
            "context_files": contexts,
            "instance_count": len(execution_plan["complete_plan"]["instances"]),
            "input_identity_hash": execution_plan["input_identity_hash"],
        },
        "execution_plan": execution_plan,
    }
    payload["manifest_hash"] = stable_sha256(payload)
    return payload


def _git(root: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args], cwd=root, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None
