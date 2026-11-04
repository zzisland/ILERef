# ra.jfreechart.catch_constant_deletion / 2.0

This file documents a snapshot of the current Python prompt; the runtime does not read this file. `{{expression}}` denotes an existing Python expression, not a separate template engine.

## Base prompt template

```text
RA v2 safety contract: SAFE_CONSTANT_ASSERTION_IN_CATCH.
Return exactly one complete Java test method, without Markdown or explanations.
DELETE_ONLY_APPROVED_CONSTANT_ASSERTION: delete only the exact target statement ranges.
PRESERVE_TRY_CATCH_STRUCTURE: retain try/catch, exception type, variable and ordering; keep the empty catch.
PRESERVE_OTHER_CATCH_STATEMENTS: do not change any non-target catch text or comment.
PRESERVE_OTHER_ASSERTIONS: retain fail, property/event assertions and their operands/messages exactly.
NO_UNRELATED_FORMATTING: preserve all non-target text, indentation, comments, variables, signatures and annotations.
NO_OTHER_RA_REMOVAL: do not touch self-comparison, equals/hashCode/reflexivity, clone/copy or wrong operands.
NO_CONTROL_FLOW_REWRITE: do not introduce assertThrows or alter the exception trigger or fail path.
If these constraints cannot be met, return REFUSE_UNSAFE_REFACTORING.
Source-bound proof: {{json.dumps(proof, ensure_ascii=False)}}
Original method:
{{instance['test_code']}}
```

## Slots and serialization

| Slot | Source / serialization |
|---|---|
| contract['smell'], ['risk_decision'] | Fields provided by ExecutableContractBuilder |
| json.dumps(contract['allowed_changes'/'forbidden_changes']) | Default JSON serialization, retaining the existing ensure_ascii default |
| json.dumps(risk['evidence'], ensure_ascii=False) | Complete risk evidence, including the source-bound CONTRACT-V4 proof, not just a target summary |
| len(assertion_targets) / json.dumps(assertion_targets, ensure_ascii=False) | Evidence projection for conclusion=MISSING_MESSAGE: evidence_id, assertion_identity, api, ast_path, parent_assertion_id, nesting_depth, matched_code |
| json.dumps(imports, ensure_ascii=False) | Imports extracted by regex from context.test_class_source, preserving the existing order |
| json.dumps(ect_contract, ensure_ascii=False) | Path-specific fields projected from proof.targets[0]; missing keys are null |
| json.dumps(case_refs, ensure_ascii=False) | case_id/summary from the input cases; an empty array when RAG is disabled |
| instance['test_code'] | Method source from the normalized execution view, not assembled arbitrarily from provenance |
| json.dumps(production_summary, ensure_ascii=False) | available, productionClassName, focalMethodSignatures/Summaries, dependencyMethodSignatures/Summaries, astMethodCalls, and astControlFlow from production_context; omit None, [], and empty strings |
| json.dumps(proof, ensure_ascii=False) | Complete proof for the dedicated RA catch path |

The no_throw ect_contract projection contains target_id, throws_text, throws_type, throws_remove_start, throws_end, body_start, body_end, body_hash, body_ast_fingerprint, statement_count, statements, and wrapper_api.

The must_throw ect_contract projection contains target_id, exception_type, catch_variable, flag, protected_statement, protected_statement_kind, exception_observations, replacement, and safety_conditions. The fail path has its own projection: target_id, exception_type, trigger, message, and replacement. When the fail call has no message, message is null.

## Optional suffix

The Full policy configuration and the risk assessment mode are separate dimensions. If risk.assessment_mode == rule_llm and admission_guidance is nonempty, build appends the following content after validate_advice/check_evidence pass and recomputes the hash. The rule_only mode does not append this suffix.

```text

Verified admission advice (guidance, NOT a safety proof). The source-bound candidate contract and all downstream checks remain mandatory.
{{json.dumps(advice, ensure_ascii=False)}}
```

The RA catch path returns early from _build, but the outer build can still append the guidance suffix. This path does not include the generic template's case references, imports, or production summary.

## Validation scope

Prompt constraints are checked by the corresponding Python validator. A single Java Markdown code fence may be stripped. RA catch allows layout differences; AST comparisons for ordinary RA and ECT fail ignore comments. Passing static checks still requires compilation, target testing, and re-detection.
