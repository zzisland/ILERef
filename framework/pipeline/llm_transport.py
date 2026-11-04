from __future__ import annotations

import hashlib
import os
from typing import Any
from urllib.parse import urlsplit


OPENCODE_USER_AGENT = "utrefactor/1.0"


def is_opencode_go_endpoint(base_url: str) -> bool:
    parsed = urlsplit(base_url.strip())
    path = parsed.path.rstrip("/")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == "opencode.ai"
        and (path == "/zen/go/v1" or path.startswith("/zen/go/v1/"))
    )


def resolve_session_identity(base_url: str, session_id: str | None = None) -> dict[str, Any]:
    if not is_opencode_go_endpoint(base_url):
        return {"required": False, "configured": True, "value": None, "source": "NOT_REQUIRED"}

    candidates = (
        (session_id, "run_id"),
        (os.getenv("UTREFACTOR_SESSION_ID"), "environment"),
        (os.getenv("UTREFACTOR_RUN_ID"), "environment_run_id"),
    )
    for value, source in candidates:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        if len(normalized) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
            raise RuntimeError("OpenCode Go session identity contains invalid header characters.")
        return {"required": True, "configured": True, "value": normalized, "source": source}
    return {"required": True, "configured": False, "value": None, "source": "MISSING"}


def llm_default_headers(base_url: str, session_id: str | None = None) -> dict[str, str]:
    identity = resolve_session_identity(base_url, session_id)
    if not identity["required"]:
        return {}
    if not identity["configured"]:
        raise RuntimeError(
            "OpenCode Go requires a stable session identity. Supply the pipeline run_id or set "
            "UTREFACTOR_SESSION_ID in the current process."
        )
    return {
        "x-opencode-session": identity["value"],
        "User-Agent": OPENCODE_USER_AGENT,
    }


def public_session_identity(base_url: str, session_id: str | None = None) -> dict[str, Any]:
    identity = resolve_session_identity(base_url, session_id)
    value = identity.pop("value")
    identity["session_id_hash"] = hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None
    return identity
