# ECT must_throw

## fail 1.0

A single try/catch contains a protected region consisting of one call or constructor trigger followed by a bound fail call, with no behavior in the catch body. The exception type must be resolved and must not catch assertion failures. Dynamic failure messages, unresolved captures/assignments, and sequences that do not match the exact trigger-then-fail pattern remain subject to review.

Replace only the approved region with `assertThrows`, preserving the complete trigger, exception type, and supported literal failure message. Do not infer a narrower throw site within the call chain.

## flag 2.0

`_analyze_ect_v2` establishes a flag initialized to false, a single assignment of true in the catch, and a post-try marker assertion. The protected region retains one complete call, constructor invocation, or supported local declaration.

`assertThrows` returns the exception with its original bound type. Approved exception observations (message, cause, properties, etc.) preserve their APIs, arguments, strings, order, and timing. Only the standalone flag declaration, catch assignment, and corresponding marker are deleted; all other setup, oracles, and cleanup are preserved.

Types are resolved by the existing `resolve_ect_exception_type`. Other uses of the flag, writes to external state, conditional assignments, multiple protected statements, unsupported comments within the region, multiple catches, finally blocks, and resources remain subject to review.

Both paths use the existing prompt branches and `validate_exception_refactoring`, followed by whole-file verification. See the respective fail and flag manifests.
