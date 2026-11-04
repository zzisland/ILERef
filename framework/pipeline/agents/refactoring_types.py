from __future__ import annotations

from typing import Any, Protocol


SUPPORTED_REFACTORING_SMELLS = frozenset({
    "Assertion_Roulette", "Redundant_Assertion", "Exception_Catching_Throwing", "Magic_Number_Test", "Conditional_Test_Logic",
})
REFACTORING_DECISIONS = frozenset({
    "CANDIDATE_GENERATED", "REFACTORING_REJECTED", "REFACTORING_FAILED",
})


class RefactoringProvider(Protocol):
    provider_name: str
    provider_version: str
    provider_type: str
    supported_smells: frozenset[str]
    supported_modes: tuple[str, ...]

    def generate(self, request: dict[str, Any], contract: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]: ...
    def validate_response(self, response: dict[str, Any]) -> None: ...


class RefactoringRejected(ValueError):
    pass
