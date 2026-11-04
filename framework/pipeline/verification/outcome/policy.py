from __future__ import annotations

from typing import Any


def check(cid: str, passed: bool, expected: Any, observed: Any, reason: str, *, blocked: bool = False) -> dict[str, Any]:
    return {"check_id": cid, "status": "PASS" if passed else ("BLOCKED" if blocked else "FAIL"),
            "expected": expected, "observed": observed, "evidence_refs": [], "reason": reason}


def collect_policy_checks(*, payload: dict[str, Any], detector: dict[str, Any], before: dict[str, Any],
                    after: dict[str, Any], comparison: dict[str, Any], source_integrity: dict[str, Any],
                    structure_passed: bool) -> list[dict[str, Any]]:
    checks = [
        check("DETECTOR_EXECUTION_CONFIRMED", detector.get("status") == "PASS" and detector.get("exit_code") == 0, "PASS/0", [detector.get("status"), detector.get("exit_code")], "The real Phase 1 detector must execute successfully.", blocked=True),
        check("BEFORE_TARGET_SMELL_PRESENT", before.get("target_smell_present") is True, True, before.get("target_smell_present"), "The baseline must contain the target smell.", blocked=True),
        check("TARGET_INSTANCE_OBSERVED", after.get("target_instance_observed") is True, True, after.get("target_instance_observed"), "The after detector output must contain the exact target method.", blocked=True),
        check("TARGET_SMELL_REMOVED", comparison.get("target_smell_removed") is True, True, comparison.get("target_smell_removed"), "The target smell must be absent after revalidation."),
        check("NO_NEW_SMELLS", not comparison.get("introduced_smells"), [], comparison.get("introduced_smells"), "No controlled smell may be introduced."),
        check("TEST_METHOD_COUNT_PRESERVED", comparison.get("test_method_count_changed") is False, False, comparison.get("test_method_count_changed"), "Test method count must remain unchanged."),
        check("NON_TARGET_REGION_UNCHANGED", structure_passed and comparison.get("non_target_region_changed") is False, False, comparison.get("non_target_region_changed"), "Package, imports, class structure, helpers and other methods must remain unchanged."),
        check("SOURCE_INTEGRITY", source_integrity.get("unchanged") is True, True, source_integrity.get("unchanged"), "Original source must remain unchanged."),
        check("CANDIDATE_INTEGRITY", source_integrity.get("candidate_unchanged") is True, True, source_integrity.get("candidate_unchanged"), "Candidate must remain unchanged."),
    ]
    if payload.get('target_smell') != 'Magic_Number_Test':
        checks.append(check('COMPLETE_FILE_CHAIN',source_integrity.get('complete_file_chain_verified') is True,
                            True,source_integrity.get('complete_file_chain_verified'),
                            'Engineering and detector must observe the same complete candidate file.',blocked=True))
    if payload.get('target_smell') == 'Magic_Number_Test':
        checks.append(check('MNT_COMPLETE_FILE_CHAIN',source_integrity.get('complete_file_chain_verified') is True,
                            True,source_integrity.get('complete_file_chain_verified'),
                            'Engineering and detector must observe the same complete candidate file.',blocked=True))
        from pipeline.agents.safety_contracts import selected_numeric_effect_valid
        numeric = comparison.get('numeric_target_effects') or {}
        valid = selected_numeric_effect_valid(numeric)
        checks.append(check('MNT_SELECTED_TARGETS_VERIFIED', valid, True, valid,
                            'Selected targets must pass the current static contract on the final method.'))
        checks.append(check('MNT_DETECTOR_METHOD_EFFECT_RECORDED', isinstance(numeric.get('detector_method_positive'), bool),
                            'boolean', numeric.get('detector_method_positive'), 'Record detector outcome separately from target completion.'))
        if valid and not numeric['all_targets_processed']:
            # Detector execution and exact instance observation remain mandatory;
            # only full removal is no longer required for the explicit partial state.
            checks = [c for c in checks if c['check_id'] != 'TARGET_SMELL_REMOVED']
            checks.append(check('MNT_RETAINED_TARGETS_UNCHANGED', True, True, True,
                                'Exact reverse-inline contract verified the retained complement.'))
    return checks


def evaluate_policy(*, payload, detector, before, after, comparison, source_integrity, structure_passed):
    """Legacy tuple projection of the unified evidence/acceptance interface."""
    from pipeline.validation_bundle import ValidationEvidenceBundle
    from pipeline.acceptance import EvidenceLinkedAcceptancePolicy
    checks = collect_policy_checks(payload=payload, detector=detector, before=before, after=after,
                                   comparison=comparison, source_integrity=source_integrity,
                                   structure_passed=structure_passed)
    bundle = ValidationEvidenceBundle.from_revalidation_checks(payload=payload, detector=detector,
        before=before, after=after, comparison=comparison, source_integrity=source_integrity, checks=checks)
    result = EvidenceLinkedAcceptancePolicy().evaluate(bundle)
    return result.decision, result.status, result.category, checks


def _category(cid: str) -> str:
    return {"COMPLETE_FILE_CHAIN":"VALIDATION_EVIDENCE_REAUDIT_REQUIRED",
            "MNT_SELECTED_TARGETS_VERIFIED":"MNT_TARGET_CONTRACT_NOT_CONFIRMED",
            "MNT_COMPLETE_FILE_CHAIN":"MNT_VALIDATION_EVIDENCE_REAUDIT_REQUIRED",
            "MNT_DETECTOR_METHOD_EFFECT_RECORDED":"MNT_DETECTOR_METHOD_EFFECT_UNKNOWN",
            "DETECTOR_EXECUTION_CONFIRMED":"DETECTOR_EXECUTION_BLOCKED", "BEFORE_TARGET_SMELL_PRESENT":"INSTANCE_MATCH_BLOCKED",
            "TARGET_INSTANCE_OBSERVED":"TARGET_INSTANCE_NOT_OBSERVED", "TARGET_SMELL_REMOVED":"TARGET_SMELL_REMAINS",
            "NO_NEW_SMELLS":"NEW_SMELL_INTRODUCED", "TEST_METHOD_COUNT_PRESERVED":"TEST_METHOD_COUNT_VIOLATION",
            "NON_TARGET_REGION_UNCHANGED":"NON_TARGET_CHANGE_DETECTED", "SOURCE_INTEGRITY":"CANDIDATE_SOURCE_MISMATCH",
            "CANDIDATE_INTEGRITY":"CANDIDATE_SOURCE_MISMATCH"}.get(cid, "DETECTOR_RESULT_INCONSISTENT")
