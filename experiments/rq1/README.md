# RQ1: effectiveness and external comparison

| Material | Location |
|---|---|
| Frozen sample membership | [membership.csv](../../data/cohorts/rq1/membership.csv) |
| 1,993 selected historical acceptance associations | [accepted_evidence_index.csv](accepted_evidence_index.csv) |
| Cohort definitions and source references | [cohorts.json](cohorts.json), [source_index.json](source_index.json) |
| Per-instance metadata | [normalized data](../../data/normalized) |
| Table inputs and evaluation protocol | [paper mapping](../../docs/EXPERIMENT_PROTOCOL.md#paper-table-inputs), [protocol](../../docs/EXPERIMENT_PROTOCOL.md) |
| Derived tables | [Tables I–IV](../../results/tables) |

From the repository root:

```text
python -B scripts/reproduce_tables.py --all
```

This computes all six paper tables locally. RQ1 includes 5,589 detected, 2,175 admitted and 1,993 selected historical acceptances. The selected union is 1,812 earlier positives plus 181 supplementary positives, not a uniform latest-code rerun.

The external comparison freezes 216 methods and 323 Method-Issue targets. Candidate coverage counts targets with a recorded saved candidate; no-candidate, confirmed failure and inconclusive outcomes remain separate.

UTRefactor source and complete baseline rerun capability are absent. The full candidate/source/raw-evidence corpus is not bundled; historical references and hashes remain in the indexes. One saved AR candidate and project snapshot are supplied separately in [the executable example](../../examples/ar_cli/README.md). Consult [repository availability](../../REPOSITORY_AVAILABILITY.csv).
