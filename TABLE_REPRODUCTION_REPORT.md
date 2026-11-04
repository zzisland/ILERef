# Table reproduction report

Status: **PASS**. Numeric cells matched: **96/96**.

Computed from normalized instance/cohort/decision records. Frozen paper cells are comparison targets only.

| Table | Matched | Cells |
|---|---:|---:|
| I | 20 | 20 |
| II | 25 | 25 |
| III | 8 | 8 |
| IV | 24 | 24 |
| V | 12 | 12 |
| VI | 7 | 7 |

RQ2-A strict and common endpoints remain separate. RQ3 79/79 describes historical replay comparisons, not a replay performed by this build.

Supplementary counts:

{
  "shared_baseline_targets": 54,
  "rq3_source_instances": 120,
  "rq3_replay_instances": 79,
  "rq3_all_five_comparisons_equal": 79,
  "strict_pairs": {
    "both": 65,
    "full_only": 19,
    "rule_only": 8,
    "neither": 0
  },
  "common_pairs": {
    "both": 72,
    "full_only": 19,
    "rule_only": 1,
    "neither": 0
  }
}

Detailed mismatches, if any, are in results/validation/table_reproduction.json.
