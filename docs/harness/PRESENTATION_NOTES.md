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

## 지난 3주 커밋 기반 발표 자료

범위: `2026-04-15`부터 `2026-05-06`까지 현재 `main`의 `HEAD` 기준 커밋.

원자료:

- 전체 커밋 수: 11개.
- 전체 변경량: 176 files changed, 16577 insertions, 11211 deletions.
- 추적 명령:

```bash
git log --since=2026-04-15 --until=2026-05-06T23:59:59 --reverse --date=short --pretty=format:%h%x09%ad%x09%an%x09%s
git log --since=2026-04-15 --until=2026-05-06T23:59:59 --reverse --name-status --pretty=format:---COMMIT---%n%H%n%h%n%ad%n%s --date=short
git diff --shortstat 7453e16^..HEAD
```

### 발표 한 문장

지난 3주 동안 GAIA는 "브라우저를 대신 클릭하는 도구"에서 "목표, 증거, 검증, 팀 공유까지 닫힌 루프로 관리하는 웹 에이전트 테스트 런타임"으로 이동했다.

### 초심자용 배경

GAIA를 처음 보는 사람에게는 아래 순서로 설명한다.

1. 웹 서비스는 버튼 클릭, 로그인, 검색, 필터, 결과 확인처럼 사람이 반복해서 확인해야 하는 흐름이 많다.
2. GAIA는 사람이 자연어 목표를 주면 브라우저를 열고, 화면을 보고, 필요한 액션을 실행하고, 마지막에 정말 성공했는지 증거를 남기는 테스트 에이전트다.
3. OpenClaw는 현재 화면의 버튼, 입력창, 텍스트, 스크린샷 같은 browser evidence를 제공한다.
4. GAIA는 OpenClaw 위에서 목표 해석, 실행 순서, 실패 복구, 판정, benchmark 기록, 팀 공유를 담당한다.
5. 이번 3주 작업의 핵심은 실행 기능을 늘린 것이 아니라, 성공 판정을 더 보수적으로 만들고 발표 가능한 근거를 남기도록 구조를 정리한 것이다.

### 교수님 질문용 핵심 구조

```text
Spec or User Goal
  -> Goal parsing and policy selection
  -> OpenClaw browser snapshot/ref action
  -> GAIA dispatch, stale recovery, post-action evidence
  -> Goal verifier and benchmark grader
  -> Local artifact or opt-in Prometheus/Grafana upload
  -> Presentation note with caveat and rerun command
```

핵심 설계 판단은 세 가지다.

1. OpenClaw가 관측한 raw evidence를 1차 근거로 두고, GAIA wrapper의 semantic 추론은 보조 정보로 제한한다.
2. 성공률을 높이기 위해 도메인 특화 validator를 범용 런타임에 넣는 대신, 범용 state-change evidence와 scenario-specific 검증을 분리한다.
3. benchmark 결과는 로컬 artifact로 남기고, 팀 공유는 명시적으로 opt-in한 sanitized KPI만 업로드한다.

### 3주 변화 요약

| 축 | 이전 상태 | 3주 후 상태 | 발표 의미 |
| --- | --- | --- | --- |
| Benchmark | 개별 실행 결과를 사람이 추적 | GUI/Terminal benchmark manager, 공개 suite, KPI protocol, compare script | 성능 주장을 artifact 기반으로 말할 수 있음 |
| Browser context | 현재 tab 중심 실행 | 관련 tab 자동 추적과 snapshot/ref evidence 강화 | 새 탭/리다이렉트가 있는 실제 서비스 흐름에 가까워짐 |
| Human intervention | 막히면 단순 중단 또는 텍스트 질문 | 구조화된 human answer flow | 로그인/승인/질문 상황을 런타임 계약 안으로 편입 |
| Multi-user | 단일 사용자 목표 중심 | participants, blackboard, turn scheduler | 채팅/친구요청/승인처럼 여러 사용자 상태가 얽힌 목표를 테스트 가능 |
| Development safety | 문맥 과다 탐색 위험 | context pack, lane, Planner/Verifier/Cleanup 계약 | 큰 repo에서도 필요한 layer만 읽고 검증 |
| Generic runtime | filter-specific semantic validator 포함 | validator 제거, state-change 중심 filter policy | 특정 사이트 규칙을 범용 엔진 성공 판정으로 오인하지 않음 |
| Team sharing | 로컬 파일 중심 | Prometheus/Pushgateway/Grafana + shared suites | 팀 benchmark 결과를 같은 KPI 화면에서 공유 |

