# Data notes

## Paper concepts and implementation

The paper proposes a general framework for LLM-based code refactoring and evaluates it on Java test smells. The AR entry point is one runnable example.

| Paper concept | Test-smell runtime field |
|---|---|
| `issue_type` | `smell_type` |
| `detected_issues` | `detected_smells` |
| `selected_issues` | `selected_smells` |

These are conceptual correspondences, not runtime aliases. Runtime instances follow [pipeline-instance.schema.json](../framework/pipeline/instances/schemas/pipeline-instance.schema.json); normalized evaluation tables follow [dataset_schema.json](../configurations/validation/dataset_schema.json).

The paper's final `ACCEPT`, `REJECT`, and `REVIEW` meanings correspond to the entry point's `ACCEPTED`, `REJECTED`, and `INCONCLUSIVE`. Historical records retain their original labels. Applicability decisions (`ADMIT`, `REVIEW`, `PRESERVE`) belong to a different stage. Only the implementation's `REFACTOR_LOW_RISK` result proceeds to generation; other outcomes and exit reasons remain in the run evidence.

## Stable identity, consolidation, and counting

The runtime [identity builder](../framework/pipeline/contracts/instance_id.py) hashes the identity version, project revision, normalized module and relative file paths, whitespace-normalized full method signature, SHA-256 of the original method source, and issue type. It retains the `utrefactor:v1:` ID prefix for compatibility; that prefix does not denote the baseline variant. A method with different issue types has different instances. `source_hash` in the historical instance table hashes complete source-file bytes; `method_source_hash` identifies the method text. They are not interchangeable.

The 5,589 historical instance IDs are preserved, not regenerated from incomplete source snapshots. `executions.csv`, `candidates.csv`, `verification_checks.csv`, and `decisions.csv` join by their explicit IDs. Frozen memberships are in `cohort_membership.csv` and `data/cohorts/`. Missing evidence and empty values never imply PASS.

For RQ1 Table IV, the selected accepted cohort contains 1,993 distinct instances: 1,812 inherited acceptances and 181 supplementary acceptances. Historical inheritance concerns the recorded source/candidate artifacts and their evaluation conditions; it is not a uniform rerun with the current contracts. The table script selects `rq1.recorded-native` decisions with `decision=ACCEPT` and `included_in_paper=true`, and checks equality with `rq1.accepted` membership. It counts sets of instance IDs, not execution rows. All 1,993 selected IDs belong to the 2,175-instance admitted cohort.

Four additional recorded native ACCEPT decisions have `included_in_paper=false` and remain excluded. The original reconciliation identifies them as `INSTANCE_NATIVE_PLUS_V12` with `RQ1_FILE_NATIVE_PROTOCOL_MISSING`; they must not be added simply by counting every ACCEPT row. Source selection and inherited/supplementary distinctions are preserved through [accepted_evidence_index.csv](../experiments/rq1/accepted_evidence_index.csv) and [source_index.json](../experiments/rq1/source_index.json).

Retries do not create additional detected or accepted instances. The original consolidation audit records a transport-recovery cycle for the final supplementary batch, counted once for its final 50 instances while retaining earlier failures. The packaged execution table has one consolidated RQ1_MAIN association per instance, including NOT_RUN records; it is not an API-attempt ledger. Complete per-request retry counts, model-call costs, and a universal first/latest/best-attempt rule cannot be reconstructed from it. Shared file candidates or verification runs also do not constitute independent model calls. Do not infer those quantities from table denominators.

## Frozen samples

The selection details below were checked against original manifests whose SHA-256 values match the packaged source indexes. The selected memberships are bundled; the original selection payloads and population inputs are listed under minimum additions below.

**RQ1 baseline (216 methods / 323 Method–Issue targets).** The frozen method manifest specifies a census of RA methods, followed by project-specific Hamilton quotas over mutually exclusive AR-only, ECT-only, and AR+ECT strata among non-RA methods. Methods carrying AR/ECT are capped at 50 per project, including RA methods that also carry AR/ECT. Remaining slots are ranked by ascending `SHA256(seed|stratum|mapping_id)`, with project seed `controlled-baseline-method-sample-v1|20260920|<project>`. This yields 51 CLI, 50 jsoup, 62 JFreeChart, and 53 Compress methods. Their issue mappings expand to the 323 targets; both arms use those targets. The manifest includes 17 confirmed mapping patches. [methods.csv](../data/normalized/methods.csv) preserves selected mappings; the population and patch payloads are required to independently reconstruct selection.

**RQ2-A / RQ3 source cohort (120 AR instances).** The original 120-instance manifest is exactly the ordered CLI/Compress subset of the frozen 240-instance parent, with 60 instances per project. The parent selected 40 historically admitted and 20 non-admitted AR instances per project, using seed `ar-rq2-ablation-v1|20261002|seed=20261002`. It included eligible engineering-failure and preserve cases as census strata and filled the remaining slots by ascending `SHA256(seed|project|stratum|instance_id)`. The 120 selected records comprise 2 engineering-failure census, 78 admitted-ranked, 1 preserve census, and 39 review-ranked cases. Historical admission labels used for selection are not the later Full admission outcome of 92/120. This is a stratified cohort, not an unqualified uniform random sample or a sample drawn from the 92 later admissions. Its IDs match both RQ2-A input memberships and `rq3.source120`.

## Historical configurations and evaluation endpoints

[configurations.json](../data/normalized/configurations.json) records configuration identities and [executions.csv](../data/normalized/executions.csv) links them to records. Labels are provenance, not complete executable settings. The current AR example's rule-only / FULL / no-RAG / no-repair settings are not defaults for every historical experiment.

