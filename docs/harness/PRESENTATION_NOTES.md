# Presentation Notes

이 문서는 다음 주 발표까지 계속 업데이트하는 발표 자료 source of truth다. 숫자, demo 근거, caveat, 발표 문장을 한 파일에 누적한다.

## 운영 규칙

- 새 실험이나 demo 증거가 생기면 날짜별 업데이트에 artifact 경로를 남긴다.
- 발표에 넣을 수치는 gate 결과와 caveat를 같이 적는다.
- 단일 성공 로그만으로 성능 개선을 주장하지 않는다. baseline/candidate 비교 artifact를 근거로 둔다.
- 오래된 artifact를 정리하더라도 이 문서의 요약과 최종 근거 경로는 먼저 갱신한다.

## 발표 핵심 메시지

GAIA는 단순 브라우저 자동화가 아니라, 목표 기반 실행 루프와 OpenClaw browser evidence, benchmark harness를 묶어서 "성공 여부를 설명 가능한 증거로 검증하는 웹 에이전트 런타임"으로 보여준다.

이번 변경의 발표 포인트는 post-action judge와 OpenClaw evidence cache를 통해 읽기/탐색형 목표에서 불필요한 추가 step을 줄이면서도 성공률과 회귀 gate를 유지했다는 점이다.

팀 공유 관점에서는 benchmark artifact를 로컬 파일로만 남기지 않고, 명시적으로 opt-in한 실행만 Prometheus Pushgateway/Grafana로 업로드해 팀원이 같은 KPI 대시보드를 볼 수 있게 했다.

## 현재 발표 후보 수치

| Date | Area | Baseline | Candidate | Gate | Success Rate | Avg Time | Avg Steps | Artifact |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-05-06 | Hacker News readonly navigation | `artifacts/tmp/perf_hn_limit3` | `artifacts/tmp/perf_hn_limit3_postjudge` | passed | 1.0 -> 1.0 | 35.22s -> 26.74s (-24.08%) | 1.67 -> 1.00 (-40.12%) | `artifacts/tmp/presentation_compare_hn_postjudge_20260506/summary.md` |
| 2026-05-06 | PyPI readonly search/detail | `artifacts/tmp/perf_pypi_limit3` | `artifacts/tmp/perf_pypi_limit3_searchjudge` | passed | 1.0 -> 1.0 | 31.58s -> 29.38s (-6.97%) | 1.67 -> 1.33 (-20.36%) | `artifacts/tmp/presentation_compare_pypi_searchjudge_20260506/summary.md` |

## 실서비스 측정

| Date | Suite | Runs | Success | Avg Time | Progress Stop | Intervention | Artifact |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2026-05-06 | `inuu_service_v1` | 10 | 9/10 (0.9) | 83.06s | 0.0 | 0.0 | `artifacts/benchmarks/presentation_inuu_service_20260506/summary.md` |
| 2026-05-06 | `inuu_service_v1` first 3 after filter validator removal | 3 | 3/3 (1.0) | 36.34s | 0.0 | 0.0 | `artifacts/benchmarks/presentation_inuu_service_state_limit3_20260506/summary.md` |

실서비스 세부 결과:

