from __future__ import annotations

from typing import Any

from .risk_evidence import RULE_VERSION
from .risk_types import ASSESSMENT_MODES, RISK_DECISIONS, RiskAssessorNotImplemented


class RuleBasedRiskAssessor:
    assessor_name = "rule_based_risk_assessor"
    assessor_version = "4.1"
    supported_modes = ("rule_only", "rule_first_llm_fallback")

    def assess(
        self,
        request: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        instance = request["input"]["instance"]
        smell = instance["smell_type"]
        from .safety_contracts import get_proof, current_rule_version
        proof = get_proof(evidence)
        if proof is not None:
            decision = proof['decision']
            result = _assessment(decision, 'LOW' if decision.startswith(('REFACTOR', 'SKIP')) else 'HIGH', 1.0,
                                 proof['reason_code'], allowed_changes=proof['allowed_changes'],
                                 missing_context=[proof['reason_code']] if decision == 'REVIEW_INSUFFICIENT_CONTEXT' else [])
            result.update(assessment_mode='rule_only', assessor_name=self.assessor_name,
                          assessor_version=self.assessor_version, rule_version=current_rule_version(smell), evidence=evidence,
                          reason_code=proof['reason_code'], confidence_semantics=proof['confidence_semantics'])
            if instance.get('smell_type') == 'Magic_Number_Test':
                result['mnt_rule_version'] = proof.get('mnt_rule_version')
            if instance.get('smell_type') == 'Conditional_Test_Logic':
                result['ctl_rule_version'] = proof.get('ctl_rule_version')
            return result
        result = _assessment("REVIEW_INSUFFICIENT_CONTEXT", "HIGH", 1.0,
                             "CURRENT_RISK_CONTRACT_REQUIRED", missing_context=["complete_source_bound_proof"])
        result['reason_code'] = 'CURRENT_RISK_CONTRACT_REQUIRED'
        result.update({
            "assessment_mode": "rule_only",
            "assessor_name": self.assessor_name,
            "assessor_version": self.assessor_version,
            "rule_version": current_rule_version(smell),
            "evidence": evidence,
        })
        self.validate_result(result)
        return result

    def validate_result(self, result: dict[str, Any]) -> None:
        if result.get("decision") not in RISK_DECISIONS:
            raise ValueError(f"Invalid rule assessment decision: {result.get('decision')}")
        if result.get("assessment_mode") not in ASSESSMENT_MODES:
            raise ValueError(f"Invalid assessment mode: {result.get('assessment_mode')}")
        confidence = result.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("Risk confidence must be between 0 and 1.")



from .decision_support import ModelDecisionAssessor, validate_advice


class LLMRiskAssessor(ModelDecisionAssessor):
    assessor_name = "llm_risk_assessor"
    assessor_version = "1.0"
    supported_modes = ("rule_llm",)

    def assess(self, request, evidence, context):
        if not request.get('input'):
            raise RiskAssessorNotImplemented('A source-bound Risk request is required; empty legacy placeholder calls are unsupported.')
        return super().assess(request, evidence, context)

    def validate_result(self, result):
        validate_advice(result)


def _assessment(
    decision: str, risk_level: str, confidence: float, reason: str, *,
    allowed_changes: list[str] | None = None,
    missing_context: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "risk_level": risk_level,
        "confidence": confidence,
        "allowed_changes": list(allowed_changes or []),
        "forbidden_changes": [],
        "required_context": [],
        "missing_context": list(missing_context or []),
        "reason": reason,
        "next_action": "",
        "policy_checks": [],
    }


def _fact_map(conclusions: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in conclusions:
        if ":" not in item:
            continue
        key, raw = item.split(":", 1)
        if raw == "true": value: Any = True
        elif raw == "false": value = False
        elif raw.isdigit(): value = int(raw)
        else: value = raw
        result[key] = value
    return result
