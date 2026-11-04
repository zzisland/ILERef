from __future__ import annotations

from typing import Any


class LlmTerminalFailure(RuntimeError):
    """Base signal for external LLM failures that must stop the entire run."""

    category = "LLM_TERMINAL_FAILURE"

    def __init__(self, *, stage: str, instance_id: str):
        self.stage = stage
        self.instance_id = instance_id
        super().__init__(
            f"External LLM terminal failure during {stage} for instance {instance_id}."
        )


class LlmBudgetExhausted(LlmTerminalFailure):
    """Terminal run-level signal for an external LLM credit exhaustion."""

    category = "LLM_BUDGET_EXHAUSTED"

    def __init__(self, *, stage: str, instance_id: str):
        self.stage = stage
        self.instance_id = instance_id
        super().__init__(
            stage=stage, instance_id=instance_id
        )

    def __str__(self) -> str:
        return f"External LLM budget exhausted during {self.stage} for instance {self.instance_id}."


class LlmAuthenticationFailed(LlmTerminalFailure):
    """Terminal run-level signal for an explicitly invalid API credential."""

    category = "LLM_AUTHENTICATION_FAILED"

    def __str__(self) -> str:
        return f"External LLM authentication failed during {self.stage} for instance {self.instance_id}."


class LlmExternalServiceFailed(LlmTerminalFailure):
    """Fail closed on an unexpected production-provider exception.

    RefactoringAgent deliberately returns a structured failed envelope so its
    callers can persist diagnostics.  A natural run must not, however, turn a
    provider exception into hundreds of ordinary manual-review decisions and
    then report the project as COMPLETED.
    """

    category = "LLM_EXTERNAL_SERVICE_FAILED"

    def __init__(self, *, stage: str, instance_id: str, error_code: str = "UNKNOWN",
                 diagnostic: str | None = None):
        self.error_code = error_code
        self.diagnostic = diagnostic
        super().__init__(stage=stage, instance_id=instance_id)

    def __str__(self) -> str:
        message = (
            f"External LLM service failed during {self.stage} for instance "
            f"{self.instance_id} (error_code={self.error_code})."
        )
        if self.diagnostic:
            message += f" Diagnostic: {self.diagnostic}"
        return message


def is_llm_budget_exhaustion(value: Any) -> bool:
    """Recognize explicit balance/credit failures without treating all 401/429s alike."""
    text = _failure_text(value).lower()
    if "creditserror" in text or "insufficient balance" in text:
        return True
    if "insufficient_balance" in text or "insufficient credits" in text:
        return True
    if "payment required" in text and ("credit" in text or "balance" in text):
        return True
    return "402" in text and ("credit" in text or "balance" in text)


def is_llm_authentication_failure(value: Any) -> bool:
    """Recognize explicit invalid credentials, not regional or permission failures."""
    text = _failure_text(value).lower()
    return any(marker in text for marker in (
        "invalid api key",
        "incorrect api key",
        "invalid_api_key",
        "api key is invalid",
    ))


def budget_failure_evidence(value: Any) -> dict[str, Any]:
    """Return bounded, credential-free evidence for the stop artifact."""
    errors = []
    if isinstance(value, dict):
        candidates = list(value.get("errors") or [])
        result = value.get("result") or {}
        if isinstance(result, dict):
            candidates.extend(result.get("agent_errors") or [])
        for item in candidates:
            if isinstance(item, dict):
                errors.append({
                    "code": str(item.get("code") or "UNKNOWN"),
                    "budget_failure": is_llm_budget_exhaustion(item),
                })
    return {
        "category": LlmBudgetExhausted.category,
        "retryable_after_budget_refresh": True,
        "error_codes": errors,
    }


def authentication_failure_evidence(value: Any) -> dict[str, Any]:
    """Return bounded evidence without persisting credential text."""
    return {
        "category": LlmAuthenticationFailed.category,
        "retryable_after_credential_correction": True,
        "credential_value_recorded": False,
        "error_code": "INVALID_API_KEY",
    }


def _flatten_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if str(key).lower() in {
                    "api_key", "apikey", "authorization", "password", "secret", "token"
                }:
                    continue
                visit(nested)
        elif isinstance(item, (list, tuple, set)):
            for nested in item:
                visit(nested)
        elif item is not None:
            parts.append(str(item))

    visit(value)
    return " ".join(parts)


def _failure_text(value: Any) -> str:
    """Extract only provider failure fields from an agent envelope.

    Prompt, RAG, candidate, and raw response text are deliberately excluded so
    ordinary source/code content can never trigger a terminal run stop.
    """
    if not isinstance(value, dict):
        return _flatten_text(value)
    if "result" not in value and "status" not in value:
        return _flatten_text(value)
    result = value.get("result") or {}
    decision = result.get("decision") if isinstance(result, dict) else None
    if value.get("status") != "FAILED" and decision != "REFACTORING_FAILED":
        return ""
    candidates = list(value.get("errors") or [])
    if isinstance(result, dict):
        candidates.extend(result.get("agent_errors") or [])
    return _flatten_text(candidates)
