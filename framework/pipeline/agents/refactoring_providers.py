from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..llm_transport import public_session_identity
from .refactoring_types import SUPPORTED_REFACTORING_SMELLS


class ExistingLLMRefactoringProvider:
    provider_name = "existing_llm_refactoring_adapter"
    provider_version = "3.0"
    provider_type = "production_llm"
    supported_smells = SUPPORTED_REFACTORING_SMELLS
    supported_modes = ("llm",)

    def generate(self, request: dict[str, Any], contract: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        # Keep the existing public adapter; its factory now resolves the refactoring role.
        from refactoring.buildindex.code_repo_v5 import get_llm_model
        client = get_llm_model(session_id=request.get("run_id"))
        if getattr(self, "on_request", None):
            self.on_request()
        response = client.invoke(context["prompt"])
        content = _response_text(response)
        if getattr(self, "on_response", None):
            self.on_response(content, getattr(response, "usage_metadata", None))
        result = {"content": content, "provider_status": "SUCCESS"}
        self.validate_response(result)
        return result

    def validate_response(self, response: dict[str, Any]) -> None:
        if response.get("provider_status") != "SUCCESS" or not isinstance(response.get("content"), str):
            raise ValueError("Existing LLM provider returned an invalid response.")


def validate_llm_runtime_configuration(session_id: str | None = None) -> dict[str, Any]:
    """Validate production transport settings without making a paid request."""
    from pipeline.model_config import resolve_role
    config = resolve_role('refactoring', require=True)
    identity = llm_runtime_identity(session_id=session_id)
    if identity['session_required'] and not identity['session_configured']:
        raise RuntimeError('OpenCode Go requires a stable session identity.')
    return identity


def llm_runtime_identity(session_id: str | None = None) -> dict[str, Any]:
    from pipeline.model_config import resolve_role
    config = resolve_role('refactoring')
    session = public_session_identity(config.base_url, session_id)
    parsed = urlsplit(config.base_url)
    safe_path = parsed.path.rstrip('/') if parsed.path.rstrip('/') in {'', '/v1', '/zen/go/v1', '/compatible-mode/v1'} else '/[REDACTED_PATH]'
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, safe_path, '', '')) if config.source == 'legacy' else None
    return {**config.identity(), 'base_url_status': 'VALID',
            'base_url_identifier': endpoint,
            'session_required': session['required'], 'session_configured': session['configured'],
            'session_id_source': session['source'], 'session_id_hash': session['session_id_hash']}


def safe_exception_diagnostic(exc: BaseException) -> str:
    """Summarize nested connection failures while redacting credential-shaped text."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current)); chain.append(current)
        current = current.__cause__ or current.__context__
    types = [type(item).__name__ for item in chain]
    text = " | ".join(str(item) for item in chain if str(item))
    text = re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_API_KEY]", text)
    lower = (" ".join(types) + " " + text).lower()
    from pipeline.external_service_failures import is_llm_budget_exhaustion, is_llm_authentication_failure
    from pipeline.model_config import ModelConfigurationError
    if isinstance(exc, ModelConfigurationError):
        return str(exc)  # Configuration errors contain field names, never values.
    if is_llm_budget_exhaustion(text):
        kind = 'CreditsError: insufficient balance'
    elif is_llm_authentication_failure(text):
        kind = 'INVALID_API_KEY'
    elif "proxy" in lower:
        kind = "PROXY_CONNECTION_FAILED"
    elif "certificate" in lower or "ssl" in lower or "tls" in lower:
        kind = "TLS_HANDSHAKE_FAILED"
    elif "name or service not known" in lower or "getaddrinfo" in lower or "gaierror" in lower or "dns" in lower:
        kind = "DNS_RESOLUTION_FAILED"
    elif "timeout" in lower or "timed out" in lower:
        kind = "CONNECTION_TIMEOUT"
    elif "connection refused" in lower or "connecterror" in lower or "network is unreachable" in lower:
        kind = "CONNECTION_FAILED"
    else:
        kind = "CONNECTION_ERROR_UNCLASSIFIED"
    bounded = "Provider diagnostic text omitted to protect credentials."
    return f"{kind}; cause_chain={' > '.join(types)}; detail={bounded}"


def _response_text(response: Any) -> str:
    """Normalize LangChain chat and Responses-API content without guessing.

    OpenAI-compatible relays may expose ``AIMessage.content`` either as text or
    as typed content blocks.  Passing the latter list into CandidateValidator
    previously raised before any useful failure evidence was retained.
    """
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            nested = block.get("content")
            if isinstance(nested, str):
                parts.append(nested)
        if parts:
            return "".join(parts)
        raise ValueError("LLM response contained no textual content blocks.")
    if content is None:
        raise ValueError("LLM response content is missing.")
    return str(content)


class DeterministicTestRefactoringProvider:
    provider_name = "deterministic_test_provider"
    provider_version = "test-only"
    provider_type = "test_double"
    supported_smells = SUPPORTED_REFACTORING_SMELLS
    supported_modes = ("test",)

    def __init__(self, content: str = "", error: Exception | None = None):
        self.content, self.error, self.calls = content, error, 0

    def generate(self, request, contract, context):
        self.calls += 1
        if self.error: raise self.error
        return {"content": self.content, "provider_status": "SUCCESS"}

    def validate_response(self, response):
        if not isinstance(response.get("content"), str): raise ValueError("Test provider content must be text.")
