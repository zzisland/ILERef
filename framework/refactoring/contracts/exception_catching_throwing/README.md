# ECT dispatcher 3.0

The entry point is `safety_contracts.analyze_ect`, rule version 4.2. This directory distinguishes the existing path contracts without expanding their scope.

- [must_throw](must_throw/README.md): fail 1.0 and flag 2.0.
- [no_throw](no_throw/README.md): whole-body wrapping 1.0.

Multiple catches, union catches, finally blocks, resources, and unresolved exception/JUnit bindings retain their existing review/skip outcomes, as determined by the Python implementation. This description does not relax admission conditions.