- `INUU_001_HOME_LOGIN_VISIBLE`: 성공, 로그인 CTA 확인.
- `INUU_002_SEARCH_CHANGES_RESULTS`: 성공, 검색어 `AI` 입력 후 결과가 `총 202개`와 AI 관련 과목으로 갱신됨.
- 이전 `INUU_003` 학점 필터 케이스: 실패 처리됨. 실제 화면 증거는 `1학점` 필터가 선택되고 결과 카드들이 1학점으로 보이지만, legacy filter validator가 액션 후 필터 컨트롤을 재탐지하지 못해 `filter_control_detect` 필수 체크에서 실패했다. 서비스 기능 실패라기보다 harness false negative 가능성이 높다.
- `INUU_004_DIVISION_FILTER`: 성공, 구분 필터 변경 후 결과 목록 반영.
- `INUU_005_PAGINATION_PERSISTS`: 성공 처리됐지만 실제 페이지네이션보다 필터 변경 증거로 완료됨. 발표 시 strong success로 말하지 않는다.
- `INUU_006_LOGIN_AND_ADD_WISHLIST`: 성공, 로그인 후 위시리스트가 총 30학점/10개 과목으로 증가.
- `INUU_007_LOGIN_AND_REMOVE_WISHLIST`: 성공, 위시리스트가 10개/30학점에서 9개/27학점으로 감소.
- `INUU_008_LOGIN_AND_CLEAR_WISHLIST`: 성공, 9번 제거 후 총 0학점 및 빈 상태 확인.
- `INUU_009_LOGIN_AND_GENERATE_COMBINATION`: 성공 처리됐지만 결과 리스트 확인 전 로딩/18학점 증거로 완료 판정된 의심 있음.
- `INUU_010_LOGIN_APPLY_FRIDAY_FREE_COMBINATION`: 성공 처리됐지만 실제 시간표 적용 전 `시간표 조합을 준비하고 있어요` 로딩 화면을 결과 본문으로 인정한 의심 있음.

## 업데이트 로그

### 2026-05-06

- `scripts/compare_benchmark_runs.py`로 HN/PyPI 공개 읽기 benchmark의 baseline/candidate artifact를 비교했다.
- 두 비교 모두 gate passed다.
- HN은 평균 시간 24.08%, 평균 step 40.12% 감소로 발표용 개선 사례에 적합하다.
- PyPI는 평균 시간 6.97%, 평균 step 20.36% 감소로 보조 사례에 적합하다.
- 두 suite 모두 각 3 runs 기준이므로 발표에서는 "작은 공개 읽기 benchmark"라는 caveat를 같이 말한다.
- `inuu_service_v1` 실서비스 full suite를 1회 실행했다.
- 실서비스 결과는 10개 중 9개 성공, 평균 83.06초, progress-stop/intervention 0이다.
- 실패 1건인 `INUU_003`은 최종 화면상 필터 적용 자체는 된 것으로 보이나, validator가 필터 컨트롤을 다시 찾지 못한 false negative 의심 케이스다.
- 해당 validator는 범용 엔진의 성공/실패 판정에 넣기 위험하다고 판단해 코어 런타임에서 제거했다. 후속 실서비스 재측정은 `INUU_003_CREDIT_FILTER_STATE` 기준으로 업데이트한다.
- 제거 후 first-3 재측정에서는 `INUU_001`, `INUU_002`, `INUU_003_CREDIT_FILTER_STATE`가 모두 성공했다. `INUU_003`은 학점 필터 select 후 OpenClaw state-change evidence로 완료 판정됐다.
- 단, 조합 생성/시간표 적용 쪽은 로딩 화면에서 완료 판정된 의심이 있으므로 발표에서는 "현재 harness가 찾아낸 판정 개선 과제"로 같이 설명한다.
- `grafana_connect` 브랜치는 전체 머지하지 않았다. 해당 브랜치에는 제거한 filter validation engine 경로가 함께 있어, `monitoring/`, `scripts/push_metrics.py`, `scripts/gaia_monitor_connect.py`, `scripts/gaia_monitor_setup.py`, GUI/CLI `--push-metrics` 경로만 선택적으로 통합했다.
- 모니터링 통합 검증: `docker compose -f monitoring/docker-compose.yml config`, `python scripts/gaia_monitor_connect.py --status`, `PYTHONPATH=. .venv/bin/python scripts/push_metrics.py --help`, 관련 unit 53개 통과.

## 팀 공유 모니터링