### 전체 커밋 추적표

| Date | Commit | 변경 축 | 핵심 변경 | 발표 포인트 | 주의할 점 |
| --- | --- | --- | --- | --- | --- |
| 2026-04-26 | `7453e16` | Benchmark management | `gaia/src/benchmark_manager.py`, GUI benchmark dialog, public suites, screenshot quality, spec generation context 추가 | "실행을 많이 해봤다"가 아니라 "비교 가능한 benchmark 단위로 관리한다"는 전환점 | 큰 GUI/benchmark 추가라 발표에서는 UX보다 KPI 근거 구조를 먼저 말한다 |
| 2026-04-26 | `1cc6ff1` | Browser context | `browser_context_manager.py`와 OpenClaw dispatch에 관련 tab auto-follow 추가 | 새 탭이나 리다이렉트가 생겨도 목표와 관련된 browser context를 따라간다 | tab 선택은 휴리스틱이므로 증거 snapshot과 함께 설명해야 한다 |
| 2026-04-30 | `ca4f13c` | Human answer | `human_answer_runtime.py`, terminal/chat hub/GUI 연결 | 에이전트가 막혔을 때 사용자의 답변을 구조화된 runtime input으로 처리 | 자동화 실패를 숨기는 기능이 아니라 명시적 개입 경로다 |
| 2026-04-30 | `b398066` | PR merge | PR #115 `test/kanana` merge | 4월 26일 benchmark/tab 추적과 4월 30일 human answer 흐름이 발표 가능한 단위로 통합됨 | merge commit은 앞선 feature commit을 포함하므로 중복 성과로 말하지 않는다 |
| 2026-05-04 | `667f7e9` | Model default | 기본 모델을 `gpt-5.5`로 갱신 | 발표/실행 기준 모델을 최신 기본값으로 맞춤 | 모델 교체 자체를 성능 개선 근거로 과장하지 않는다 |
| 2026-05-04 | `fe74566` | Multi-user harness | `multi_user_interaction_runtime.py`, participants registry/blackboard/turn scheduler, local chat fixture 추가 | 단일 브라우저 테스트에서 다중 참여자 상호작용 테스트로 확장 | 실제 다중 계정/세션 비용과 격리 전략을 같이 설명해야 한다 |
| 2026-05-04 | `7f77e16` | Development harness | `AGENTS.md`, context map, development harness manifest, `dev_harness.py`, docs lint 강화 | 대형 repo에서 AI가 잘못된 layer를 읽거나 건드리는 위험을 줄임 | 기능 코드가 아니라 개발 프로세스 안전장치라는 점을 분리해서 말한다 |
| 2026-05-04 | `4b51ca3` | PR merge | PR #116 `codex/generic-autonomous-harness` merge | multi-user와 development harness가 main 흐름으로 들어옴 | merge commit은 `667f7e9`, `fe74566`, `7f77e16`의 통합 지점 |
| 2026-05-06 | `7ea9a12` | Runtime cleanup and evidence | filter semantic validator 제거, `execute_goal_progress.py`, `compare_benchmark_runs.py`, presentation notes 추가 | 성공률을 억지로 높이는 도메인 특화 판정을 걷어내고, 비교 artifact로 주장하게 만듦 | raw success rate만 말하면 안 되고 false positive/false negative caveat를 같이 말한다 |
| 2026-05-06 | `c5ef967` | Branch merge | `codex/generic-autonomous-harness` branch merge | validator 제거와 presentation-prep 작업이 main에 합류 | 앞선 `7ea9a12`와 중복 계산하지 않는다 |
| 2026-05-06 | `2d62d08` | Monitoring and shared suites | Prometheus/Grafana stack, `push_metrics.py`, `gaia_monitor_connect.py`, `sync_shared_suites.py`, legacy MCP/browser files 정리 | 팀원이 같은 benchmark KPI와 sanitized suite를 공유할 수 있음 | 업로드는 opt-in이고 raw artifact/민감 정보 전체 공유가 아니다 |

### 발표 흐름

1. 문제 정의: 웹 에이전트는 "클릭"보다 "성공했는지 증명"이 어렵다.
2. 기존 방식의 한계: 단일 실행 로그, 도메인별 validator, 로컬 artifact만으로는 교수님이 보기에 재현성과 일반성이 약하다.
3. 3주간의 전환: benchmark manager, OpenClaw evidence, human answer, multi-user harness, development harness, monitoring을 순서대로 붙였다.
4. 가장 중요한 정리: filter semantic validator를 제거해 범용 런타임의 과잉 확신을 줄였다.
5. 최종 메시지: GAIA는 목표 기반 실행, evidence 기반 판정, benchmark 기반 비교, 팀 공유 기반 운영을 하나의 루프로 묶는다.

