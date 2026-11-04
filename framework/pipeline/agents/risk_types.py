from __future__ import annotations

from typing import Any, Protocol


SUPPORTED_RISK_SMELLS = {
    "Assertion_Roulette",
    "Redundant_Assertion",
    "Exception_Catching_Throwing",
    "Magic_Number_Test", "Conditional_Test_Logic",
}
ASSESSMENT_MODES = {
    "rule_llm",
    "rule_only",
    "rule_first_llm_fallback",
    "llm_assisted",
}
RISK_DECISIONS = {
    "REFACTOR_LOW_RISK",
    "SKIP_FALSE_POSITIVE",
    "SKIP_NOT_APPLICABLE",
    "REVIEW_HIGH_RISK",
    "REVIEW_INSUFFICIENT_CONTEXT",
}


class RiskAssessmentProvider(Protocol):
    assessor_name: str
    assessor_version: str
    supported_modes: tuple[str, ...]

    def assess(
        self,
        request: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]: ...

    def validate_result(self, result: dict[str, Any]) -> None: ...


class RiskAssessorNotImplemented(NotImplementedError):
    pass
