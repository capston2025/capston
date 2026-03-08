# Benchmark Summary

- suite: boj_readonly_v1
- scenarios: 20
- repeats: 1
- model: gpt-5.4
- success_rate: 0.7
- avg_time_seconds: 31.18
- KPI scenario_success_rate: 0.7
- KPI reproducibility_rate: None
- KPI progress_stop_failure_rate: 0.3
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 14, 'FAIL': 6}

## Failures

- BOJ_006_PROBLEMSET_SEARCH_1000: FAIL / benchmark_timeout(60s)
- BOJ_007_PROBLEMSET_OPEN_1000: FAIL / benchmark_timeout(60s)
- BOJ_008_PROBLEM_1000_DETAIL: FAIL / benchmark_timeout(45s)
- BOJ_009_PROBLEM_2557_DETAIL: FAIL / benchmark_timeout(45s)
- BOJ_010_STATUS_PAGE: FAIL / benchmark_timeout(45s)
- BOJ_012_BOARD_LIST_PAGE: FAIL / benchmark_timeout(45s)
