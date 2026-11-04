# ECT dispatcher 3.0

入口为 `safety_contracts.analyze_ect`，rule version 4.2。本目录区分已有 path contract，没有新增范围。

- [must_throw](must_throw/README.md)：fail 1.0 与 flag 2.0。
- [no_throw](no_throw/README.md)：完整 body 包装 1.0。

multiple catch、union catch、finally、resource、异常/JUnit 绑定不明等保持原 review/skip，具体决策由 Python 返回。此说明不是放宽准入的条件列表。
