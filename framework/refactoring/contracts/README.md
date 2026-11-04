# Refactoring contracts

`registry.yaml` registers the identities, versions, and Python entry points of six contracts. Each contract includes:

- `manifest.yaml` or `*.manifest.yaml`: registration metadata.
- `contract.yaml`: applicability conditions, allowed edits, protected regions, and candidate checks.
- `prompt.md`: prompt templates, input slots, and output requirements.

Admission, prompt construction, and validation are executed by the registered Python entry points. YAML and Markdown provide inspectable documentation.

| contract_id / version | contract.yaml | prompt.md | scope |
|---|---|---|---|
| ar.grounded_message / 4.1 | [assertion_roulette/contract.yaml](assertion_roulette/contract.yaml) | [assertion_roulette/prompt.md](assertion_roulette/prompt.md) | general |
| ra.ordinary_redundant_assertion / 4.2 | [redundant_assertion/ordinary/contract.yaml](redundant_assertion/ordinary/contract.yaml) | [redundant_assertion/ordinary/prompt.md](redundant_assertion/ordinary/prompt.md) | general |
| ra.jfreechart.catch_constant_deletion / 2.0 | [redundant_assertion/jfreechart/contract.yaml](redundant_assertion/jfreechart/contract.yaml) | [redundant_assertion/jfreechart/prompt.md](redundant_assertion/jfreechart/prompt.md) | project_specific |
| ect.must_throw.fail / 1.0 | [exception_catching_throwing/must_throw/fail/contract.yaml](exception_catching_throwing/must_throw/fail/contract.yaml) | [exception_catching_throwing/must_throw/fail/prompt.md](exception_catching_throwing/must_throw/fail/prompt.md) | general |
| ect.must_throw.flag / 2.0 | [exception_catching_throwing/must_throw/flag/contract.yaml](exception_catching_throwing/must_throw/flag/contract.yaml) | [exception_catching_throwing/must_throw/flag/prompt.md](exception_catching_throwing/must_throw/flag/prompt.md) | general |
| ect.no_throw / 1.0 | [exception_catching_throwing/no_throw/contract.yaml](exception_catching_throwing/no_throw/contract.yaml) | [exception_catching_throwing/no_throw/prompt.md](exception_catching_throwing/no_throw/prompt.md) | general |

Run from the repository root:

```powershell
python -B scripts/check_package.py
python -B tests/test_contract_resources.py
python -B tests/test_instance_pipeline.py
```

The package check verifies registration metadata, implementation entry points, rule fields, and prompt resources. Resource regression checks ensure that missing or mismatched resources fail validation. The single-instance regression checks cover AR source binding, candidate constraints, and failure handling; they do not establish end-to-end validation of the other contracts.

Changes to rules or prompts must be reflected in the corresponding Python implementation and validation. Resource consistency checks do not replace compilation, target testing, re-detection, or final acceptance decisions.
