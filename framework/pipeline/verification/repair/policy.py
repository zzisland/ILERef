from __future__ import annotations
from typing import Any
from pipeline.agents.candidate_validation import CandidateValidator, find_assertion_calls, java_semantic_tokens

def repair_ineligibility_reasons(payload: dict[str, Any], config: dict[str, Any], validator: CandidateValidator) -> list[str]:
    reasons = []
    if payload.get("smell_type") != "Assertion_Roulette": reasons.append("UNSUPPORTED_SMELL")
    if int(payload.get("attempt_number", 1)) > config["max_attempts"]: reasons.append("MAX_ATTEMPTS_REACHED")
    baseline, initial = payload.get("baseline") or {}, payload.get("initial_candidate") or {}
    if not baseline.get("method_code") or not initial.get("method_code"): reasons.append("CANDIDATE_OR_BASELINE_MISSING")
    if not payload.get("baseline_hash_verified"): reasons.append("BASELINE_HASH_UNVERIFIED")
    if not payload.get("source_run_evidence_complete"): reasons.append("SOURCE_EVIDENCE_INCOMPLETE")
    failure = payload.get("validation_failure") or {}; codes = set(failure.get("failure_codes") or [])
    if not codes: reasons.append("FAILURE_EVIDENCE_MISSING")
    if codes - set(config["allowed_failure_codes"]): reasons.append("NON_WHITELIST_FAILURE")
    coverage = next((x for x in failure.get("failed_contracts") or [] if x.get("check_id") == "ONLY_TARGET_MESSAGES_ADDED"), None)
    if coverage and any(item.get("semantic_arguments_equal") is False for item in (coverage.get("evidence") or [])):
        reasons.append("INITIAL_NON_MESSAGE_ARGUMENT_DRIFT")
    # Re-evaluate with the current validator. A historical validator false
    # rejection is not a repair task (for example, testGroovy after its fix).
    if not reasons:
        current = validator.validate(raw_response=initial["method_code"], request=payload["original_request"],
                                     contract=payload["risk_contract"], original_file=payload["source_file_code"])
        current_failures = {x["check_id"] for x in current["checks"] if x["status"] == "FAIL"}
        cascade_failures={"TARGET_ASSERTION_MESSAGE_MISSING","NESTED_ASSERTION_NOT_REFACTORED"}
        if "ONLY_TARGET_MESSAGES_ADDED" in current_failures:
            current_failures=current_failures-cascade_failures
        if not current_failures: reasons.append("INITIAL_CANDIDATE_NOW_VALID")
        elif current_failures - set(config["allowed_failure_codes"]): reasons.append("CURRENT_NON_WHITELIST_FAILURE")
        elif "MESSAGES_MEANINGFUL" in current_failures and "ONLY_TARGET_MESSAGES_ADDED" in current_failures:
            quality = next(x for x in current["checks"] if x["check_id"] == "MESSAGES_MEANINGFUL")
            if quality.get("evidence"): reasons.append("NON_CASCADE_QUALITY_FAILURE_WITH_COVERAGE_FAILURE")
    return reasons


def validate_repair_edit_scope(initial: str, repaired: str | None) -> list[dict[str, Any]]:
    def item(name, passed): return {"check_id": name, "status": "PASS" if passed else "FAIL", "severity": "ERROR",
                                    "message": name.replace("_", " ").title(), "evidence": None, "expected": None, "actual": None}
    if not repaired:
        return [item("REPAIR_CANDIDATE_PRESENT", False)]
    old, new = find_assertion_calls(initial), find_assertion_calls(repaired)
    count = len(old) == len(new)
    order = count and [x["name"] for x in old] == [x["name"] for x in new]
    args_ok = order
    if args_ok:
        for before, after in zip(old, new):
            a, b = before.get("arguments"), after.get("arguments")
            if a is None or b is None: args_ok = False; break
            # Initial candidate may already have a message or may have missed it.
            if len(b) == len(a):
                args_ok = all(java_semantic_tokens(x) == java_semantic_tokens(y) for x, y in zip(a[:-1], b[:-1]))
            elif len(b) == len(a) + 1:
                args_ok = all(java_semantic_tokens(x) == java_semantic_tokens(y) for x, y in zip(a, b[:-1]))
            else: args_ok = False
            if not args_ok: break
    # Full baseline contract separately protects non-assertion code/signature.
    return [item("REPAIR_ASSERTION_COUNT_UNCHANGED", count), item("REPAIR_ASSERTION_ORDER_UNCHANGED", order),
            item("REPAIR_ONLY_MESSAGES_CHANGED", args_ok)]
