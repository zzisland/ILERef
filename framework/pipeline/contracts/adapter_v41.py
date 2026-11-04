from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

from .instance_id import build_instance_id, source_hash
from .unified_instance import UnifiedRefactoringInstance


METHOD_PATTERN = re.compile(
    r"(?m)^\s*(?:@\s*(?:[\w$.]+)(?:\s*\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized|abstract|native|strictfp)\s+)*"
    r"(?:<[^>{}]+>\s+)?[\w$][\w.$<>\[\], ?@]*\s+"
    r"(?P<name>[\w$]+)\s*\((?P<params>[^;{}]*)\)"
)


def adapt_schema_v41_record(
    record: dict[str, Any],
    *,
    project_version: str,
    smell_type: str,
) -> UnifiedRefactoringInstance:
    """Adapt one schema 4.1 record without changing its historical representation."""
    if record.get("schemaVersion") != "4.1":
        raise ValueError(
            f"Unsupported context schemaVersion {record.get('schemaVersion')!r}; expected '4.1'."
        )
    test_context = record.get("testContext")
    if not isinstance(test_context, dict):
        raise ValueError("schema 4.1 record must contain an object testContext.")

    test_code = str(test_context.get("testCode") or "")
    method_signature = extract_method_signature(test_code)
    canonical_smell = normalize_smell_name(smell_type)
    relative_path = str(
        test_context.get("testRelativePath")
        or test_context.get("testFilePath")
        or test_context.get("testFilename")
        or ""
    )
    module_path = str(test_context.get("testModulePath") or "")
    instance_id = build_instance_id(
        project_version=project_version,
        module_path=module_path,
        test_relative_path=relative_path,
        method_signature=method_signature,
        original_method_source=test_code,
        smell_type=canonical_smell,
    )
    return UnifiedRefactoringInstance({
        "instance_schema_version": "2.0",
        "source_schema_version": "4.1",
        "instance_id": instance_id,
        "project_version": project_version,
        "module_path": module_path.replace("\\", "/"),
        "test_relative_path": relative_path.replace("\\", "/"),
        "test_file_path": str(test_context.get("testFilePath") or ""),
        "test_filename": str(test_context.get("testFilename") or ""),
        "test_package_name": str(test_context.get("testPackageName") or ""),
        "split_filename": str(test_context.get("splitFilename") or ""),
        "method_signature": method_signature,
        "method_source_hash": source_hash(test_code),
        "smell_type": canonical_smell,
        "detected_smells": [
            normalize_smell_name(value) for value in test_context.get("smellList", []) or []
        ],
        "selected_smells": [
            normalize_smell_name(value) for value in test_context.get("selectedSmells", []) or []
        ],
        "ignored_smells": [
            normalize_smell_name(value) for value in test_context.get("ignoredSmells", []) or []
        ],
        "line_range": {
            "start": int(test_context.get("testCodeStartLine") or 0),
            "end": int(test_context.get("testCodeEndLine") or 0),
        },
        "test_code": test_code,
        "production_context": record.get("productionContext") or {},
        "source_record": record,
    })


def iter_schema_v41_instances(
    records: Iterable[dict[str, Any]],
    *,
    project_version: str,
) -> Iterator[dict[str, Any]]:
    """Expand each schema 4.1 method record into one instance per detected smell."""
    for record in records:
        test_context = record.get("testContext") or {}
        for smell in test_context.get("smellList", []) or []:
            yield adapt_schema_v41_record(
                record,
                project_version=project_version,
                smell_type=smell,
            )


def extract_method_signature(test_code: str) -> str:
    match = METHOD_PATTERN.search(test_code or "")
    if not match:
        raise ValueError("Could not extract a method signature from schema 4.1 testCode.")
    params = " ".join(match.group("params").split())
    return f"{match.group('name')}({params})"


def normalize_smell_name(value: str) -> str:
    normalized = re.sub(r"\s+", "_", str(value or "").strip().replace("-", "_"))
    aliases = {
        "Assertion_Roulette": "Assertion_Roulette",
        "Redundant_Assertion": "Redundant_Assertion",
        "Exception_Catching_Throwing": "Exception_Catching_Throwing",
        "Exception_Handling": "Exception_Catching_Throwing",
    }
    return aliases.get(normalized, normalized)
