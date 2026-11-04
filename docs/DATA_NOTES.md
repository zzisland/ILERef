# 数据口径

## 论文概念与实现

论文提出面向 LLM 代码重构的通用框架，并在 Java 测试异味领域进行验证。本包保留该领域的实现与实验材料；AR 单实例入口是框架的一个运行示例。

| 论文概念名称 | 测试异味运行实例字段 |
|---|---|
| `issue_type` | `smell_type` |
| `detected_issues` | `detected_smells` |
| `selected_issues` | `selected_smells` |

此表说明概念对应关系，不声明运行接口支持两套字段别名。构建实例以 `framework/pipeline/instances/schemas/pipeline-instance.schema.json` 为准；规范化实验 CSV 使用各自的数据 schema，例如 `instances.csv` 使用 `issue_type`。

论文最终判定 `ACCEPT`、`REJECT`、`REVIEW` 的接受、拒绝和证据不足含义，在单实例入口分别以 `ACCEPTED`、`REJECTED`、`INCONCLUSIVE` 表达；历史记录保留原标签。适用性阶段的 `ADMIT`、`REVIEW`、`PRESERVE` 是另一个阶段的概念，不等同于最终验收结果。入口仅让实现中的 `REFACTOR_LOW_RISK` 进入生成；其他适用性结果及退出原因保存在运行证据中。

## 实验记录与重算

- `data/normalized/instances.csv` 为历史实例；`executions.csv`、`candidates.csv`、`verification_checks.csv`、`decisions.csv` 通过各自 ID 连接。空值和缺失证据不等于 PASS。
- `cohort_membership.csv` 与 `data/cohorts/` 保存冻结样本；`configurations.json` 区分生成、验证和决策配置。`configurations/validation/dataset_schema.json` 是字段定义。
- RQ1 对应总体有效性与 UTRefactor 共同标准比较。表 IV 的 5,589 / 2,175 / 1,993 分别为检测、准入和合并记录中的不同接受实例；1,993 由 1,812 条较早接受与 181 条补充接受组成，条件接受率为 91.6%。表 III 的候选覆盖、确认成功、失败及证据不足不能合并。
- RQ2-A 对应适用性判断与契约约束生成的联合消融。表 V 保留论文的 Full-strict 84/92 与 Rule-Guided-common 73/92；另行保留共同标准下的 Full 91/92 与 Rule-Guided 73/92。Full 的未尝试/放弃样本保留。RQ2-B 对同一批 143 个历史候选比较证据验收与仅编译/目标测试验收，不新增模型调用。
- RQ3 对应实例级可追溯性与结果归属。`rq3_pairs.csv` 保存论文所报告历史重放的来源/候选哈希、目标测试身份、检测结果和最终决策配对比较。79/79 是这些记录的一致性统计；重算命令不重新编译、测试或检测这 79 个候选，也不验证跨环境重放。
- `paper_cells.json` 仅在计算完成后比较论文数值，不作为计算输入的替代。
- `data/source_records/index.csv`、`REPOSITORY_AVAILABILITY.csv` 和 `EXCLUDED_MATERIALS.csv` 原样保留历史证据可用性声明；索引、历史路径别名及哈希不代表原始文件随包存在。这些表描述历史实验对象，不是当前复现包的文件清单。
- 原始 CLI 检测输入是保存的 schema 4.1 上下文快照，不是重新执行检测器得到的结果，也不是未经筛选的原始检测 CSV。原 `selectedSmells=[]`、`ignoredSmells` 和 `legacy://` 路径保留于 `source_record`；本次选择与源码路径仅写入重新构建的规范实例及绑定证据。
- `results/runs/` 是新模型运行或显式准备检查；`results/checks/` 是软件/测试夹具验证；二者都不覆盖 `data/` 中的历史实验数据。