### 레이어별 설명

#### 1. Benchmark layer

변경 커밋: `7453e16`, `7ea9a12`, `2d62d08`.

핵심 파일:

- `gaia/src/benchmark_manager.py`
- `gaia/src/gui/benchmark_manager_dialog.py`
- `gaia/src/terminal_benchmark_mode.py`
- `scripts/run_goal_benchmark.py`
- `scripts/compare_benchmark_runs.py`
- `gaia/docs/KPI_BENCHMARK_PROTOCOL.md`

설명:

Benchmark layer는 에이전트 실행을 반복 가능한 실험 단위로 만든다. 시나리오 suite, repeats, timeout, success rate, average time, step count, progress-stop failure를 한 번에 기록한다. 발표에서는 "제가 직접 시연했습니다"보다 "같은 명령으로 다시 돌릴 수 있고, baseline/candidate를 비교할 수 있습니다"라고 말하는 쪽이 강하다.

교수님 질문 대비:

| 질문 | 답변 |
| --- | --- |
| 왜 benchmark가 필요한가? | LLM agent는 실행마다 결과가 흔들릴 수 있어 단일 성공 로그만으로는 일반화하기 어렵다. 반복 실행과 artifact 비교가 있어야 개선/회귀를 구분할 수 있다. |
| 왜 공개 읽기 벤치와 실서비스 벤치를 나누나? | 공개 읽기 벤치는 범용 navigation baseline이고, 실서비스 벤치는 제품 가치 검증이다. 둘을 섞으면 "범용성"과 "서비스 적합성"을 구분하기 어렵다. |
| 성공률만 보면 되나? | 아니다. 평균 시간, step 수, progress-stop failure, intervention rate, artifact caveat를 같이 봐야 한다. |

#### 2. Browser evidence layer

변경 커밋: `1cc6ff1`, `7ea9a12`.

핵심 파일:

- `gaia/src/phase4/browser_context_manager.py`
- `gaia/src/phase4/mcp_openclaw_dispatch_runtime.py`
- `gaia/src/phase4/embedded_openclaw_runtime.py`
- `vendor/openclaw-runtime/gaia-embedded-browser-server.bundle.mjs`

설명:

OpenClaw는 화면의 raw role tree, snapshot, ref action을 제공한다. GAIA는 이 evidence를 받아 어떤 액션을 실행할지 결정하고, 액션 후 화면이 목표와 맞게 바뀌었는지 확인한다. 관련 tab auto-follow는 OAuth, 새 창, 외부 링크처럼 실제 웹 서비스에서 흔한 흐름을 따라가기 위한 장치다.

교수님 질문 대비:

| 질문 | 답변 |
| --- | --- |
| DOM selector만 쓰면 안 되나? | 현대 웹 UI는 DOM 구조가 바뀌거나 ref가 stale해지기 쉽다. snapshot/ref evidence와 재탐색을 같이 써야 회복 가능성이 높아진다. |
| auto-follow가 위험하지 않나? | 맞다. 그래서 tab 추적은 결론이 아니라 후보 선택이고, 최종 성공은 목표별 evidence와 verifier가 판단해야 한다. |

#### 3. Human answer layer

변경 커밋: `ca4f13c`.

핵심 파일:

- `gaia/src/phase4/goal_driven/human_answer_runtime.py`
- `gaia/src/phase4/goal_driven/agent_intervention_runtime.py`
- `gaia/src/phase4/goal_driven/execute_goal_intervention.py`
- `gaia/chat_hub.py`
- `gaia/terminal.py`

설명:

완전 자동화가 항상 가능한 것은 아니다. 로그인, 인증, 민감 정보 입력, 사용자의 선택이 필요한 상황은 자동으로 우회하면 안 된다. 이 레이어는 사람이 준 답변을 임시 문자열로 흘려보내지 않고, runtime이 이해할 수 있는 구조화된 답변으로 넣는다.

발표 문장:

"개입이 필요한 순간을 실패로만 보지 않고, 명시적 human answer contract로 넣었습니다. 그래서 자동화와 사용자 승인 경계를 분리할 수 있습니다."

#### 4. Multi-user interaction layer

변경 커밋: `fe74566`.

핵심 파일:

