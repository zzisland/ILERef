# 实例模板

`pipeline-instance.template.json` 展示统一实例结构：`instance_schema_version=2.0`，`source_schema_version=4.1`。字段规范见 [pipeline-instance.schema.json](../schemas/pipeline-instance.schema.json)。

- `source_record` 保存原始检测记录，不写入候选、执行路径或归一化字段。
- 顶层字段保存执行所需的项目版本、方法身份、问题类型、源码与选择信息。
- 实际实例由 `adapt_schema_v41_record` 构建，再由入口完成源码绑定和适用性判断；不要手工填写 ID 或哈希。
- `examples/` 的 AR、RA、ECT 文件仅展示字段结构，不能替代实际输入或验证结果。

在项目根目录运行 `python -B scripts/run_instance.py --config config/run_ar.json --prepare-only`，可从包内检测记录构建实例、契约和提示词。
