from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.contracts import make_agent_result, validate_contract
from pipeline.hashing import sha256_method_text

from pipeline.agents.base import AgentDisabledError, BaseAgent, load_iterative_repair_agent_config
from pipeline.agents.candidate_validation import CandidateValidator, find_assertion_calls, java_semantic_tokens
from pipeline.agents.prompt_service import IterativeRepairPromptService
from pipeline.agents.refactoring_providers import ExistingLLMRefactoringProvider


class IterativeRepairAgent(BaseAgent):
    agent_name = "iterative_repair"
    agent_version = "1.0"

    def __init__(self, *, enabled: bool | None = None, provider=None, config_path=None, prompt_service=None, validator=None):
        config = load_iterative_repair_agent_config(config_path)
        super().__init__(enabled=config["enabled"] if enabled is None else enabled)
        self.config = config
        self.provider = provider or ExistingLLMRefactoringProvider()
        self.prompt_service = prompt_service or IterativeRepairPromptService()
        self.validator = validator or CandidateValidator()

    @property
    def input_contract(self):
        return ("baseline.method_code", "initial_candidate.method_code", "validation_failure.failed_contracts")

    @property
    def output_contract(self):
        return "iterative_repair/attempts/<attempt_id>/result.json"

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise AgentDisabledError("Agent 'iterative_repair' is disabled and was not executed.")
        self.validate_input(request)
        payload = request["input"]
        reasons = repair_ineligibility_reasons(payload, self.config, self.validator)
        attempt_number = int(payload.get("attempt_number", 1))
        attempt_id = hashlib.sha256(f'{request["run_id"]}:{request["instance_id"]}:{attempt_number}'.encode()).hexdigest()[:16]
        base = self._base_result(payload, attempt_id, attempt_number, reasons)
        if reasons:
            return self._envelope(request, "SUCCESS", {**base, "final_status": "MANUAL_REVIEW_REQUIRED"})
        prompt, prompt_hash = self.prompt_service.build(payload)
        try:
            response = self.provider.generate(request, payload.get("risk_contract") or {}, {"prompt": prompt})
            self.provider.validate_response(response)
            raw = response.get("content") or ""
            validation = self.validator.validate(raw_response=raw, request=payload["original_request"],
                                                 contract=payload["risk_contract"], original_file=payload["source_file_code"])
            scope_checks = validate_repair_edit_scope(payload["initial_candidate"]["method_code"], validation.get("candidate_method"))
            passed = validation["passed"] and all(item["status"] == "PASS" for item in scope_checks)
            checks = validation["checks"] + scope_checks
            candidate = validation.get("candidate_method")
            result = {**base,
                "repair": {"llm_invoked": True, "model": self.provider.provider_name, "prompt_version": self.prompt_service.prompt_version,
                           "prompt_hash": prompt_hash, "allowed_edit_scope": "ASSERTION_MESSAGES_ONLY", "candidate_code": candidate or "",
                           "candidate_hash": sha256_method_text(candidate) if candidate else "", "raw_response": raw, "prompt": prompt},
                "static_validation": {"status": "PASSED" if passed else "FAILED",
                    "passed_contracts": [x["check_id"] for x in checks if x["status"] == "PASS"],
                    "failed_contracts": [x["check_id"] for x in checks if x["status"] == "FAIL"], "evidence": checks},
                "final_status": "REPAIR_READY_FOR_FULL_VALIDATION" if passed else "MANUAL_REVIEW_REQUIRED"}
            return self._envelope(request, "SUCCESS", result)
        except Exception as exc:
            result = {**base, "repair": {**base["repair"], "llm_invoked": True}, "final_status": "MANUAL_REVIEW_REQUIRED"}
            return self._envelope(request, "FAILED", result, [{"code": type(exc).__name__, "message": str(exc)}])

    def _base_result(self, payload, attempt_id, attempt_number, reasons):
        failures = payload.get("validation_failure") or {}
        return {"attempt_id": attempt_id, "attempt_number": attempt_number,
            "eligibility": {"eligible": not reasons, "reason_codes": reasons},
            "input_failure": {"primary_failure_code": failures.get("primary_failure_code"), "failure_codes": failures.get("failure_codes") or []},
            "repair": {"llm_invoked": False, "model": "", "prompt_version": self.prompt_service.prompt_version,
                       "allowed_edit_scope": "ASSERTION_MESSAGES_ONLY", "candidate_code": "", "candidate_hash": ""},
            "static_validation": {"status": "NOT_RUN", "passed_contracts": [], "failed_contracts": [], "evidence": []},
            "full_validation": {"status": "NOT_RUN"}, "evidence_paths": []}

    def _envelope(self, request, status, result, errors=None):
        envelope = make_agent_result(run_id=request["run_id"], instance_id=request["instance_id"], agent=self.agent_name,
                                     agent_version=self.agent_version, status=status, result=result, errors=errors)
        validate_contract(envelope, "agent-result")
        return envelope


from pipeline.verification.repair.policy import repair_ineligibility_reasons, validate_repair_edit_scope
