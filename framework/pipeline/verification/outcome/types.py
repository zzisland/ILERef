from __future__ import annotations

REVALIDATION_DECISIONS = frozenset({"REVALIDATION_PASSED", "REVALIDATION_FAILED", "REVALIDATION_BLOCKED"})
SUPPORTED_SMELLS = frozenset({"Assertion_Roulette", "Redundant_Assertion", "Exception_Catching_Throwing", "Magic_Number_Test", "Conditional_Test_Logic"})
REQUIRED_CHECKS = (
    "detector_execution_confirmed", "target_instance_observed", "target_smell_removed",
    "no_new_smells", "test_method_count_preserved", "non_target_region_unchanged",
    "complete_candidate_file_chain_verified",
)


def required_checks_for(smell):
    if smell != 'Magic_Number_Test':
        return REQUIRED_CHECKS
    return tuple(check for check in REQUIRED_CHECKS if check not in ('target_smell_removed','complete_candidate_file_chain_verified')) + (
        'selected_numeric_targets_verified', 'retained_numeric_targets_unchanged', 'detector_method_effect_recorded',
        'complete_candidate_file_chain_verified')
