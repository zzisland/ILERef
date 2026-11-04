# UnifiedRefactoringInstance 模板

`pipeline-instance.template.json` 使用 `instance_schema_version=2.0`、`source_schema_version=4.1`。

- `source_record` 是原始检测记录的只读 provenance。不能回写执行路径、候选或归一化字段。
- 顶层 normalized fields 是正式执行视图，owner binding 等证据仍由原阶段生成。
- 模板占位符需要替换；优先用 `adapt_schema_v41_record` 构建实例，避免手工填写 ID/hash。
- `examples/` 是结构示例，通过 schema 不代表通过源码绑定、准入或工程验证，不要直接执行。
- 唯一正式 schema 在 `../schemas/pipeline-instance.schema.json`。历史实例通过 `LegacyInstanceAdapter` 转为新 schema 后验证，不维护第二份旧 schema/template。
- 重构契约由 builder 产生，使用 `refactoring_contract.contract_id` 与 `refactoring_contract.version`。
- CLI `--context-json` 仍接收 schema 4.1 原始记录数组；未改变 CLI 输入语义。
