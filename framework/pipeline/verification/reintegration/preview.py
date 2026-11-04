"""Single-method reintegration preview, preserving the historical check order."""
from pipeline.agents.refactoring_contract import replace_method_in_copy


def compose_preview(original_file, candidate, contract, checks):
    from pipeline.verification.candidate.validation import check
    candidate_file = replace_method_in_copy(original_file, candidate, contract["method_start_line"], contract["method_end_line"])
    if contract['smell'] == 'Redundant_Assertion' and all(c['status'] == 'PASS' for c in checks if c['severity'] == 'ERROR'):
        from pipeline.agents.safety_contracts import get_proof
        proof = get_proof(contract.get('safety_evidence') or []) or {}
        if proof.get('ra_transform') == 'CATCH_CONSTANT_DELETION':
            from pipeline.agents.ra_contract import materialize_file
            candidate, candidate_file = materialize_file(original_file, candidate, contract)
    outside_ok = (
        candidate_file.splitlines()[:contract["method_start_line"] - 1] == original_file.splitlines()[:contract["method_start_line"] - 1]
        and candidate_file.splitlines()[contract["method_start_line"] - 1 + len(candidate.splitlines()):]
        == original_file.splitlines()[contract["method_end_line"]:]
    )
    checks.append(check("ONLY_TARGET_METHOD_REPLACED", outside_ok, "Content outside the target method is unchanged."))
    return candidate, candidate_file
