from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .risk_types import SUPPORTED_RISK_SMELLS


COMMON_FORBIDDEN_CHANGES = [
    "DELETE_TEST_METHOD",
    "CHANGE_METHOD_SIGNATURE",
    "CHANGE_TEST_ANNOTATION",
    "MODIFY_CLASS_STRUCTURE",
    "CREATE_OR_MODIFY_HELPER",
    "MODIFY_NON_TARGET_METHOD",
    "MODIFY_PRODUCTION_CODE",
    "PROCESS_MULTIPLE_TARGET_SMELLS",
]


class RiskPolicyGuard:
    guard_name = "risk_policy_guard"
    guard_version = "4.1"

    def __init__(self, policy_path: str | Path | None = None):
        self.policy = load_risk_policy(policy_path)
        self.rule_version = self.policy["rule_version"]

    def apply(
        self,
        assessment: dict[str, Any],
        *,
        request: dict[str, Any],
        provider_kind: str = "rule",
    ) -> dict[str, Any]:
        result = deepcopy(assessment)
        instance = request["input"]["instance"]
        checks: list[dict[str, Any]] = []

        def check(check_id: str, passed: bool, reason: str) -> None:
            checks.append({"check_id": check_id, "passed": passed, "reason": reason})

        supported = instance["smell_type"] in SUPPORTED_RISK_SMELLS
        from .safety_contracts import VERSION, current_rule_version
        version_ok = self.rule_version == VERSION
        configured_smell_version = self.policy['smell_rule_versions'].get(instance['smell_type'])
        if configured_smell_version is not None:
            version_ok = version_ok and configured_smell_version == current_rule_version(instance['smell_type'])
        check('CURRENT_RULE_VERSION', version_ok, 'Configured rules must match the executable safety contract version.')
        supported = supported and instance["smell_type"] in self.policy["supported_smells"]
        check("SUPPORTED_SMELL", supported, "Only explicitly supported risk contracts may be approved.")
        target_smells = request["input"].get("target_smells") or [instance["smell_type"]]
        single_target = len(target_smells) == 1 and target_smells[0] == instance["smell_type"]
        check("SINGLE_TARGET_SMELL", single_target, "One Risk Agent execution must assess one target smell.")
        no_delete = not request["input"].get("delete_test_method", False)
        no_delete = no_delete and request["input"].get("legacy_action") != "REMOVE"
        check("NO_TEST_METHOD_DELETION", no_delete, "Test method deletion is never automatically approved.")
        context_complete = not result.get("missing_context")
        check("REQUIRED_CONTEXT_AVAILABLE", context_complete, "Missing required context blocks low-risk approval.")
        provider_allowed = provider_kind == "rule"
        check("ASSESSOR_IMPLEMENTED", provider_allowed, "Unimplemented or failed LLM assessment cannot approve risk.")
        evidence_valid, evidence_reason = valid_low_risk_evidence(result, instance, request['input'].get('context') or {})
        check("LOW_RISK_EVIDENCE_VALID", evidence_valid, evidence_reason)

        if result.get("decision") == "REFACTOR_LOW_RISK":
            failed = [item for item in checks if not item["passed"]]
            if failed:
                context_ids = {"REQUIRED_CONTEXT_AVAILABLE", "LOW_RISK_EVIDENCE_VALID"}
                context_failure = all(item["check_id"] in context_ids for item in failed)
                result["decision"] = (
                    "REVIEW_INSUFFICIENT_CONTEXT" if context_failure else "REVIEW_HIGH_RISK"
                )
                result["risk_level"] = "HIGH"
                result["confidence"] = 1.0
                result["reason"] = "RiskPolicyGuard rejected automatic approval: " + "; ".join(
                    item["reason"] for item in failed
                )
                result["allowed_changes"] = []
                result['reason_code'] = 'POLICY_GUARD_REJECTED:' + ','.join(item['check_id'] for item in failed)

        forbidden = set(list(result.get("forbidden_changes") or []) + COMMON_FORBIDDEN_CHANGES)
        if instance['smell_type'] == 'Exception_Catching_Throwing':
            from .safety_contracts import get_proof
            proof = get_proof(result.get('evidence') or []) or {}
            if (proof.get('ect_path') == 'NO_THROW_ASSERT_DOES_NOT_THROW'
                    and 'REMOVE_APPROVED_THROWS_CLAUSE' in (result.get('allowed_changes') or [])):
                # The single source-bound throws-clause deletion is authorized;
                # every other method identity/signature element remains frozen.
                forbidden.discard('CHANGE_METHOD_SIGNATURE')
                forbidden.add('CHANGE_METHOD_NAME_RETURN_PARAMETERS_ANNOTATIONS_OR_OTHER_MODIFIERS')
        result["forbidden_changes"] = sorted(forbidden)
        result["policy_checks"] = checks
        result["next_action"] = {
            "REFACTOR_LOW_RISK": "REFACTOR",
            "SKIP_FALSE_POSITIVE": "STOP",
            "SKIP_NOT_APPLICABLE": "STOP",
            "REVIEW_HIGH_RISK": "MANUAL_REVIEW",
            "REVIEW_INSUFFICIENT_CONTEXT": "MANUAL_REVIEW",
        }[result["decision"]]
        return result


def valid_low_risk_evidence(
    assessment: dict[str, Any], instance: dict[str, Any], context: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if assessment.get("decision") != "REFACTOR_LOW_RISK":
        return True, "Evidence approval constraints apply only to low-risk decisions."
    from .safety_contracts import current_rule_version, get_proof, verify_proof
    if assessment.get('rule_version') != current_rule_version(instance.get('smell_type')):
        return False, 'RULE_VERSION_REAUDIT_REQUIRED'
    proof = get_proof(assessment.get('evidence') or [])
    if not proof or assessment.get('allowed_changes') != proof.get('allowed_changes'):
        return False, 'CHANGE_AUTHORITY_MISMATCH'
    return verify_proof(assessment.get('evidence') or [], instance, context or {})


def load_risk_policy(path: str | Path | None = None) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    policy_path = Path(path) if path else project_root / "config" / "decision_policy.yaml"
    if not policy_path.is_absolute():
        policy_path = (project_root / policy_path).resolve()
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    risk = payload.get("risk_assessment")
    if not isinstance(risk, dict):
        raise ValueError("decision_policy.yaml must define risk_assessment.")
    supported = risk.get("supported_smells")
    constraints = risk.get("hard_constraints")
    requirements = risk.get("context_requirements")
    if not isinstance(supported, list) or not supported:
        raise ValueError("risk_assessment.supported_smells must be a non-empty list.")
    if not isinstance(constraints, dict) or not isinstance(requirements, dict):
        raise ValueError("Risk hard_constraints and context_requirements must be mappings.")
    permissive = sorted(key for key, value in constraints.items() if value is not False)
    if permissive:
        raise ValueError(
            "Phase 2 hard constraints must remain false: " + ", ".join(permissive)
        )
    return {
        "rule_version": str(risk.get("rule_version") or ""),
        "smell_rule_versions": dict(risk.get('smell_rule_versions') or {}),
        "supported_smells": set(str(item) for item in supported),
        "hard_constraints": constraints,
        "context_requirements": requirements,
    }
