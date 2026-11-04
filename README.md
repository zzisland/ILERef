# ILERef

ILERef 提供基于实例、可执行契约和验证证据的测试代码重构实现，以及 RQ1–RQ3 实验记录和论文表 I–VI 的重算脚本。

随包提供 Apache Commons CLI 原始源码、已有检测记录、Java 前端和检测 JAR。单实例入口以 `UtilTest.testFindNonWhitespacePos(Character, boolean)` 的 Assertion Roulette（AR）为例，执行：

```text
已有检测记录与源码 → 实例构建与源码绑定 → 适用性判断
→ 契约与提示词 → 模型生成新候选 → 契约与结构检查
→ 源码回填 → 编译与目标测试 → 重检测与归属 → 最终决策及证据保存
```

流程从已有检测记录开始，省略初始检测；每次重新构建实例、判断适用性并生成候选。原始材料位于 `examples/ar_cli/`，本次目标和资源路径由 `config/run_ar.json` 指定。

## 环境与配置

参考环境：Python 3.13.9、JDK 25.0.2、Maven 3.9.15。Python 依赖版本见 [requirements.txt](requirements.txt)，Java 依赖版本见各模块的 POM。Java 和 Maven 需在 PATH 中可用，也可通过 `JAVA_HOME`、`MAVEN_HOME` 指定安装位置。

以下命令均在项目根目录执行。已有环境满足依赖时可直接使用；新环境安装依赖：

```powershell
python -m pip install -r requirements.txt
```

编辑 [config/model.example.yaml](config/model.example.yaml) 中 `roles.refactoring` 下的 `model` 和 `base_url`，分别填写模型名称和 OpenAI-compatible 服务地址。API 密钥由环境变量 `ILEREF_API_KEY` 提供。默认使用规则式适用性判断和 FULL 可执行契约，关闭 RAG 与迭代修复。

Maven 默认从 Maven Central 获取依赖，并缓存在 `results/maven_repository/`。复用已有公共依赖缓存时可设置 `ILEREF_MAVEN_REPOSITORY`；依赖齐全后可用 `ILEREF_MAVEN_OFFLINE=1` 离线运行。若缓存使用其他镜像标识，可通过 `ILEREF_MAVEN_MIRROR_ID` 指定。

## 运行一个 AR 实例

```powershell
$env:ILEREF_API_KEY = '填写实际凭据'
python -B scripts/run_instance.py --name ar_001
```

入口默认读取 `config/run_ar.json`，也可用 `--config` 指定包内其他配置。每次使用不同的运行名称；源码回填和验证在隔离副本中执行。

按需检查包资源、准备阶段或编译测试环境：

```powershell
python -B scripts/check_package.py
python -B tests/test_instance_pipeline.py
python -B scripts/run_instance.py --prepare-only --name prepare_001
python -B scripts/run_instance.py --check-environment --name env_001
```

`--prepare-only` 执行到契约与提示词构建；`--check-environment` 另外编译 Java 前端、原始项目并执行原始目标测试。两种检查模式均不生成候选，结束时为 `INCONCLUSIVE`（退出码 2）。

## 重算论文表格

```powershell
python -B scripts/reproduce_tables.py --all
python -B scripts/validate_dataset.py
```

表格脚本从 `data/` 和 `experiments/` 中的冻结记录计算表 I–VI，并与论文的 96 个数值单元格比较。表格写入 `results/tables/`，验证报告写入 `results/validation/`。字段与统计口径见 [数据说明](docs/DATA_NOTES.md)。

## 输出与范围

每次运行保存到 `results/runs/<name>/`，包括输入、实例与源码绑定、适用性结果、契约、提示词、模型响应、候选、验证日志、测试 XML、重检测 CSV，以及最终 `result.json` 和证据哈希清单。中断或拒绝时保留已生成的证据，后续阶段标记为 `NOT_RUN`。

| 最终结果 | 含义 | 退出码 |
|---|---|---|
| `ACCEPTED` | 契约、结构、编译、目标测试、目标 AR 消除及证据一致性检查全部通过 | 0 |
| `REJECTED` | 明确不满足准入或验证要求 | 1 |
| `INCONCLUSIVE` | 尚无足够证据支持结论，或仅执行了准备/环境检查 | 2 |

单实例运行需要配置模型服务；表格重算使用历史记录，不重跑全部论文实验。包内保留历史证据索引，但未包含完整的历史候选、契约及原始日志，因此完整历史证据检查可能为 `BLOCKED`。历史数据与新运行输出分开保存。

许可证与来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
