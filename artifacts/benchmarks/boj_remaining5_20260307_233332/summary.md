# Benchmark Summary

- suite: boj_remaining5
- scenarios: 5
- repeats: 1
- model: gpt-5.4
- success_rate: 0.2
- avg_time_seconds: 44.99
- status_counts: {'FAIL': 4, 'SUCCESS': 1}

## Failures

- BOJ_006_PROBLEMSET_SEARCH_1000: FAIL / 필터 의미 검증에서 필수 항목 실패가 발생했습니다. 동일 액션이 반복되어 실행을 중단했습니다. 목표를 더 구체적으로 입력하거나 /url 후 다시 시도하세요.
- BOJ_007_PROBLEMSET_OPEN_1000: FAIL / benchmark_timeout(60s)
- BOJ_014_ADDED_EN_PAGE: FAIL / benchmark_timeout(45s)
- BOJ_018_TAGS_PAGE: FAIL / benchmark_timeout(45s)
