"""Repair orchestration; every repaired candidate re-enters CandidateValidator."""
import json
from pathlib import Path
from pipeline.agents.iterative_repair import IterativeRepairAgent
from pipeline.services.candidate_recovery import CandidateRecoveryService
from pipeline.contracts import make_agent_request
from pipeline.hashing import sha256_method_text, sha256_utf8_lf, METHOD_HASH_VERSION

def attempt_iterative_repair(self, instance, original_request, initial_result, store):
    agent = self.iterative_repair_agent or IterativeRepairAgent(config_path=self.agent_config_path)
    if not agent.enabled:
        return initial_result
    details = initial_result["result"]
    contract = details.get("risk_contract") or {}
    raw = details.get("raw_response") or ""
    failed = [item for item in details.get("validation_checks") or [] if item.get("status") == "FAIL"]
    source_path = Path(contract.get("source_file") or "")
    source_code = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    lines = source_code.splitlines(keepends=True)
    start, end = contract.get("method_start_line"), contract.get("method_end_line")
    source_method = "".join(lines[start - 1:end]) if isinstance(start, int) and isinstance(end, int) else ""
    primary = "ONLY_TARGET_MESSAGES_ADDED" if any(x.get("check_id") == "ONLY_TARGET_MESSAGES_ADDED" for x in failed) else (failed[0].get("check_id") if failed else None)
    payload = {
        "source_run_id": self.run_id, "smell_type": instance["smell_type"], "attempt_number": 1,
        "method_identity": {"module": instance.get("module_path"), "relative_path": instance.get("test_relative_path"),
            "package_name": instance.get("test_package_name"), "method_signature": instance.get("method_signature"),
            "start_line": start, "end_line": end},
        "baseline": {"method_code": instance["test_code"], "source_hash": contract.get("source_baseline_hash")},
        "initial_candidate": {"method_code": raw, "candidate_hash": sha256_method_text(raw) if raw else "", "generation_id": "initial"},
        "validation_failure": {"primary_failure_code": primary, "failure_codes": [x.get("check_id") for x in failed],
            "failed_contracts": failed, "evidence": [x.get("evidence") for x in failed]},
        "repair_policy": agent.config, "context": original_request["input"].get("context") or {},
        "baseline_hash_verified": bool(source_method and sha256_method_text(source_method) == contract.get("source_baseline_hash")),
        "source_run_evidence_complete": bool(raw and failed and source_code and contract),
        "original_request": original_request, "risk_contract": contract, "source_file_code": source_code,
    }
    repair_request = make_agent_request(run_id=self.run_id, instance_id=instance["instance_id"], agent="iterative_repair",
                                        agent_version="1.0", input_payload=payload)
    repair_result = agent.execute(repair_request)
    attempt = repair_result["result"]; attempt_id = attempt["attempt_id"]
    prefix = f"attempts/{attempt_id}"
    store.write_json("iterative_repair", f"{prefix}/request.json", repair_request)
    store.write_json("iterative_repair", f"{prefix}/initial_result.json", initial_result)
    store.write_text("iterative_repair", f"{prefix}/initial_candidate.java", raw)
    store.write_json("iterative_repair", f"{prefix}/failure_evidence.json", payload["validation_failure"])
    repair = attempt.get("repair") or {}
    store.write_text("iterative_repair", f"{prefix}/prompt.txt", repair.get("prompt") or "")
    store.write_text("iterative_repair", f"{prefix}/raw_response.txt", repair.get("raw_response") or "")
    if repair.get("candidate_code"):
        store.write_text("iterative_repair", f"{prefix}/repaired_candidate.java", repair["candidate_code"])
    store.write_json("iterative_repair", f"{prefix}/static_validation.json", attempt.get("static_validation") or {})
    store.write_json("iterative_repair", f"{prefix}/result.json", repair_result)
    if attempt.get("final_status") != "REPAIR_READY_FOR_FULL_VALIDATION":
        return initial_result
    candidate = repair["candidate_code"]
    repaired = json.loads(json.dumps(initial_result))
    rd = repaired["result"]
    rd["decision"] = "CANDIDATE_GENERATED"; rd["validation_checks"] = attempt["static_validation"]["evidence"]
    rd["raw_response"] = candidate; rd["prompt"] = repair.get("prompt") or ""; rd["reason"] = "Iterative repair candidate passed all static contracts."
    rd["next_action"] = "SAVE_CANDIDATE"; rd["generation_attempts"] = 1
    rd["candidate"] = {"method_code": candidate, "file_code": None, "method_sha256": sha256_utf8_lf(candidate),
        "method_sha256_lf": sha256_utf8_lf(candidate), "method_hash_version": METHOD_HASH_VERSION,
        "source_baseline_hash": contract["source_baseline_hash"], "candidate_original_hash": contract["source_baseline_hash"],
        "candidate_refactored_hash": sha256_method_text(candidate), "file_sha256": None, "parsed": True}
    rd["iterative_repair"] = {"attempt_id": attempt_id, "result_ref": f"iterative_repair/{prefix}/result.json"}
    # Agent-result schema intentionally remains compatible with old readers;
    # the detailed repair result is stored in its own validated envelope.
    rd.pop("iterative_repair")
    return repaired


