# ECT must_throw

## fail 1.0

单 try/catch，protected region 为单调用/构造 trigger 后接已绑定 fail，catch 无行为。异常类型必须明确绑定且不能捕获 assertion failure。动态 fail 消息、未知 capture/assignment、非精确 trigger-fail 序列继续 review。

只替换批准 region 为 `assertThrows`，保留完整 trigger、异常类型和受支持的 fail 字面量消息，不猜测调用链内更小的 throw site。

## flag 2.0

`_analyze_ect_v2` 证明 false 初始化 flag、catch 唯一 true 标记和 post-try marker assertion。protected region 保留一个完整调用、构造或支持的局部声明。

`assertThrows` 返回原绑定异常；批准的 exception observations（message/cause/property 等）保持原 API、参数、字符串、顺序与时机。仅删除纯 flag 声明、catch 赋值和对应 marker，其余 setup/oracle/cleanup 保留。

类型使用原 `resolve_ect_exception_type`。flag 其他使用、外部写、条件赋值、多 protected statements、不支持的区域注释、multiple catch/finally/resource 保持 review。

两个路径都用原 prompt 分支和 `validate_exception_refactoring`，随后经过整文件验证。分别见 fail / flag manifest。
