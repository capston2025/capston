# Benchmark Summary

- suite: inuu_service_v1
- scenarios: 10
- repeats: 2
- model: gpt-5.4
- success_rate: 0.2
- avg_time_seconds: 113.7
- KPI scenario_success_rate: 0.2
- KPI reproducibility_rate: 0.2
- KPI progress_stop_failure_rate: 0.7
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 4, 'FAIL': 16}

## Failures

- INUU_002_SEARCH_CHANGES_RESULTS: FAIL / 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- INUU_004_DIVISION_FILTER: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 필터 의미 검증 실패: 필수 체크 실패 4건
- INUU_005_PAGINATION_PERSISTS: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- INUU_006_LOGIN_AND_ADD_WISHLIST: FAIL / benchmark_timeout(180s)
- INUU_007_LOGIN_AND_REMOVE_WISHLIST: FAIL / benchmark_timeout(180s)
- INUU_008_LOGIN_AND_CLEAR_WISHLIST: FAIL / benchmark_timeout(180s)
- INUU_009_LOGIN_AND_GENERATE_COMBINATION: FAIL / benchmark_timeout(180s)
- INUU_010_LOGIN_APPLY_FRIDAY_FREE_COMBINATION: FAIL / benchmark_timeout(180s)
- INUU_002_SEARCH_CHANGES_RESULTS: FAIL / 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- INUU_004_DIVISION_FILTER: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 필터 의미 검증 실패: 필수 체크 실패 5건
- INUU_005_PAGINATION_PERSISTS: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- INUU_006_LOGIN_AND_ADD_WISHLIST: FAIL / 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- INUU_007_LOGIN_AND_REMOVE_WISHLIST: FAIL / benchmark_timeout(180s)
- INUU_008_LOGIN_AND_CLEAR_WISHLIST: FAIL / benchmark_timeout(180s)
- INUU_009_LOGIN_AND_GENERATE_COMBINATION: FAIL / benchmark_timeout(180s)
- INUU_010_LOGIN_APPLY_FRIDAY_FREE_COMBINATION: FAIL / benchmark_timeout(180s)
