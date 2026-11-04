# AR grounded message

- **assertion binding**：`safety_contracts.resolve_api`、`java_assertions.find_assertion_calls` 确认断言、接收者与嵌套结构；shadowing、绑定不明和不支持的重载保持 review。
- **message position**：`safety_contracts.overload` 区分缺失、已有消息与不明重载，不按参数数量盲插消息。已有 message/supplier、delta、API 与操作数顺序保持。
- **grounded_message**：`safety_contracts.grounded_message` 从原断言构造诊断，禁止臆造业务含义或带副作用的消息求值。
- **allowed edit**：仅给证明缺失消息的目标添加诊断；原方法须满足现有多个断言条件，单断言不纳入自动支持。
- **forbidden edit**：操作数、API、receiver、顺序、调用次数、已有消息、非目标语句、imports/helpers/class 变化。
- **validator**：`candidate_validation.validate_assertion_roulette`，随后原 CandidateValidator 与文件/工程/复检门禁。

正式入口见 `manifest.yaml`。此目录没有新重写实现。
