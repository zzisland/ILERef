from __future__ import annotations

import hashlib
import json
import re
from typing import Any


class RefactoringPromptService:
    prompt_version = "5.2-contract-v4.1-ect-v3"

    def build(self, request: dict[str, Any], contract: dict[str, Any], context: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> tuple[str, str]:
        prompt, prompt_hash = self._build(request, contract, context, cases)
        risk = request['input']['risk_result']['result']
        advice = risk.get('admission_guidance')
        if risk.get('assessment_mode') == 'rule_llm' and advice:
            from .decision_support import check_evidence, source_documents, validate_advice
            validate_advice(advice)
            if not check_evidence(advice, source_documents(request, context))[0]:
                raise ValueError('Admission guidance evidence no longer matches source.')
            prompt += ('\nVerified admission advice (guidance, NOT a safety proof). '
                       'The source-bound candidate contract and all downstream checks remain mandatory.\n'
                       + json.dumps(advice, ensure_ascii=False))
            prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        return prompt, prompt_hash

    def _build(self, request: dict[str, Any], contract: dict[str, Any], context: dict[str, Any], cases: list[dict[str, Any]] | None = None) -> tuple[str, str]:
        if contract['smell'] == 'Redundant_Assertion':
            from .safety_contracts import get_proof
            proof = get_proof(request['input']['risk_result']['result'].get('evidence') or []) or {}
            if proof.get('ra_transform') == 'CATCH_CONSTANT_DELETION':
                from .ra_contract import build_prompt
                prompt = build_prompt(request['input']['instance'], proof)
                return prompt, hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        if contract['smell'] == 'Conditional_Test_Logic':
            from .ctl_contract import build_prompt
            prompt = build_prompt(request, contract, cases)
            return prompt, hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        instance = request["input"]["instance"]
        risk = request["input"]["risk_result"]["result"]
        if contract['smell'] == 'Magic_Number_Test':
            from .safety_contracts import get_proof
            from .mnt_naming import pending_targets
            proof = get_proof(risk['evidence'])
            if proof:
                prompt = (
                    'Conservative MNT candidate generation, rule 5.2. Risk approval permits an attempt, NOT acceptance.\n'
                    'Return one JSON object with exactly method_code (complete Java method string), naming_proposals (array), '
                    'selected_target_ids (nonempty unique array), and retained_targets (array). '
                    'Select only from contract.targets, never from blocked numeric_targets. You may select a subset. '
                    'For EVERY unselected numeric target return target_id, reason_code and reason in retained_targets. '
                    'Use reason_code KEEP_UNSELECTED, NO_ADDITIONAL_BENEFIT, OUTSIDE_SUPPORTED_SCOPE, or INSUFFICIENT_NAMING_EVIDENCE. '
                    'Retention reasons are advisory, not accepted safety or semantic proofs. Do not force a name. '
                    'No markdown, helper, import, field, or explanation outside JSON. '
                    'Preserve comment information and all tool/evidence markers. Layout changes are allowed; '
                    'unproven prose changes require review, and ungrounded explanations are forbidden. '
                    'Candidate comments can never establish naming evidence. '
                    'If impossible return REFUSE_UNSAFE_REFACTORING.\n'
                    'For EACH SELECTED PROPOSE_LOCAL_NAME target, propose exactly target_id, name, qualifier, explanation, evidence_ids. '
                    'Choose qualifier ONLY from naming_context.qualifiers: this is original intent or a quoted fixture token, '
                    'not a proof of numeric correctness or inferred business meaning. An empty qualifier means '
                    'only preparation context is available, not an approved intent phrase. '
                    'No fixed name phrase, prefix, suffix, word order or explanation template is mandatory. '
                    'Names must independently expose source-grounded intent at the expected argument; preserve '
                    'negation and relationships. Unknown synonyms and unverified benefit require review. '
                    'Explanations may quote original intent in neutral prose. Do not invent units or business claims. '
                    'explanation_template is only an optional quoting example. Cite ALL naming_context.evidence evidence_ids; '
                    'these identify exact ranges and quotes in the ORIGINAL method. Your self-assessment is not evidence.\n'
                    'Insert final <literal_type> <proposed_name> = <literal>; immediately before the approved assertion. '
                    'Replace ONLY its direct expected numeric argument. Preserve literal spelling, type and scope. '
                    'For any preapproved DECLARE_LOCAL or REUSE_TEST_LOCAL target follow its contract exactly; do not propose a name for it. '
                    'All original statements, messages, calls, assertion APIs, other arguments and ordering must remain unchanged. '
                    'Unselected targets and all non-target executable code must stay unchanged. Zero effective changes are rejected. '
                    'No production constant reuse. Unique names must not shadow existing identifiers or each other. '
                    'Partial completion is NOT complete method smell elimination.\n'
                    f'Source-bound contract: {json.dumps(proof, ensure_ascii=False)}\n'
                    f'Original method:\n{instance["test_code"]}\n'
                )
                return prompt, hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        case_refs = [{"case_id": x.get("case_id"), "summary": x.get("summary")} for x in (cases or [])]
        imports = re.findall(r"(?m)^\s*import\s+[^;]+;", context.get("test_class_source") or "")
        production = instance.get("production_context") or {}
        production_summary = {
            key: production.get(key) for key in (
                "available", "productionClassName", "focalMethodSignatures",
                "focalMethodSummaries", "dependencyMethodSignatures",
                "dependencyMethodSummaries", "astMethodCalls", "astControlFlow",
            ) if production.get(key) not in (None, [], "")
        }
        assertion_targets = [
            {
                "evidence_id": item.get("evidence_id"),
                "assertion_identity": item.get("assertion_identity"),
                "api": item.get("assertion_api"),
                "ast_path": item.get("ast_path"),
                "parent_assertion_id": item.get("parent_assertion_id"),
                "nesting_depth": item.get("nesting_depth"),
                "matched_code": item.get("matched_code"),
            }
            for item in risk.get("evidence") or []
            if item.get("conclusion") == "MISSING_MESSAGE"
        ]
        ect_instructions = ''
        if contract['smell'] == 'Exception_Catching_Throwing':
            from .safety_contracts import get_proof
            proof = get_proof(risk.get('evidence') or []) or {}
            if proof.get('ect_path') == 'NO_THROW_ASSERT_DOES_NOT_THROW':
                target = (proof.get('targets') or [{}])[0]
                ect_contract = {
                    key: target.get(key) for key in (
                        'target_id', 'throws_text', 'throws_type', 'throws_remove_start', 'throws_end',
                        'body_start', 'body_end', 'body_hash', 'body_ast_fingerprint',
                        'statement_count', 'statements', 'wrapper_api',
                    )
                }
                ect_instructions = (
                    'ECT v3 NO-THROW contract (path v1): remove only the approved throws clause and wrap the entire '
                    'approved method body exactly once with '
                    'org.junit.jupiter.api.Assertions.assertDoesNotThrow(() -> { ... });. Preserve every original '
                    'statement, token, comment, helper call, method reference, argument, literal and evaluation order. '
                    'Do not select a narrower throw site. Do not add, delete, move, rename, clean up, catch, retry, '
                    'rewrite, or import anything else. Return the complete target method only.\n'
                    f'ECT v3 no-throw source-bound target: {json.dumps(ect_contract, ensure_ascii=False)}\n'
                )
            elif (proof.get('ect_path') == 'MUST_THROW_ASSERT_THROWS'
                  and proof.get('ect_path_contract_version') == '1.0'):
                target = (proof.get('targets') or [{}])[0]
                ect_contract = {
                    key: target.get(key) for key in (
                        'target_id', 'exception_type', 'trigger', 'message', 'replacement',
                    )
                }
                ect_instructions = (
                    'ECT v1 MUST-THROW fail contract: replace only the approved try/catch region '
                    'containing the complete trigger followed by fail and an empty catch with the supplied '
                    'assertThrows replacement. Use the exact bound exception type and execute the complete '
                    'trigger exactly once. Preserve the original literal failure message when present; '
                    'do not invent a message when it is absent. Preserve all setup, verification, unrelated '
                    'assertions and code outside the approved region. Do not narrow the trigger or add '
                    'imports, helpers, retries, calls, state writes, catch/finally/resources. '
                    'Candidate acceptance still requires the static source-bound contract.\n'
                    f'ECT v1 source-bound target: {json.dumps(ect_contract, ensure_ascii=False)}\n'
                )
            elif (proof.get('ect_path') == 'MUST_THROW_ASSERT_THROWS'
                  or proof.get('ect_contract_version') == '2.0'):
                target = (proof.get('targets') or [{}])[0]
                ect_contract = {
                    key: target.get(key) for key in (
                        'target_id', 'exception_type', 'catch_variable', 'flag', 'protected_statement',
                        'protected_statement_kind', 'exception_observations', 'replacement', 'safety_conditions',
                    )
                }
                ect_instructions = (
                    'ECT v2 MUST-THROW flag contract: use the exact bound exception type and replace only the approved '
                    'flag declaration + try/catch + marker assertion region. Execute the complete protected_statement '
                    'exactly once inside assertThrows; do not narrow a declaration or call chain to a guessed throw site. '
                    'Remove only the pure flag declaration, its catch assignment, and its corresponding post-try marker '
                    'assertion. Preserve every exception observation assertion with the assertThrows return value, including '
                    'the original API, operands, strings, ordering, cause/property checks, and evaluation timing. Preserve all '
                    'setup, positive-path checks, unrelated assertions, state preparation and cleanup outside the approved '
                    'region. Do not add catch/finally/resources, imports, helpers, retries, calls, or state writes. The supplied '
                    'replacement is the source-bound target shape; candidate acceptance still requires the static contract.\n'
                    f'ECT v2 source-bound target: {json.dumps(ect_contract, ensure_ascii=False)}\n'
                )
        prompt = (
            "You are performing one conservative Java test-method refactoring.\n"
            f"Target smell: {contract['smell']}\n"
            f"Risk decision: {contract['risk_decision']}\n"
            f"Allowed changes: {json.dumps(contract['allowed_changes'])}\n"
            f"Forbidden changes: {json.dumps(contract['forbidden_changes'])}\n"
            f"Risk evidence: {json.dumps(risk['evidence'], ensure_ascii=False)}\n"
            f"Exact target assertion count: {len(assertion_targets)}\n"
            f"Exact target assertions (including nested calls): {json.dumps(assertion_targets, ensure_ascii=False)}\n"
            f"Available test-class imports: {json.dumps(imports, ensure_ascii=False)}\n"
            "Return exactly one complete refactored test method. Do not return a class, imports, helper methods, Markdown fences, or explanations. "
            "Keep the method name, return type, parameters, annotations, all unapproved signature elements, statement order, and all unapproved behavior unchanged. "
            "If the contract cannot be satisfied, return exactly REFUSE_UNSAFE_REFACTORING.\n"
            "For JUnit 5 assertion messages, place the message in the final argument position.\n"
            "AR: Add only the exact ordinary string literal in each CONTRACT-V4 target.message. No calls, suppliers, concatenation, state reads, or invented claims. "
            "RA: Delete only CONTRACT-V4 target statement ranges. "
            "ECT: Replace only the full approved target region with its replacement; preserve all setup/verify and the original literal failure message. "
            "MNT: Follow CONTRACT-V4 numeric targets exactly. Insert the specified final local declaration at insert_at and replace only that numeric argument, "
            "or reuse only the explicitly approved immutable test local. Preserve types, spelling and scope. Do not extract retained or unresolved numbers.\n"
            "Every listed target assertion, including an assertion nested inside another assertion argument, must receive its own meaningful message. "
            "Do not change an assertion API, expected/actual/non-message arguments, exception type, Executable lambda, parent-child nesting, or control flow.\n"
            f"{ect_instructions}"
            f"Optional same-smell case references: {json.dumps(case_refs, ensure_ascii=False)}\n"
            f"Original target method:\n{instance['test_code']}\n"
            f"Relevant production context summary:\n{json.dumps(production_summary, ensure_ascii=False)}\n"
        )
        return prompt, hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class IterativeRepairPromptService:
    prompt_version = "iterative_repair_assertion_roulette_v2_contract4"

    def build(self, payload: dict[str, Any]) -> tuple[str, str]:
        failure = payload["validation_failure"]
        prompt = (
            "You are repairing one rejected Assertion Roulette candidate, not refactoring the method again.\n"
            "Edit scope: assertion messages only. Preserve every already-meaningful message.\n"
            "You MUST NOT change assertion conditions, expected/actual/non-message arguments, assertion count/order, non-assertion statements, "
            "method signature, annotations, throws, local variables, calls, control flow, loops, or exception handling.\n"
            "For every target assertion, supply a final JUnit 5 message. Repair only the reported failures.\n"
            "Use exactly the ordinary string literal in each CONTRACT-V4 target.message: a quotation of the baseline assertion. No calls, suppliers, concatenation or inferred business claims.\n"
            f"Current source-bound evidence: {json.dumps(payload.get('original_request', {}).get('input', {}).get('risk_result', {}).get('result', {}).get('evidence', []), ensure_ascii=False)}\n"
            "Forbidden low-information messages include: description, since, forRemoval, isForRemoval, result, value, expected, actual, check value, assert true, not null.\n"
            "Return exactly one complete Java test method; no Markdown fence, explanation, class, imports, helper, or alternative.\n"
            f"Failure codes: {json.dumps(failure.get('failure_codes') or [])}\n"
            f"Failed contract evidence: {json.dumps(failure.get('failed_contracts') or [], ensure_ascii=False)}\n"
            f"Baseline method:\n{payload['baseline']['method_code']}\n"
            f"Initial rejected candidate:\n{payload['initial_candidate']['method_code']}\n"
        )
        return prompt, hashlib.sha256(prompt.encode("utf-8")).hexdigest()
