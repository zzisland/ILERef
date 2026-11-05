# RQ2: Contribution of Core Mechanisms

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

**RQ2-A / Table V — Applicability and Contract-Constrained Refactoring:** 120 frozen AR inputs. The published comparison on 92 matched targets is Full-strict 84/92 versus Rule-Guided-common 73/92. The separate common-criteria comparison is Full 91/92 versus Rule-Guided 73/92. Both summaries are retained in `results/tables/table_V.json`; `table_V_paired.csv` contains the published paired rates. This ablation evaluates applicability assessment and executable contracts together.

**RQ2-B / Table VI — Evidence-Driven Acceptance:** 143 previously generated candidates, with 137 evidence-driven acceptances versus 143 compile/target-test-only acceptances. The six differences include a remaining AR issue, insufficient re-detection evidence, and associated-test failures also observed in the original program; they are not six established refactoring-induced regressions. `results/tables/table_VI.json` lists the differing instances.

The command recomputes recorded outcomes. Paired counts, sample selection, historical configurations, and the breakdown of the six differences are documented in [data notes](../../docs/DATA_NOTES.md).
