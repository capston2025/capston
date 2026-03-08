# Benchmark Summary

- suite: recovery_stress_v1
- scenarios: 10
- repeats: 2
- model: gpt-5.4
- success_rate: 0.5
- avg_time_seconds: 64.16
- KPI scenario_success_rate: 0.5
- KPI reproducibility_rate: 0.5
- KPI progress_stop_failure_rate: 0.4
- KPI self_recovery_rate: None
- KPI intervention_rate: 0.0
- status_counts: {'SUCCESS': 10, 'FAIL': 10}

## Failures

- REC_002_LOGIN_MODAL_BACKDROP_CLOSE: FAIL / benchmark_timeout(120s)
- REC_004_DYNAMIC_SEARCH_THEN_RESULT_ACTION: FAIL / 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- REC_005_FILTER_RELOAD_PERSISTENCE: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 필터 의미 검증 실패: 필수 체크 실패 4건
- REC_008_CLEAR_WISHLIST_ZERO_STATE: FAIL / benchmark_timeout(180s)
- REC_009_COMBINATION_MODAL_AND_APPLY: FAIL / benchmark_timeout(180s)
- REC_002_LOGIN_MODAL_BACKDROP_CLOSE: FAIL / benchmark_timeout(120s)
- REC_004_DYNAMIC_SEARCH_THEN_RESULT_ACTION: FAIL / 화면 상태가 반복되어 더 이상 진행이 어렵습니다. 현재 페이지에서 수동 전환 후 다시 시도하세요.
- REC_005_FILTER_RELOAD_PERSISTENCE: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 필터 의미 검증 실패: 필수 체크 실패 4건
- REC_008_CLEAR_WISHLIST_ZERO_STATE: FAIL / 동일 액션이 반복되어 실행을 중단했습니다. 목표를 더 구체적으로 입력하거나 /url 후 다시 시도하세요.
- REC_009_COMBINATION_MODAL_AND_APPLY: FAIL / benchmark_timeout(180s)
