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

**RQ2-A / Table V — Applicability and Contract-Constrained Refactoring:** 120 frozen AR inputs. Full admits 92 and accepts 84 under its strict contract-based policy. Rule-Guided attempts 120 and accepts 73 under shared downstream evaluation. On the 92 matched targets, the published comparison is Full-strict 84/92 versus Rule-Guided-common 73/92, with paired counts 65/19/8/0. A separate comparison under the common downstream criteria gives Full 91/92 versus Rule-Guided 73/92, with paired counts 72/19/1/0. In both summaries the order is both / Full only / Rule-Guided only / neither. The published policy comparison and the common-criteria comparison are retained separately. This ablation evaluates the combined contribution of applicability assessment and executable contracts.

The computed `results/tables/table_V.json` includes both paired summaries; the `results/tables/table_V_paired.csv` describes the published paired rates. Both variants historically disabled RAG.

**RQ2-B / Table VI — Evidence-Driven Acceptance:** 143 previously generated candidates, with 137 evidence-driven acceptances and 143 compile/target-test-only acceptances. The six differences comprise one remaining AR issue, two cases with insufficient re-detection evidence and three cases with associated-test failures also observed in the original program. They are not six demonstrated refactoring-induced failures. `results/tables/table_VI.json` lists the six differing Instance IDs.

These are frozen historical metadata and derived statistics. The complete candidate/contract/raw-log corpus is not bundled; the table command does not rerun either ablation. The [separate AR workflow](../../README.md) builds a fresh contract and requests a new model candidate for single-instance validation.