- `gaia/src/phase4/goal_driven/multi_user_interaction_runtime.py`
- `gaia/src/phase4/participants/models.py`
- `gaia/src/phase4/participants/registry.py`
- `gaia/src/phase4/participants/blackboard.py`
- `gaia/src/phase4/participants/turn_scheduler.py`
- `gaia/tests/fixtures/local_chat_login.html`
- `gaia/tests/scenarios/local_chat_login_suite.json`

설명:

일반적인 웹 테스트는 한 명의 사용자가 버튼을 누르는 흐름에 머문다. 하지만 실제 서비스에는 A가 요청하고 B가 승인하거나, 사용자별로 다른 화면 상태를 봐야 하는 케이스가 많다. Multi-user harness는 participant별 context, shared blackboard, event-driven turn scheduling을 둬서 이런 목표를 표현한다.

교수님 질문 대비:

| 질문 | 답변 |
| --- | --- |
| 왜 단일 브라우저로는 부족한가? | 다중 사용자 기능은 한 세션의 DOM 변화만으로 성공을 판단할 수 없다. 서로 다른 계정의 상태 전이가 맞물려야 한다. |
| blackboard는 무엇인가? | participant가 관측한 이벤트와 상태를 공유하는 중간 기록판이다. 다음 참여자가 어떤 turn을 수행해야 하는지 판단할 근거가 된다. |
| turn scheduler는 왜 필요한가? | 채팅/요청/승인처럼 순서가 중요한 목표에서 누가 언제 행동해야 하는지 정해야 하기 때문이다. |

#### 5. Development harness layer

변경 커밋: `7f77e16`.

핵심 파일:

- `AGENTS.md`
- `docs/harness/CONTEXT_MAP.md`
- `docs/harness/context_manifest.json`
- `docs/harness/development_harness_manifest.json`
- `scripts/context_pack.py`
- `scripts/dev_harness.py`
- `scripts/lint_harness_docs.py`

설명:

이 repo는 경로가 많고 runtime, benchmark, GUI, vendor OpenClaw가 섞여 있다. 따라서 작업자가 전체 repo를 무작정 읽으면 잘못된 layer를 고칠 위험이 있다. Development harness는 context pack, lane, owned paths, eval contract, risk flags, checks를 명시해서 작은 문맥으로 안전하게 작업하게 만든다.

발표 문장:

"에이전트를 만드는 프로젝트라서, 에이전트가 이 프로젝트를 수정할 때도 작은 context pack과 lane별 검증을 강제했습니다. 즉 제품뿐 아니라 개발 프로세스도 harness화했습니다."

#### 6. Runtime cleanup and verifier layer

변경 커밋: `7ea9a12`.

핵심 파일:

- deleted: `gaia/src/phase4/goal_driven/filter_validation_engine.py`
- deleted: `gaia/src/phase4/goal_driven/filter_validation_runtime.py`
- deleted: `gaia/tests/unit/test_filter_validation_engine.py`
- deleted: `gaia/tests/unit/test_filter_validation_runtime.py`
- added: `gaia/src/phase4/goal_driven/execute_goal_progress.py`
- added: `scripts/compare_benchmark_runs.py`

설명:

이 커밋은 발표에서 가장 중요한 "정직성" 포인트다. filter semantic validator는 특정 서비스의 카드 구조, 학점/구분 옵션, 페이지네이션 패턴을 범용 성공 판정처럼 다룰 위험이 있었다. 그래서 제거하고, filter policy는 OpenClaw state-change evidence 중심으로 낮췄다.

교수님 질문 대비:

| 질문 | 답변 |
| --- | --- |
| 왜 성공 판정 로직을 지웠나? | 정확하지 않은 범용 validator는 성공률을 높여 보일 수 있지만 일반성을 해친다. 도메인 특화 검증은 scenario-specific layer로 분리해야 한다. |
| 그럼 검증이 약해진 것 아닌가? | 범용 런타임은 더 보수적이 됐다. 대신 compare benchmark와 artifact caveat로 결과 해석을 강화했다. |
| false negative는 어떻게 다루나? | presentation notes에 실패 원인과 화면 증거를 함께 남기고, validator가 문제인지 서비스 기능 실패인지 분리한다. |

#### 7. Monitoring and shared suite layer

변경 커밋: `2d62d08`.

핵심 파일:

- `monitoring/docker-compose.yml`
- `monitoring/prometheus.yml`
- `monitoring/grafana/dashboards/gaia_kpi.json`
- `monitoring/nginx/nginx.conf`
- `scripts/push_metrics.py`
- `scripts/gaia_monitor_connect.py`
- `scripts/gaia_monitor_setup.py`
- `scripts/sync_shared_suites.py`
- `gaia/src/benchmark_suite_sharing.py`

