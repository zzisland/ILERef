from __future__ import annotations

from typing import Any


def match_record(records: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    expected = (payload["target_package"], payload["target_class"], payload["method_signature"], payload["source_relative_path"].replace("\\", "/"))
    exact = [r for r in records if (r["package"], r["class"], r["method_signature"], r["relative_path"]) == expected]
    if len(exact) != 1:
        raise ValueError(f"Expected one stable detector instance match, found {len(exact)}.")
    return exact[0]


def compare(before: dict[str, Any], after: dict[str, Any], payload: dict[str, Any], *, method_counts: tuple[int, int], non_target_changed: bool) -> dict[str, Any]:
    controlled = set(payload["controlled_smells"]); before_s = set(before["smells"]); after_s = set(after["smells"])
    unknown = sorted((before_s | after_s) - controlled)
    introduced = sorted((after_s - before_s) & controlled)
    return {
        "target_smell_removed": payload["target_smell"] in before_s and payload["target_smell"] not in after_s,
        "before_smells": sorted(before_s), "after_smells": sorted(after_s),
        "introduced_smells": introduced, "removed_smells": sorted(before_s - after_s),
        "unchanged_smells": sorted(before_s & after_s), "unknown_smells": unknown,
        "test_method_count_before": method_counts[0], "test_method_count_after": method_counts[1],
        "test_method_count_changed": method_counts[0] != method_counts[1],
        "non_target_region_changed": non_target_changed, "detector_compatible": True,
        "matching_confidence": 1.0, "matching_evidence": ["package", "class", "method_signature", "source_relative_path"],
    }

