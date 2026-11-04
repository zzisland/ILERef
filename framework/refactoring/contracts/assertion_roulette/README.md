# AR grounded message

- **Assertion binding**: `safety_contracts.resolve_api` and `java_assertions.find_assertion_calls` identify assertions, receivers, and nesting. Shadowing, unresolved bindings, and unsupported overloads remain subject to review.
- **Message position**: `safety_contracts.overload` distinguishes missing messages, existing messages, and unresolved overloads; it does not insert messages based solely on argument count. Existing messages/suppliers, deltas, APIs, and operand order are preserved.
- **Grounded message**: `safety_contracts.grounded_message` constructs diagnostics from the original assertion. Invented domain claims and message evaluation with side effects are prohibited.
- **Allowed edit**: add diagnostics only to targets proven to lack a message. The original method must satisfy the existing multiple-assertion condition; single-assertion methods are not automatically supported.
- **Forbidden edits**: changes to operands, APIs, receivers, order, call counts, existing messages, non-target statements, imports, helpers, or classes.
- **Validator**: `candidate_validation.validate_assertion_roulette`, followed by the existing CandidateValidator and file, engineering, and re-detection checks.

See `manifest.yaml` for the implementation entry points. This directory does not introduce a replacement implementation.