- 스택: Prometheus + Pushgateway + Grafana + nginx basic auth.
- 설정 문서: `monitoring/README.md`.
- 서버 연결 정보는 각 개발자 로컬 `~/.gaia/monitoring.json`에 저장한다. 토큰은 git에 커밋하지 않는다.
- CLI: `scripts/run_goal_benchmark.py ... --push-metrics`를 붙인 실행만 KPI metrics와 sanitize된 suite JSON을 업로드한다.
- Terminal benchmark mode: 실행 직전에 `업로드하기` / `로컬만 저장`을 방향키로 고른다. `gaia --terminal --push-metrics` 또는 `python -m gaia.cli --terminal --push-metrics`로 시작하면 기본 opt-in으로 전달된다.
- Terminal benchmark mode에서 업로드를 선택했는데 `~/.gaia/monitoring.json`이 없으면 `지금 연결하기` / `연결 명령 보기` / `로컬만 저장` 연결 메뉴를 먼저 보여준다.
- Terminal benchmark mode의 `지표 확인`은 `Grafana 열기` / `로컬 결과 보기` / `이전으로` 중 선택한다. Grafana를 선택했는데 모니터링 연결이 없으면 같은 연결 메뉴를 먼저 보여주고, 로컬을 선택하면 기존 HTML 결과 보드를 생성한다.
- Terminal benchmark mode는 모니터링 서버 연결이 있으면 사이트 선택 직후 `/shared/suites/<site>.json`을 자동으로 한 번 가져와 로컬 suite와 병합한다. `팀 테스트 공유` 메뉴에서는 수동 push/pull도 가능하다.
- `--push-metrics` 실행도 같은 공유 경로에 suite를 같이 올린다. Grafana/Prometheus는 KPI 조회용이고, 테스트 정의 공유는 별도 JSON 저장 경로로 분리했다.
- suite 공유 시 `password`, `token`, `secret`, `api_key` 같은 민감 key는 업로드 전에 제거한다.
- GUI benchmark manager: `모니터링 서버로 메트릭 업로드 (--push-metrics)` 체크박스를 켠 실행만 업로드한다.
- 발표 caveat: "공유된다"는 것은 benchmark summary/results에서 뽑은 KPI metric이 Pushgateway/Grafana에 올라가고, suite JSON은 민감 key 제거 후 shared suites 경로에 저장된다는 뜻이다. raw artifact 전체나 테스트 계정 정보가 대시보드에 공유되는 구조는 아니다.

## 범용 엔진 위험 로직 정리

- 제거: filter-specific semantic validator 엔진 및 runtime wrapper. 특정 사이트의 결과 카드 구조, 학점/구분 옵션, 페이지네이션 패턴을 범용 성공 판정으로 쓰는 로직이라 코어 엔진에 두기 위험했다.
- 제거: SELECT 액션 직후 semantic validator를 실행해 `GoalResult` 성공/실패를 즉시 덮어쓰는 post-action 경로.
- 제거: terminal 실행 종료 후 같은 validator를 다시 실행해 성공 결과를 실패로 바꾸는 fallback 경로.
- 제거: exploratory select 액션마다 validator report를 붙이는 경로.
- 조정: filter policy는 semantic validator mandatory/optional 계약을 쓰지 않고, OpenClaw state-change evidence만 filter state-change 성공 신호로 본다.
- 조정: `result_consistency` expected signal은 범용 런타임에서 추론하지 않는다. 도메인별 row consistency는 별도 scenario-specific validator로 분리해야 한다.
- 조정: PRD 생성기와 `inuu_service_suite`의 학점 필터 케이스는 semantic goal type 대신 filter state-change 계약으로 낮췄다.

## 재실행 명령

```bash
python scripts/compare_benchmark_runs.py \
  --baseline artifacts/tmp/perf_hn_limit3 \
  --candidate artifacts/tmp/perf_hn_limit3_postjudge \
  --output-dir artifacts/tmp/presentation_compare_hn_postjudge_20260506 \
  --fail-on-regression
```

```bash
python scripts/compare_benchmark_runs.py \
  --baseline artifacts/tmp/perf_pypi_limit3 \
  --candidate artifacts/tmp/perf_pypi_limit3_searchjudge \
  --output-dir artifacts/tmp/presentation_compare_pypi_searchjudge_20260506 \
  --fail-on-regression
```

