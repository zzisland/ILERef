# RA 普通路径与 project_specific 路径

## Ordinary redundant assertion

`safety_contracts.analyze_ra` 只删除支持的纯恒真/恒空独立语句。`assertNotNull(new ...)` 不允许删除构造器执行。

Self-comparison 必须满足现有 final primitive 局部绑定和已证明重载条件；浮点、对象、字段、方法调用或绑定不明保持 review。equals/hashCode/clone/copy 等反身性或契约语境由原 semantic category guard 保护。

Oracle preservation 依赖 `confirmed_oracle` 与现有直线控制流、可达性证明。“assert 开头”的调用、未知 helper 或分支不自动算独立 oracle。只删除已批准目标，其他语句、消息、字面量与求值顺序保持。

## JFreeChart catch constant deletion：project_specific

`ra_contract.analyze_catch` 处理已审计的 setter(null)/Marker 事件 oracle 子集：唯一末尾 try、单 catch、trigger + 已绑定 fail、catch 中精确 `assertTrue(true);`，前置构造/监听/事件重置/getter/事件来源断言满足固定形状。

异常绑定、fail 不被 catch 捕获、其余 oracle 和全部其他语句保持，均为原有条件。multiple catch、finally、resource、额外 catch 行为、循环/分支、copy/equals/反身性语境仍 review。JFreeChart 依赖绑定特例仍在原 Python 中，不增加项目名 gate，也不得登记为一般 catch 删除规则。

`expected_candidate` / `validate_candidate` 验证局部删除，`build_prompt` 为专用 prompt；普通路径使用原通用 prompt 与 `validate_redundant_assertion`。两个 manifest 独立登记。
