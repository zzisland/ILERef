# ILERef 单实例复现

从随包保存的 Apache Commons CLI 检测后上下文记录开始，重新构建一个 Assertion Roulette（AR）实例，执行源码身份绑定、适用性判断、契约与提示词构建、真实模型生成、候选检查、源码回填、编译、目标测试、重检测归属和最终证据复核。初始检测不执行；重构后使用固定的检测 JAR 和 Java 前端。

原始源码固定于 `8d56926d951f1193d438c328bcdb01ddb48aac01`，归档及逐文件哈希位于 `examples/ar_cli/`。目标为 `UtilTest.testFindNonWhitespacePos(Character, boolean)`。历史记录包含三类问题，且曾忽略 AR；`detection.json` 原样保留，`config/run_ar.json` 单独声明本次选择。入口重新绑定路径、源码、AST 范围和方法归属，并重新判断适用性，不沿用历史准入、候选或 ACCEPT。

## 环境与运行

本地检查使用 Python 3.13.9、JDK 25.0.2、Maven 3.9.15。Python 依赖版本见 `requirements.txt`；新环境可执行 `python -m pip install -r requirements.txt`。Java/Maven 通过 PATH 或 JAVA_HOME/MAVEN_HOME 配置。Maven 依赖版本在两个 POM 中；默认从 Maven Central 解析到本目录的 `results/maven_repository/`。不包含虚拟环境、历史缓存或密钥。

将 `config/model.example.yaml` 中的 `refactoring.model` 和 `base_url` 改为实际模型与 OpenAI-compatible 服务地址，凭据由环境变量注入。默认实例适用性采用现有 rule_only，生成采用 FULL 可执行契约；RAG 和迭代修复关闭。

```powershell
cd E:\paper\ILERef-repro
$env:ILEREF_API_KEY = '在本地设置实际凭据'
python -B scripts/run_instance.py --name ar_001
```

配置路径可用 `--config config/run_ar.json` 指定，包内输入路径均相对项目根目录解析。每次使用新的运行名称。默认入口必须请求模型生成新候选，没有历史响应回放或候选文件输入选项。

## 检查与表格重算

```powershell
python -B scripts/check_package.py
python -B tests/test_instance_pipeline.py
python -B scripts/run_instance.py --prepare-only --name prepare_001
python -B scripts/run_instance.py --check-environment --name env_001
python -B scripts/reproduce_tables.py --all
python -B scripts/validate_dataset.py
```

`--prepare-only` 只检查到契约/提示词；`--check-environment` 另行编译前端、编译原始项目并确认原始目标测试执行。这两个检查模式均不生成候选，最终为 INCONCLUSIVE，退出码为 2。`tests/check_engineering.py` 用新构造、显式标记为 TEST-ONLY 的源码引用夹具执行真实下游检查；它不是模型运行或论文实验结果。

本次按要求复用了已有本地环境，没有安装依赖。复用已有公共 Maven 缓存离线检查时，可设置 `ILEREF_MAVEN_REPOSITORY` 为缓存目录、`ILEREF_MAVEN_OFFLINE=1`；若缓存来自不同 mirror id，再设置 `ILEREF_MAVEN_MIRROR_ID` 为该标识。新包不自动寻找旧项目缓存，也不读取个人 Maven settings。

## 输出与限制

新运行保存到 `results/runs/<name>/`：原检测记录、本次选择与源码绑定、实例、适用性、契约、提示词、无密钥模型身份、模型响应、候选、结构检查、编译/测试日志、测试 XML、重检测 CSV、归属结果、当前文件复核、最终 `result.json` 和证据哈希清单。源码回填发生于隔离副本。候选被拒绝或执行中断时，保留已产生的证据，未执行阶段保持 NOT_RUN。

ACCEPTED（退出 0）要求契约、结构、实际编译、精确目标测试、目标 AR 消除及当前源码/候选哈希链全部通过；REJECTED（退出 1）表示明确不满足准入或验证要求；INCONCLUSIVE（退出 2）表示凭据、服务、环境、检测或归属等证据不足。测试进程退出 0、历史 ACCEPT 或单一检测标签均不能代替完整接受。

历史论文数据位于 `data/`、`experiments/`，不接收新运行输出。重算结果位于 `results/tables/` 和 `results/validation/`。本次表 I–VI 的 96/96 数值单元格一致，元数据检查通过；历史完整候选、契约和原始执行字节仍有缺失，完整证据检查为 BLOCKED。RQ3 的 79/79 是历史配对记录的重算，并非本次重跑。字段口径见 `docs/DATA_NOTES.md`。

本次本地缺少 `langchain-openai` 和模型凭据，尚未验证真实模型生成及其端到端结果。模型客户端指定版本未在本机安装测试。工程与回归检查结果保存在 `results/checks/`：7 项回归通过，原始目标执行 16 次，TEST-ONLY 夹具的完整下游验证通过，含空格路径迁移并禁止旧目录读取的检查通过；测试夹具结果与模型运行严格分开。契约文档中的旧 test_entry/Stage3 审计链接仅保留为历史来源，不提供完整旧审计工程；运行资源由 `scripts/check_package.py` 检查。检测器源码快照已保留，但尚未重建其 JAR。许可证与来源见 `THIRD_PARTY_NOTICES.md`。