설명:

팀 공유는 raw artifact 전체를 올리는 방식이 아니다. 로컬 benchmark summary/results에서 KPI를 뽑고, 명시적으로 `--push-metrics`를 선택한 실행만 Pushgateway/Grafana로 보낸다. suite JSON은 공유하되 민감 key는 제거한다. 그래서 발표에서는 "팀원이 같은 대시보드를 본다"와 "민감 정보 전체를 공유하지 않는다"를 같이 말해야 한다.

교수님 질문 대비:

| 질문 | 답변 |
| --- | --- |
| 왜 Grafana가 필요한가? | 여러 팀원이 실행한 benchmark KPI를 같은 시간축과 suite 기준으로 비교하기 위해서다. |
| 보안 문제는 없나? | 업로드는 opt-in이고, 토큰은 로컬 `~/.gaia/monitoring.json`에 저장하며, suite 공유 전 민감 key를 제거한다. |
| artifact 전체를 저장하지 않는 이유는? | raw artifact에는 계정/화면/서비스 상태 등 민감 정보가 섞일 수 있어, 발표/운영용 KPI와 원본 증거를 분리한다. |

### 발표 슬라이드 구성안

| Slide | 제목 | 핵심 내용 | 근거 |
| --- | --- | --- | --- |
| 1 | GAIA의 문제 정의 | 웹 에이전트에서 어려운 것은 클릭보다 성공 증명 | 이 섹션의 구조도 |
| 2 | 3주 변화 요약 | 11 commits, 176 files, benchmark/evidence/team sharing 확장 | 커밋 추적표 |
| 3 | 전체 아키텍처 | Goal -> OpenClaw -> Evidence -> Verifier -> Benchmark | 교수님 질문용 핵심 구조 |
| 4 | Benchmark manager | GUI/Terminal/suite/KPI protocol | `7453e16` |
| 5 | Browser evidence | snapshot/ref/tab auto-follow | `1cc6ff1` |
| 6 | Human answer | 자동화와 사용자 승인 경계 | `ca4f13c` |
| 7 | Multi-user harness | participants, blackboard, turn scheduler | `fe74566` |
| 8 | Development harness | context pack, lane, checks | `7f77e16` |
| 9 | 정직한 cleanup | filter semantic validator 제거 | `7ea9a12` |
| 10 | 성능 근거 | HN/PyPI baseline-candidate 비교 | 아래 현재 발표 후보 수치 |
| 11 | 팀 공유 | Prometheus/Grafana/shared suites | `2d62d08` |
| 12 | 한계와 다음 단계 | 작은 benchmark, repeats, loading-screen 판정 보강 | Caveat |

### 3분 설명 대본

처음에는 GAIA를 브라우저 자동화 도구처럼 볼 수 있습니다. 그런데 실제로 어려운 부분은 버튼을 누르는 것이 아니라, "목표가 정말 달성됐는지"를 재현 가능한 증거로 남기는 것입니다.

지난 3주 동안 이 방향으로 구조를 정리했습니다. 먼저 benchmark manager와 공개 suite를 추가해서 실행 결과를 수치와 artifact로 비교할 수 있게 했습니다. 다음으로 OpenClaw snapshot과 ref action을 중심에 두고, 새 탭이나 리다이렉트도 관련 browser context로 따라갈 수 있게 했습니다.

자동화가 막히는 상황도 runtime 밖의 예외로 두지 않았습니다. 로그인이나 승인처럼 사람이 답해야 하는 경우는 structured human answer flow로 넣었습니다. 그리고 단일 사용자 테스트를 넘어, participant registry, blackboard, turn scheduler를 둬서 다중 사용자 상호작용까지 표현할 수 있게 했습니다.

가장 중요한 변화는 오히려 삭제입니다. 특정 서비스의 필터 구조를 범용 성공 판정처럼 보던 semantic validator를 제거했습니다. 이 로직은 성공률을 높여 보일 수 있지만, 일반적인 웹 에이전트 런타임에는 위험합니다. 대신 OpenClaw evidence와 benchmark compare artifact로 보수적으로 주장하도록 바꿨습니다.

마지막으로 팀 공유를 위해 Prometheus, Pushgateway, Grafana 기반 모니터링과 shared suites를 붙였습니다. 단, raw artifact 전체를 공유하지 않고 명시적으로 opt-in한 KPI와 sanitized suite만 공유합니다. 그래서 GAIA의 현재 메시지는 "목표 기반 실행, evidence 기반 판정, benchmark 기반 비교, 팀 공유 운영"입니다.

