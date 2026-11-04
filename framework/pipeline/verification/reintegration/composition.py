from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.agents.refactoring_contract import normalized_source, sha256_text
from pipeline.hashing import FILE_ID_VERSION, METHOD_HASH_VERSION, build_file_id, sha256_method_text, sha256_utf8_lf


class FileCandidateError(ValueError):
    """Raised when independently approved method candidates cannot be merged safely."""


@dataclass(frozen=True)
class FileCandidateBundle:
    source_file: Path
    source_hash: str
    candidate_source: str
    candidate_hash: str
    file_id: str
    methods: tuple[dict[str, Any], ...]
    file_id_version: str = FILE_ID_VERSION
    source_relative_path: str = ""
    candidate_file_sha256: str | None = None


@dataclass(frozen=True)
class CandidateFailure:
    instance_id: str
    code: str
    message: str


def prevalidate_method_candidates(source_file: Path, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[CandidateFailure]]:
    """Validate each candidate against one immutable source snapshot.

    Invalid candidates are isolated.  Range conflicts are then rejected for
    every participant without preventing unrelated candidates from continuing.
    """
    source_file = Path(source_file).resolve()
    if not source_file.is_file():
        raise FileCandidateError(f"Source test file does not exist: {source_file}")
    original = source_file.read_text(encoding="utf-8")
    source_hash = sha256_text(original)
    lines = original.splitlines(keepends=True)
    prepared: list[dict[str, Any]] = []
    failures: list[CandidateFailure] = []
    seen: set[str] = set()
    for item in candidates:
        instance_id = str(item.get("instance_id") or "")
        try:
            if not instance_id or instance_id in seen:
                raise FileCandidateError("CANDIDATE_IDENTITY_INVALID")
            seen.add(instance_id)
            if Path(item.get("source_file") or "").resolve() != source_file:
                raise FileCandidateError("CANDIDATE_SOURCE_FILE_MISMATCH")
            if item.get("source_hash") != source_hash:
                raise FileCandidateError("CANDIDATE_SOURCE_FILE_HASH_MISMATCH")
            start, end = item.get("start_line"), item.get("end_line")
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines):
                raise FileCandidateError("CANDIDATE_METHOD_RANGE_INVALID")
            identity_mode = "FULL_SIGNATURE_AND_RANGE" if str(item.get("method_signature") or "").strip() else "LEGACY_RANGE_ONLY"
            original_method = "".join(lines[start - 1:end])
            source_baseline_hash = sha256_method_text(original_method)
            declared_original = item.get("candidate_original_hash")
            if declared_original is None:
                # Explicit historical compatibility: verify the stored method
                # text, never interpret a missing declaration as success.
                if normalized_source(original_method) != normalized_source(str(item.get("original_method") or "")):
                    raise FileCandidateError("LEGACY_CANDIDATE_ORIGINAL_HASH_UNVERIFIED")
                verification_mode = "LEGACY_ORIGINAL_TEXT_VERIFIED"
            elif declared_original != source_baseline_hash or item.get("source_baseline_hash") != source_baseline_hash:
                raise FileCandidateError("CANDIDATE_ORIGINAL_HASH_MISMATCH")
            else:
                verification_mode = f"METHOD_HASH_V{item.get('method_hash_version') or METHOD_HASH_VERSION}"
            candidate_method = str(item.get("candidate_method") or "")
            if not candidate_method.strip():
                raise FileCandidateError("CANDIDATE_METHOD_EMPTY")
            actual_refactored_hash = sha256_method_text(candidate_method)
            expected_refactored_hash = item.get("candidate_refactored_hash")
            if expected_refactored_hash is not None:
                if expected_refactored_hash != actual_refactored_hash:
                    raise FileCandidateError("CANDIDATE_REFACTORED_HASH_MISMATCH")
            else:
                legacy_hash = item.get("candidate_method_sha256_lf")
                if not legacy_hash:
                    raise FileCandidateError("LEGACY_CANDIDATE_REFACTORED_HASH_UNVERIFIED")
                if sha256_utf8_lf(candidate_method) != legacy_hash:
                    raise FileCandidateError("CANDIDATE_METHOD_HASH_MISMATCH")
                verification_mode += "+LEGACY_REFACTORED_HASH_VERIFIED"
            prepared.append({**item, "candidate_method": candidate_method,
                             "source_baseline_hash": source_baseline_hash,
                             "candidate_refactored_hash": actual_refactored_hash,
                             "method_identity_mode": identity_mode,
                             "hash_verification_mode": verification_mode})
        except FileCandidateError as exc:
            failures.append(CandidateFailure(instance_id, str(exc), str(exc)))

    conflicted: set[str] = set()
    for index, left in enumerate(prepared):
        left_span = set(range(left["start_line"], left["end_line"] + 1))
        for right in prepared[index + 1:]:
            if left_span.intersection(range(right["start_line"], right["end_line"] + 1)):
                conflicted.update((left["instance_id"], right["instance_id"]))
    if conflicted:
        prepared = [item for item in prepared if item["instance_id"] not in conflicted]
        failures.extend(CandidateFailure(i, "CANDIDATE_METHOD_CONFLICT", "Candidate method ranges overlap.") for i in sorted(conflicted))
    return prepared, failures


def assemble_method_candidates(
    source_file: Path, candidates: list[dict[str, Any]], *, project_id: str | None = None,
    module_path: str = "", source_relative_path: str | None = None,
) -> FileCandidateBundle:
    """Merge non-overlapping, Phase-3-approved methods into one test-file copy.

    Line ranges always refer to the immutable original file. Replacements are
    therefore applied bottom-up, matching the legacy assembler semantics.
    """
    source_file = Path(source_file).resolve()
    if not source_file.is_file():
        raise FileCandidateError(f"Source test file does not exist: {source_file}")
    if not candidates:
        raise FileCandidateError("At least one method candidate is required.")
    original = source_file.read_text(encoding="utf-8")
    source_hash = sha256_text(original)
    lines = original.splitlines(keepends=True)
    prepared, failures = prevalidate_method_candidates(source_file, candidates)
    if failures:
        raise FileCandidateError(f"{failures[0].code}: {failures[0].instance_id}")

    merged = list(lines)
    for item in sorted(prepared, key=lambda row: row["start_line"], reverse=True):
        replacement = (item["candidate_method"].rstrip() + "\n").splitlines(keepends=True)
        merged[item["start_line"] - 1:item["end_line"]] = replacement
    candidate_source = "".join(merged)
    if candidate_source == original:
        raise FileCandidateError("Merged file candidate has no changes.")
    relative_identity = source_relative_path or source_file.name
    file_id = build_file_id(
        project_id=project_id or "legacy",
        module_path=module_path,
        source_relative_path=relative_identity,
    )
    stable_methods = tuple(sorted(prepared, key=lambda row: (row["start_line"], row["instance_id"])))
    return FileCandidateBundle(
        source_file=source_file,
        source_hash=source_hash,
        candidate_source=candidate_source,
        candidate_hash=sha256_utf8_lf(candidate_source),
        file_id=file_id,
        methods=stable_methods,
        source_relative_path=str(relative_identity).replace("\\", "/"),
    )
