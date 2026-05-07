# KPI benchmark protocol

## 목적

GAIA의 발표용 KPI를 단일 공개 읽기 벤치만으로 설명하지 않고, 아래 3개 suite로 분리해 측정한다.

1. 공개 읽기 벤치
2. 실서비스 벤치
3. 복구 스트레스 벤치

발표용 일반성 방어가 필요할 때는 `external_public_manifest.json` 기준 외부 공개 사이트 30개 / 150개 시나리오 KPI pack을 별도로 실행한다.

## Suite 구성

### 1. 공개 읽기 벤치

- 파일: `/Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/boj_readonly_suite.json`
- 목적:
  - 범용 읽기/탐색 성능
  - ref-only 실행 안정성
  - 개입 없는 공개 사이트 baseline

### 1-1. 외부 공개 다양성 벤치

- manifest: `/Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/external_public_manifest.json`
- 목적:
  - 내부 서비스 휴리스틱 우려 방어
  - 한국 사용자에게 익숙한 공개 사이트 중심의 범용 웹 다양성 측정
  - 포털/뉴스/커머스/공공데이터/개발자/금융·게임/채용/문화 사이트의 변동성 분리 기록
- 주의:
  - 로그인, 결제, 장바구니 확정, 글쓰기, 댓글, 삭제, CAPTCHA 우회는 포함하지 않는다.
  - 실제 부분 실행에서 CAPTCHA 또는 bot-wall 차단이 반복된 사이트는 primary curated pack에서 제외한다.
  - 특정 scenario URL에서만 CAPTCHA가 재현되면 같은 사이트의 안정적인 공개 read-only URL로 교체한다.
  - 차단이 재현된 사이트를 제외할 때도 30개 사이트 / 150개 시나리오 규모는 유지하도록 안정적인 공개 read-only 사이트로 대체한다.
  - 실행 중 새로 CAPTCHA/보안문자/보안 확인 화면이 나오면 `BLOCKED_USER_ACTION` + `blocked_captcha`로 분리하고 primary 성공률 계산에서 제외한다.
  - 시나리오 문장은 일반 템플릿이 아니라 각 사이트의 실제 공개 업무 흐름(검색, 비교, 상세 확인, 목록/필터/지도/차트 탐색)에 맞춘다.
  - 지도/경로 시나리오는 hidden tab 클릭 자체보다 공개 deep link로 진입한 경로/장소/지도 정보 확인을 우선한다.
  - 동적 사이트 실패는 `blocked`, `timeout`, `site volatility`, `login gate` caveat로 분리한다.

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

### Primary 성공률

- `primary_success_rate`
- CAPTCHA, 보안문자, 로그인 gate처럼 사용자의 명시적 개입이 필요한 `BLOCKED_USER_ACTION` run을 제외한 성공률
- 발표에서 "자동화가 실제로 시도할 수 있었던 공개 시나리오" 기준 성공률로 사용한다.

### 개입률

- `intervention_rate`
- `BLOCKED_USER_ACTION`으로 끝난 run 비율

## 권장 실행 명령

### 공개 읽기 벤치

```bash
GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python /Users/coldmans/Documents/GitHub/capston/scripts/run_goal_benchmark.py \
  --suite /Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/boj_readonly_suite.json \
  --repeats 2 \
  --timeout-cap 120 \
  --session-prefix boj-readonly
```

### 실서비스 + 복구 스트레스 + 공개 읽기 통합 KPI pack

```bash
GAIA_LLM_MODEL=gpt-5.5 \
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

### 외부 공개 다양성 KPI pack

```bash
PYTHONPATH=. GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python /Users/coldmans/Documents/GitHub/capston/scripts/run_kpi_benchmark_pack.py \
  --suite-manifest /Users/coldmans/Documents/GitHub/capston/gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-20260507 \
  --push-metrics
```

### 성능 개선 전후 비교

`scripts/run_goal_benchmark.py`가 남긴 두 artifact directory를 비교해 성공률/평균 시간/진행 멈춤/step 수 회귀를 확인한다.

```bash
python /Users/coldmans/Documents/GitHub/capston/scripts/compare_benchmark_runs.py \
  --baseline /Users/coldmans/Documents/GitHub/capston/artifacts/tmp/perf_hn_limit3 \
  --candidate /Users/coldmans/Documents/GitHub/capston/artifacts/tmp/perf_hn_limit3_postjudge \
  --output-dir /Users/coldmans/Documents/GitHub/capston/artifacts/tmp/compare_hn_postjudge \
  --fail-on-regression
```

### 로컬 benchmark 기록 정리

터미널 benchmark mode의 `지표 확인` 메뉴에서 `실패 기록 삭제`를 선택하면 현재 사이트/URL에 매칭되는 로컬 artifact 중 실패가 포함된 기록만 삭제한다.

CLI에서 먼저 확인만 할 때:

```bash
python /Users/coldmans/Documents/GitHub/capston/scripts/prune_benchmark_records.py \
  --site-key wikipedia \
  --url https://ko.wikipedia.org/
```

실제로 삭제할 때:

```bash
python /Users/coldmans/Documents/GitHub/capston/scripts/prune_benchmark_records.py \
  --site-key wikipedia \
  --url https://ko.wikipedia.org/ \
  --confirm
```

## 해석 원칙

1. 공개 읽기 벤치는 범용 baseline이다.
2. 실서비스 벤치는 실제 제품 가치 검증이다.
3. 복구 스트레스 벤치는 `자가 복구율`을 보기 위한 별도 지표다.
4. 발표에서는 3개를 따로 보여주고, 마지막에 통합 KPI를 제시한다.
5. `자가 복구율`이 `null`이면 recovery event가 없는 데이터셋이므로, 실패가 아니라 “측정 불가”로 해석한다.
6. 성능 개선 주장은 단일 성공 로그가 아니라 비교 artifact의 gate와 delta를 같이 제시한다.
