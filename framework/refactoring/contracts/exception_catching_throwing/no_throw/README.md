# ECT no_throw 1.0

`_analyze_ect_nothrow_v1` 只支持已绑定 Jupiter 普通 void 零参数测试；一个 throws 类型须由 `resolve_ect_checked_exception_type` 证明，helper closure 可检查。

protected region 是完整方法体。仅移除批准 throws clause，并用全限定 `org.junit.jupiter.api.Assertions.assertDoesNotThrow(() -> { ... })` 包装整个 body。原语句、token、注释、调用/方法引用、参数、字面量和顺序保持，不添加 import。

不在此路径删除 fail branch 或 flag：直接/可达断言、assumption、已有异常断言、显式 throw、控制流和不支持的 capture 保持现有 review/skip。try/catch/finally/resource 不自动清理，外部异常观察扩展不明仍 review。

`materialize_ect_nothrow` 给出目标形状，原 prompt 与 `validate_exception_refactoring` 检查完整 body 契约；失败诊断沿用现有策略，不重新解释异常语义。
