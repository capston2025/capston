# Benchmark Summary

- suite: boj_readonly_v1
- scenarios: 20
- repeats: 1
- model: gpt-5.4
- success_rate: 0.75
- avg_time_seconds: 30.38
- status_counts: {'SUCCESS': 15, 'FAIL': 5}

## Failures

- BOJ_006_PROBLEMSET_SEARCH_1000: FAIL / benchmark_timeout(60s)
- BOJ_007_PROBLEMSET_OPEN_1000: FAIL / benchmark_timeout(60s)
- BOJ_014_ADDED_EN_PAGE: FAIL / DOM 요소를 반복적으로 읽지 못해 실행을 중단했습니다. 페이지 로딩 상태나 MCP host 연결을 확인하세요.
- BOJ_015_PROBLEM_RANKING_PAGE: FAIL / benchmark_timeout(45s)
- BOJ_018_TAGS_PAGE: FAIL / benchmark_timeout(45s)
