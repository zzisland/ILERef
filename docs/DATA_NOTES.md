# Data notes

## Paper concepts and implementation

The paper proposes a general framework for LLM-based code refactoring and evaluates it on Java test smells. This package contains the implementation and evaluation materials for that domain; the AR entry point is one runnable example of the framework.

| Paper concept | Test-smell runtime instance field |
|---|---|
| `issue_type` | `smell_type` |
| `detected_issues` | `detected_smells` |
| `selected_issues` | `selected_smells` |

These are conceptual correspondences, not interchangeable runtime field aliases. Instance construction follows `framework/pipeline/instances/schemas/pipeline-instance.schema.json`. Normalized evaluation CSV files follow their own data schema; for example, `instances.csv` uses `issue_type`.

The paper's final decisions `ACCEPT`, `REJECT`, and `REVIEW` express acceptance, rejection, and insufficient evidence. The single-instance entry point uses `ACCEPTED`, `REJECTED`, and `INCONCLUSIVE` for these meanings; historical records retain their original labels. The applicability concepts `ADMIT`, `REVIEW`, and `PRESERVE` belong to a separate stage and are not final acceptance decisions. Only the implementation's `REFACTOR_LOW_RISK` result proceeds to generation. Other applicability results and exit reasons are preserved in the run evidence.

## Evaluation records and recomputation

- `data/normalized/instances.csv` contains historical instances. `executions.csv`, `candidates.csv`, `verification_checks.csv`, and `decisions.csv` are linked by their respective IDs. Empty values and missing evidence do not mean PASS.
- `cohort_membership.csv` and `data/cohorts/` preserve frozen samples. `configurations.json` distinguishes generation, verification, and decision configurations. Field definitions are in `configurations/validation/dataset_schema.json`.
- RQ1 covers overall effectiveness and the comparison with UTRefactor under common criteria. Table IV's 5,589 / 2,175 / 1,993 counts denote detected instances, admitted instances, and distinct accepted instances in the consolidated records. The 1,993 accepted instances comprise 1,812 earlier acceptances and 181 supplementary acceptances, yielding a recorded conditional acceptance rate of 91.6%. Table III keeps candidate coverage, confirmed success, confirmed failure, and inconclusive outcomes separate.
- RQ2-A jointly ablates applicability assessment and contract-constrained generation. Table V preserves the paper's Full-strict 84/92 versus Rule-Guided-common 73/92 comparison. The common-criteria comparison, Full 91/92 versus Rule-Guided 73/92, is retained separately. Full's unattempted/abstained inputs remain in the records. RQ2-B compares evidence-driven acceptance with compile/target-test-only acceptance on the same 143 historical candidates, without new model calls.
- RQ3 covers instance-level traceability and outcome attribution. `rq3_pairs.csv` stores paired comparisons of source/candidate hashes, target-test identities, detection outcomes, and final decisions from the historical replay reported in the paper. The 79/79 result summarizes agreement in those records; the recomputation command does not compile, test, or re-detect the 79 candidates again or validate replay across environments.
- `paper_cells.json` is used only to compare values after computation, not as a substitute for the calculation inputs.
- `data/source_records/index.csv`, `REPOSITORY_AVAILABILITY.csv`, and `EXCLUDED_MATERIALS.csv` retain the historical evidence-availability declarations. Index entries, historical path aliases, and hashes do not establish that the original files are bundled. These tables describe historical experimental objects, not the current package inventory.
- The original CLI detection input is a preserved schema 4.1 context snapshot, not a new detector run or an unfiltered raw detection CSV. The original `selectedSmells=[]`, `ignoredSmells`, and `legacy://` paths remain in `source_record`. The current selection and source paths are written only to the reconstructed normalized instance and binding evidence.
- `results/runs/` contains new model runs or explicit preparation checks; `results/checks/` contains software and test-fixture checks. Neither overwrites the historical evaluation data in `data/`.
