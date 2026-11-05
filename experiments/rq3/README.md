# RQ3: Instance-Level Traceability and Outcome Attribution

| Material | Location |
|---|---|
| Frozen sample memberships | [membership.csv](../../data/cohorts/rq3/membership.csv) |
| 79 original/replay pair associations | [rq3_pairs.csv](../../data/normalized/rq3_pairs.csv) |
| Cohorts and source references | [cohorts.json](cohorts.json), [source_index.json](source_index.json) |
| Auxiliary evidence references | [auxiliary_evidence_index.json](auxiliary_evidence_index.json) |
| Record definitions and material availability | [data notes](../../docs/DATA_NOTES.md) |

RQ3 covers source traceability for 120 frozen instances and the paper's reported instance-guided verification replay of 79 historical cases. The package preserves original/replay associations and comparisons for source hashes, candidate hashes, target-test identities, post-refactoring outcomes and final decisions; all five recorded comparisons agree for 79/79 pairs.

The commands below inspect the preserved records and recompute historical replay comparisons.

From the repository root:

```text
python -B scripts/inspect_instance.py --example --output results/inspection/example.json
python -B scripts/reproduce_tables.py --all
```

The example query includes its RQ3 pair when present. Table reproduction reports the RQ3 counts as supplementary values in `results/validation/table_reproduction.json`; RQ3 is not an additional Table I–VI numeric cell.

The auxiliary index retains references to 36 negatives, 28 historical corrections and 124 regression entries. Selection, replay interpretation, and material availability are documented in [data notes](../../docs/DATA_NOTES.md).