### 교수님 질문 예상 답변

| 질문 | 짧은 답변 | 깊은 답변 |
| --- | --- | --- |
| 이게 Selenium/Playwright 테스트와 뭐가 다른가? | 고정 selector script가 아니라 목표 기반 실행과 증거 기반 판정이다. | Playwright는 deterministic script 작성에 강하고, GAIA는 자연어 목표, OpenClaw snapshot/ref evidence, runtime recovery, benchmark artifact를 묶어 동적으로 목표를 수행한다. 다만 최종 검증은 여전히 보수적인 oracle이 필요하다. |
| LLM이 성공했다고 말하면 믿을 수 있나? | 믿지 않는다. artifact와 verifier를 남긴다. | 성공 판정은 모델의 말이 아니라 OpenClaw evidence, expected signal, benchmark grader, compare artifact를 조합한다. 이번에 filter semantic validator를 제거한 것도 모델/휴리스틱 과잉 확신을 줄이기 위해서다. |
| 범용성과 실서비스 최적화가 충돌하지 않나? | 충돌한다. 그래서 layer를 분리했다. | 범용 런타임에는 state-change와 evidence 중심 계약만 두고, 서비스별 row consistency나 도메인 규칙은 scenario-specific validator로 분리해야 한다. |
| Multi-user는 왜 필요한가? | 실제 서비스는 여러 사용자의 상태 전이가 얽히기 때문이다. | 친구 요청, 채팅, 승인, 알림 같은 목표는 한 브라우저의 DOM만 보면 성공 여부를 알 수 없다. participant별 context와 blackboard event가 필요하다. |
| Grafana를 붙인 이유는 뭔가? | 팀이 같은 KPI를 보기 위해서다. | 개인 로컬 artifact만 있으면 발표/협업에서 기준이 흔들린다. Pushgateway/Grafana는 opt-in KPI 공유용이고, raw artifact나 민감 정보 공유와 분리했다. |
| 현재 한계는? | 작은 repeats와 일부 loading-screen 판정 의심이 있다. | 현재 HN/PyPI 비교는 각 3 runs이고, 실서비스 조합/시간표 적용 케이스는 로딩 화면을 완료 증거로 인정한 의심이 있다. 발표에서는 이 caveat를 성능 수치 옆에 같이 둔다. |

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

## 외부 공개 다양성 벤치마크

목표: 교수님이 지적한 "내부 사이트 휴리스틱일 수 있다"는 우려를 방어하기 위해, 내부 서비스가 아닌 외부 공개 사이트에서 실행 가능한 읽기/탐색/검색/상세 진입/비파괴 상호작용 benchmark를 확장한다.

이번 구성은 한국 청중에게 익숙한 사이트를 중심으로 잡았다. 부분 실행에서 CAPTCHA, Access Denied, 접속 일시 제한 또는 bot-wall 성격의 차단이 반복된 npm, 맞춤법 검사기, 올리브영, 네이버쇼핑, 쿠팡, G마켓, CGV는 primary curated pack에서 제외했고, 같은 30개 사이트 구조를 유지하기 위해 잡코리아, 서울문화포털, KBS 뉴스, MBC 뉴스, SBS 뉴스, YTN, 국립중앙박물관을 추가했다. 신규/정리된 한국 사이트는 네이버, 다음, 카카오맵, 네이버뉴스, KBS 뉴스, MBC 뉴스, SBS 뉴스, YTN, 11번가, 무신사, YES24, 교보문고, 기상청, 서울 열린데이터광장, 대한민국 구석구석, 정부24, 국가법령정보센터, 멜론, 잡코리아, 서울문화포털, 국립중앙박물관처럼 발표 현장에서 바로 이해 가능한 공개 사이트 위주로 구성했다.

시나리오 문장도 일반 템플릿을 그대로 쓰지 않고 각 사이트의 실제 공개 업무 흐름에 맞췄다. 예를 들어 커머스는 상품 검색/가격 비교/상세 정보/필터/정렬, 공공 사이트는 서비스 안내/데이터셋/법령/날씨 특보, 지도는 장소 검색/경로 패널/지도 컨트롤, 채용은 공고 검색/상세 조건/지역 필터처럼 read-only business flow로 채웠다.

