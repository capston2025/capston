# Benchmark Summary

- suite: boj_readonly_v1
- scenarios: 20
- repeats: 1
- model: gpt-5.4
- success_rate: 0.8
- avg_time_seconds: 28.07
- KPI scenario_success_rate: 0.8
- KPI reproducibility_rate: None
- KPI progress_stop_failure_rate: 0.2
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 16, 'FAIL': 4}

## Failures

- BOJ_006_PROBLEMSET_SEARCH_1000: FAIL / benchmark_timeout(60s)
- BOJ_007_PROBLEMSET_OPEN_1000: FAIL / benchmark_timeout(60s)
- BOJ_011_RANKLIST_PAGE: FAIL / benchmark_timeout(45s)
- BOJ_017_STEP_PAGE: FAIL / benchmark_timeout(45s)
