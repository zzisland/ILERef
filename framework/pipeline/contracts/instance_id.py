from __future__ import annotations

import hashlib
import json
from typing import Any


INSTANCE_ID_VERSION = "1"


def source_hash(source: str) -> str:
    """Return a stable SHA-256 for the exact original method source."""
    return hashlib.sha256((source or "").encode("utf-8")).hexdigest()


def build_instance_id(
    *,
    project_version: str,
    module_path: str,
    test_relative_path: str,
    method_signature: str,
    original_method_source: str,
    smell_type: str,
) -> str:
    """Build an ID from the minimum identity fields required by the pipeline."""
    identity: dict[str, Any] = {
        "instance_id_version": INSTANCE_ID_VERSION,
        "project_version": str(project_version or "").strip(),
        "module_path": _normalize_path(module_path),
        "test_relative_path": _normalize_path(test_relative_path),
        "method_signature": " ".join(str(method_signature or "").split()),
        "original_method_source_hash": source_hash(original_method_source),
        "smell_type": str(smell_type or "").strip(),
    }
    missing = [key for key, value in identity.items() if key != "module_path" and not value]
    if missing:
        raise ValueError("Cannot build instance_id; missing fields: " + ", ".join(missing))
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "utrefactor:v1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_path(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")

