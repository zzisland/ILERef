# ect.no_throw / 1.0

此文件为当前 Python Prompt 的规范快照；runtime 不读取本文件。`{{expression}}` 是原 Python 表达式占位符，不是新增模板引擎。

## 最终基础模板

```text
You are performing one conservative Java test-method refactoring.
Target smell: {{contract['smell']}}
Risk decision: {{contract['risk_decision']}}
Allowed changes: {{json.dumps(contract['allowed_changes'])}}
Forbidden changes: {{json.dumps(contract['forbidden_changes'])}}
Risk evidence: {{json.dumps(risk['evidence'], ensure_ascii=False)}}
Exact target assertion count: {{len(assertion_targets)}}
Exact target assertions (including nested calls): {{json.dumps(assertion_targets, ensure_ascii=False)}}
Available test-class imports: {{json.dumps(imports, ensure_ascii=False)}}
Return exactly one complete refactored test method. Do not return a class, imports, helper methods, Markdown fences, or explanations. Keep the method name, return type, parameters, annotations, all unapproved signature elements, statement order, and all unapproved behavior unchanged. If the contract cannot be satisfied, return exactly REFUSE_UNSAFE_REFACTORING.
For JUnit 5 assertion messages, place the message in the final argument position.
AR: Add only the exact ordinary string literal in each CONTRACT-V4 target.message. No calls, suppliers, concatenation, state reads, or invented claims. RA: Delete only CONTRACT-V4 target statement ranges. ECT: Replace only the full approved target region with its replacement; preserve all setup/verify and the original literal failure message. MNT: Follow CONTRACT-V4 numeric targets exactly. Insert the specified final local declaration at insert_at and replace only that numeric argument, or reuse only the explicitly approved immutable test local. Preserve types, spelling and scope. Do not extract retained or unresolved numbers.
Every listed target assertion, including an assertion nested inside another assertion argument, must receive its own meaningful message. Do not change an assertion API, expected/actual/non-message arguments, exception type, Executable lambda, parent-child nesting, or control flow.
ECT v3 NO-THROW contract (path v1): remove only the approved throws clause and wrap the entire approved method body exactly once with org.junit.jupiter.api.Assertions.assertDoesNotThrow(() -> { ... });. Preserve every original statement, token, comment, helper call, method reference, argument, literal and evaluation order. Do not select a narrower throw site. Do not add, delete, move, rename, clean up, catch, retry, rewrite, or import anything else. Return the complete target method only.
ECT v3 no-throw source-bound target: {{json.dumps(ect_contract, ensure_ascii=False)}}
Optional same-smell case references: {{json.dumps(case_refs, ensure_ascii=False)}}
Original target method:
{{instance['test_code']}}
Relevant production context summary:
{{json.dumps(production_summary, ensure_ascii=False)}}
```

## 槽位与序列化

| 槽位 | 来源 / 序列化 |
|---|---|
| contract['smell'], ['risk_decision'] | ExecutableContractBuilder 的正式字段 |
| json.dumps(contract['allowed_changes'/'forbidden_changes']) | 默认 JSON 序列化；保持现行 ensure_ascii 默认值 |
| json.dumps(risk['evidence'], ensure_ascii=False) | 全部风险 evidence，包含 source-bound CONTRACT-V4 proof；不是只传目标摘要 |
| len(assertion_targets) / json.dumps(assertion_targets, ensure_ascii=False) | conclusion=MISSING_MESSAGE 的 evidence 投影：evidence_id、assertion_identity、api、ast_path、parent_assertion_id、nesting_depth、matched_code |
| json.dumps(imports, ensure_ascii=False) | context.test_class_source 的 import 正则提取，保留现行顺序 |
| json.dumps(ect_contract, ensure_ascii=False) | proof.targets[0] 的路径字段投影；不存在的键为 null |
| json.dumps(case_refs, ensure_ascii=False) | 入参 cases 的 case_id/summary；未启用 RAG 时为空数组 |
| instance['test_code'] | normalized 执行视图的方法源码，不从 provenance 随意拼接 |
| json.dumps(production_summary, ensure_ascii=False) | production_context 的 available、productionClassName、focalMethodSignatures/Summaries、dependencyMethodSignatures/Summaries、astMethodCalls、astControlFlow；省略 None/[]/空串 |
| json.dumps(proof, ensure_ascii=False) | RA catch 专用全部 proof |

no_throw 的 ect_contract 投影：target_id、throws_text、throws_type、throws_remove_start、throws_end、body_start、body_end、body_hash、body_ast_fingerprint、statement_count、statements、wrapper_api。

must_throw 的 ect_contract 投影：target_id、exception_type、catch_variable、flag、protected_statement、protected_statement_kind、exception_observations、replacement、safety_conditions。fail 路径已独立投影 target_id、exception_type、trigger、message、replacement；无 fail 消息时 message 为 null。

## 可选后缀

Full 策略组合与 risk assessment mode 是不同维度。若 risk.assessment_mode == rule_llm 且 admission_guidance 非空，build 在 validate_advice/check_evidence 通过后追加以下内容并重算 hash；rule_only 模式不追加该后缀。

```text

Verified admission advice (guidance, NOT a safety proof). The source-bound candidate contract and all downstream checks remain mandatory.
{{json.dumps(advice, ensure_ascii=False)}}
```

RA catch 的 early return 发生在 _build；仍可经过外层 build 的 guidance 后缀。它不包含通用模板的 case references/imports/production summary。

## 验证边界

提示词约束由对应 Python validator 检查。单个 Java Markdown 代码块可被清理；RA catch 允许布局差异，普通 RA 和 ECT fail 的 AST 比较忽略注释。静态检查通过后仍需编译、目标测试和重检测。
