# RQ3: Instance-Level Traceability and Outcome Attribution

| Material | Location |
|---|---|
| Frozen sample memberships | [membership.csv](../../data/cohorts/rq3/membership.csv) |
| 79 original/replay pair associations | [rq3_pairs.csv](../../data/normalized/rq3_pairs.csv) |
| Cohorts and source references | [cohorts.json](cohorts.json), [source_index.json](source_index.json) |
| Auxiliary evidence references | [auxiliary_evidence_index.json](auxiliary_evidence_index.json) |
| Interpretation and missing materials | [data notes](../../docs/DATA_NOTES.md), [workflow and scope](../../README.md) |

RQ3 covers source traceability for 120 frozen instances and the paper's reported instance-guided verification replay of 79 historical cases. The package preserves original/replay associations and comparisons for source hashes, candidate hashes, target-test identities, post-refactoring outcomes and final decisions; all five recorded comparisons agree for 79/79 pairs.

The commands below recompute these recorded replay comparisons. They do not perform a new replay or test cross-environment reproducibility. The historical experiment and this package's record-recomputation command are distinct operations.

From the repository root:

```text
python -B scripts/inspect_instance.py --example --output results/inspection/example.json
python -B scripts/reproduce_tables.py --all
```

The example query includes its RQ3 pair when present. Table reproduction reports the RQ3 counts as supplementary values in `results/validation/table_reproduction.json`; RQ3 is not an additional Table I–VI numeric cell.

The auxiliary index retains references to 36 negatives, 28 historical corrections and 124 regression entries. Exact historical inputs/version dependencies are not all available. This repository includes indexes and metadata, not the complete original/replay source files or engineering environments.
