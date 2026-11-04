# 重构契约

`registry.yaml` 登记六条契约的身份、版本及 Python 入口。每条契约包含：

- `manifest.yaml` 或 `*.manifest.yaml`：注册信息。
- `contract.yaml`：适用条件、允许修改、保护范围和候选检查规则。
- `prompt.md`：提示词模板、输入槽位和输出要求。

准入、提示词构建和验证由登记的 Python 入口执行；YAML 和 Markdown 用于阅读与检查。

| contract_id / version | contract.yaml | prompt.md | scope |
|---|---|---|---|
| ar.grounded_message / 4.1 | [assertion_roulette/contract.yaml](assertion_roulette/contract.yaml) | [assertion_roulette/prompt.md](assertion_roulette/prompt.md) | general |
| ra.ordinary_redundant_assertion / 4.2 | [redundant_assertion/ordinary/contract.yaml](redundant_assertion/ordinary/contract.yaml) | [redundant_assertion/ordinary/prompt.md](redundant_assertion/ordinary/prompt.md) | general |
| ra.jfreechart.catch_constant_deletion / 2.0 | [redundant_assertion/jfreechart/contract.yaml](redundant_assertion/jfreechart/contract.yaml) | [redundant_assertion/jfreechart/prompt.md](redundant_assertion/jfreechart/prompt.md) | project_specific |
| ect.must_throw.fail / 1.0 | [exception_catching_throwing/must_throw/fail/contract.yaml](exception_catching_throwing/must_throw/fail/contract.yaml) | [exception_catching_throwing/must_throw/fail/prompt.md](exception_catching_throwing/must_throw/fail/prompt.md) | general |
| ect.must_throw.flag / 2.0 | [exception_catching_throwing/must_throw/flag/contract.yaml](exception_catching_throwing/must_throw/flag/contract.yaml) | [exception_catching_throwing/must_throw/flag/prompt.md](exception_catching_throwing/must_throw/flag/prompt.md) | general |
| ect.no_throw / 1.0 | [exception_catching_throwing/no_throw/contract.yaml](exception_catching_throwing/no_throw/contract.yaml) | [exception_catching_throwing/no_throw/prompt.md](exception_catching_throwing/no_throw/prompt.md) | general |

在项目根目录执行：

```powershell
python -B scripts/check_package.py
python -B tests/test_contract_resources.py
python -B tests/test_instance_pipeline.py
```

包检查核对注册信息、实现入口、规则字段和提示词资源；资源回归检查验证缺失或错配时会失败。单实例回归覆盖 AR 的源码绑定、候选约束和失败处理，不代表其他契约已完成端到端验证。

规则与提示词修改后应同步对应 Python 实现和验证。检查资源一致性不代替编译、目标测试、重检测及最终接受判断。
