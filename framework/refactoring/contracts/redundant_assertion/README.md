# RA ordinary and project-specific paths

## Ordinary redundant assertion

`safety_contracts.analyze_ra` deletes only supported, pure standalone assertions whose result is constant or vacuous. Deleting `assertNotNull(new ...)` must not remove constructor execution.

Self-comparison requires the existing resolved final primitive local binding and a proven overload. Floating-point values, objects, fields, method calls, and unresolved bindings remain subject to review. The existing semantic-category guard protects reflexivity and contract contexts such as equals/hashCode/clone/copy.

Oracle preservation relies on `confirmed_oracle` and the existing straight-line control-flow and reachability checks. Calls whose names begin with "assert", unknown helpers, or branches do not automatically count as independent oracles. Only approved targets are deleted; other statements, messages, literals, and evaluation order are preserved.

## JFreeChart catch constant deletion: project_specific

`ra_contract.analyze_catch` handles the audited subset of setter(null)/Marker event oracles: a single trailing try, one catch, a trigger followed by a bound fail call, and exactly `assertTrue(true);` in the catch. Preceding construction, listener registration, event reset, getter, and event-source assertions must match the supported shapes.

Existing conditions require resolved exception bindings, a fail call that is not caught by the catch, and preservation of other oracles and statements. Multiple catches, finally blocks, resources, additional catch behavior, loops/branches, and copy/equals/reflexivity contexts remain subject to review. JFreeChart dependency-binding special cases remain in the existing Python implementation; no project-name gate is added, and this must not be registered as a general catch-deletion rule.

`expected_candidate` and `validate_candidate` enforce the local deletion; `build_prompt` supplies the dedicated prompt. The ordinary path uses the existing generic prompt and `validate_redundant_assertion`. The two manifests are registered separately.
