from __future__ import annotations

"""Per-instance detector effect evaluation for shared file-gate executions."""

from pathlib import Path
from typing import Any

from pipeline.agents.detector_identity import (
    DetectorIdentityAmbiguous,
    canonical_detector_identity,
    match_canonical_record,
)


def canonicalize_records(
    records: list[dict[str, Any]], *, project_id: str, module_path: str,
    package_name: str, source_relative_path: str, class_name: str,
) -> list[dict[str, Any]]:
    package_path = package_name.replace(".", "/")
    normalized: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        split = Path(str(item.get("split_filename") or "")).stem
        item["canonical_identity"] = canonical_detector_identity(
            project_id=project_id, module_path=module_path,
            package_name=package_name, source_relative_path=source_relative_path,
            class_name=class_name,
            split_relative_path="/".join(filter(None, ("split", package_path, split + ".java"))),
            split_file_name=split, method_signature=item.get("method_signature") or "",
        )
        normalized.append(item)
    return normalized


def revalidate_instances(
    *, project_id: str, source_relative_path: str,
    targets: list[dict[str, Any]], before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]], detector_execution_status: str,
    method_count_guard_passed: bool,
    method_inventory: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility projection; detector observations and policy are separate."""
    from pipeline.acceptance import EvidenceLinkedAcceptancePolicy
    from pipeline.validation_bundle import ValidationEvidenceBundle
    effects = collect_instance_effects(project_id=project_id, source_relative_path=source_relative_path,
        targets=targets, before_records=before_records, after_records=after_records,
        detector_execution_status=detector_execution_status,
        method_count_guard_passed=method_count_guard_passed, method_inventory=method_inventory)
    policy = EvidenceLinkedAcceptancePolicy()
    results = []
    for effect in effects:
        bundle = ValidationEvidenceBundle(route='DETECTOR_EFFECT', revalidation=effect)
        label = policy.evaluate(bundle).decision
        # Retain historical key order as well as values in the wire projection.
        result = {}
        for key, value in effect.items():
            result[key] = value
            if key == 'target_smell_revalidation_status':
                result['final_acceptance_status'] = label
        results.append(result)
    return results


def collect_instance_effects(
    *, project_id: str, source_relative_path: str,
    targets: list[dict[str, Any]], before_records: list[dict[str, Any]],
    after_records: list[dict[str, Any]], detector_execution_status: str,
    method_count_guard_passed: bool,
    method_inventory: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if detector_execution_status != "PASS":
        return [
            _result(target, "RECHECK_BLOCKED", reason="DETECTOR_NOT_SUCCESSFUL")
            for target in targets
        ]

    for target in targets:
        smell = str(target.get("smell_type") or "")
        expected = _target_identity(project_id, source_relative_path, target, smell)
        try:
            before, before_mapping, before_candidates = match_canonical_record(expected, before_records)
            after, after_mapping, after_candidates = match_canonical_record(expected, after_records)
        except DetectorIdentityAmbiguous as exc:
            results.append(_result(
                target, "RECHECK_INCONCLUSIVE",
                identity=expected, reason="DETECTOR_IDENTITY_AMBIGUOUS", evidence=str(exc),
            ))
            continue
        if before is None:
            results.append(_result(
                target, "RECHECK_INCONCLUSIVE",
                identity=expected, reason="BASELINE_TARGET_RECORD_MISSING",
                before_mapping=before_mapping, after_mapping=after_mapping,
            ))
            continue
        if smell not in set(before.get("smells") or []):
            results.append(_result(
                target, "RECHECK_INCONCLUSIVE",
                identity=expected, reason="BASELINE_TARGET_SMELL_MISSING",
                before=before, after=after, before_mapping=before_mapping, after_mapping=after_mapping,
            ))
            continue
        if after is None:
            results.append(_result(
                target, "RECHECK_INCONCLUSIVE",
                identity=expected, reason="AFTER_TARGET_METHOD_NOT_OBSERVED",
                before=before, before_mapping=before_mapping, after_mapping=after_mapping,
            ))
            continue
        guard = method_inventory.get('passed') is True if method_inventory is not None else method_count_guard_passed
        if not guard:
            unknown = method_inventory is not None and method_inventory.get('status') == 'UNKNOWN'
            results.append(_result(
                target, "RECHECK_INCONCLUSIVE" if unknown else "METHOD_COUNT_VIOLATION",
                identity=expected,
                reason=method_inventory['reason_code'] if method_inventory is not None else "TEST_METHOD_COUNT_CHANGED", before=before, after=after,
                before_mapping=before_mapping, after_mapping=after_mapping,
            ))
            continue

        remains = smell in set(after.get("smells") or [])
        numeric = target.get('numeric_target_effects') if smell == 'Magic_Number_Test' else None
        from pipeline.agents.safety_contracts import selected_numeric_effect_valid
        if smell == 'Magic_Number_Test' and not selected_numeric_effect_valid(numeric):
            results.append(_result(target, 'RECHECK_INCONCLUSIVE',
                                   identity=expected, reason='MNT_TARGET_CONTRACT_NOT_CONFIRMED', before=before, after=after))
            continue
        introduced = sorted(set((after or {}).get("smells") or []) - set(before.get("smells") or []))
        if introduced:
            status, reason = "NEW_SMELL_INTRODUCED", "PROHIBITED_NEW_SMELL"
        elif smell == 'Magic_Number_Test' and not numeric['all_targets_processed']:
            status, reason = 'MNT_PARTIALLY_PROCESSED', 'MNT_SELECTED_TARGETS_VERIFIED_REMAINDER_UNCHANGED'
        elif remains:
            status, reason = "TARGET_SMELL_REMAINS", "TARGET_RECORD_STILL_PRESENT"
        else:
            status, reason = "TARGET_SMELL_REMOVED", "EXACT_TARGET_RECORD_REMOVED"
        results.append(_result(
            target, status, identity=expected, reason=reason,
            before=before, after=after, before_mapping=before_mapping, after_mapping=after_mapping,
            new_smells=introduced, mapping_confidence="HIGH",
        ))
    return results


def _target_identity(project_id: str, source_relative_path: str, target: dict[str, Any], smell: str) -> dict[str, str]:
    package = str(target.get("test_package_name") or "")
    class_name = Path(str(target.get("test_filename") or "")).stem
    split = Path(str(target.get("split_filename") or "")).stem
    return canonical_detector_identity(
        project_id=project_id, module_path=str(target.get("module_path") or ""),
        package_name=package, source_relative_path=source_relative_path,
        class_name=class_name,
        split_relative_path="/".join(filter(None, ("split", package.replace(".", "/"), split + ".java"))),
        split_file_name=split, method_signature=str(target.get("method_signature") or ""),
        smell_type=smell,
    )


def _record_evidence(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    source = record.get("source_record") or {}
    result = {
        "canonical_identity": record.get("canonical_identity"),
        "smells": list(record.get("smells") or []),
        "detector_test_file_path": source.get("TestFilePath"),
        "detector_test_class": source.get("TestClass"),
        "identity_completion_source": record.get("identity_completion_source"),
    }
    return result


def _result(
    target: dict[str, Any], status: str, *,
    identity: dict[str, Any] | None = None, reason: str,
    evidence: str | None = None, before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None, before_mapping: str | None = None,
    after_mapping: str | None = None, new_smells: list[str] | None = None,
    mapping_confidence: str = "NONE",
) -> dict[str, Any]:
    result = {
        "instance_id": target.get("instance_id"),
        "smell_type": target.get("smell_type"),
        "method_signature": target.get("method_signature"),
        "canonical_identity": identity,
        "before_mapping_status": before_mapping,
        "after_mapping_status": after_mapping,
        "mapping_confidence": mapping_confidence,
        "target_smell_revalidation_status": status,
        "reason": reason,
        "evidence": evidence,
        "before_record": _record_evidence(before),
        "after_record": _record_evidence(after),
        "new_smells": list(new_smells or []),
    }
    if target.get('smell_type') == 'Magic_Number_Test':
        result['numeric_target_effects'] = {**(target.get('numeric_target_effects') or {}),
            'detector_method_positive': None if after is None else 'Magic_Number_Test' in set(after.get('smells') or [])}
    return result
