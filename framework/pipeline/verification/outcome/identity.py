from __future__ import annotations

"""Canonical detector identities and ambiguity-safe record matching."""

from pathlib import Path
from typing import Any, Iterable


class DetectorIdentityAmbiguous(ValueError):
    pass


def canonical_detector_identity(
    *,
    project_id: str,
    module_path: str,
    package_name: str,
    source_relative_path: str,
    class_name: str,
    split_relative_path: str,
    split_file_name: str,
    method_signature: str,
    smell_type: str | None = None,
) -> dict[str, str]:
    package_name = str(package_name or "").strip(".")
    class_name = Path(str(class_name or "")).stem
    split_file_name = Path(str(split_file_name or "")).stem
    return {
        "project_id": str(project_id or ""),
        "module_path": _relative(module_path),
        "package_name": package_name,
        "source_relative_path": _relative(source_relative_path),
        "fully_qualified_class_name": ".".join(filter(None, (package_name, class_name))),
        "original_test_class_name": class_name,
        "split_relative_path": _relative(split_relative_path),
        "split_file_name": split_file_name,
        "complete_method_signature": str(method_signature or "").strip(),
        "smell_type": str(smell_type or ""),
    }


def identity_key(identity: dict[str, Any], *, include_smell: bool = False) -> tuple[str, ...]:
    fields = (
        "project_id",
        "module_path",
        "package_name",
        "source_relative_path",
        "fully_qualified_class_name",
        "split_relative_path",
        "split_file_name",
        "complete_method_signature",
    )
    values = tuple(str(identity.get(field) or "") for field in fields)
    return values + ((str(identity.get("smell_type") or ""),) if include_smell else ())


def match_canonical_record(
    expected: dict[str, Any], records: Iterable[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    """Return one exact record, absence, or raise on ambiguous evidence.

    A missing after record is a valid absence.  Any record that shares only a
    short class/split name but differs by package/path is deliberately ignored.
    """

    expected_key = identity_key(expected)
    exact = [record for record in records if identity_key(record.get("canonical_identity") or {}) == expected_key]
    if len(exact) == 1:
        return exact[0], "EXACT_IDENTITY_MATCH", exact
    if len(exact) > 1:
        raise DetectorIdentityAmbiguous(
            f"Multiple detector records match canonical identity {expected_key!r}."
        )
    return None, "NO_EXACT_IDENTITY_MATCH", []


def _relative(value: str | Path | None) -> str:
    text = str(value or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")
