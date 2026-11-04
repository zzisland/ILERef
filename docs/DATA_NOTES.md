# 数据口径

- `data/normalized/instances.csv` 为历史实例；`executions.csv`、`candidates.csv`、`verification_checks.csv`、`decisions.csv` 通过各自 ID 连接。空值和缺失证据不等于 PASS。
- `cohort_membership.csv` 与 `data/cohorts/` 保存冻结样本；`configurations.json` 区分生成、验证和决策配置。`configurations/validation/dataset_schema.json` 是字段定义。
- RQ1 保留检测、准入、接受和共同基线口径；候选覆盖率、确认成功、失败及证据不足不能合并。
- RQ2-A 的 strict 与 common 终点分开计算，Full 的未尝试/放弃样本保留；RQ2-B 对同一配对集合比较证据验收与仅编译/测试验收。
- RQ3 的 `rq3_pairs.csv` 比较历史来源、候选、目标测试身份、检测结果和最终决策；本次仅重算记录，不重放全部实验。
- `paper_cells.json` 仅在计算完成后比较论文数值，不作为计算输入的替代。
- `data/source_records/index.csv`、`REPOSITORY_AVAILABILITY.csv` 和 `EXCLUDED_MATERIALS.csv` 原样保留历史证据可用性声明；索引、历史路径别名及哈希不代表原始文件随包存在。这些表描述历史实验对象，不是当前复现包的文件清单。
- 原始 CLI 检测输入是保存的 schema 4.1 上下文快照，不是重新执行检测器得到的结果，也不是未经筛选的原始检测 CSV。原 `selectedSmells=[]`、`ignoredSmells` 和 `legacy://` 路径保留于 `source_record`；本次选择与源码路径仅写入重新构建的规范实例及绑定证据。
- `results/runs/` 是新模型运行或显式准备检查；`results/checks/` 是软件/测试夹具验证；二者都不覆盖 `data/` 中的历史实验数据。
