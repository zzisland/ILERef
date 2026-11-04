# Instance templates

`pipeline-instance.template.json` illustrates the unified instance structure with `instance_schema_version=2.0` and `source_schema_version=4.1`. Field definitions are in [pipeline-instance.schema.json](../schemas/pipeline-instance.schema.json).

- `source_record` preserves the original detection record; candidates, execution paths, and normalized fields are not written into it.
- Top-level fields contain the project revision, method identity, issue type, source code, and selection information required for execution.
- Actual instances are constructed by `adapt_schema_v41_record`, followed by source binding and applicability assessment in the entry point. Do not fill in IDs or hashes manually.
- The AR, RA, and ECT files in `examples/` illustrate the field structure only; they do not replace actual inputs or verification results.

From the repository root, run `python -B scripts/run_instance.py --config config/run_ar.json --prepare-only` to construct an instance, contract, and prompt from the bundled detection record.
