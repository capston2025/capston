# Benchmark Summary

- suite: gamegoo_public_v1
- scenarios: 5
- repeats: 1
- model: gpt-5.4
- success_rate: 0.4
- avg_time_seconds: 137.11
- KPI scenario_success_rate: 0.4
- KPI reproducibility_rate: None
- KPI progress_stop_failure_rate: 0.6
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 2, 'FAIL': 3}

## Failures

- GAMEGOO_002_FILTER_AND_LIST_REFRESH: FAIL / benchmark_timeout(180s)
- GAMEGOO_003_SORT_OR_TAB_SWITCH_REFRESH: FAIL / benchmark_timeout(180s)
- GAMEGOO_005_DETAIL_CLOSE_RECOVERY: FAIL / benchmark_timeout(180s)
