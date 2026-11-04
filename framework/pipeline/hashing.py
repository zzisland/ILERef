from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path, PurePosixPath
from typing import Any


HASH_CHAIN_VERSION = "1"
FILE_ID_VERSION = "1"
METHOD_HASH_VERSION = "2"


def file_hash_chain_failure(expected, validation_input, injected, validated, current):
    """Shared fail-closed pre-detector check; all values are observed file bytes."""
    values = (("PRE_DETECTOR_MANIFEST", expected), ("PRE_DETECTOR_VALIDATION", validation_input),
              ("PRE_DETECTOR_VALIDATION", injected), ("PRE_DETECTOR_VALIDATION", validated),
              ("PRE_DETECTOR_CURRENT_FILE", current))
    for stage, value in values:
        if not value:
            return {"status":"HASH_NOT_VERIFIED", "stage":stage, "expected":expected, "actual":value}
        if value != expected:
            return {"status":"HASH_MISMATCH", "stage":stage, "expected":expected, "actual":value}
    return None


def normalize_lf(text: str) -> str:
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def sha256_utf8_lf(text: str) -> str:
    """Hash logical UTF-8 text after newline normalization only."""
    return hashlib.sha256(normalize_lf(text).encode("utf-8")).hexdigest()


def sha256_utf8_exact(text: str) -> str:
    """Compatibility hash for contracts whose v1 identity uses exact text."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def normalize_method_text(text: str) -> str:
    """Canonicalize a method without hiding code, comment, or literal changes.

    Only transport-level line endings, surrounding blank lines, and indentation
    common to every non-blank line are ignored.  All text inside the method is
    otherwise retained verbatim.
    """
    lines = normalize_lf(text).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return textwrap.dedent("\n".join(lines))


def sha256_method_text(text: str) -> str:
    return hashlib.sha256(normalize_method_text(text).encode("utf-8")).hexdigest()


def sha256_file_bytes(path: Path) -> str:
    """Hash the exact bytes consumed by build, test, or detector stages."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_relative_path(value: str | Path) -> str:
    raw = str(value).strip().replace("\\", "/")
    if len(raw) >= 2 and raw[1] == ":":
        raw = raw[2:]
    parts = [part for part in PurePosixPath(raw).parts if part not in ("", ".", "/")]
    if any(part == ".." for part in parts):
        raise ValueError("Relative artifact identity path may not contain '..'.")
    return "/".join(parts)


def build_file_id(
    *, project_id: str, module_path: str, source_relative_path: str,
    file_id_version: str = FILE_ID_VERSION,
) -> str:
    identity: dict[str, Any] = {
        "file_id_version": str(file_id_version),
        "project_id": str(project_id).strip(),
        "module_path": canonical_relative_path(module_path),
        "source_relative_path": canonical_relative_path(source_relative_path),
    }
    if not identity["project_id"] or not identity["source_relative_path"]:
        raise ValueError("Stable file identity requires project_id and source_relative_path.")
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"file:v{file_id_version}:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
