# RQ2: ablations

| Material | Location |
|---|---|
| Frozen sample memberships | [membership.csv](../../data/cohorts/rq2/membership.csv) |
| Cohorts and source references | [cohorts.json](cohorts.json), [source_index.json](source_index.json) |
| Recorded decisions and checks | [decisions.csv](../../data/normalized/decisions.csv), [verification_checks.csv](../../data/normalized/verification_checks.csv) |
| Six acceptance-policy differences | [acceptance_six_differences.json](acceptance_six_differences.json) |
| Interpretation | [data notes](../../docs/DATA_NOTES.md) |

From the repository root:

```text
python -B scripts/reproduce_tables.py --all
```

**RQ2-A / Table V:** 120 frozen AR inputs. Full admits 92 and accepts 84 under its native strict endpoint. Rule-Guided attempts 120 and accepts 73 under the common endpoint. The strict paired counts are 65/19/8/0. Full's separate common endpoint accepts 91, with common paired counts 72/19/1/0. These two evaluations must not be merged.

The computed `results/tables/table_V.json` includes both paired summaries; the `results/tables/table_V_paired.csv` describes the published paired rates. Both variants historically disabled RAG.

**RQ2-B / Table VI:** 143 paired candidates, with 137 evidence-driven acceptances and 143 compile/target-test-only acceptances. `results/tables/table_VI.json` lists the six differing Instance IDs.

These are frozen historical metadata and derived statistics. The complete candidate/contract/raw-log corpus is not bundled; the table command does not rerun either ablation. The [separate AR workflow](../../README.md) builds a fresh contract and requests a new model candidate for single-instance validation.