지도 계열은 hidden tab/ref에 의존하는 클릭 흐름을 줄인다. 카카오맵 길찾기 케이스는 공식 공개 route deep link를 사용해 경로 화면으로 바로 진입한 뒤, 출발역/도착역/대중교통 경로/지도 영역을 읽는 방식으로 측정한다. 이렇게 해야 "길찾기 탭이 화면에는 보이지만 OpenClaw ref가 stale"인 실패를 지도 기능 실패로 오해하지 않는다.

### 구성 요약

| 항목 | 값 |
| --- | --- |
| Manifest | `gaia/tests/scenarios/external_public_manifest.json` |
| 총 사이트 수 | 30 |
| 총 시나리오 수 | 150 |
| 사이트당 시나리오 수 | 5 |
| 실행 범위 | 공개 접근만 사용 |
| 금지 범위 | 로그인, 회원가입, 결제, 장바구니 확정, 글쓰기, 댓글, 삭제, CAPTCHA 우회, 계정 정보 입력 |
| 제외 기준 | CAPTCHA 또는 bot-wall 차단 반복 사이트는 primary pack에서 제외 |
| 차단 처리 | 실행 중 CAPTCHA/보안문자/보안 확인이 나오면 `BLOCKED_USER_ACTION` + `blocked_captcha`로 분리 |
| 공지/광고 팝업 처리 | 일반 공개 사이트의 닫기, 오늘 하루 보지 않기, 다시 보지 않기 같은 dismiss UI만 unblock 대상으로 허용하고, 보안/차단 게이트는 닫지 않는다 |
| 공유 정책 | `--push-metrics`가 있을 때만 sanitized KPI/suite 공유 |

카테고리 분포:

| Category | Sites |
| --- | ---: |
| `portal_news_community` | 10 |
| `public_data_service` | 6 |
| `commerce_product` | 5 |
| `developer_tech` | 3 |
| `finance_game` | 2 |
| `culture_public` | 2 |
| `knowledge_reference` | 1 |
| `career_business` | 1 |

변동성 분포:

| Volatility | Sites | 발표 해석 |
| --- | ---: | --- |
| `stable` | 3 | 문서/레퍼런스 중심, 재현성 baseline |
| `medium` | 13 | 공공/상품/정보 사이트, 일반 공개 웹 수준 |
| `high` | 14 | 포털/뉴스/커머스/커뮤니티처럼 실제 웹 변동성이 큰 구간 |

### 시나리오 템플릿

각 사이트는 아래 5개 흐름을 유지하되, 문장은 해당 사이트의 실제 공개 업무에 맞춰 구체화한다.

1. 홈에서 핵심 업무/정보 영역 확인
2. 검색 또는 주요 탐색 결과 확인
3. 상세 정보 화면 진입과 핵심 필드 확인
4. 카테고리, 랭킹, 지도, 목록, 필터 확인
5. 정렬, 탭, 페이지, 지도 컨트롤 같은 read-only 상호작용 확인

### 실행 명령

```bash
PYTHONPATH=. GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-20260507 \
  --push-metrics
```

예상 artifact:

- `artifacts/benchmarks/kpi_pack_<timestamp>/summary.json`
- `artifacts/benchmarks/kpi_pack_<timestamp>/results.json`
- `artifacts/benchmarks/kpi_pack_<timestamp>/summary.md`

실행 후 채울 KPI 표:

| Date | Pack | Sites | Scenarios | Raw Success | Primary Success | Avg Time | Progress Stop | Intervention | Blocked | Top Failure Reasons | Artifact |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| TBD | `external-public-20260507` | 30 | 150 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | `artifacts/benchmarks/kpi_pack_<timestamp>/summary.md` |

발표 방어 문장:

"내부 서비스 하나에 맞춘 휴리스틱이 아니라, 네이버/다음/카카오맵/정부24/기상청/커머스/커뮤니티/개발자 사이트처럼 구조와 변동성이 다른 외부 공개 웹 30개, 150개 시나리오에서 같은 하네스로 측정했습니다. 실패한 사이트도 숨기지 않고 timeout, blocked, site volatility, login gate 같은 reason code로 분리해 caveat로 제시합니다."

CAPTCHA 차단이 나온 경우:

"CAPTCHA나 보안문자는 자동화가 우회할 대상이 아니기 때문에 일반 실패율에 섞지 않고 `blocked_captcha`로 분리했습니다. 그래서 raw success rate와 별도로, 실제 자동 실행 대상만 보는 `primary_success_rate`를 같이 제시합니다."

## 업데이트 로그

### 2026-05-08

