# KPI benchmark protocol

## 목적

GAIA의 발표용 KPI를 단일 공개 읽기 벤치만으로 설명하지 않고, 아래 3개 suite로 분리해 측정한다.

1. 공개 읽기 벤치
2. 실서비스 벤치
3. 복구 스트레스 벤치

## Suite 구성

### 1. 공개 읽기 벤치

- 파일: `/Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/boj_readonly_suite.json`
- 목적:
  - 범용 읽기/탐색 성능
  - ref-only 실행 안정성
  - 개입 없는 공개 사이트 baseline

### 2. 실서비스 벤치

- 파일: `/Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/inuu_service_suite.json`
- 목적:
  - 로그인
  - 검색/필터
  - 위시리스트
  - 조합 생성
  - 시간표 반영
- 주의:
  - 인증이 필요한 케이스는 테스트 계정 환경변수가 필요하다.
  - 권장:
    - `GAIA_TEST_USERNAME`
    - `GAIA_TEST_PASSWORD`

### 3. 복구 스트레스 벤치

- 파일: `/Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/recovery_stress_suite.json`
- 목적:
  - 모달/오버레이
  - stale snapshot/ref
  - auth prompt
  - 후속 액션 연결
  - fallback/resnapshot/retry 계열 측정

## KPI 정의

### 재현성

- `reproducibility_rate`
- 같은 시나리오를 `K`번 반복했을 때 전부 `SUCCESS`인 비율

### 진행 멈춤 실패율

- `progress_stop_failure_rate`
- timeout, stuck, observe_no_dom, blocked_timeout 등으로 종료된 비율

### 자가 복구율

- `self_recovery_rate`
- stale/resnapshot/fallback/request_exception 등 recovery event가 발생한 run 중 최종 성공 비율

### 시나리오 성공률

- `scenario_success_rate`
- 전체 run 중 최종 `SUCCESS` 비율

### 개입률

- `intervention_rate`
- `BLOCKED_USER_ACTION`으로 끝난 run 비율

## 권장 실행 명령

### 공개 읽기 벤치

```bash
GAIA_LLM_MODEL=gpt-5.4 GAIA_RAIL_ENABLED=0 \
python /Users/coldmans/Documents/GitHub/capston/scripts/run_goal_benchmark.py \
  --suite /Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/boj_readonly_suite.json \
  --repeats 2 \
  --timeout-cap 120 \
  --session-prefix boj-readonly
```

### 실서비스 + 복구 스트레스 + 공개 읽기 통합 KPI pack

```bash
GAIA_LLM_MODEL=gpt-5.4 \
GAIA_RAIL_ENABLED=0 \
GAIA_TEST_USERNAME=202101681 \
GAIA_TEST_PASSWORD=qwer \
python /Users/coldmans/Documents/GitHub/capston/scripts/run_kpi_benchmark_pack.py \
  --suite /Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/boj_readonly_suite.json \
  --suite /Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/inuu_service_suite.json \
  --suite /Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/recovery_stress_suite.json \
  --repeats 2 \
  --timeout-cap 180 \
  --session-prefix gaia-kpi
```

## 해석 원칙

1. 공개 읽기 벤치는 범용 baseline이다.
2. 실서비스 벤치는 실제 제품 가치 검증이다.
3. 복구 스트레스 벤치는 `자가 복구율`을 보기 위한 별도 지표다.
4. 발표에서는 3개를 따로 보여주고, 마지막에 통합 KPI를 제시한다.
5. `자가 복구율`이 `null`이면 recovery event가 없는 데이터셋이므로, 실패가 아니라 “측정 불가”로 해석한다.
