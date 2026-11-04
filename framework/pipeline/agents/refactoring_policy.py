from __future__ import annotations

from typing import Any


class RefactoringPolicyGuard:
    guard_version = "3.0"

    def apply(self, *, request: dict[str, Any], contract: dict[str, Any], validation: dict[str, Any], provider_type: str, test_provider_injected: bool = False) -> list[dict[str, Any]]:
        payload = request["input"]
        checks = []
        def add(cid, passed, reason): checks.append({"check_id": cid, "passed": bool(passed), "reason": reason})
        add("RISK_APPROVED_ONLY", payload.get("pipeline_state") == "RISK_APPROVED", "Only RISK_APPROVED instances may execute.")
        add("SINGLE_METHOD_SCOPE", contract.get("target_scope") == "SINGLE_TEST_METHOD" and contract.get("max_target_methods") == 1, "One target method is required.")
        add("NO_CLASS_OR_IMPORT_CHANGES", not contract.get("class_level_changes_allowed") and not contract.get("import_changes_allowed"), "Class and import changes are forbidden.")
        add("NO_HELPER_OR_PRODUCTION_CHANGES", not contract.get("helper_changes_allowed") and not contract.get("production_code_changes_allowed"), "Helper and production changes are forbidden.")
        add("RISK_EVIDENCE_PRESENT", bool(contract.get("risk_evidence_ids")), "Risk evidence is mandatory.")
        add("CANDIDATE_VALID", validation.get("passed") is True, "Every critical candidate validation must pass.")
        add("PRODUCTION_PROVIDER_NOT_TEST_DOUBLE", provider_type != "test_double" or test_provider_injected, "Test providers require explicit dependency injection.")
        return checks

    @staticmethod
    def passed(checks: list[dict[str, Any]]) -> bool:
        return all(item["passed"] for item in checks)