| Evaluation | Recorded configuration / endpoint | Interpretation |
|---|---|---|
| RQ1 overall, Table IV | `rq1.native.*` / `rq1.recorded-native` | Consolidated native outcomes under recorded configurations; selected membership controls inclusion |
| RQ1 baseline, Table III | `RQ1-SYMMETRIC-COMMON-1.4-20261018` / `rq1.baseline-common` | Common target-level criteria; final records can inherit compatible v1.3 evidence rather than re-execute it |
| RQ2-A, Table V | `rq2.a.full`, `rq2.a.rule` / `rq2.a.full-strict`, `rq2.a.common` | Published Full-strict 84/92 versus Rule-Guided-common 73/92; separate common-criteria Full result 91/92 |
| RQ2-B, Table VI | `compile-exact-target-only-acceptance-1` / `rq2.b.evidence-driven`, `rq2.b.compile-target-only` | Two policies applied to the same 143 previously generated candidates; no new generation |
| RQ3 | `rq3.source`, `rq3.original.common`, `rq3.replay.common` / `rq3.common` | Source identity and original/replay comparisons; table scripts do not execute replay |

The original RQ1 audit distinguishes AR prompt versions 5.1/5.2, historical multi-smell composition, and different RAG outcomes. Supplementary file-native batches used frozen project templates, RAG top_k=3, and disabled repair/recovery, apart from the separately recorded transport recovery. These settings must not be replaced by the current example configuration. RQ2-A is reported with RAG disabled for both variants. Complete effective settings, budgets, per-request prompt hashes, and model backend/version identity are not recoverable from the configuration labels alone; the relevant original snapshots are required. A model alias does not establish fixed backend weights.

RQ2-A measures the combined contribution of applicability assessment and executable contracts. Its strict/common paired counts are respectively 65/19/8/0 and 72/19/1/0 (both / Full only / Rule-Guided only / neither). RQ2-B's six differences comprise one remaining AR issue, two insufficient re-detection cases, and three associated-test failures also observed in the original program; they are not six established regressions introduced by refactoring.

RQ3's 79/79 value summarizes equality of recorded source/candidate hashes, target-test identities, detection outcomes, and final decisions in [rq3_pairs.csv](../data/normalized/rq3_pairs.csv). The auxiliary references cover 36 negative cases, 28 historical corrections, and 124 regression entries. Recomputing these records does not verify new compilation, testing, detection, or cross-environment replay.

## Material availability and minimum additions

The package contains normalized records, frozen memberships, source/evidence indexes, and the standalone CLI example. Complete historical candidates, contract payloads, raw logs/XML/detector outputs, and the complete UTRefactor execution setup are not bundled. `validate_dataset.py` checks metadata and recorded consistency: a successful metadata check can still report `PARTIAL` with full evidence `BLOCKED`. `--require-full-evidence` returns exit code 2 for that condition. `paper_cells.json` supplies comparison targets only after table computation.

[REPOSITORY_AVAILABILITY.csv](../REPOSITORY_AVAILABILITY.csv), [EXCLUDED_MATERIALS.csv](../EXCLUDED_MATERIALS.csv), and [data/source_records/index.csv](../data/source_records/index.csv) retain historical availability declarations. `EXPORTED` in a historical index is not a claim that its payload is currently bundled. Use `repository_status` and actual file presence; aliases and hashes alone are not evidence bytes.

The following are the minimum original materials needed for deeper verification; they are not runtime dependencies of this package:

| Question | Minimum original materials to add |
|---|---|
| RQ1 inheritance, selection, and retries | Indexed `instance_reconciliation.csv`, `accepted_reuse_1812.csv`, supplementary batch summaries, the consolidation/selection rules, and recovery journal linking attempts to selected source/candidate hashes |
| Baseline sampling and common evaluation | Indexed `baseline_sample_manifest.json`, candidate population/method mapping and confirmed patch, selection script; final target records and v1.3/v1.4 inheritance certificates |
| 120-instance AR selection | Indexed `manifest_120.json`, parent `ar_240_manifest.json`, `full_assessments.json`, `freeze_samples.py`, and the checksummed population/overlay inputs used by that script |
| Effective historical configurations | Per-run model/parameter, prompt, RAG, contract, admission, validation and repair-budget snapshots, with hashes binding them to execution records |
| Independent replay | Original/candidate bytes and contracts for the selected cases, bound commands, test XML and detector outputs, source revisions and tool/dependency identities; baseline source/adapters for UTRefactor reruns |

The RQ1 original manifest and reconciliation hashes can be found in [rq1/source_index.json](../experiments/rq1/source_index.json); the 120-instance manifest hash is in [rq2/source_index.json](../experiments/rq2/source_index.json). These hashes identify the originals inspected for the selection descriptions above. Restoring them should preserve their provenance and anonymity without rewriting historical decisions.

## Example inputs and outputs

The CLI detection input is a preserved schema 4.1 context snapshot, not an unfiltered raw detection CSV. Its original `selectedSmells=[]`, `ignoredSmells`, and `legacy://` paths remain in `source_record`. The example records the new AR selection separately, binds it to the packaged source, and reassesses applicability; it does not inherit historical admission or candidates.

`results/runs/` stores new runs and preparation checks; `results/checks/` stores software/test-fixture checks. Report and inspection output overrides are restricted to `results/`. A run name cannot be reused, and source reintegration occurs in an isolated copy. Historical records under `data/` and `experiments/` are inputs, not destinations for new results.