```bash
GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
GAIA_TEST_USERNAME=<test-account> GAIA_TEST_PASSWORD=<test-password> \
python scripts/run_goal_benchmark.py \
  --suite gaia/tests/scenarios/inuu_service_suite.json \
  --repeats 1 \
  --timeout-cap 180 \
  --session-prefix presentation-inuu \
  --output-dir artifacts/benchmarks/presentation_inuu_service_20260506
```

```bash
GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_goal_benchmark.py \
  --suite gaia/tests/scenarios/inuu_service_suite.json \
  --repeats 1 \
  --limit 3 \
  --timeout-cap 180 \
  --session-prefix presentation-inuu-state \
  --output-dir artifacts/benchmarks/presentation_inuu_service_state_limit3_20260506
```

공유 대시보드에 발표용 실행을 올릴 때:

```bash
python scripts/gaia_monitor_connect.py http://<server-ip>:9091 --token <team-token>
python scripts/run_goal_benchmark.py \
  --suite gaia/tests/scenarios/inuu_service_suite.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix presentation-inuu \
  --output-dir artifacts/benchmarks/presentation_inuu_service_shared_20260506 \
  --push-metrics
```

## 발표에 쓸 문장 후보

- "목표 달성 여부를 모델 주장 하나로 끝내지 않고, OpenClaw snapshot과 benchmark artifact로 검증 가능한 형태로 남겼습니다."
- "읽기/탐색형 목표에서는 post-action judge를 제한적으로 사용해, 성공률은 유지하면서 평균 실행 시간과 step 수를 줄였습니다."
- "성능 개선 주장은 단일 demo가 아니라 baseline/candidate 비교 gate를 통과한 artifact로 제시합니다."
- "팀 실행 결과는 로컬 파일에서 끝나지 않고, 명시적으로 업로드한 benchmark KPI만 Grafana에 모아 같이 볼 수 있게 했습니다."
- "발표 현장에서는 같은 지표 메뉴에서 팀 공유 Grafana와 로컬 HTML 리포트를 선택해 보여줄 수 있게 했습니다."

## Caveat

- 현재 HN/PyPI 비교는 각 suite 3 runs 기준이다. 발표에서 수치를 말할 때는 작은 공개 읽기 benchmark라는 전제를 같이 말한다.
- 실서비스 측정은 `repeats=1`이다. 재현성 수치가 필요하면 발표 전 `repeats=2` 이상의 KPI pack을 다시 실행한다.
- 실서비스 조합 생성/시간표 적용 케이스는 로딩 화면을 완료 증거로 인정한 의심이 있으므로 raw success rate만 단독으로 내세우지 않는다.
- `self_recovery_rate`는 이번 비교 artifact에서 `null`이다. recovery event가 없는 공개 읽기 dataset이라 측정 불가로 해석한다.
- 실제 서비스 benchmark나 계정 기반 시나리오는 비용/정책 영향이 있으므로 발표 직전 별도 요청 또는 명시적 실행 계획으로 갱신한다.
- Grafana 업로드는 opt-in이다. 발표에서 팀 공유 대시보드를 보여주려면 발표 직전 `--push-metrics`가 붙은 실행 또는 수동 `scripts/push_metrics.py --suite-dir <artifact>` 실행을 남긴다.

## 다음 업데이트 후보

- OpenClaw snapshot/tabs cache가 backend trace에 남기는 `snapshot_before_cache_hit`, `tabs_before_cache_hit` 예시 추가.
- 실서비스 benchmark pack 결과가 생기면 공개 읽기 benchmark와 분리해서 표 추가.
- 발표 demo 순서: 목표 입력 -> OpenClaw 실행 -> evidence snapshot -> benchmark summary 순으로 압축.
- `INUU_009`, `INUU_010`이 조합 결과/시간표 적용 완료 전 로딩 화면을 성공으로 보는 판정 문제를 보강한다.
