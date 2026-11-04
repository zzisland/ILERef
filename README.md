# ILERef

ILERef (Instance-Level Evidence for Reliable LLM-Based Code Refactoring) is a general framework for LLM-based code refactoring. It organizes refactoring around source-bound instances, applicability assessment, executable contracts, and evidence-driven acceptance. The paper evaluates the framework on three test-smell types (AR, RA, and ECT) across four Java open-source projects.

This repository provides the framework implementation for test-smell refactoring, RQ1–RQ3 evaluation records, scripts to recompute Tables I–VI, and a single-instance AR workflow that generates a new candidate. Other languages and refactoring tasks require corresponding detection, contract, and verification adapters; their configurations and evaluation results are outside the scope of this package.

The package includes original Apache Commons CLI source, a preserved detection record, a Java frontend, and the detector JAR. The single-instance workflow targets Assertion Roulette (AR) in `UtilTest.testFindNonWhitespacePos(Character, boolean)`:

```text
Preserved detection record and source → Instance reconstruction and source binding
→ Applicability assessment → Contract and prompt construction → New model candidate
→ Contract and structural checks → Source reintegration → Compilation and target testing
→ Issue re-detection and attribution → Final decision and evidence preservation
```

This entry point starts after initial detection. Each run reconstructs the instance, reassesses applicability, and requests a model candidate if admitted. Original inputs are in `examples/ar_cli/`; `config/run_ar.json` specifies the target and resource paths.

## Environment and configuration

Reference environment: Python 3.13.9, JDK 25.0.2, and Maven 3.9.15. Python dependency versions are listed in [requirements.txt](requirements.txt); Java dependency versions are specified in the module POMs. Java and Maven must be available through PATH or configured with `JAVA_HOME` and `MAVEN_HOME`.

Run the commands below from the repository root. An existing environment with the required dependencies can be reused. For a new environment, install the Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

In [config/model.example.yaml](config/model.example.yaml), set `model` and `base_url` under `roles.refactoring` to the model identifier and OpenAI-compatible endpoint. Supply credentials through the `ILEREF_API_KEY` environment variable. The default configuration uses rule-based applicability assessment and FULL executable contracts, with RAG and iterative repair disabled.

Maven resolves dependencies from Maven Central and caches them in `results/maven_repository/` by default. To reuse an existing public dependency cache, set `ILEREF_MAVEN_REPOSITORY`. Once all dependencies are available, set `ILEREF_MAVEN_OFFLINE=1` for offline execution. If the cache uses a different mirror identifier, specify it with `ILEREF_MAVEN_MIRROR_ID`.

## Run one AR instance

```powershell
$env:ILEREF_API_KEY = 'your-api-key'
python -B scripts/run_instance.py --name ar_001
```

The entry point reads `config/run_ar.json` by default; use `--config` to select another configuration within the package. Use a new name for each run. Source reintegration and verification take place in an isolated copy.

To check package resources, preparation, or the compilation and testing environment:

```powershell
python -B scripts/check_package.py
python -B tests/test_instance_pipeline.py
python -B scripts/run_instance.py --prepare-only --name prepare_001
python -B scripts/run_instance.py --check-environment --name env_001
```

`--prepare-only` stops after contract and prompt construction. `--check-environment` additionally builds the Java frontend, compiles the original project, and executes the original target test. Neither mode generates a candidate; both finish with `INCONCLUSIVE` (exit code 2).

## Recompute the paper tables

```powershell
python -B scripts/reproduce_tables.py --all
python -B scripts/validate_dataset.py
```

The table script computes Tables I–VI from frozen records in `data/` and `experiments/`, then compares the results with 96 numeric cells from the paper. Tables are written to `results/tables/`; validation reports are written to `results/validation/`. See [data notes](docs/DATA_NOTES.md) for field definitions and counting conventions.

## Outputs and scope

The paper's instances, contracts, and staged verification are implemented here with test-smell-specific fields and tools. [Data notes](docs/DATA_NOTES.md) map the paper's conceptual names to implementation fields and decision labels.

Each run writes to `results/runs/<name>/`, including inputs, the instance and source binding, applicability results, the contract, prompt, model response, candidate, verification logs, test XML, re-detection CSV, final `result.json`, and an evidence hash manifest. Rejected or interrupted runs retain the evidence already produced; subsequent stages remain `NOT_RUN`.

| Final result | Meaning | Exit code |
|---|---|---|
| `ACCEPTED` | Contract, structure, compilation, target testing, target AR removal, and evidence consistency checks all pass | 0 |
| `REJECTED` | Admission or verification requirements are demonstrably unmet | 1 |
| `INCONCLUSIVE` | Evidence is insufficient, or only preparation/environment checks were requested | 2 |

A single-instance run requires a configured model service. Table recomputation uses the frozen evaluation records reported in the paper; for RQ3, it recomputes historical replay comparisons without executing the 79 cases again. Historical evidence indexes are included, but the complete historical candidates, contracts, and raw logs are not bundled, so full historical evidence validation may report `BLOCKED`. Historical data and new run outputs are stored separately.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses and provenance.
