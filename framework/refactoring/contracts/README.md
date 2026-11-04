# 正式重构契约目录

`registry.yaml` 将 contract_id/version 映射到 contract_file、prompt_file、Python implementation、validator 与 tests。每条 `contract.yaml` 保存 Stage 3 确认的规范及来源绑定；同目录 `prompt.md` 保存当前实际 Prompt 基础模板、表达式槽位和可选后缀。

**Python 是唯一执行逻辑。** runtime 不读取 contract.yaml / prompt.md 执行准入、变换、生成或验证。`contract_for()` 仍只返回原有 contract_id/version；文档完整性检查仅由显式检查命令/测试调用，不加到 Full 运行路径。

## 六条正式契约

| contract_id / version | contract.yaml | prompt.md | scope |
|---|---|---|---|
| ar.grounded_message / 4.1 | [assertion_roulette/contract.yaml](assertion_roulette/contract.yaml) | [assertion_roulette/prompt.md](assertion_roulette/prompt.md) | general |
| ra.ordinary_redundant_assertion / 4.2 | [redundant_assertion/ordinary/contract.yaml](redundant_assertion/ordinary/contract.yaml) | [redundant_assertion/ordinary/prompt.md](redundant_assertion/ordinary/prompt.md) | general |
| ra.jfreechart.catch_constant_deletion / 2.0 | [redundant_assertion/jfreechart/contract.yaml](redundant_assertion/jfreechart/contract.yaml) | [redundant_assertion/jfreechart/prompt.md](redundant_assertion/jfreechart/prompt.md) | project_specific |
| ect.must_throw.fail / 1.0 | [exception_catching_throwing/must_throw/fail/contract.yaml](exception_catching_throwing/must_throw/fail/contract.yaml) | [exception_catching_throwing/must_throw/fail/prompt.md](exception_catching_throwing/must_throw/fail/prompt.md) | general |
| ect.must_throw.flag / 2.0 | [exception_catching_throwing/must_throw/flag/contract.yaml](exception_catching_throwing/must_throw/flag/contract.yaml) | [exception_catching_throwing/must_throw/flag/prompt.md](exception_catching_throwing/must_throw/flag/prompt.md) | general |
| ect.no_throw / 1.0 | [exception_catching_throwing/no_throw/contract.yaml](exception_catching_throwing/no_throw/contract.yaml) | [exception_catching_throwing/no_throw/prompt.md](exception_catching_throwing/no_throw/prompt.md) | general |

JFreeChart catch 继续为 project_specific，仅对应当前 audited setter(null)/Marker event-oracle 结构。标签没有新增 project-name gate，也没有推广 catch 删除规则。

## 维护与检查

```powershell
.venv/Scripts/python.exe -m pipeline.contract_registry
.venv/Scripts/python.exe -m unittest pipeline.tests.test_contract_documents
```

保留 manifest 作为已有 registry 元数据镜像；规则正文只在各 contract.yaml，Prompt 快照只在对应 prompt.md。registry/manifest 均登记这两条路径，避免旧工具引用失效。

规则版本可跨不同 ID 重复；要求 contract_id 唯一、version 非空，因此 (contract_id, version) 也唯一。不得误把所有 1.0 版本视为冲突。

contract.yaml 中 forbidden_edits / protected_elements / required_preservation 通过 source_field 指向同文件 edit_and_preservation_constraints，allowed_edits 指向 transformation_target；保留 Stage 3 联合审计原文，不擅自拆出新的规则语义。

文档、Prompt、Stage 3 报告或相关实现发生变化时检查会失败，需明确复审并同步来源绑定；不得靠重新生成来掩盖业务变更。检查依赖保留的 analysis/framework_refactor_stage3_20260920 审计材料；缺失时失败。检查不是安全证明或权限边界。

已知 ECT fail 的 flag 提示段、Markdown 清理、布局/注释验证边界均原样保留，详见 Stage 3 alignment 报告。将来修正这些差异属于独立业务变更。
