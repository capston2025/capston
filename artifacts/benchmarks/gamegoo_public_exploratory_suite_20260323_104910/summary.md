# Benchmark Summary

- suite: gamegoo_public_exploratory_v1
- scenarios: 5
- repeats: 1
- model: gpt-5.4
- success_rate: 0.2
- avg_time_seconds: 148.01
- KPI scenario_success_rate: 0.2
- KPI reproducibility_rate: None
- KPI progress_stop_failure_rate: 0.8
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 1, 'FAIL': 4}

## Failures

- GAMEGOO_002_FILTER_AND_LIST_REFRESH: FAIL / benchmark_timeout(180s)
- GAMEGOO_003_CATEGORY_TAB_SWITCH_REFRESH: FAIL / benchmark_timeout(180s)
- GAMEGOO_004_OPEN_POST_DETAIL: FAIL / benchmark_timeout(180s)
- GAMEGOO_005_RETURN_TO_LIST_FROM_DETAIL: FAIL / benchmark_timeout(180s)
