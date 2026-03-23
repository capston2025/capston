# Benchmark Summary

- suite: wikipedia_public_exploratory_v2
- scenarios: 4
- repeats: 1
- model: gpt-5.4
- success_rate: 0.5
- avg_time_seconds: 98.03
- KPI scenario_success_rate: 0.5
- KPI reproducibility_rate: None
- KPI progress_stop_failure_rate: 0.5
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 2, 'FAIL': 2}

## Failures

- WIKI_003_OPEN_RESULT_ARTICLE: FAIL / benchmark_timeout(180s)
- WIKI_004_BACK_TO_RESULTS: FAIL / benchmark_timeout(180s)
