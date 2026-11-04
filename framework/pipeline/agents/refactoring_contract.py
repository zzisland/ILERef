from __future__ import annotations

from pipeline.instances.legacy_adapter import LegacyInstanceAdapter

from pathlib import Path
from typing import Any

from pipeline.contracts import validate_contract, UnifiedRefactoringInstance

from .refactoring_types import SUPPORTED_REFACTORING_SMELLS, RefactoringRejected
from .risk_policy import valid_low_risk_evidence
from pipeline.hashing import sha256_method_text, sha256_utf8_exact


def sha256_text(value: str) -> str:
    """Compatibility alias for the established logical-text hash."""
    return sha256_utf8_exact(value)


def normalized_source(value: str) -> str:
    # schema 4.1 removes indentation from the first annotation line while
    # preserving indentation on the remaining method lines.
    return value.replace("\r\n", "\n").replace("\r", "\n").lstrip().rstrip()


def build_refactoring_contract(request: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry; Full uses the independent executable builder."""
    from pipeline.contract_builders import ExecutableContractBuilder
    return ExecutableContractBuilder().build(request)


def _build_executable_contract(request: dict[str, Any]) -> dict[str, Any]:
    payload = request.get("input") or {}
    if payload.get("pipeline_state") != "RISK_APPROVED":
        raise RefactoringRejected("Refactoring requires pipeline_state=RISK_APPROVED.")
    instance = payload.get("instance")
    risk = payload.get("risk_result")
    if not isinstance(instance, dict) or not isinstance(risk, dict):
        raise RefactoringRejected("Instance and Risk result are required.")
    instance = LegacyInstanceAdapter().adapt(instance)
    validate_contract(risk, "agent-result")
    rr = risk["result"]
    if risk.get("agent") != "risk_assessment" or rr.get("decision") != "REFACTOR_LOW_RISK":
        raise RefactoringRejected("Only a validated REFACTOR_LOW_RISK result may generate a candidate.")
    smell = instance.get("smell_type")
    target_smells = payload.get("target_smells") or []
    if smell not in SUPPORTED_REFACTORING_SMELLS or target_smells != [smell]:
        raise RefactoringRejected("Exactly one supported target smell is required.")
    if not rr.get("allowed_changes") or not rr.get("forbidden_changes") or not rr.get("evidence"):
        raise RefactoringRejected("Risk change bounds and evidence are required.")
    if risk.get('instance_id') != instance.get('instance_id') or request.get('instance_id') != instance.get('instance_id'):
        raise RefactoringRejected('CROSS_INSTANCE_RISK_RESULT')
    evidence_ok, evidence_reason = valid_low_risk_evidence(rr, instance, payload.get('context') or {})
    if not evidence_ok:
        raise RefactoringRejected(evidence_reason)
    if rr.get('assessment_mode') == 'rule_llm':
        from .decision_support import verify_admission
        try:
            verify_admission(rr, instance, payload.get('context') or {})
        except ValueError as exc:
            raise RefactoringRejected(str(exc)) from None
    if instance.get("method_source_hash") != sha256_text(instance.get("test_code") or ""):
        raise RefactoringRejected("Original method hash does not match the schema 4.1 method source.")
    source_path = Path(instance.get("test_file_path") or "")
    if not source_path.is_file():
        raise FileNotFoundError(f"Original test source does not exist: {source_path}")
    original_file = source_path.read_text(encoding="utf-8")
    from .safety_contracts import get_proof, digest
    proof = get_proof(rr['evidence'])
    if proof['binding']['class_hash'] != digest(original_file):
        raise RefactoringRejected('SOURCE_CLASS_CHANGED_SINCE_RISK')
    lines = original_file.splitlines(keepends=True)
    line_range = instance.get("line_range") or {}
    start, end = line_range.get("start"), line_range.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines):
        raise RefactoringRejected("Original test method line range is invalid.")
    current_method = "".join(lines[start - 1:end])
    if normalized_source(current_method) != normalized_source(instance.get("test_code") or ""):
        raise RefactoringRejected("Current source method no longer matches the schema 4.1 method source.")
    return {
        "run_id": request["run_id"], "instance_id": request["instance_id"],
        "smell": smell, "source_file": str(source_path),
        "source_hash": sha256_text(original_file),
        "method_signature": instance["method_signature"],
        "method_start_line": start, "method_end_line": end,
        "original_method_hash": instance["method_source_hash"],
        "source_baseline_hash": sha256_method_text(current_method),
        "risk_decision": rr["decision"], "risk_level": rr["risk_level"],
        "risk_rule_version": rr["rule_version"],
        **({'ra_contract_version': proof['binding']['ra_contract_version'],
            'ra_prompt_version': proof.get('ra_prompt_version')} if smell == 'Redundant_Assertion' else {}),
        "safety_evidence": rr['evidence'],
        "safety_instance": instance,
        **({'mnt_contract_version': proof.get('mnt_rule_version'),
            'mnt_selection_contract_version': proof.get('selection_contract_version'),
            'mnt_generation_permission': proof.get('generation_permission', 'PREAPPROVED_NAME_STATIC_CONTRACT_REQUIRED'),
            'safety_java_environment': (payload.get('context') or {}).get('java_environment') or
            __import__('pipeline.agents.java_type_evidence', fromlist=['engineering_context']).engineering_context(instance)}
           if smell == 'Magic_Number_Test' else {}),
        **({'ctl_rule_version': proof.get('ctl_rule_version')} if smell == 'Conditional_Test_Logic' else {}),
        **({'ect_contract_version': proof.get('ect_contract_version'),
            'ect_path': proof.get('ect_path'),
            'ect_path_contract_version': proof.get('ect_path_contract_version')}
           if smell == 'Exception_Catching_Throwing' else {}),
        "risk_evidence_ids": [item["evidence_id"] for item in rr["evidence"]],
        "allowed_changes": list(rr["allowed_changes"]),
        "forbidden_changes": list(rr["forbidden_changes"]),
        "required_context": list(rr["required_context"]),
        "missing_context": list(rr["missing_context"]),
        "target_scope": "SINGLE_TEST_METHOD", "max_target_methods": 1,
        "production_code_changes_allowed": False,
        "helper_changes_allowed": False, "class_level_changes_allowed": False,
        "import_changes_allowed": False,
    }


def replace_method_in_copy(original_file: str, candidate_method: str, start: int, end: int) -> str:
    lines = original_file.splitlines(keepends=True)
    replacement = (candidate_method.rstrip() + "\n").splitlines(keepends=True)
    lines[start - 1:end] = replacement
    return "".join(lines)
