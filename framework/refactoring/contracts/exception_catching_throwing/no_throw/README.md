# ECT no_throw 1.0

`_analyze_ect_nothrow_v1` supports only resolved, ordinary Jupiter test methods with a void return type and no parameters. A single declared exception type must be established by `resolve_ect_checked_exception_type`, and the helper-call closure must be inspectable.

The protected region is the entire method body. Remove only the approved throws clause and wrap the complete body with fully qualified `org.junit.jupiter.api.Assertions.assertDoesNotThrow(() -> { ... })`. Preserve all original statements, tokens, comments, calls/method references, arguments, literals, and their order. Do not add imports.

This path does not delete fail branches or flags. Direct/reachable assertions, assumptions, existing exception assertions, explicit throws, control flow, and unsupported captures retain their existing review/skip outcomes. Try/catch/finally/resource structures are not automatically cleaned up; unresolved extensions of external exception observation remain subject to review.

`materialize_ect_nothrow` defines the target shape. The existing prompt and `validate_exception_refactoring` enforce the whole-body contract. Failure diagnostics follow the existing policy without reinterpreting exception semantics.
