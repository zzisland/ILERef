from __future__ import annotations

import hashlib
import re
from typing import Any

from pipeline.agents.base import load_candidate_recovery_config
from pipeline.agents.candidate_validation import (
    CandidateValidator,
    assertion_message_diagnostic,
    extract_single_method,
    java_semantic_tokens,
)
from pipeline.agents.refactoring_contract import normalized_source, sha256_text
from pipeline.agents.risk_evidence import (
    SUPPORTED_ASSERTIONS,
    find_assertion_calls,
    is_message_argument,
    strip_comments_and_literals,
)
from pipeline.hashing import sha256_method_text


UNSUPPORTED_CONTROL = re.compile(r"\b(?:if|else|for|while|do|switch|case|try|catch|finally|assertAll)\b|->")


class CandidateRecoveryService:
    """Deterministically rebuild an Assertion Roulette candidate from baseline semantics."""

    version = "candidate-recovery-v1"

    def __init__(self, *, config_path=None, enabled: bool | None = None, validator=None):
        config = load_candidate_recovery_config(config_path)
        if enabled is not None:
            config["enabled"] = bool(enabled)
        self.config = config
        self.enabled = config["enabled"]
        self.validator = validator or CandidateValidator()

    def recover(self, request: dict[str, Any]) -> dict[str, Any]:
        recovery_id = hashlib.sha256(
            ":".join((str(request.get("run_id") or ""), str(request.get("instance_id") or ""),
                      str((request.get("baseline") or {}).get("source_hash") or ""),
                      str((request.get("initial_candidate") or {}).get("candidate_hash") or ""), "1")).encode()
        ).hexdigest()[:16]
        result = self._base(request, recovery_id)
        reasons = self._basic_reasons(request)
        if reasons:
            result["eligibility"] = {"eligible": False, "reason_codes": reasons}
            result["final_status"] = "RECOVERY_NOT_ELIGIBLE"
            return result
        baseline, candidate = request["baseline"]["method_code"], request["initial_candidate"]["method_code"]
        mapping, map_reasons = self._mapping(baseline, candidate, request)
        result["mapping"] = mapping
        if map_reasons:
            result["eligibility"] = {"eligible": False, "reason_codes": map_reasons}
            result["final_status"] = "RECOVERY_MAPPING_FAILED" if any("MAPPING" in x for x in map_reasons) else "RECOVERY_NOT_ELIGIBLE"
            return result
        baseline_method, _ = extract_single_method(baseline)
        recovered, reused, rejected = self._reconstruct(baseline_method or baseline, mapping["evidence"])
        result["recovery"].update({"recovered_candidate": recovered, "candidate_hash": sha256_method_text(recovered),
                                   "reused_messages": reused, "rejected_messages": rejected})
        if rejected:
            result["eligibility"] = {"eligible": False, "reason_codes": ["CANDIDATE_MESSAGE_NOT_REUSABLE"]}
            result["final_status"] = "RECOVERY_NOT_ELIGIBLE"
            return result
        validation = self.validator.validate(raw_response=recovered, request=request["original_request"],
                                             contract=request["risk_contract"], original_file=request["source_file_code"])
        checks = validation["checks"]
        passed = validation["passed"]
        result["eligibility"] = {"eligible": True, "reason_codes": []}
        result["static_validation"] = {"status": "PASSED" if passed else "FAILED",
            "passed_contracts": [x["check_id"] for x in checks if x["status"] == "PASS"],
            "failed_contracts": [x["check_id"] for x in checks if x["status"] == "FAIL"], "evidence": checks}
        result["final_status"] = "RECOVERY_READY_FOR_FULL_VALIDATION" if passed else "RECOVERY_STATIC_VALIDATION_FAILED"
        return result

    def _base(self, request, recovery_id):
        return {"schema_version": "1.0", "run_id": request.get("run_id"), "source_run_id": request.get("source_run_id"),
            "instance_id": request.get("instance_id"), "component": "candidate_recovery", "recovery_id": recovery_id,
            "eligibility": {"eligible": False, "reason_codes": []},
            "mapping": {"status": "NOT_RUN", "assertion_count": 0, "mapped_assertions": 0,
                        "ambiguous_assertions": [], "evidence": []},
            "recovery": {"mode": "BASELINE_ASSERTION_RECONSTRUCTION", "llm_invoked": False,
                         "recovered_candidate": "", "candidate_hash": "", "reused_messages": [], "rejected_messages": []},
            "static_validation": {"status": "NOT_RUN", "passed_contracts": [], "failed_contracts": [], "evidence": []},
            "full_validation": {"status": "NOT_RUN"}, "final_status": "RECOVERY_ELIGIBILITY_CHECKED", "evidence_paths": []}

    def _basic_reasons(self, request):
        reasons = []
        if not self.enabled: reasons.append("RECOVERY_DISABLED")
        if request.get("smell_type") != "Assertion_Roulette": reasons.append("UNSUPPORTED_SMELL")
        if int(request.get("attempt_number", 1)) != 1: reasons.append("MAX_ATTEMPTS_REACHED")
        baseline, candidate = request.get("baseline") or {}, request.get("initial_candidate") or {}
        if not baseline.get("method_code") or not candidate.get("method_code"): reasons.append("CANDIDATE_OR_BASELINE_MISSING")
        if baseline.get("parse_status") != "PASSED" or candidate.get("parse_status") != "PASSED": reasons.append("PARSE_STATUS_UNVERIFIED")
        if not request.get("baseline_hash_verified"): reasons.append("BASELINE_HASH_UNVERIFIED")
        if baseline.get("method_code") and baseline.get("source_hash") != sha256_method_text(baseline["method_code"]): reasons.append("BASELINE_HASH_MISMATCH")
        if candidate.get("method_code") and candidate.get("candidate_hash") != sha256_method_text(candidate["method_code"]): reasons.append("INITIAL_CANDIDATE_HASH_MISMATCH")
        if not request.get("source_run_evidence_complete"): reasons.append("SOURCE_EVIDENCE_INCOMPLETE")
        risk_evidence = (((request.get("original_request") or {}).get("input") or {}).get("risk_result") or {}).get("result",{}).get("evidence") or []
        if not any(item.get("conclusion") == "JUNIT5_ASSERTIONS_AVAILABLE" for item in risk_evidence):
            reasons.append("JUNIT5_CONTEXT_UNVERIFIED")
        failure = request.get("validation_failure") or {}
        failed = failure.get("failed_contracts") or []
        coverage = next((x for x in failed if x.get("check_id") == "ONLY_TARGET_MESSAGES_ADDED"), None)
        if not coverage: reasons.append("RECOVERABLE_FAILURE_EVIDENCE_MISSING")
        elif not any(x.get("semantic_arguments_equal") is False for x in coverage.get("evidence") or []): reasons.append("PURE_MESSAGE_FAILURE_USE_ITERATIVE_REPAIR")
        return reasons

    def _mapping(self, baseline: str, candidate: str, request: dict[str, Any]):
        evidence, reasons, ambiguous = [], [], []
        base_method, base_checks = extract_single_method(baseline)
        cand_method, cand_checks = extract_single_method(candidate)
        if not base_method or any(x["status"] == "FAIL" for x in base_checks): reasons.append("BASELINE_PARSE_FAILED")
        if not cand_method or any(x["status"] == "FAIL" for x in cand_checks): reasons.append("CANDIDATE_PARSE_FAILED")
        if reasons: return {"status":"FAILED","assertion_count":0,"mapped_assertions":0,"ambiguous_assertions":[],"evidence":[]}, reasons
        masked_base, masked_candidate = strip_comments_and_literals(base_method), strip_comments_and_literals(cand_method)
        if UNSUPPORTED_CONTROL.search(masked_base) or UNSUPPORTED_CONTROL.search(masked_candidate): reasons.append("UNSUPPORTED_CONTROL_FLOW")
        original_contract = request["risk_contract"]
        # Generic validator checks signature/annotations/source; perform them before reconstruction too.
        generic = self.validator._generic(cand_method, base_method, original_contract, request["source_file_code"])
        generic_failures = {x["check_id"] for x in generic if x["status"] == "FAIL"}
        mapping_blockers = {"METHOD_SIGNATURE_UNCHANGED", "TEST_ANNOTATIONS_UNCHANGED", "SOURCE_HASH_MATCH", "NO_PLACEHOLDER"}
        if generic_failures & mapping_blockers: reasons.extend("INITIAL_" + x for x in sorted(generic_failures & mapping_blockers))
        old, new = find_assertion_calls(base_method), find_assertion_calls(cand_method)
        if len(old) != len(new): reasons.append("ASSERTION_COUNT_CHANGED")
        elif [x["name"] for x in old] != [x["name"] for x in new]: reasons.append("ASSERTION_ORDER_CHANGED")
        if java_semantic_tokens(_outside(base_method, old)) != java_semantic_tokens(_outside(cand_method, new)):
            reasons.append("NON_ASSERTION_CODE_CHANGED")
        # Every V1 assertion is a direct METHOD_ROOT statement. Its stable
        # statement/assertion ordinal distinguishes repeated source-identical
        # calls without relying on the possibly drifted argument values.
        if reasons: return {"status":"FAILED","assertion_count":len(old),"mapped_assertions":0,"ambiguous_assertions":ambiguous,"evidence":[]}, reasons
        for index,(before,after) in enumerate(zip(old,new),1):
            api=before["name"]
            if api not in SUPPORTED_ASSERTIONS: reasons.append("UNSUPPORTED_ASSERTION_API"); break
            minimum=SUPPORTED_ASSERTIONS[api]; bargs=before.get("arguments") or []; cargs=after.get("arguments") or []
            if len(bargs)==minimum+1 and is_message_argument(bargs[-1]):
                if "->" in bargs[-1]: reasons.append("MESSAGE_SUPPLIER_UNSUPPORTED"); break
                baseline_message=bargs[-1]; baseline_non=bargs[:-1]
                if cargs != bargs: reasons.append("BASELINE_MESSAGE_MUST_BE_PRESERVED"); break
                message=baseline_message; source="BASELINE"
            elif len(bargs)==minimum and len(cargs)==minimum+1 and is_message_argument(cargs[-1]) and "->" not in cargs[-1]:
                baseline_message=None; baseline_non=bargs; message=cargs[-1]; source="INITIAL_CANDIDATE"
            else:
                reasons.append("UNSUPPORTED_ASSERTION_OVERLOAD"); break
            diagnostic=assertion_message_diagnostic(before,message)
            identity={"method_signature":request.get("method_identity",{}).get("method_signature"),"control_path":"METHOD_ROOT",
                      "statement_ordinal":index,"assertion_ordinal":index,"api":api}
            evidence.append({"identity":identity,"baseline_start":before["start"],"baseline_end":before["end"],
                "candidate_start":after["start"],"candidate_end":after["end"],"baseline_non_message_arguments":baseline_non,
                "candidate_non_message_arguments":cargs[:-1],"message_literal":message,"message_source":source,
                "message_diagnostic":diagnostic,"parameter_drift":java_semantic_tokens(','.join(baseline_non)) != java_semantic_tokens(','.join(cargs[:-1]))})
        if reasons: return {"status":"FAILED","assertion_count":len(old),"mapped_assertions":len(evidence),"ambiguous_assertions":ambiguous,"evidence":evidence}, reasons
        return {"status":"UNIQUE","assertion_count":len(old),"mapped_assertions":len(evidence),"ambiguous_assertions":[],"evidence":evidence}, []

    def _reconstruct(self, baseline: str, mappings: list[dict[str, Any]]):
        recovered, reused, rejected = baseline, [], []
        for item in reversed(mappings):
            diagnostic=item["message_diagnostic"]
            if diagnostic["reasons"]:
                rejected.append({"identity":item["identity"],"message":diagnostic["message"],"reasons":diagnostic["reasons"]})
                continue
            args=list(item["baseline_non_message_arguments"])
            args.append(item["message_literal"])
            replacement=f'{item["identity"]["api"]}(' + ", ".join(args) + ")"
            recovered=recovered[:item["baseline_start"]]+replacement+recovered[item["baseline_end"]:]
            reused.append({"identity":item["identity"],"message":diagnostic["message"],"source":item["message_source"]})
        reused.reverse(); rejected.reverse()
        return recovered,reused,rejected


def _outside(source: str, assertions: list[dict[str, Any]]) -> str:
    pieces=[]; cursor=0
    for index,item in enumerate(assertions):
        pieces.extend((source[cursor:item["start"]],f" __ASSERTION_{index}__ ")); cursor=item["end"]
    pieces.append(source[cursor:]); return "".join(pieces)