def attempt_candidate_recovery(self, instance, original_request, initial_result, store):
    service = self.candidate_recovery_service or CandidateRecoveryService(config_path=self.agent_config_path)
    if not service.enabled:
        return initial_result
    details = initial_result["result"]
    contract = details.get("risk_contract") or {}
    raw = details.get("raw_response") or ""
    failed = [item for item in details.get("validation_checks") or [] if item.get("status") == "FAIL"]
    source_path = Path(contract.get("source_file") or "")
    source_code = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    lines = source_code.splitlines(keepends=True)
    start, end = contract.get("method_start_line"), contract.get("method_end_line")
    source_method = "".join(lines[start - 1:end]) if isinstance(start, int) and isinstance(end, int) else ""
    primary = "ONLY_TARGET_MESSAGES_ADDED" if any(x.get("check_id") == "ONLY_TARGET_MESSAGES_ADDED" for x in failed) else (failed[0].get("check_id") if failed else None)
    request = {
        "schema_version": "1.0", "run_id": self.run_id, "source_run_id": self.run_id,
        "instance_id": instance["instance_id"], "smell_type": instance["smell_type"], "attempt_number": 1,
        "method_identity": {"module": instance.get("module_path"), "relative_path": instance.get("test_relative_path"),
            "package_name": instance.get("test_package_name"), "method_signature": instance.get("method_signature"),
            "start_line": start, "end_line": end},
        "baseline": {"method_code": source_method, "source_hash": contract.get("source_baseline_hash"), "parse_status": "PASSED"},
        "initial_candidate": {"method_code": raw, "candidate_hash": sha256_method_text(raw) if raw else "",
                              "generation_id": "initial", "parse_status": "PASSED"},
        "validation_failure": {"primary_failure_code": primary, "failure_codes": [x.get("check_id") for x in failed],
            "failed_contracts": failed, "evidence": [x.get("evidence") for x in failed]},
        "recovery_policy": service.config,
        "baseline_hash_verified": bool(source_method and sha256_method_text(source_method) == contract.get("source_baseline_hash")),
        "source_run_evidence_complete": bool(raw and failed and source_code and contract),
        "original_request": original_request, "risk_contract": contract, "source_file_code": source_code,
    }
    recovery = service.recover(request)
    recovery_id = recovery["recovery_id"]
    prefix = f"attempts/{recovery_id}"
    request_path = store.write_json("candidate_recovery", f"{prefix}/request.json", request)
    store.write_json("candidate_recovery", f"{prefix}/initial_result.json", initial_result)
    store.write_text("candidate_recovery", f"{prefix}/initial_candidate.java", raw)
    store.write_json("candidate_recovery", f"{prefix}/mapping.json", recovery.get("mapping") or {})
    candidate = recovery.get("recovery", {}).get("recovered_candidate") or ""
    if candidate:
        store.write_text("candidate_recovery", f"{prefix}/recovered_candidate.java", candidate)
    store.write_json("candidate_recovery", f"{prefix}/static_validation.json", recovery.get("static_validation") or {})
    result_path = store.write_json("candidate_recovery", f"{prefix}/result.json", recovery)
    recovery["evidence_paths"] = [str(request_path), str(result_path)]
    if recovery.get("final_status") != "RECOVERY_READY_FOR_FULL_VALIDATION":
        return initial_result
    recovered = json.loads(json.dumps(initial_result))
    rd = recovered["result"]
    rd["decision"] = "CANDIDATE_GENERATED"
    rd["validation_checks"] = recovery["static_validation"]["evidence"]
    rd["raw_response"] = candidate
    rd["reason"] = "Candidate Recovery rebuilt baseline assertion semantics and passed all static contracts."
    rd["next_action"] = "SAVE_CANDIDATE"
    rd["candidate"] = {"method_code": candidate, "file_code": None, "method_sha256": sha256_utf8_lf(candidate),
        "method_sha256_lf": sha256_utf8_lf(candidate), "method_hash_version": METHOD_HASH_VERSION,
        "source_baseline_hash": contract["source_baseline_hash"], "candidate_original_hash": contract["source_baseline_hash"],
        "candidate_refactored_hash": sha256_method_text(candidate), "file_sha256": None, "parsed": True}
    return recovered


