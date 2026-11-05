# ILERef

ILERef (Instance-Level Evidence for Reliable LLM-Based Code Refactoring) is a general framework that organizes LLM-based refactoring around source-bound instances, applicability assessment, executable contracts, and evidence-driven acceptance. The paper evaluates it on Assertion Roulette (AR), Redundant Assertion (RA), and Exception Catching/Throwing (ECT) across four Java projects.

This repository provides the core implementation, a minimal end-to-end AR example with real model generation, and RQ1–RQ3 records with scripts to recompute Tables I–VI. Historical data definitions and material availability are documented in [DATA_NOTES.md](docs/DATA_NOTES.md).

## Framework overview

```text
Preserved detection record + original source
  → Instance reconstruction and source binding
  → Applicability assessment
  → Executable contract and prompt construction
  → Model generation of a new candidate
  → Contract and structural checks
  → Source reintegration
  → Compilation and target testing
  → Issue re-detection and attribution
  → Final decision and evidence preservation
```

The example starts after initial detection. The Python pipeline constructs instances, assesses applicability, builds contracts and prompts, and coordinates validation. The Java frontend and detector JAR support source analysis and re-detection. Acceptance requires consistent evidence from the current candidate and verification run.

| Location | Purpose |
|---|---|
| [framework/](framework/) | Core pipeline, instance schemas, contracts, prompts, verification, and Java frontend |
| [config/](config/) | Example run settings and credential-free model configuration |
| [examples/ar_cli/](examples/ar_cli/) | Original Apache Commons CLI source archive, detection record, hashes, and provenance |
| [tools/](tools/) | Detector JAR and its source and licensing materials |
| [scripts/](scripts/) | Single-instance entry point, checks, table recomputation, and record inspection |
| [data/](data/) | Normalized experiment records, frozen memberships, and evidence indexes |
| [experiments/](experiments/) | RQ1–RQ3 data navigation and interpretation |
| [tests/](tests/) | Focused software regression checks |
| [docs/DATA_NOTES.md](docs/DATA_NOTES.md) | Data definitions, sampling, evaluation conventions, and material availability |

## Environment and model configuration

Reference versions: Python 3.11.9, JDK 25.0.2, and Maven 3.9.15. Python dependencies are pinned in [requirements.txt](requirements.txt); Java dependencies are specified in the POMs. Make Java and Maven available through PATH or `JAVA_HOME` and `MAVEN_HOME`.

Run all commands from the repository root. Reuse an environment with the required dependencies, or install them in a new environment:

```powershell
python -m pip install -r requirements.txt
```

In [config/model.example.yaml](config/model.example.yaml), configure `roles.refactoring`:

- Set `base_url` and `model` for an OpenAI-compatible model service.
- Keep `api_key: ${ILEREF_API_KEY}` and provide the credential through the environment.
- Adjust `timeout` and `max_tokens` to the service's supported settings.

The AR example uses rule-based applicability assessment and FULL executable contracts; RAG and iterative repair are disabled. These are example settings, not a description of every historical experiment.

Maven downloads public dependencies to `results/maven_repository/` by default. An existing cache can be selected with `ILEREF_MAVEN_REPOSITORY`; `ILEREF_MAVEN_OFFLINE=1` enables offline use when all dependencies are present. Set `ILEREF_MAVEN_MIRROR_ID` if that cache uses a different repository identifier.

## Minimal end-to-end example

The example targets `UtilTest.testFindNonWhitespacePos(Character, boolean)` in the bundled Apache Commons CLI revision. It reconstructs the AR instance from the preserved detection record, binds it to the source, and reassesses applicability before requesting a new model candidate.

After configuring the model service:

```powershell
$env:ILEREF_API_KEY = 'your-api-key'
python -B scripts/run_instance.py --name ar_001
```

This command runs the full workflow above, including real model generation and downstream verification. It does not replay a saved candidate. Source reintegration takes place in an isolated copy; use a new run name each time. [config/run_ar.json](config/run_ar.json) specifies the target and resources, with paths resolved relative to the repository root. Use `--config` to select another package-local configuration.

Optional preflight:

```powershell
python -B scripts/check_package.py
```

The preflight checks resources, imports, dependencies, and tool availability without calling a model. The entry point also exposes `--prepare-only` and `--check-environment` for diagnostics; neither generates a candidate or establishes end-to-end acceptance. See `python -B scripts/run_instance.py --help`.

## Recompute the paper tables

```powershell
python -B scripts/reproduce_tables.py --all
python -B scripts/validate_dataset.py
```

These commands recompute recorded statistics and check dataset consistency; they do not rerun the historical experiments. Table recomputation compares 96 numeric cells from Tables I–VI. Outputs are saved in `results/tables/` and `results/validation/`. See [DATA_NOTES.md](docs/DATA_NOTES.md) for evaluation conventions and evidence availability.

## Outputs

Each run writes to `results/runs/<name>/`: inputs and source binding, applicability assessment, contract, prompt, model response, candidate, verification logs, test XML, re-detection CSV, final `result.json`, and an evidence hash manifest. Interrupted or rejected runs retain the evidence produced so far; later stages remain `NOT_RUN`.

| Result | Meaning | Exit code |
|---|---|---|
| `ACCEPTED` | Contract, structure, compilation, exact target testing, target AR removal, and final evidence checks pass | 0 |
| `REJECTED` | Admission or verification requirements are demonstrably unmet | 1 |
| `INCONCLUSIVE` | Evidence is insufficient, or a diagnostic-only mode was selected | 2 |

New outputs remain separate from historical inputs under `data/` and `experiments/`. Generated results and private model configuration are excluded from Git; credentials are supplied through the environment.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses and provenance.