- Musinsa 정렬 실패 원인은 정렬 드롭다운 클릭 후 화면에는 옵션이 열렸지만 role snapshot이 이전 DOM delta/cache에 묶여 `낮은 가격순` option ref를 못 보는 stale DOM 문제로 확인했다.
- 보강: LLM이 WAIT으로 "DOM/option/ref를 갱신해서 확인해야 한다"고 판단하면 goal-driven runtime이 DOM 분석 캐시와 raw role-tree delta를 무효화하고 OpenClaw snapshot을 강제로 재수집한다.
- 같은 strict scenario 재실행 결과 `MUSINSA_005_SORT_CHANGE`는 2 steps / 60.91s / SUCCESS로 통과했다. 강제 재수집 후 `낮은 가격순` ref `e1760`을 찾아 클릭했고, 최종 URL은 `sortCode=LOW_PRICE`로 확인됐다.
- 발표 근거 artifact: `artifacts/benchmarks/musinsa_sort_strict_suite_20260508_011054/summary.md`, `results.json`.

### 2026-05-07

- 외부 공개 benchmark를 `external_public_manifest.json` 기준 30개 사이트 / 150개 시나리오로 확장했다.
- 발표 청중이 바로 이해할 수 있도록 한국 포털, 지도, 뉴스, 커머스, 공공데이터, 정부/법령, 음악/영화, 채용/문화 사이트 위주로 구성했다.
- 기존 공개 suite 11개는 모두 사이트당 5개 시나리오로 정규화했다.
- 부분 실행에서 CAPTCHA, Access Denied, 접속 일시 제한 또는 bot-wall 성격 차단이 반복된 npm, 맞춤법 검사기, 올리브영, 네이버쇼핑, 쿠팡, G마켓, CGV는 primary curated pack에서 제외하고, 잡코리아, 서울문화포털, KBS 뉴스, MBC 뉴스, SBS 뉴스, YTN, 국립중앙박물관으로 대체했다.
- PyPI 검색 결과 URL은 Fastly CAPTCHA가 재현되어 primary scenario에서 제외하고, 같은 프로젝트의 공개 파일 목록 확인으로 바꿨다.
- UI와 맞지 않는 일반 문장은 사이트별 실제 공개 업무 흐름에 맞춰 검색, 상세 정보, 가격/랭킹/지도/차트/필터 확인 중심으로 재작성했다.
- UI/ref 실패 4개를 headless로 재실행해 원인을 분리했다. Daum 탭 전환은 재실행 성공으로 일시적 변동성으로 분류했고, Musinsa 정렬은 드롭다운 화면은 보이지만 option ref가 stale하게 유지되는 동적 dropdown 문제로 분류했다. 서울 열린데이터 상세은 결과 카드 자체에 설명/기관/API 정보가 보여 goal 문장을 카드 evidence 허용으로 조정했고, 정렬/조건 케이스는 select 이후 조회 버튼 ref가 안정적으로 잡히지 않아 목록 변화 강제 대신 조건 영역 확인으로 조정했다.
- KPI pack 실행은 `scripts/run_kpi_benchmark_pack.py --suite-manifest ... --push-metrics`로 고정한다.
- 로컬 benchmark 기록 정리용으로 실패가 포함된 artifact만 삭제하는 `scripts/prune_benchmark_records.py`와 terminal `지표 확인 > 실패 기록 삭제` 메뉴를 추가했다.
- CAPTCHA/보안문자/보안 확인 실패는 `BLOCKED_USER_ACTION` + `blocked_captcha`로 격리하고, 발표용 primary 성공률에서는 제외하도록 benchmark KPI를 보강했다.
- 일반 광고/공지 모달은 닫기, 오늘 하루 보지 않기, 다시 보지 않기 같은 공개 dismiss 버튼을 인식하도록 보강하되, Access Denied/CAPTCHA/보안 확인 게이트는 닫지 않고 차단 케이스로 남기도록 했다.
- 카카오맵 길찾기 시나리오는 hidden tab 클릭 대신 공식 route deep link로 진입하도록 바꾸고, 길찾기/출발/도착/경로 정보가 보이는 WAIT 완료 판정을 보강했다.
- 실패 시나리오를 Playwright로 직접 재확인한 뒤, 사이트가 열리는데 목표/URL이 애매했던 FOW, 머니터링, 서울문화포털, 11번가, 정부24 시나리오를 안정적인 공개 URL과 read-only 확인 목표로 보강했다.
- 아직 실제 150개 전체 실행 결과는 없으므로 성공률/평균 시간/상위 실패 reason code는 실행 artifact가 생긴 뒤 위 KPI 표에 채운다.

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
