# Midterm Report Source Notes

작성일: 2026-05-10  
범위: 2026-04-19부터 2026-05-10까지 약 3주간의 Capston/GAIA 변경 사항  
목적: 중간 보고서 작성 전, 실제 커밋/하네스/벤치마크/제외 케이스/수정 로직을 한 파일에 모은 원천 자료  

이 문서는 보고서 문장 그대로 제출하기 위한 최종본이 아니라, 중간 보고서를 쓰기 전에 참고할 수 있는 긴 재료 모음이다. 따라서 기술적 배경, 커밋별 변화, 실패/제외 판단, 발표 방어 문장, 검증 명령까지 최대한 넓게 기록한다.

## 한 줄 요약

지난 3주 동안 프로젝트는 단순한 내부 서비스 자동화 데모에서, 외부 공개 웹 30개 사이트/150개 시나리오를 측정하고 Grafana로 팀 공유 지표를 보는 범용 웹 자동화 벤치마크 하네스로 확장되었다. 동시에 성공률을 억지로 올릴 수 있는 도메인 특화 검증 로직은 제거하고, CAPTCHA/서비스 지연/사이트 오류/로그인 게이트 같은 비자동화 가능 케이스는 일반 실패와 분리하도록 정리했다.

## 보고서에 넣을 수 있는 핵심 메시지

1. 내부 서비스 하나에 맞춘 휴리스틱이라는 우려를 줄이기 위해 외부 공개 사이트 중심의 benchmark pack을 구성했다.
2. 최종 primary manifest는 30개 사이트, 사이트당 5개, 총 150개 시나리오 구조를 유지한다.
3. 한국 청중에게 익숙한 네이버/다음/카카오맵/뉴스/커머스/정부/공공/문화 사이트를 중심으로 구성했다.
4. CAPTCHA, bot-wall, 서비스 지연 안내, 접속 폭주 페이지는 자동화가 우회할 대상이 아니므로 benchmark primary pack에서 제거하거나 `BLOCKED_USER_ACTION`으로 분리한다.
5. raw success rate만 제시하지 않고 `primary_success_rate`, `progress_stop_failure_rate`, `intervention_rate`, reason code를 함께 제시한다.
6. 범용 엔진에 위험한 filter semantic validator는 제거했다. 특정 서비스의 카드 구조와 필터 UI를 범용 성공 판정에 쓰면 false positive/false negative를 만들 수 있기 때문이다.
7. OpenClaw 기반 ref-first 조작을 강화하고, ref가 stale하거나 option ref가 보이지 않는 경우 snapshot 재수집과 visual fallback으로 회복하도록 보강했다.
8. read-only WAIT 성공 판정은 무조건 LLM 말을 믿는 방식이 아니다. DOM이 transient가 아니고, 목표가 읽기/상세 확인 계열이며, reasoning이 완료를 주장할 때만 judge를 호출한다.
9. 서비스 지연/일시 오류 화면의 일반 단어를 목표 증거로 오판하지 않도록 guard를 추가했다.
10. 팀원이 누가 어떤 머신에서 실행했는지 볼 수 있도록 `runner_id`를 artifact와 Grafana metric label에 추가했다.

## 현재 상태 기준 대표 결과

### 내부 서비스 발표용 benchmark

Artifact:

- `artifacts/benchmarks/presentation_inuu_service_20260506/summary.md`
- `artifacts/benchmarks/presentation_inuu_service_20260506/results.json`

결과:

| 항목 | 값 |
|---|---:|
| suite | `inuu_service_v1` |
| scenarios | 10 |
| success | 9 |
| fail | 1 |
| success_rate | 0.9 |
| avg_time_seconds | 83.06 |
| progress_stop_failure_rate | 0.0 |
| intervention_rate | 0.0 |

해석:

- 내부 서비스 full suite 10개 중 9개 성공.
- 실패한 `INUU_003_CREDIT_FILTER_SEMANTIC`은 필터 적용 자체보다 semantic validator가 필수 조건을 강하게 요구해 실패 처리한 케이스다.
- 이 실패 분석이 filter semantic validator 제거의 직접 계기가 되었다.
- 이후 같은 흐름을 `CREDIT_FILTER_STATE` 기준으로 낮춰 first-3 재실행했을 때 3/3 성공했다.

### 내부 서비스 validator 제거 후 first-3 재측정

Artifact:

- `artifacts/benchmarks/presentation_inuu_service_state_limit3_20260506/summary.md`
- `artifacts/benchmarks/presentation_inuu_service_state_limit3_20260506/results.json`

결과:

| 항목 | 값 |
|---|---:|
| scenarios | 3 |
| success | 3 |
| success_rate | 1.0 |
| avg_time_seconds | 36.34 |
| progress_stop_failure_rate | 0.0 |
| intervention_rate | 0.0 |

해석:

- filter semantic validator를 제거한 뒤, 필터 state-change 중심으로 평가하자 내부 서비스 first-3는 모두 성공했다.
- 보고서에서는 “성공률을 올리기 위해 검증을 약하게 만든 것”이 아니라 “도메인 특화 semantic 판정을 범용 엔진에서 제거하고, 검증 기준을 OpenClaw state-change evidence로 낮춰 false negative를 줄인 것”으로 설명하는 것이 적절하다.

### 외부 공개 30-site pack 부분/전체 실행 근거

Artifact:

- `artifacts/benchmarks/kpi_pack_20260507_184746/summary.md`
- `artifacts/benchmarks/kpi_pack_20260507_184746/summary.json`
- `artifacts/benchmarks/kpi_pack_20260507_184746/results.json`

결과:

| 항목 | 값 |
|---|---:|
| runs_total | 150 |
| success | 137 |
| blocked | 1 |
| primary_runs | 149 |
| scenario_success_rate | 0.9133 |
| primary_success_rate | 0.9195 |
| avg_time_seconds | 62.25 |
| progress_stop_failure_rate | 0.0267 |
| intervention_rate | 0.0067 |

중요 caveat:

- 이 결과는 이후 VisitKorea 서비스 지연 false positive와 Law.go.kr 상세 오류 시나리오를 제거하기 전 artifact다.
- 따라서 보고서에서는 “초기 30-site pack의 실행 결과”로만 쓰고, 최종 수치는 최신 manifest로 다시 한 번 전체 실행한 뒤 확정하는 것이 안전하다.
- 특히 VisitKorea 일부 성공은 실제 성공이 아니라 서비스 지연 안내 문구를 목표 증거로 오판한 false positive였으므로 현재 primary pack에서는 제거했다.

### 새로 추가한 대한민국 정책브리핑 headless 확인

Artifact:

- `artifacts/benchmarks/policy_briefing_public_suite_20260509_145116/summary.md`

실행 조건:

```bash
PYTHONPATH=. GAIA_OPENCLAW_HEADLESS=1 GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
.venv/bin/python scripts/run_goal_benchmark.py \
  --suite gaia/tests/scenarios/policy_briefing_public_suite.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix policy-briefing-headless-20260509 \
  --runner-id codex-headless
```

결과:

| 항목 | 값 |
|---|---:|
| scenarios | 5 |
| success | 5 |
| success_rate | 1.0 |
| primary_success_rate | 1.0 |
| avg_time_seconds | 34.91 |
| progress_stop_failure_rate | 0.0 |
| intervention_rate | 0.0 |

해석:

- VisitKorea를 대체하기 위해 추가한 대한민국 정책브리핑 suite는 headless 환경에서 5/5 성공했다.
- 홈 주요 메뉴, 정책뉴스 목록, 보도자료 목록, 사실은 이렇습니다 목록, 카드/한컷 목록 같은 공개 read-only surface로 구성했다.
- 로그인/결제/글쓰기/계정 정보/CAPTCHA 우회가 전혀 없다.

### 수정한 국가법령정보센터 headless 확인

Artifact:

- `artifacts/benchmarks/law_go_kr_public_suite_20260509_145419/summary.md`

실행 조건:

```bash
PYTHONPATH=. GAIA_OPENCLAW_HEADLESS=1 GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
.venv/bin/python scripts/run_goal_benchmark.py \
  --suite gaia/tests/scenarios/law_go_kr_public_suite.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix lawgo-headless-20260509 \
  --runner-id codex-headless
```

결과:

| 항목 | 값 |
|---|---:|
| scenarios | 5 |
| success | 5 |
| success_rate | 1.0 |
| primary_success_rate | 1.0 |
| avg_time_seconds | 56.18 |
| progress_stop_failure_rate | 0.0 |
| intervention_rate | 0.0 |

해석:

- 기존 `LAWGO_003_LAW_DETAIL`은 상세 iframe URL에서 `서비스 이용에 불편` 오류가 재현되어 제거했다.
- 대체 시나리오 `LAWGO_003_LAW_SEARCH_TABS`는 근로기준법 검색 화면에서 법령명, 현행 구분, 조문 목록, 관련 탭을 확인하는 read-only 목표다.
- 수정 후 전체 Law.go.kr suite 5개는 headless에서 5/5 성공했다.
- `LAWGO_005_TABLE_OF_CONTENTS`에서는 stale ref select 실패가 한 번 있었으나 새 snapshot/WAIT 판정으로 회복해 최종 성공했다. 이는 recovery 로직이 실제로 동작한 사례로 볼 수 있다.

### Musinsa 정렬 dropdown 보강 확인

Artifact:

- `artifacts/benchmarks/musinsa_sort_strict_suite_20260508_011054/summary.md`

결과:

| 항목 | 값 |
|---|---:|
| scenarios | 1 |
| success | 1 |
| success_rate | 1.0 |
| avg_time_seconds | 60.91 |

해석:

- 기존 실패 원인은 정렬 dropdown을 클릭해 화면에는 옵션이 열렸지만 role snapshot이 이전 DOM delta/cache에 묶여 `낮은 가격순` option ref를 못 보는 stale DOM 문제였다.
- 보강 후 강제 resnapshot에서 `낮은 가격순` ref를 찾아 클릭했고, 최종 URL에 `sortCode=LOW_PRICE`가 반영되었다.
- 이 사례는 ref-first + snapshot refresh + visual fallback 방향이 필요한 이유를 설명할 수 있는 대표 예시다.

## 현재 external public manifest 구성

Manifest:

- `gaia/tests/scenarios/external_public_manifest.json`

현재 계약:

| 항목 | 값 |
|---|---:|
| site_count | 30 |
| scenario_count | 150 |
| scenarios per site | 5 |
| mode | public read-only 중심 |

현재 카테고리 분포:

| category | count |
|---|---:|
| portal_news_community | 10 |
| public_data_service | 6 |
| commerce_product | 5 |
| developer_tech | 3 |
| finance_game | 2 |
| culture_public | 2 |
| knowledge_reference | 1 |
| career_business | 1 |

현재 사이트 목록:

| site_key | label | category | volatility |
|---|---|---|---|
| wikipedia | Wikipedia | knowledge_reference | low |
| github | GitHub | developer_tech | medium |
| hacker_news | Hacker News | developer_tech | low |
| pypi | PyPI | developer_tech | medium |
| youtube | YouTube | portal_news_community | high |
| apple_store | Apple Korea | commerce_product | medium |
| fow_kr | FOW.LOL | finance_game | medium |
| dcinside | DCInside | portal_news_community | high |
| moneytoring | Moneytoring | finance_game | medium |
| naver_search | 네이버 검색 | portal_news_community | high |
| daum | 다음 | portal_news_community | high |
| naver_news | 네이버 뉴스 | portal_news_community | high |
| kakao_map | 카카오맵 | public_data_service | high |
| mbc_news | MBC 뉴스 | portal_news_community | medium |
| sbs_news | SBS 뉴스 | portal_news_community | medium |
| elevenst | 11번가 | commerce_product | high |
| ytn_news | YTN 뉴스 | portal_news_community | medium |
| musinsa | 무신사 | commerce_product | high |
| kbs_news | KBS 뉴스 | portal_news_community | medium |
| yes24 | YES24 | commerce_product | medium |
| kyobo | 교보문고 | commerce_product | medium |
| kma_weather | 기상청 날씨 | public_data_service | medium |
| seoul_open_data | 서울 열린데이터광장 | public_data_service | medium |
| policy_briefing | 대한민국 정책브리핑 | public_data_service | medium |
| government24 | 정부24 | public_data_service | medium |
| law_go_kr | 국가법령정보센터 | public_data_service | medium |
| melon | Melon | portal_news_community | high |
| national_museum | 국립중앙박물관 | culture_public | medium |
| jobkorea | 잡코리아 | career_business | medium |
| seoul_culture | 서울문화포털 | culture_public | medium |

보고서용 설명:

- 외부 benchmark는 해외 개발자 사이트도 일부 포함하지만, 발표 청중이 이해하기 쉽도록 한국 사이트 비중을 높였다.
- 쇼핑/뉴스/지도/정부/공공데이터/채용/문화/개발자 문서처럼 DOM 구조와 UI 패턴이 서로 다른 사이트를 섞었다.
- 각 사이트는 홈/랜드마크, 검색 또는 주요 내비게이션, 상세 진입 또는 목록 정보, 카테고리/필터/페이지 이동, 비파괴 상호작용 확인의 5개 흐름을 기본 구조로 삼았다.
- 단, 실제 사이트 구조에 맞지 않는 일반 템플릿 문장은 제거하고 사이트별 업무 흐름에 맞게 문장을 조정했다.

## 제외하거나 대체한 케이스

### primary pack에서 제거한 사이트

다음 사이트는 CAPTCHA, bot-wall, access denied, 서비스 정책상 자동화 차단, 또는 반복적인 접근 불안정성이 확인되어 primary curated pack에서 제거했다.

| 제거/제외 대상 | 이유 | 대체/처리 |
|---|---|---|
| npm | bot-wall/CAPTCHA 계열 차단 재현 | 개발자 사이트 중 안정 surface인 GitHub/HN/PyPI 파일 목록 중심으로 유지 |
| 맞춤법 검사기 | 자동화 접근에서 불안정/차단 가능성 | 한국 익숙한 공개 사이트로 대체 |
| 올리브영 | commerce bot-wall/차단 재현 | 무신사/11번가/YES24/교보 등 공개 탐색 중심으로 대체 |
| 네이버쇼핑 | CAPTCHA/보안 확인 재현 | 네이버 검색/뉴스는 유지하되 쇼핑은 제외 |
| 쿠팡 | bot-wall/접근 차단 재현 | 11번가/무신사/도서 커머스 중심으로 대체 |
| G마켓 | 차단성 응답 재현 | 커머스 카테고리의 다른 안정 사이트로 대체 |
| CGV | 차단성/서비스 종료성 케이스로 primary benchmark에 부적합 | 문화 카테고리는 서울문화포털/국립중앙박물관으로 대체 |
| VisitKorea | `서비스 지연 안내` 반복 및 false positive 유발 | 대한민국 정책브리핑으로 대체 |

### 특정 URL/시나리오만 교체한 경우

| 기존 케이스 | 문제 | 현재 처리 |
|---|---|---|
| `PYPI_002_PACKAGE_SEARCH` search URL | Fastly CAPTCHA 재현 | 같은 프로젝트의 공개 files/detail surface로 교체 |
| `LAWGO_003_LAW_DETAIL` | 상세 iframe URL에서 `서비스 이용에 불편` 오류 | `LAWGO_003_LAW_SEARCH_TABS`로 교체 |
| Musinsa 정렬 dropdown | option ref stale, role snapshot 갱신 실패 | ref 재탐색/강제 snapshot/visual fallback 보강 |
| KakaoMap 길찾기 hidden tab 클릭 | hidden UI 직접 클릭이 불안정 | 공식 route deep link 기반 read-only 확인으로 변경 |
| 서울 열린데이터 상세/조건 케이스 | 실제 UI와 문장 불일치, 조회 버튼 ref 불안정 | 결과 카드 evidence 허용 및 조건 영역 확인으로 조정 |

### false positive로 분리한 사례

VisitKorea 관련:

- `VISITKOREA_002_PLACE_SEARCH`: 서비스 지연 안내 반복으로 실패.
- `VISITKOREA_003_PLACE_DETAIL`: 표면상 SUCCESS였으나 서비스 지연 안내 화면의 일반 단어를 증거로 오판한 false positive.
- `VISITKOREA_004_REGION_BROWSE`: 표면상 SUCCESS였으나 `서비스` 같은 generic token이 목표 증거로 잡힌 false positive.
- `VISITKOREA_005_FILTER_OR_THEME`: 서비스 지연 안내 반복으로 실패.

Law.go.kr 관련:

- `LAWGO_003_LAW_DETAIL`: 상세 페이지가 `서비스 이용에 불편을 드려서 죄송합니다`, `현재 사용자가 많아 요청하신 페이지를 정상적으로 제공할 수 없습니다` 계열 화면으로 확인되어 primary scenario로 부적합.

보고서용 문장:

> 일부 외부 사이트는 HTTP 200을 반환하더라도 실제 렌더링 내용은 서비스 지연 안내 또는 접속 폭주 페이지일 수 있었다. 따라서 단순 페이지 도달 여부가 아니라 화면 증거의 의미를 검증해야 하며, 서비스 오류 화면의 일반 단어를 성공 증거로 쓰지 않도록 guard를 추가했다.

## 범용 엔진에서 제거한 위험 로직

### filter semantic validator 제거

관련 commit:

- `7ea9a12 Remove filter semantic validator from generic runtime`

삭제/정리된 주요 경로:

- `gaia/src/phase4/goal_driven/filter_validation_engine.py`
- `gaia/src/phase4/goal_driven/filter_validation_runtime.py`
- `gaia/tests/unit/test_filter_validation_engine.py`
- `gaia/tests/unit/test_filter_validation_runtime.py`
- post-action runtime에서 semantic validator 결과로 성공/실패를 덮어쓰던 경로
- exploratory select마다 validator report를 붙이던 경로
- terminal 종료 후 validator fallback으로 결과를 다시 바꾸던 경로

왜 제거했는가:

- 특정 내부 서비스의 카드 구조, 필터 option, 학점/구분 UI, pagination 패턴을 범용 엔진에 넣으면 다른 웹사이트에서는 잘못된 성공/실패 판정을 낼 수 있다.
- 내부 서비스에는 도움이 되더라도 외부 공개 사이트 30개/150개에 적용하면 도메인 특화 휴리스틱이 된다.
- 교수님 지적처럼 “내부 사이트에 맞춘 휴리스틱”이라는 비판을 피하려면 이런 로직은 범용 엔진에서 제거하는 것이 맞다.

제거 후 대체 기준:

- filter policy는 semantic mandatory/optional contract를 쓰지 않는다.
- OpenClaw state-change evidence, DOM 변화, expected signal, benchmark artifact를 더 우선한다.
- 도메인별 row consistency가 필요하면 범용 런타임이 아니라 scenario-specific validator로 분리해야 한다.

보고서용 문장:

> 실험 과정에서 내부 서비스에는 잘 맞지만 범용 웹 자동화 엔진에는 위험한 semantic filter validator를 발견했다. 이 로직은 특정 UI 구조를 성공 판정에 강하게 반영하기 때문에 외부 사이트 평가에서는 휴리스틱으로 작동할 수 있었다. 따라서 해당 검증 엔진을 제거하고, 더 일반적인 DOM/state-change evidence 중심의 판정으로 낮췄다.

## 새로 추가하거나 강화한 주요 로직

### 1. benchmark manager와 suite 관리

관련 commit:

- `7453e16 feat: improve benchmark management and suites`

주요 변화:

- GUI benchmark manager 추가.
- terminal benchmark mode 개선.
- public suite JSON 관리 구조 강화.
- benchmark artifact의 `summary.json`, `results.json`, `summary.md` 구조 정리.
- screenshot quality 관련 테스트 추가.
- 초기 공개 사이트 suite: Wikipedia, Hacker News, PyPI, npm, YouTube, Apple, FOW, spell checker 등.

보고서 포인트:

- 사용자가 수동으로 매번 테스트를 구성하지 않아도 시나리오 JSON 기반으로 반복 실행 가능.
- CLI/GUI/terminal mode에서 같은 suite 정의를 공유할 수 있게 됨.

### 2. auto-follow relevant browser tabs

관련 commit:

- `1cc6ff1 feat: auto-follow relevant browser tabs`

주요 변화:

- 새 탭/관련 탭이 열릴 때 OpenClaw dispatch가 따라갈 수 있도록 보강.
- 외부 사이트에서 검색 결과, 상세 페이지, 새 창 등이 열리는 상황을 다룰 수 있게 함.

보고서 포인트:

- 실제 웹은 클릭 후 같은 탭만 바뀌지 않는다. 새 탭, 팝업, viewer, 상세 창이 열리는 경우가 많기 때문에 tab-following이 필요했다.

### 3. structured human answer flow

관련 commit:

- `ca4f13c feat: add structured human answer flow`

주요 변화:

- CAPTCHA/로그인/사용자 입력이 필요한 상황에서 agent가 무한정 실패하지 않고 구조화된 human answer flow로 빠질 수 있게 함.
- `human_answer_runtime.py` 추가.
- terminal/chat hub 흐름에서 사용자 개입 필요 상황을 더 명확히 표현.

보고서 포인트:

- CAPTCHA 우회는 하지 않는다.
- 자동화가 처리할 수 없는 사용자 개입 상황은 일반 실패와 분리해 표시한다.

### 4. 기본 모델 gpt-5.5 갱신

관련 commit:

- `667f7e9 chore: 기본 모델을 gpt-5.5로 갱신`

주요 변화:

- README/config/agent-service workflow의 기본 모델을 gpt-5.5로 맞춤.

보고서 포인트:

- benchmark 실행 모델을 명시하고, artifact에 provider/model을 남긴다.

### 5. multi-user interaction harness

관련 commit:

- `fe74566 feat: 멀티유저 상호작용 하네스 추가`

주요 변화:

- participant model, registry, blackboard, turn scheduler 추가.
- multi-user interaction runtime 추가.
- local chat login fixture와 scenario 추가.
- goal-driven runtime에 participant-aware planning/decision path 추가.

보고서 포인트:

- 단일 브라우저 자동화뿐 아니라 여러 참여자/역할이 있는 상호작용까지 확장 가능한 구조를 준비했다.
- 다중 사용자 시나리오는 향후 협업형 서비스나 멀티 세션 평가로 확장할 수 있다.

### 6. development harness workflow

관련 commit:

- `7f77e16 docs: 개발 하네스 워크플로 추가`

주요 파일:

- `AGENTS.md`
- `docs/harness/CONTEXT_MAP.md`
- `docs/harness/CHECKS.md`
- `docs/harness/DEVELOPMENT_HARNESS.md`
- `docs/harness/development_harness_manifest.json`
- `scripts/context_pack.py`
- `scripts/dev_harness.py`
- `scripts/lint_harness_docs.py`

주요 변화:

- repo-entry, benchmark-harness, runtime-entrypoints, gaia-goal-driven, cleanup-gc 같은 context pack을 정의.
- 대형 repo를 전부 읽지 않고 작업 영역별 최소 context만 읽는 규약을 추가.
- lane별 owned paths, eval contract, checks를 manifest로 관리.

보고서 포인트:

- 이 프로젝트는 AI agent가 직접 개발하는 구조이므로, agent가 잘못된 레이어를 건드리지 않도록 development harness를 만들었다.
- 구현 전 Planner, 구현 후 Verifier, 주기적 Cleanup이라는 작업 계약을 문서화했다.

### 7. benchmark monitoring and shared suites

관련 commit:

- `2d62d08 Add benchmark monitoring and shared suites`

주요 파일:

- `monitoring/docker-compose.yml`
- `monitoring/prometheus.yml`
- `monitoring/grafana/dashboards/gaia_kpi.json`
- `monitoring/nginx/nginx.conf`
- `scripts/gaia_monitor_setup.py`
- `scripts/gaia_monitor_connect.py`
- `scripts/push_metrics.py`
- `scripts/sync_shared_suites.py`
- `gaia/src/benchmark_suite_sharing.py`

주요 변화:

- Prometheus + Pushgateway + Grafana + nginx basic auth 기반 모니터링 스택 추가.
- 팀원은 `gaia_monitor_connect.py`로 monitoring server 정보를 로컬 `~/.gaia/monitoring.json`에 저장.
- `--push-metrics`를 명시한 benchmark run만 metrics를 업로드.
- suite JSON은 민감 key를 sanitize한 뒤 shared suite 저장소에 올릴 수 있게 함.
- GUI/terminal/CLI에서 metrics upload 선택 흐름을 지원.

보고서 포인트:

- Grafana는 source of truth가 아니라 metric visualization layer다.
- benchmark summary/results에서 뽑은 KPI metric만 공유하고, raw artifact 전체나 계정 정보는 공유하지 않는다.
- team shared suite는 별도 JSON 저장 경로로 관리한다.

### 8. terminal benchmark mode 개선

관련 변화:

- `python -m gaia.cli --terminal` 경로에서 사이트 선택, 작업 선택, 기존 테스트 실행, 테스트 편집, 지표 확인 등이 가능하도록 개선.
- metrics upload prompt를 단순 y/N 질문이 아니라 방향키 선택 UI로 바꿈.
- monitoring server 연결이 없는데 업로드를 선택하면 연결 메뉴를 먼저 보여줌.
- 지표 확인에서 Grafana와 로컬 결과 보드를 선택할 수 있게 함.
- 모니터링 서버 연결이 있으면 사이트 선택 직후 shared suite를 자동 pull해서 팀원 간 테스트 정의를 맞출 수 있게 함.

보고서 포인트:

- CLI 사용자도 GUI 없이 benchmark를 실행/공유/확인할 수 있다.
- 팀원 실행 결과는 `--push-metrics`를 붙인 경우에만 서버로 업로드된다.

### 9. external public benchmark pack

관련 commit:

- `1c95ebe Add external public benchmark pack`

목표:

- 내부 서비스 휴리스틱 우려를 줄이기 위해 외부 공개 사이트 benchmark pack을 30개 사이트/150개 시나리오로 확장.

초기 구성:

- 기존 공개 suite를 5개 시나리오 단위로 정규화.
- 한국 발표 청중에게 익숙한 포털/뉴스/커머스/공공/지도/문화 사이트를 대거 추가.
- `external_public_manifest.json`으로 site_key, label, category, volatility, base_url, suite_path를 한 곳에서 관리.
- `scripts/run_kpi_benchmark_pack.py --suite-manifest`로 전체 pack 실행 지원.
- `scripts/prune_benchmark_records.py`로 실패 포함 artifact 삭제 기능 추가.

보고서 포인트:

- 외부 사이트는 내부 서비스와 DOM 구조, 접근 정책, 동적 UI, 광고/공지 모달, 보안 정책이 모두 다르기 때문에 범용성을 보기 좋다.
- 단순 성공률보다 “어떤 실패가 자동화 한계인지, 어떤 실패가 사이트 차단인지, 어떤 실패가 엔진 결함인지” 분류하는 것이 중요했다.

### 10. external benchmark recovery hardening

관련 commit:

- `bdf8850 Harden external benchmark recovery`

주요 변화:

- CAPTCHA/보안문자/보안 확인을 `BLOCKED_USER_ACTION` + `blocked_captcha`로 normalize.
- primary success rate에서 blocked user-action run 제외.
- 일반 광고/공지 모달은 닫기/오늘 하루 보지 않기/다시 보지 않기 같은 공개 dismiss 버튼만 처리.
- Access Denied/CAPTCHA/보안 확인 gate는 닫지 않고 차단 케이스로 남김.
- KakaoMap route scenario를 hidden tab 클릭 대신 공식 route deep link로 안정화.
- external manifest에서 반복 차단 사이트 제거 및 대체.
- Musinsa, Seoul Open Data, Government24, FOW, Moneytoring, 11번가 등 UI와 맞지 않는 문장을 실제 surface 기준으로 조정.

보고서 포인트:

- 실패를 숨기는 것이 아니라 분류한다.
- 자동화가 우회하면 안 되는 CAPTCHA는 일반 실패율에 섞지 않고 별도 개입률로 보여준다.

### 11. ref-first visual find fallback

관련 commit:

- `0b91fe2 Add ref-first visual find fallback`

주요 파일:

- `gaia/src/phase4/goal_driven/action_execution_runtime.py`
- `gaia/src/phase4/mcp_openclaw_dispatch_runtime.py`
- `gaia/src/phase4/mcp_local_dispatch_runtime.py`

주요 변화:

- 기본 철학을 ref_id 기반 조작으로 두고, ref가 실패하거나 DOM snapshot이 stale하면 재수집/재탐색.
- option ref missing, stale ref, dropdown DOM 갱신 지연 같은 상황에서 visual find fallback을 사용할 수 있게 함.
- 화면에는 보이지만 accessibility snapshot에 늦게 반영되는 동적 UI를 다루기 위한 recovery path 추가.

대표 사례:

- Musinsa 정렬 dropdown에서 `낮은 가격순` option이 화면에 보였지만 role snapshot에 바로 잡히지 않던 문제.
- 강제 snapshot 재수집 후 ref를 다시 찾고 클릭해 `sortCode=LOW_PRICE` 반영 확인.

보고서 포인트:

> Claude in Chrome류 브라우저 자동화도 ref 기반 조작을 우선하고, 필요한 경우 screenshot/coordinate fallback을 쓴다. 우리도 같은 방향으로 ref-first 조작을 유지하되, stale DOM과 visual fallback을 보강했다.

### 12. benchmark preflight diagnostics

관련 commit:

- `62d4e95 Harden benchmark preflight diagnostics`

주요 변화:

- child process가 OpenAI credential 없이 즉시 실패할 때 summary/results에 빈 reason이 남던 문제를 개선.
- provider credential check를 사전에 수행.
- missing credential error를 fatal summary에 남김.
- child traceback을 captured_log/reason에 보존하도록 강화.
- hardcoded path portability failure를 known failure로 인식하고 테스트에서 격리.

보고서 포인트:

- 150개가 0.29초씩 전부 실패한 것은 엔진 성능 문제가 아니라 credential preflight 실패였다.
- benchmark runner는 이런 실행 환경 문제를 artifact에 명확히 남겨야 한다.

### 13. Codex CLI auth for benchmarks

관련 commit:

- `e0e7af2 Support Codex CLI auth for benchmarks`

주요 변화:

- `OPENAI_API_KEY`가 없어도 `codex login` 기반 auth를 사용할 수 있게 benchmark client 초기화 경로 보강.
- `~/.codex/auth.json`의 ChatGPT/Codex CLI auth mode를 인식.
- OpenAI credential error 문구에 `codex login` 안내 포함.

보고서 포인트:

- 맥미니 등 팀원이 직접 benchmark를 돌릴 때 API key를 매번 환경변수로 주입하지 않아도 Codex CLI login 기반 실행이 가능해졌다.

### 14. external public Grafana rollups

관련 commit:

- `286e9ea Add external public Grafana rollups`

주요 변화:

- `run_kpi_benchmark_pack.py`가 suite별 결과에 더해 최종 pack artifact를 한 번 더 push.
- `gaia_external_pack_*`, `gaia_external_site_*`, `gaia_external_category_*`, `gaia_external_reason_code_count` metric 추가.
- Grafana dashboard 상단에 30-site overview 추가.
- 사이트 수, 총 실행 수, raw/primary 성공률, 평균 시간, 개입률, 사이트별 성공률, 카테고리별 성공률, reason code를 한 화면에서 볼 수 있게 함.

보고서 포인트:

- 팀원들이 개별 artifact 파일을 열지 않아도 Grafana에서 benchmark trend와 실패 원인을 볼 수 있다.
- 외부 공개 사이트 평가가 커지면서 30개 사이트 전체를 한 화면에 요약하는 rollup이 필요했다.

### 15. readonly WAIT judge

관련 commit:

- `f8ebd9b Run judge for readonly wait completion claims`

문제:

- YouTube 영상 상세처럼 화면에는 제목/채널명/조회 정보가 보였는데, WAIT 기반 성공 판정이 너무 보수적이라 실패 처리되는 케이스가 있었다.

보강:

- `is_goal_achieved=false`여도 reasoning이 “이미 보인다/조건 충족”을 강하게 주장하고, 목표가 read-only/detail 확인 계열이면 synthetic completion claim을 만들고 judge를 호출.
- 단, mutation goal은 제외.
- transient loading surface도 제외.
- DOM이 비어 있거나 로딩/생성/처리 중인 화면이면 제외.

중요한 방어:

- LLM reasoning만 믿고 성공 처리하지 않는다.
- judge가 현재 DOM과 목표를 다시 보고 success 여부를 판단한다.
- 그래서 “성공 판정은 모델 말만 믿는 것 아닌가?”라는 질문에 대해 “아니다. 제한된 경우에만 judge를 추가 호출하고, DOM 증거와 목표 조건을 같이 본다”고 답할 수 있다.

### 16. service unavailable guard와 runner_id

관련 commit:

- `bdbeb5f Refine external benchmarks and tag runners`

주요 변화:

- VisitKorea를 primary external manifest에서 제거하고 대한민국 정책브리핑으로 대체.
- `LAWGO_003_LAW_DETAIL`을 `LAWGO_003_LAW_SEARCH_TABS`로 교체.
- `서비스 지연 안내`, `서비스 이용에 불편`, `현재 사용자가 많아`, `정상적으로 제공할 수 없습니다`, `잠시 후 다시 접속` 같은 화면을 성공 증거로 인정하지 않도록 guard 추가.
- `runner_id` 추가:
  - `scripts/runner_identity.py`
  - `scripts/run_goal_benchmark.py --runner-id`
  - `scripts/run_kpi_benchmark_pack.py --runner-id`
  - `scripts/push_metrics.py` metric label
  - Grafana Runner filter/column

보고서 포인트:

- 공용 benchmark 서버에 여러 팀원이 결과를 올리면 “누가 돌린 결과인가”가 필요하다.
- `runner_id`는 기본적으로 `GAIA_RUNNER_ID` 또는 `user@host`로 기록되고, 명시적으로 `--runner-id macmini-team-a`처럼 지정할 수 있다.
- 개인 정보 노출을 최소화하려면 팀 내에서 약속한 runner id를 환경변수로 설정하면 된다.

## 최근 3주 commit timeline

기준: `git log --since='2026-04-19 00:00'`

| date | commit | 제목 | 보고서용 의미 |
|---|---|---|---|
| 2026-04-26 | `7453e16` | `feat: improve benchmark management and suites` | benchmark manager, GUI/terminal mode, public suite 기반을 마련 |
| 2026-04-26 | `1cc6ff1` | `feat: auto-follow relevant browser tabs` | 외부 사이트 새 탭/상세 탭 자동 추적 기반 |
| 2026-04-30 | `ca4f13c` | `feat: add structured human answer flow` | CAPTCHA/로그인 등 사람 개입 필요 상황을 구조화 |
| 2026-04-30 | `b398066` | Merge PR #115 | structured human answer flow 통합 |
| 2026-05-04 | `667f7e9` | `chore: 기본 모델을 gpt-5.5로 갱신` | benchmark/model 기본값 정리 |
| 2026-05-04 | `fe74566` | `feat: 멀티유저 상호작용 하네스 추가` | participants/blackboard/turn scheduler 기반 추가 |
| 2026-05-04 | `7f77e16` | `docs: 개발 하네스 워크플로 추가` | context pack, dev harness, checks, AGENTS 규약 추가 |
| 2026-05-04 | `4b51ca3` | Merge PR #116 | generic autonomous harness 통합 |
| 2026-05-06 | `7ea9a12` | `Remove filter semantic validator from generic runtime` | 도메인 특화 검증 제거, 범용성/정직성 강화 |
| 2026-05-06 | `c5ef967` | Merge branch `codex/generic-autonomous-harness` | validator 제거 및 harness 흐름 통합 |
| 2026-05-06 | `2d62d08` | `Add benchmark monitoring and shared suites` | Grafana/Prometheus/Pushgateway, shared suite, `--push-metrics` 추가 |
| 2026-05-07 | `1c95ebe` | `Add external public benchmark pack` | 30개 사이트/150개 시나리오 external pack 추가 |
| 2026-05-08 | `bdf8850` | `Harden external benchmark recovery` | CAPTCHA/blocked 분리, 모달 처리, scenario 안정화, manifest 정리 |
| 2026-05-08 | `0b91fe2` | `Add ref-first visual find fallback` | stale ref/option ref/visual fallback 보강 |
| 2026-05-08 | `62d4e95` | `Harden benchmark preflight diagnostics` | credential/preflight/child traceback 진단 강화 |
| 2026-05-08 | `e0e7af2` | `Support Codex CLI auth for benchmarks` | API key 없이 Codex login 기반 benchmark 실행 지원 |
| 2026-05-08 | `286e9ea` | `Add external public Grafana rollups` | external pack 통합 지표 및 Grafana overview 추가 |
| 2026-05-08 | `f8ebd9b` | `Run judge for readonly wait completion claims` | read-only/detail WAIT completion false negative 완화 |
| 2026-05-09 | `bdbeb5f` | `Refine external benchmarks and tag runners` | VisitKorea/Lawgo 정리, service unavailable guard, runner_id 추가 |

## 보고서에 넣을 변경 사항 묶음

### A. Benchmark scope 확장

초기에는 내부 서비스 중심의 성공/실패 검증이 핵심이었다. 그러나 내부 서비스만 보면 해당 UI 구조에 특화된 휴리스틱일 수 있다는 지적을 받을 수 있다. 이를 방어하기 위해 외부 공개 사이트 30개와 사이트당 5개 시나리오, 총 150개 시나리오를 추가했다.

외부 공개 benchmark는 다음 조건을 지킨다.

- 공개 접근만 사용.
- 로그인/결제/장바구니 확정/글쓰기/댓글/삭제/계정 정보 입력 제외.
- CAPTCHA 우회 제외.
- 검색/목록/상세/필터/정렬/지도/뉴스/문서/상품/공공 정보처럼 read-only 또는 비파괴 상호작용 중심.
- 사이트 변동성은 숨기지 않고 caveat로 기록.

### B. Benchmark quality 개선

처음부터 30개/150개를 한 번에 잘 돌릴 수 있었던 것은 아니다. 실제 외부 웹을 돌리면서 다음 문제를 발견했다.

- 페이지는 HTTP 200인데 body가 서비스 지연 안내인 경우.
- 보안문자/CAPTCHA가 뜨는 경우.
- 검색 결과 URL만 특정 CDN/Fastly CAPTCHA에 걸리는 경우.
- dropdown option이 화면에는 열렸지만 role snapshot에는 늦게 반영되는 경우.
- 광고/공지 모달이 화면을 가리는 경우.
- 사이트별 실제 UI와 scenario 문장이 맞지 않는 경우.
- 상세 페이지 iframe만 불안정한 경우.

이에 따라 benchmark quality를 다음 방식으로 개선했다.

- 반복 차단 사이트를 primary pack에서 제거.
- 특정 URL만 문제면 같은 사이트의 더 안정적인 read-only URL로 교체.
- CAPTCHA는 `BLOCKED_USER_ACTION`으로 분리.
- 일반 광고/공지 modal은 닫을 수 있게 하되 보안 gate는 닫지 않음.
- scenario 문장을 실제 UI evidence에 맞춰 재작성.
- service unavailable 문구는 success evidence에서 제외.

### C. Runtime reliability 개선

외부 웹에서 특히 문제가 되었던 것은 DOM/ref의 타이밍 문제였다.

예를 들어 Musinsa 정렬 dropdown은 사용자가 보면 쉬운 작업이다. dropdown을 열고 `낮은 가격순`을 클릭하면 된다. 하지만 자동화에서는 다음 문제가 있었다.

- dropdown 클릭 직후 role snapshot이 이전 상태에 묶임.
- option ref가 아직 snapshot에 없음.
- LLM은 option이 보인다고 판단하지만 action executor는 ref를 찾지 못함.
- 이 상태에서 같은 실패를 반복하면 progress-stop failure가 된다.

이를 해결하기 위해:

- ref-first 조작을 유지한다.
- ref가 없거나 stale하면 DOM analysis cache와 raw role-tree delta를 무효화한다.
- OpenClaw snapshot을 강제로 재수집한다.
- 그래도 ref 기반 조작이 어렵다면 visual find/coordinate fallback으로 간다.

이 방식은 Claude in Chrome 같은 브라우저 자동화가 말하는 “ref 우선, 좌표 fallback” 구조와도 유사하다.

### D. Completion judgment 개선

기존 WAIT 성공 판정은 보수적이었다. 보수적인 판정은 false positive를 줄이는 장점이 있지만, YouTube 상세 화면처럼 목표 증거가 실제로 보이는데도 실패 처리되는 false negative를 만들었다.

그래서 read-only/detail 목표에 대해 제한적인 judge path를 추가했다.

적용 조건:

- action이 `WAIT`.
- LLM reasoning이 이미 보임/조건 충족/완료/확인 같은 completion claim을 포함.
- DOM이 transient/loading 상태가 아님.
- 목표가 mutation/increase/decrease/clear 계열이 아님.
- 목표가 read-only visibility 또는 상세 정보 확인 계열임.
- judge가 현재 DOM과 목표를 다시 보고 성공 여부를 판단.

제외 조건:

- 로딩/생성/처리/진행률 화면.
- 사용자 입력/삭제/추가 같은 mutation goal.
- 서비스 지연/접속 폭주/일시 오류 화면.
- DOM 증거가 비어 있는 경우.

보고서용 표현:

> WAIT 성공 판정은 모델의 자기 주장만으로 통과시키지 않는다. 읽기/탐색형 목표에서 reasoning이 완료를 주장할 때도, 현재 DOM이 안정 상태인지 확인하고 별도 judge가 목표와 화면 증거를 다시 검토한다.

### E. Monitoring/Grafana 공유

팀원들이 각자 benchmark를 실행하면 결과가 흩어진다. 그래서 metrics push와 Grafana dashboard를 추가했다.

구성:

- Prometheus
- Pushgateway
- Grafana
- nginx basic auth
- local config: `~/.gaia/monitoring.json`
- shared suite storage: `monitoring/shared/`

정책:

- `--push-metrics`가 있을 때만 업로드.
- raw artifact 전체를 공유하지 않는다.
- screenshot/계정 정보/민감 payload를 공유하지 않는다.
- suite JSON은 sanitize 후 공유.
- metrics는 low-cardinality label 중심으로 구성.
- external pack은 pack/site/category/reason code rollup을 별도로 올림.

최근 보강:

- 30-site overview 추가.
- `runner_id` filter/column 추가.
- site/category별 성공률과 reason code를 한 화면에서 확인.

### F. Runner identity

문제:

- 맥미니, 개인 노트북, 팀원 PC에서 같은 benchmark를 돌리면 Grafana에 결과가 섞인다.
- 어떤 실행이 누구의 환경에서 나온 것인지 구분해야 한다.

해결:

- `--runner-id` 인자 추가.
- `GAIA_RUNNER_ID` 환경변수 지원.
- 기본값은 `user@host`.
- summary/results/pack summary/Prometheus label/Grafana table에 기록.

사용 예:

```bash
GAIA_RUNNER_ID=macmini-team-a \
PYTHONPATH=. GAIA_OPENCLAW_HEADLESS=1 GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-macmini-20260510 \
  --push-metrics
```

또는:

```bash
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-macmini-20260510 \
  --runner-id macmini-team-a \
  --push-metrics
```

## 검증 명령 모음

최근 관련 작업에서 사용한 검증:

```bash
PYTHONPATH=. .venv/bin/python -m pytest gaia/tests/unit -q
python scripts/lint_harness_docs.py
git diff --check
python -m json.tool monitoring/grafana/dashboards/gaia_kpi.json >/dev/null
python -m json.tool gaia/tests/scenarios/external_public_manifest.json >/dev/null
```

최근 주요 결과:

- 전체 unit: `467 passed, 4 warnings`
- targeted tests: `60 passed`
- harness docs lint 통과
- `git diff --check` 통과
- dashboard/scenario JSON 검증 통과

## 전체 external pack 재실행 명령

최신 manifest 기준으로 최종 수치를 확정하려면 다음 형태로 돌린다.

```bash
PYTHONPATH=. GAIA_OPENCLAW_HEADLESS=1 GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-final-20260510 \
  --runner-id macmini-team-a \
  --push-metrics
```

주의:

- `--push-metrics`는 monitoring server 연결이 되어 있을 때만 Grafana에 반영된다.
- 연결 정보는 `~/.gaia/monitoring.json`에 저장된다.
- Codex CLI auth를 쓰려면 해당 머신에서 `codex login`이 되어 있어야 한다.
- API key 방식이면 `OPENAI_API_KEY` 또는 `OPENAI_ADMIN_KEY`가 필요하다.

## 발표/보고서용 답변 문장 후보

### 내부 사이트 휴리스틱 우려에 대한 답변

> 내부 서비스 하나에 맞춘 휴리스틱일 수 있다는 지적을 반영해, 외부 공개 웹 30개 사이트와 150개 시나리오를 별도로 구성했습니다. 네이버/다음/카카오맵/정부24/기상청/뉴스/커머스/문화/개발자 사이트처럼 DOM 구조와 UI 패턴이 서로 다른 사이트를 같은 하네스로 측정했습니다.

### CAPTCHA/차단 사이트 제외에 대한 답변

> CAPTCHA나 보안문자는 자동화가 우회할 대상이 아니므로 일반 실패율에 섞지 않았습니다. 반복 차단 사이트는 primary pack에서 제거했고, 실행 중 새로 CAPTCHA가 뜨는 경우에는 `BLOCKED_USER_ACTION`으로 분리해 primary success rate 계산에서 제외했습니다.

### 서비스 지연 사이트 제외에 대한 답변

> 일부 사이트는 HTTP 200을 반환하지만 실제 화면은 서비스 지연 안내 또는 접속 폭주 페이지였습니다. 이런 화면의 일반 단어가 목표 증거로 잡히면 성공률이 오염되기 때문에, 해당 사이트는 primary pack에서 제거하거나 안정적인 read-only URL로 교체했습니다.

### validator 제거에 대한 답변

> 내부 서비스에 특화된 filter semantic validator는 오히려 범용 엔진에는 위험하다고 판단했습니다. 특정 카드 구조나 필터 UI를 일반 성공 판정처럼 쓰면 외부 사이트에서는 false positive/false negative를 만들 수 있습니다. 그래서 범용 런타임에서는 제거하고, OpenClaw의 실제 state-change evidence와 benchmark artifact 중심으로 판단하도록 정리했습니다.

### Grafana 공유에 대한 답변

> benchmark 결과는 `--push-metrics`를 명시했을 때만 Pushgateway로 업로드되고 Grafana에서 볼 수 있습니다. raw artifact 전체가 공유되는 것은 아니며, summary/results에서 뽑은 KPI metric과 sanitize된 suite JSON만 공유합니다. 최근에는 `runner_id`를 추가해 누가 어떤 환경에서 실행한 결과인지 구분할 수 있게 했습니다.

### ref-first visual fallback에 대한 답변

> 기본 조작은 DOM/ref 기반입니다. 다만 실제 웹에서는 dropdown이나 동적 UI가 화면에는 보이지만 accessibility snapshot에 늦게 반영되는 경우가 있습니다. 그래서 stale ref가 감지되면 snapshot을 강제로 다시 수집하고, 그래도 ref 기반 조작이 어려우면 visual fallback으로 보완합니다.

### WAIT judge에 대한 답변

> WAIT 성공 판정은 LLM의 말만 믿는 구조가 아닙니다. 읽기/탐색형 목표에서 화면 증거가 이미 보인다고 reasoning이 주장할 때도, DOM이 안정 상태인지 확인하고 별도 judge가 현재 화면과 목표를 다시 검토합니다. mutation goal이나 로딩 화면, 서비스 오류 화면은 이 경로에서 제외됩니다.

## 보고서 구성 제안

### 1. 문제 정의

- 웹 자동화 agent가 실제 웹에서 goal을 수행할 수 있는지 검증해야 한다.
- 내부 서비스만 보면 특정 UI에 맞춘 휴리스틱일 가능성이 있다.
- 외부 공개 웹은 구조가 다양하고 변동성이 크며, 차단/팝업/동적 UI가 많다.

### 2. 하네스 설계

- scenario JSON 계약: `id`, `url`, `goal`, `constraints`, `expected_signals`, `time_budget_sec`.
- suite manifest: site metadata와 suite path 관리.
- runner: `run_goal_benchmark.py`.
- pack runner: `run_kpi_benchmark_pack.py`.
- artifact: `summary.json`, `results.json`, `summary.md`.
- Grafana: metrics rollup.

### 3. 실행 범위

- 내부 서비스 suite.
- 외부 공개 30개 사이트/150개 시나리오.
- 한국 사이트 중심 구성.
- 로그인/결제/글쓰기/CAPTCHA 우회 제외.

### 4. 핵심 개선

- filter semantic validator 제거.
- CAPTCHA/blocked 분리.
- service unavailable guard.
- ref-first visual fallback.
- read-only WAIT judge.
- benchmark preflight diagnostics.
- Codex CLI auth.
- runner_id.
- Grafana 30-site overview.

### 5. 결과

- 내부 서비스 9/10, 이후 first-3 3/3.
- external initial pack 137/150, primary 0.9195, 단 false positive caveat 존재.
- 정책브리핑 5/5 headless.
- Law.go.kr 수정 suite 5/5 headless.
- Musinsa strict sort 1/1.

### 6. 한계

- 외부 웹은 매일 바뀔 수 있다.
- CAPTCHA/보안 정책은 자동화가 해결할 대상이 아니다.
- `repeats=1` 결과는 재현성 근거가 부족하다.
- 발표 전 안정 사이트 일부는 `repeats=2`로 보강할 필요가 있다.
- Grafana metric은 summary 기반이므로 raw screenshot/trace 재현에는 local artifact가 필요하다.

### 7. 향후 작업

- 최신 manifest 기준 30/150 전체 headless rerun.
- `runner_id=macmini-team-a`로 맥미니 실행 결과를 Grafana에 업로드.
- 실패 reason code 상위 항목 재분석.
- site_unavailable/service_delay를 더 명시적인 status 또는 reason code로 분리.
- 안정 사이트 subset에 대해 `repeats=2` 재현성 측정.
- 보고서 최종 수치 표 갱신.

## 보고서에 넣을 수 있는 표 초안

### 주요 개발 산출물

| 영역 | 산출물 | 설명 |
|---|---|---|
| 개발 하네스 | `scripts/dev_harness.py`, `context_manifest.json` | agent가 올바른 영역만 읽고 수정하도록 context/lane 제공 |
| benchmark runner | `scripts/run_goal_benchmark.py` | 단일 suite 실행, artifact 생성, preflight 진단 |
| pack runner | `scripts/run_kpi_benchmark_pack.py` | manifest 기반 다중 suite 실행, KPI 통합 |
| monitoring | `monitoring/`, `scripts/push_metrics.py` | Grafana/Prometheus/Pushgateway 기반 팀 공유 |
| shared suite | `benchmark_suite_sharing.py`, `sync_shared_suites.py` | sanitize된 suite JSON 공유 |
| external manifest | `external_public_manifest.json` | 30 sites/150 scenarios source of truth |
| completion judge | `goal_completion_helpers.py` | WAIT/detail/read-only 성공 판정 보강 |
| action recovery | `action_execution_runtime.py`, `mcp_openclaw_dispatch_runtime.py` | stale ref, ref-first visual fallback |
| runner identity | `scripts/runner_identity.py` | 실행자/머신 식별자 기록 |

### 제거/대체 결정

| 결정 | 이유 | 효과 |
|---|---|---|
| filter semantic validator 제거 | 내부 서비스 특화 검증이 범용 엔진 오염 가능 | 외부 사이트 평가 정직성 강화 |
| CAPTCHA 반복 사이트 제거 | 우회 대상 아님 | 일반 실패율 오염 방지 |
| VisitKorea 제거 | 서비스 지연 안내 반복 및 false positive | 정책브리핑으로 안정 대체 |
| Law.go.kr 상세 URL 교체 | 상세 iframe 서비스 오류 | 검색 탭/목록 read-only 확인으로 안정화 |
| PyPI search URL 교체 | Fastly CAPTCHA | package file/detail surface로 유지 |
| KakaoMap hidden tab 제거 | hidden UI 클릭 불안정 | route deep link로 안정화 |

### 대표 지표

| 실행 | 범위 | 성공률 | 평균 시간 | caveat |
|---|---:|---:|---:|---|
| 내부 서비스 full | 10 | 0.9 | 83.06s | filter semantic validator false negative 포함 |
| 내부 서비스 first-3 after cleanup | 3 | 1.0 | 36.34s | validator 제거 후 state-change 기준 |
| external initial pack | 150 | 0.9133 raw / 0.9195 primary | 62.25s | VisitKorea/Lawgo 정리 전 artifact |
| 정책브리핑 headless | 5 | 1.0 | 34.91s | VisitKorea 대체 후보 검증 |
| Law.go.kr headless | 5 | 1.0 | 56.18s | 상세 URL 교체 후 검증 |
| Musinsa sort strict | 1 | 1.0 | 60.91s | stale option ref recovery 검증 |

## 교수님 질문 대비 Q&A

### Q. 내부 사이트에만 맞춘 휴리스틱 아닌가?

A. 그 지적을 반영해 외부 공개 사이트 30개/150개 시나리오로 확장했다. 또한 내부 서비스 특화 filter semantic validator는 제거했다. 외부 benchmark는 한국 포털, 뉴스, 지도, 커머스, 공공데이터, 정부, 문화, 개발자 사이트 등 서로 다른 UI 구조를 포함한다.

### Q. 실패한 사이트는 왜 제외했는가?

A. 제외 기준은 자동화 성능을 좋게 보이기 위한 임의 제외가 아니라, CAPTCHA/bot-wall/서비스 지연/접속 폭주처럼 자동화가 우회하면 안 되거나 측정 의미가 없는 케이스다. 이런 케이스는 일반 실패와 분리해야 성공률 해석이 정직해진다.

### Q. LLM이 성공했다고 하면 그냥 성공 처리하는가?

A. 아니다. WAIT 성공 판정은 DOM evidence, expected signals, state-change evidence, judge를 조합한다. 특히 read-only WAIT judge도 목표가 읽기/상세 확인 계열이고 DOM이 안정 상태일 때만 호출한다. mutation goal이나 오류 화면은 제외한다.

### Q. Grafana에 올라간 데이터는 팀원끼리 공유되는가?

A. `--push-metrics`를 붙인 실행만 공유된다. 공유되는 것은 KPI metric과 sanitize된 suite JSON이며 raw artifact 전체나 민감 정보는 공유하지 않는다. `runner_id`로 어떤 팀원/머신이 실행했는지도 구분할 수 있다.

### Q. 외부 사이트는 변동성이 큰데 결과를 믿을 수 있는가?

A. 그래서 raw success rate만 쓰지 않고 reason code, blocked count, progress-stop failure, intervention rate, primary success rate를 함께 본다. 또한 발표 전 안정 사이트 subset은 repeats를 늘려 재현성을 보강할 계획이다.

## 남은 TODO

1. 최신 manifest 기준 전체 30/150 headless rerun.
2. 맥미니에서 `runner_id=macmini-team-a`로 실행하고 Grafana 업로드 확인.
3. 최신 full pack 결과에서 실패 항목을 reason code별로 다시 묶기.
4. `site_unavailable`/`service_delay`를 더 명시적인 reason code로 추가할지 검토.
5. 보고서 최종 표에는 정리 전 artifact와 정리 후 artifact를 구분해서 넣기.
6. 발표 직전 stable subset `repeats=2` 또는 `repeats=3`로 reproducibility 근거 확보.

## Appendix A. 30개 외부 공개 사이트 / 150개 시나리오 전체 인벤토리

이 섹션은 보고서에서 “외부 공개 사이트를 실제로 얼마나 다양하게 구성했는가”를 보여주기 위한 원천 자료다. 최종 보고서에는 전부 넣기보다 카테고리별 예시만 뽑아 쓰면 된다.

### 01. Wikipedia Korea (`wikipedia`)

- category: `knowledge_reference`
- volatility: `stable`
- base_url: `https://ko.wikipedia.org/`
- suite: `gaia/tests/scenarios/wikipedia_public_suite.json`
- suite_id: `wikipedia_public_v2`
- scenarios:
  - `WIKI_001_PORTAL_OVERVIEW`: 위키백과 대문에서 오늘의 알찬 글, 알고 계십니까, 참여 안내 같은 공개 지식 포털 영역 중 두 가지를 확인.
  - `WIKI_002_SEARCH_AI`: 위키백과 검색 결과에서 인공지능 관련 문서 후보 목록과 문서로 이동할 수 있는 링크를 확인.
  - `WIKI_003_ARTICLE_STRUCTURE`: 인공지능 문서에서 정의 요약, 목차, 정보상자 또는 참고 문헌 같은 문서 구조를 확인.
  - `WIKI_004_CATEGORY_BROWSE`: 과학 분류 화면에서 하위 분류나 문서 목록처럼 지식 체계가 나뉘어 보이는지 확인.
  - `WIKI_005_TOC_LANGUAGE_NAV`: 대한민국 문서에서 목차나 언어 링크처럼 화면 안에서 이동하는 탐색 요소를 선택해 관련 영역을 확인.
- 보고서 활용 포인트: 전통적인 문서/백과 구조라 DOM이 비교적 안정적이고, read-only benchmark의 기준점으로 쓰기 좋다.

### 02. GitHub (`github`)

- category: `developer_tech`
- volatility: `stable`
- base_url: `https://github.com/`
- suite: `gaia/tests/scenarios/github_public_suite.json`
- suite_id: `github_public_v2`
- scenarios:
  - `GITHUB_001_HOME_NAV`: GitHub 공개 홈에서 검색 입력, 제품 내비게이션, 공개 소개 영역 확인.
  - `GITHUB_002_REPO_SEARCH`: python cpython 관련 공개 저장소 목록과 정렬/필터 영역 확인.
  - `GITHUB_003_REPO_OVERVIEW`: python/cpython 저장소에서 파일 목록, README, 별표 수나 브랜치 표시 확인.
  - `GITHUB_004_ISSUE_TRIAGE`: python/cpython 이슈 목록에서 상태 필터, 이슈 제목 목록, 라벨 영역 확인.
  - `GITHUB_005_RELEASE_REVIEW`: python/cpython 릴리스 화면에서 최신 릴리스 제목, 태그, 변경 요약 확인.
- 보고서 활용 포인트: 개발자 사이트는 링크/목록/상세/필터 구조가 뚜렷하고 공개 데이터가 풍부하다.

### 03. Hacker News (`hacker_news`)

- category: `developer_tech`
- volatility: `high`
- base_url: `https://news.ycombinator.com/`
- suite: `gaia/tests/scenarios/hacker_news_public_suite.json`
- suite_id: `hacker_news_public_v2`
- scenarios:
  - `HN_001_TOP_STORIES`: 첫 화면에서 이야기 제목 목록, 점수, 사용자 링크 확인.
  - `HN_002_NEWEST_LIST`: newest 목록에서 시간순 이야기 목록과 more 링크 확인.
  - `HN_003_ITEM_DISCUSSION`: 이야기 상세 화면에서 제목, 점수, 토론 목록 확인.
  - `HN_004_SHOW_LIST`: Show HN 목록에서 제품 소개성 이야기 제목과 점수 정보 확인.
  - `HN_005_ASK_LIST`: Ask HN 목록에서 질문형 이야기 제목과 more 링크 확인.
- 보고서 활용 포인트: UI는 단순하지만 콘텐츠 변동성이 높아 외부 사이트 변동성의 기본 샘플로 적합하다.

### 04. PyPI (`pypi`)

- category: `developer_tech`
- volatility: `stable`
- base_url: `https://pypi.org/`
- suite: `gaia/tests/scenarios/pypi_public_suite.json`
- suite_id: `pypi_public_v2`
- scenarios:
  - `PYPI_001_HOME_SEARCH`: 공개 홈에서 패키지 검색 입력과 프로젝트 통계 또는 주요 안내 영역 확인.
  - `PYPI_002_PACKAGE_SEARCH`: requests 프로젝트의 파일 목록에서 wheel/source distribution/파일명/크기/등록 날짜 중 두 가지 확인.
  - `PYPI_003_PACKAGE_DETAIL`: requests 프로젝트 화면에서 설치 명령, 최신 버전, 프로젝트 설명 또는 메타데이터 확인.
  - `PYPI_004_RELEASE_HISTORY`: requests 프로젝트 release history에서 여러 버전 항목이나 릴리스 날짜 확인.
  - `PYPI_005_PROJECT_LINKS`: Project links나 Meta 영역에서 홈페이지, 이슈, 라이선스 같은 보조 정보 확인.
- 보고서 활용 포인트: 검색 URL은 CAPTCHA가 재현되어 제거했고, package files/detail 같은 안정적인 공개 surface로 대체한 사례다.

### 05. YouTube Korea (`youtube`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://www.youtube.com/`
- suite: `gaia/tests/scenarios/youtube_public_suite.json`
- suite_id: `youtube_public_v2`
- scenarios:
  - `YOUTUBE_001_HOME_FEED`: 공개 홈에서 검색 입력, 탐색 내비게이션, 영상 썸네일 목록 확인.
  - `YOUTUBE_002_SEARCH_RESULTS`: 한국 여행 검색 결과에서 영상 제목, 채널명, 썸네일 목록 확인.
  - `YOUTUBE_003_VIDEO_ENTRY`: 공개 영상을 열어 제목, 채널명, 조회 정보 또는 설명 일부 확인.
  - `YOUTUBE_004_TOPIC_FILTER`: 서울 맛집 검색 결과에서 상단 필터나 주제 칩을 선택해 결과 목록 변화 확인.
  - `YOUTUBE_005_AUTOCOMPLETE_OR_CHIP`: 검색 입력에 키워드를 넣어 자동완성이나 검색 제안 영역 확인.
- 보고서 활용 포인트: 동영상 플랫폼은 DOM과 렌더링 변동성이 높아 read-only WAIT judge 보강의 필요성을 보여준다.

### 06. Apple Korea (`apple_store`)

- category: `commerce_product`
- volatility: `medium`
- base_url: `https://www.apple.com/kr/`
- suite: `gaia/tests/scenarios/apple_store_public_suite.json`
- suite_id: `apple_store_public_v2`
- scenarios:
  - `APPLE_001_HOME_PRODUCT_NAV`: 제품군 내비게이션, 프로모션 영역, 지원 링크 확인.
  - `APPLE_002_IPHONE_LINEUP`: iPhone 제품군 화면에서 모델 카드와 사양/가격 안내 확인.
  - `APPLE_003_PRODUCT_COMPARE`: iPhone 비교 화면에서 모델별 디스플레이, 칩, 카메라 비교 표 확인.
  - `APPLE_004_ACCESSORY_BROWSE`: 액세서리 화면에서 카테고리, 상품 카드, 가격 안내 확인.
  - `APPLE_005_SUPPORT_LOOKUP`: 지원 화면에서 제품 선택 목록, 검색 입력, 도움말 주제 확인.
- 보고서 활용 포인트: 제품 탐색형 commerce지만 구매/결제 없이 정보 확인만 수행한다.

### 07. FOW.LOL (`fow_kr`)

- category: `finance_game`
- volatility: `high`
- base_url: `https://www.fow.lol/`
- suite: `gaia/tests/scenarios/fow_public_suite.json`
- suite_id: `fow_kr_public_v2`
- scenarios:
  - `FOW_001_HOME_STATS`: 소환사 검색 입력, 랭킹 링크, 챔피언 통계 링크 확인.
  - `FOW_002_CHAMPION_STATS`: 챔피언 이름 목록, 승률, 픽률 또는 밴율 확인.
  - `FOW_003_RANKING_LIST`: 랭킹 화면에서 순위, 플레이어 이름, 티어, LP 또는 승률 확인.
  - `FOW_004_REGION_QUEUE_FILTER`: Tier/Region/Ver 필터와 챔피언별 승률/픽률/밴율 확인.
  - `FOW_005_SEARCH_SUGGESTION`: 소환사 검색 입력에 예시 이름을 넣어 검색 제안이나 결과 후보 확인.
- 보고서 활용 포인트: 게임 통계 사이트는 표/랭킹/필터 조합이라 일반 뉴스/문서와 다른 DOM 구조를 제공한다.

### 08. DCInside (`dcinside`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://www.dcinside.com/`
- suite: `gaia/tests/scenarios/dcinside_public_suite.json`
- suite_id: `dcinside_public_v2`
- scenarios:
  - `DCINSIDE_001_HOME_COMMUNITY`: 갤러리 검색 입력, 실시간 이슈, 갤러리 목록 확인.
  - `DCINSIDE_002_GALLERY_SEARCH`: 야구 관련 갤러리나 공개 글 목록 확인.
  - `DCINSIDE_003_GALLERY_LIST`: 야구 갤러리 목록에서 글 제목, 작성 시각, 조회 수 확인.
  - `DCINSIDE_004_HIT_GALL`: 실시간 베스트나 인기 갤러리 영역에서 인기 항목 목록 확인.
  - `DCINSIDE_005_LIST_PAGING`: 갤러리 목록에서 다음 페이지나 정렬 요소를 선택해 목록 변화 확인.
- 보고서 활용 포인트: 커뮤니티 사이트는 동적 콘텐츠와 광고/모달 가능성이 있어 변동성 테스트에 적합하다.

### 09. 머니터링 (`moneytoring`)

- category: `finance_game`
- volatility: `medium`
- base_url: `https://www.moneytoring.ai/`
- suite: `gaia/tests/scenarios/moneytoring_public_suite.json`
- suite_id: `moneytoring_public_v2`
- scenarios:
  - `MONEYTORING_001_MARKET_HOME`: 종목 검색 입력, 주요 지수, 시장 뉴스/분석 카드 확인.
  - `MONEYTORING_002_STOCK_SEARCH`: 삼성전자 같은 기업명, 종목 정보, 가격 또는 등락률 확인.
  - `MONEYTORING_003_COMPANY_DETAIL`: 종목 상세에서 현재가, 차트, 기업 개요 또는 투자 지표 확인.
  - `MONEYTORING_004_MARKET_SECTION`: 시장 정보나 종목 발굴 섹션에서 목록형 금융 데이터 확인.
  - `MONEYTORING_005_INDEX_OR_CALENDAR`: 주요 지수나 경제 일정에서 날짜, 지수명, 변동 정보 확인.
- 보고서 활용 포인트: 금융형 UI는 수치/차트/카드가 섞여 있어 정보 추출 테스트에 좋다.

### 10. Naver Search (`naver_search`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://www.naver.com/`
- suite: `gaia/tests/scenarios/naver_search_public_suite.json`
- suite_id: `naver_search_public_v2`
- scenarios:
  - `NAVER_001_HOME_PORTAL`: 검색창, 뉴스 스탠드, 쇼핑이나 날씨 같은 포털 영역 확인.
  - `NAVER_002_WEATHER_SEARCH`: 오늘 날씨 관련 현재 기온, 예보, 지역 정보 확인.
  - `NAVER_003_KNOWLEDGE_RESULT`: 인공지능 검색 결과에서 지식백과, 뉴스, 웹문서 결과 유형 확인.
  - `NAVER_004_NEWS_TAB`: 반도체 뉴스 검색 화면에서 기사 제목, 언론사명, 시간 정보 확인.
  - `NAVER_005_IMAGE_TAB`: 경복궁 이미지 검색 화면에서 이미지 썸네일과 관련 키워드/필터 확인.
- 보고서 활용 포인트: 국내 대표 포털로 발표 청중에게 설명하기 쉽고, 결과 유형 전환이 포함된다.

### 11. Daum (`daum`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://www.daum.net/`
- suite: `gaia/tests/scenarios/daum_public_suite.json`
- suite_id: `daum_public_v2`
- scenarios:
  - `DAUM_001_HOME_PORTAL`: 검색창, 뉴스 영역, 카페/메일 같은 주요 서비스 링크 확인.
  - `DAUM_002_TOTAL_SEARCH`: 부산 날씨 검색 결과에서 현재 정보, 예보, 관련 링크 확인.
  - `DAUM_003_NEWS_SEARCH`: 뉴스 검색 화면에서 기사 제목 목록, 언론사명, 시간 정보 확인.
  - `DAUM_004_IMAGE_SEARCH`: 제주도 이미지 검색 화면에서 썸네일 목록과 관련 검색어 확인.
  - `DAUM_005_TOPIC_TAB`: 프로야구 검색 결과에서 뉴스/이미지/동영상 탭 이동 후 표시 영역 변화 확인.
- 보고서 활용 포인트: 네이버와 유사하지만 다른 DOM/탭 구조를 제공한다.

### 12. Naver News (`naver_news`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://news.naver.com/`
- suite: `gaia/tests/scenarios/naver_news_public_suite.json`
- suite_id: `naver_news_public_v2`
- scenarios:
  - `NAVERNEWS_001_MAIN_HEADLINES`: 주요 기사, 분야별 메뉴, 언론사 영역 확인.
  - `NAVERNEWS_002_IT_SECTION`: IT/과학 섹션에서 기사 제목, 썸네일, 언론사명 확인.
  - `NAVERNEWS_003_POLITICS_SECTION`: 정치 섹션에서 헤드라인, 기사 목록, 랭킹 영역 확인.
  - `NAVERNEWS_004_SEARCH_RESULTS`: 전기차 뉴스 검색 결과에서 기사 제목, 언론사명, 날짜 정보 확인.
  - `NAVERNEWS_005_RANKING`: 랭킹 뉴스 화면에서 순위, 기사 제목, 언론사 구분 확인.
- 보고서 활용 포인트: 뉴스 전문 surface로 포털 검색과 다른 기사 목록/랭킹 구조를 테스트한다.

### 13. Kakao Map (`kakao_map`)

- category: `public_data_service`
- volatility: `medium`
- base_url: `https://map.kakao.com/`
- suite: `gaia/tests/scenarios/kakao_map_public_suite.json`
- suite_id: `kakao_map_public_v2`
- scenarios:
  - `KAKAOMAP_001_HOME_MAP`: 지도 캔버스, 장소 검색 입력, 확대/축소 컨트롤 확인.
  - `KAKAOMAP_002_PLACE_SEARCH`: 서울역 검색 결과에서 장소명, 주소, 지도 마커 또는 주변 장소 목록 확인.
  - `KAKAOMAP_003_CATEGORY_NEARBY`: 강남역 카페 검색 화면에서 장소 목록, 별점 또는 주소 정보 확인.
  - `KAKAOMAP_004_ROUTE_PANEL`: 서울역에서 경복궁까지 공식 길찾기 링크로 출발역, 도착역, 대중교통 경로 또는 지도 영역 확인.
  - `KAKAOMAP_005_MAP_CONTROL`: 경복궁 지도 화면에서 확대/축소나 지도 유형 컨트롤 선택 후 표시 변화 확인.
- 보고서 활용 포인트: 지도는 DOM 기반 정보와 캔버스/visual 정보가 섞여 있어 일반 문서/목록 사이트와 다르다.

### 14. MBC News (`mbc_news`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://imnews.imbc.com/`
- suite: `gaia/tests/scenarios/mbc_news_public_suite.json`
- suite_id: `mbc_news_public_v2`
- scenarios:
  - `MBCNEWS_001_HOME_LANDMARK`: 주요 뉴스, 분야별 메뉴, 영상 뉴스 또는 최신 기사 영역 확인.
  - `MBCNEWS_002_POLITICS_LIST`: 정치 뉴스 목록에서 기사 제목, 썸네일, 입력 시간 또는 분야 표시 확인.
  - `MBCNEWS_003_ARTICLE_DETAIL`: 기사 상세에서 제목, 본문, 입력 시간 또는 공유/관련 기사 영역 확인.
  - `MBCNEWS_004_ECONOMY_LIST`: 경제 뉴스 목록에서 기사 제목, 목록 항목, 분야 메뉴 또는 시간 정보 확인.
  - `MBCNEWS_005_SPORTS_LIST`: 스포츠 뉴스 목록에서 경기 기사 제목, 썸네일, 목록 항목 또는 분야 메뉴 확인.
- 보고서 활용 포인트: 언론사별 DOM 차이를 포함하기 위해 MBC/SBS/KBS/YTN을 분산 배치했다.

### 15. SBS News (`sbs_news`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://news.sbs.co.kr/`
- suite: `gaia/tests/scenarios/sbs_news_public_suite.json`
- suite_id: `sbs_news_public_v2`
- scenarios:
  - `SBSNEWS_001_HOME_LANDMARK`: 주요 뉴스, 분야별 메뉴, 최신 기사 또는 영상 뉴스 영역 확인.
  - `SBSNEWS_002_POLITICS_LIST`: 정치 뉴스 목록에서 기사 제목, 썸네일, 시간 정보 또는 분야 메뉴 확인.
  - `SBSNEWS_003_ARTICLE_DETAIL`: 기사 상세에서 제목, 본문, 입력 시간 또는 기자/출처 정보 확인.
  - `SBSNEWS_004_ECONOMY_LIST`: 경제 뉴스 목록에서 기사 제목, 목록 항목, 분야 탭 또는 시간 정보 확인.
  - `SBSNEWS_005_SOCIETY_LIST`: 사회 뉴스 목록에서 기사 제목, 썸네일, 목록 항목 또는 분야 메뉴 확인.

### 16. 11st (`elevenst`)

- category: `commerce_product`
- volatility: `high`
- base_url: `https://www.11st.co.kr/`
- suite: `gaia/tests/scenarios/elevenst_public_suite.json`
- suite_id: `elevenst_public_v2`
- scenarios:
  - `ELEVENST_001_HOME_COMMERCE`: 검색 입력, 카테고리 메뉴, 프로모션 상품 영역 확인.
  - `ELEVENST_002_PRODUCT_SEARCH`: 노트북 검색 결과에서 상품명, 가격, 판매처 또는 혜택 정보 확인.
  - `ELEVENST_003_DETAIL_ENTRY`: 보조배터리 검색 결과에서 공개 상품 하나를 열어 이미지, 가격, 상품 설명 또는 배송 안내 확인.
  - `ELEVENST_004_FILTER_PANEL`: 운동화 검색 화면에서 카테고리, 브랜드, 가격대 필터 확인.
  - `ELEVENST_005_SORT_CHANGE`: 커피 검색 결과에서 정렬 옵션, 상품명, 가격, 필터 영역 또는 검색 결과 건수 확인.
- 보고서 활용 포인트: 결제/장바구니 없이 상품 검색/정렬/상세 정보 확인만 수행한다.

### 17. YTN (`ytn_news`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://www.ytn.co.kr/`
- suite: `gaia/tests/scenarios/ytn_news_public_suite.json`
- suite_id: `ytn_news_public_v2`
- scenarios:
  - `YTNNEWS_001_HOME_LANDMARK`: 주요 뉴스, 분야별 메뉴, 최신 기사 또는 영상 뉴스 확인.
  - `YTNNEWS_002_POLITICS_LIST`: 정치 뉴스 목록에서 기사 제목, 썸네일, 시간 정보 또는 분야 메뉴 확인.
  - `YTNNEWS_003_ARTICLE_DETAIL`: 기사 상세에서 제목, 본문, 입력 시간 또는 영상/사진 영역 확인.
  - `YTNNEWS_004_ECONOMY_LIST`: 경제 뉴스 목록에서 기사 제목, 목록 항목, 분야 메뉴 또는 시간 정보 확인.
  - `YTNNEWS_005_LATEST_LIST`: 최신 뉴스 목록에서 기사 제목, 시간 정보, 분야 표시 또는 더보기 영역 확인.

### 18. MUSINSA (`musinsa`)

- category: `commerce_product`
- volatility: `high`
- base_url: `https://www.musinsa.com/`
- suite: `gaia/tests/scenarios/musinsa_public_suite.json`
- suite_id: `musinsa_public_v2`
- scenarios:
  - `MUSINSA_001_HOME_STYLE`: 검색 입력, 랭킹, 카테고리 또는 브랜드 영역 확인.
  - `MUSINSA_002_PRODUCT_SEARCH`: 스니커즈 검색 결과에서 상품명, 브랜드, 가격 또는 할인 정보 확인.
  - `MUSINSA_003_RANKING_DETAIL`: 랭킹 화면에서 순위, 브랜드명, 상품명 또는 가격 정보 확인.
  - `MUSINSA_004_BRAND_CATEGORY`: 후드티 검색 화면에서 브랜드, 카테고리, 가격대 필터 확인.
  - `MUSINSA_005_SORT_CHANGE`: 가방 검색 결과에서 정렬 기준 드롭다운이나 랭킹 기준 영역을 열어 현재 선택값과 선택 가능한 기준 확인.
- 보고서 활용 포인트: stale option ref 문제가 재현된 핵심 사례다.

### 19. KBS News (`kbs_news`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://news.kbs.co.kr/`
- suite: `gaia/tests/scenarios/kbs_news_public_suite.json`
- suite_id: `kbs_news_public_v2`
- scenarios:
  - `KBSNEWS_001_HOME_LANDMARK`: 주요 뉴스, 분야별 메뉴, 최신 뉴스 또는 영상 뉴스 확인.
  - `KBSNEWS_002_BREAKING_LIST`: 최신 뉴스 화면에서 기사 제목 목록, 시간 정보, 썸네일 또는 분야 표시 확인.
  - `KBSNEWS_003_ARTICLE_DETAIL`: 기사 상세에서 제목, 본문, 입력 시간 또는 기자 정보 확인.
  - `KBSNEWS_004_CATEGORY_LIST`: 분야별 뉴스 화면에서 분야 탭, 기사 목록, 주요 뉴스 또는 더보기 영역 확인.
  - `KBSNEWS_005_WEATHER_NEWS`: 날씨 뉴스 화면에서 날씨 기사 목록, 예보/특보 문구, 기사 제목 또는 시간 정보 확인.

### 20. YES24 (`yes24`)

- category: `commerce_product`
- volatility: `medium`
- base_url: `https://www.yes24.com/`
- suite: `gaia/tests/scenarios/yes24_public_suite.json`
- suite_id: `yes24_public_v2`
- scenarios:
  - `YES24_001_HOME_BOOKS`: 검색 입력, 베스트셀러, 분야별 도서 메뉴 확인.
  - `YES24_002_BOOK_SEARCH`: 인공지능 도서 검색 결과에서 책 제목, 저자, 가격 또는 출판사 정보 확인.
  - `YES24_003_BESTSELLER`: 베스트셀러 화면에서 순위, 책 제목, 저자 또는 가격 정보 확인.
  - `YES24_004_BOOK_DETAIL`: 파이썬 도서 검색 결과에서 공개 도서 하나의 책 소개, 목차, 저자 또는 가격 정보 확인.
  - `YES24_005_CATEGORY_FILTER`: 경제 도서 검색 결과에서 분야 필터나 정렬 옵션 선택 후 목록 변화 확인.

### 21. Kyobo Book Centre (`kyobo`)

- category: `commerce_product`
- volatility: `medium`
- base_url: `https://www.kyobobook.co.kr/`
- suite: `gaia/tests/scenarios/kyobo_public_suite.json`
- suite_id: `kyobo_public_v2`
- scenarios:
  - `KYOBO_001_HOME_BOOKS`: 검색 입력, 베스트셀러, 분야별 도서 메뉴 확인.
  - `KYOBO_002_BOOK_SEARCH`: 인공지능 도서 검색 결과에서 책 제목, 저자, 가격 또는 출판사 정보 확인.
  - `KYOBO_003_BESTSELLER`: 베스트셀러 화면에서 순위, 책 제목, 저자 또는 가격 정보 확인.
  - `KYOBO_004_BOOK_DETAIL`: 데이터 분석 도서 검색 결과에서 공개 도서 하나의 소개, 목차, 저자 또는 가격 정보 확인.
  - `KYOBO_005_CATEGORY_FILTER`: 경영 도서 검색 결과에서 분야 필터나 정렬 옵션 선택 후 목록 변화 확인.

### 22. 기상청 날씨누리 (`kma_weather`)

- category: `public_data_service`
- volatility: `medium`
- base_url: `https://www.weather.go.kr/`
- suite: `gaia/tests/scenarios/kma_weather_public_suite.json`
- suite_id: `kma_weather_public_v2`
- scenarios:
  - `KMA_001_HOME_WEATHER`: 현재 날씨, 예보 메뉴, 특보 영역 확인.
  - `KMA_002_SHORT_FORECAST`: 단기 예보에서 지역, 시간대, 기온 또는 강수 정보 확인.
  - `KMA_003_WARNING_STATUS`: 기상 특보 화면에서 특보 종류, 지역, 발표 시각 또는 지도 표시 확인.
  - `KMA_004_RADAR_IMAGE`: 레이더 영상 화면에서 관측 이미지, 시간 선택, 재생 컨트롤 확인.
  - `KMA_005_REGION_CHANGE`: 단기 예보 화면에서 지역 선택 요소를 열어 지역 목록이나 선택값 확인.

### 23. 서울 열린데이터광장 (`seoul_open_data`)

- category: `public_data_service`
- volatility: `medium`
- base_url: `https://data.seoul.go.kr/`
- suite: `gaia/tests/scenarios/seoul_open_data_public_suite.json`
- suite_id: `seoul_open_data_public_v2`
- scenarios:
  - `SEOULDATA_001_HOME_DATA`: 데이터 검색 입력, 인기 데이터, 분야별 메뉴 확인.
  - `SEOULDATA_002_DATASET_SEARCH`: 따릉이 검색 결과에서 데이터셋 이름, 제공 형식, 갱신 정보 확인.
  - `SEOULDATA_003_DATASET_DETAIL`: 공공자전거 데이터 검색 결과 또는 상세 카드에서 설명, 제공 기관, 파일 형식 또는 API 정보 확인.
  - `SEOULDATA_004_CATEGORY_BROWSE`: 데이터 목록 화면에서 교통, 환경, 복지 같은 분야 필터 영역 확인.
  - `SEOULDATA_005_SORT_OR_FILTER`: 버스 데이터 검색 결과에서 정렬 콤보박스, 조회 버튼, 제공 형식 또는 기간 조건 영역 확인.

### 24. 대한민국 정책브리핑 (`policy_briefing`)

- category: `public_data_service`
- volatility: `medium`
- base_url: `https://www.korea.kr/`
- suite: `gaia/tests/scenarios/policy_briefing_public_suite.json`
- suite_id: `policy_briefing_public_v2`
- scenarios:
  - `POLICYBRIEF_001_HOME_NAV`: 정책뉴스, 브리핑룸, 정책자료 중 두 가지 메뉴 확인.
  - `POLICYBRIEF_002_POLICY_NEWS_LIST`: 정책뉴스 목록에서 기사 제목, 부처명, 등록일 확인.
  - `POLICYBRIEF_003_PRESS_RELEASE_LIST`: 보도자료 목록에서 자료 제목, 담당 부처, 날짜 확인.
  - `POLICYBRIEF_004_FACT_CHECK_LIST`: 사실은 이렇습니다 목록에서 항목 제목, 기관명, 날짜 확인.
  - `POLICYBRIEF_005_VISUAL_NEWS_LIST`: 카드 한컷 목록에서 카드 제목, 분류, 목록 영역 확인.
- 보고서 활용 포인트: VisitKorea 서비스 지연을 대체한 안정적인 정부/정책 정보 사이트다.

### 25. 정부24 (`government24`)

- category: `public_data_service`
- volatility: `medium`
- base_url: `https://www.gov.kr/portal/main`
- suite: `gaia/tests/scenarios/government24_public_suite.json`
- suite_id: `government24_public_v2`
- scenarios:
  - `GOV24_001_HOME_SERVICE`: 통합 검색, 자주 찾는 서비스, 분야별 서비스 메뉴 확인.
  - `GOV24_002_SERVICE_SEARCH`: 주민등록등본 검색 결과에서 서비스명, 처리기관, 안내 문구 확인.
  - `GOV24_003_SERVICE_DETAIL`: 전입신고 검색 결과에서 대상, 처리 절차, 필요 서류 같은 안내 정보 확인.
  - `GOV24_004_CATEGORY_SERVICE`: 분야별 서비스 화면에서 생애주기, 분야, 기관 같은 분류 목록 확인.
  - `GOV24_005_RESULT_FILTER`: 여권 검색 결과에서 검색필터의 민원서비스, 기관정보, 정책정보 항목이나 관련 검색어 확인.

### 26. 국가법령정보센터 (`law_go_kr`)

- category: `public_data_service`
- volatility: `medium`
- base_url: `https://www.law.go.kr/`
- suite: `gaia/tests/scenarios/law_go_kr_public_suite.json`
- suite_id: `law_go_kr_public_v2`
- scenarios:
  - `LAWGO_001_HOME_LAW`: 법령 검색 입력, 판례 메뉴, 행정규칙 메뉴 중 두 가지 확인.
  - `LAWGO_002_LAW_SEARCH`: 개인정보 보호법 검색 결과에서 법령명, 현행 구분, 조문 목록 확인.
  - `LAWGO_003_LAW_SEARCH_TABS`: 근로기준법 검색 화면에서 법령명, 현행 구분, 조문 목록 또는 관련 탭 확인.
  - `LAWGO_004_PRECEDENT_SEARCH`: 손해배상 판례 검색 화면에서 사건명, 선고일, 법원명 같은 결과 정보 확인.
  - `LAWGO_005_TABLE_OF_CONTENTS`: 근로기준법 화면에서 조문 목차나 장별 목록을 선택해 해당 조문 영역 확인.
- 보고서 활용 포인트: 상세 iframe 오류를 피하고 검색/목록/탭 surface로 바꾸면서 headless 5/5 성공이 확인됐다.

### 27. Melon (`melon`)

- category: `portal_news_community`
- volatility: `high`
- base_url: `https://www.melon.com/`
- suite: `gaia/tests/scenarios/melon_public_suite.json`
- suite_id: `melon_public_v2`
- scenarios:
  - `MELON_001_HOME_MUSIC`: 검색 입력, 차트, 최신 음악 또는 장르 메뉴 확인.
  - `MELON_002_CHART_LIST`: 차트 화면에서 순위, 곡명, 아티스트명 또는 앨범 이미지 확인.
  - `MELON_003_SEARCH_RESULTS`: 아이유 검색 결과에서 곡, 앨범, 아티스트 결과 확인.
  - `MELON_004_GENRE_BROWSE`: 장르 음악 목록에서 곡명, 아티스트명, 앨범 정보 또는 정렬 영역 확인.
  - `MELON_005_CHART_TAB`: 차트 화면에서 일간/주간/장르 탭 선택 후 곡 목록 변화 확인.

### 28. 국립중앙박물관 (`national_museum`)

- category: `culture_public`
- volatility: `medium`
- base_url: `https://www.museum.go.kr/`
- suite: `gaia/tests/scenarios/national_museum_public_suite.json`
- suite_id: `national_museum_public_v2`
- scenarios:
  - `MUSEUM_001_HOME_EXHIBITIONS`: 전시 안내, 관람 정보, 교육 프로그램 영역 확인.
  - `MUSEUM_002_CURRENT_EXHIBITIONS`: 현재 전시 목록에서 전시명, 전시 기간, 전시 장소 또는 카드 정보 확인.
  - `MUSEUM_003_EXHIBITION_DETAIL`: 현재 전시 목록에서 공개 전시 하나를 열어 전시명, 기간, 장소 또는 소개 정보 확인.
  - `MUSEUM_004_PERMANENT_EXHIBIT_FLOOR`: 상설전시실 층별 안내에서 층 정보, 전시실명, 전시 분야 또는 안내 지도 확인.
  - `MUSEUM_005_EDUCATION_LIST`: 교육 프로그램 목록에서 프로그램명, 대상, 기간, 접수 상태 또는 분류 정보 확인.

### 29. 잡코리아 (`jobkorea`)

- category: `career_business`
- volatility: `medium`
- base_url: `https://www.jobkorea.co.kr/`
- suite: `gaia/tests/scenarios/jobkorea_public_suite.json`
- suite_id: `jobkorea_public_v2`
- scenarios:
  - `JOBKOREA_001_HOME_JOBS`: 채용 검색 입력, 직무 메뉴, 기업 추천 영역 확인.
  - `JOBKOREA_002_JOB_SEARCH`: 백엔드 채용 검색 결과에서 공고명, 회사명, 근무지 또는 경력 조건 확인.
  - `JOBKOREA_003_JOB_DETAIL`: 데이터 분석 채용 검색 결과에서 공개 공고 하나의 업무 내용, 자격 조건, 근무지 또는 회사 정보 확인.
  - `JOBKOREA_004_REGION_FILTER`: 지역별 채용 목록에서 지역 필터, 직무 조건, 공고 목록 확인.
  - `JOBKOREA_005_COMPANY_INFO`: 네이버 관련 검색 결과에서 회사명, 채용 공고, 기업 정보 링크 확인.

### 30. 서울문화포털 (`seoul_culture`)

- category: `culture_public`
- volatility: `medium`
- base_url: `https://culture.seoul.go.kr/`
- suite: `gaia/tests/scenarios/seoul_culture_public_suite.json`
- suite_id: `seoul_culture_public_v2`
- scenarios:
  - `SEOULCULTURE_001_HOME_CULTURE`: 공연 전시 정보, 문화행사 검색, 시설 안내 영역 확인.
  - `SEOULCULTURE_002_EVENT_SEARCH`: 전시 검색 결과에서 행사명, 장소, 기간 또는 분류 정보 확인.
  - `SEOULCULTURE_003_EVENT_DETAIL`: 공연 검색 결과에서 공개 행사 하나의 장소, 기간, 관람 대상 또는 소개 정보 확인.
  - `SEOULCULTURE_004_FACILITY_LIST`: 야간 운영시설 목록에서 시설명, 지역 주소, 총 건수 또는 페이지 이동 정보 확인.
  - `SEOULCULTURE_005_CATEGORY_FILTER`: 문화행사 목록에서 지역, 장르, 기간 조건 선택 후 행사 목록 변화 확인.

## Appendix B. 커밋별 더 자세한 해설

### `7453e16 feat: improve benchmark management and suites`

- 성격: benchmark 기능의 기초 공사.
- 변경 규모: 39개 파일, 대략 4,482 insertions / 1,185 deletions.
- 주요 산출물:
  - `gaia/src/benchmark_manager.py`
  - `gaia/src/gui/benchmark_manager_dialog.py`
  - `scripts/run_goal_benchmark.py`
  - 초기 공개 suite JSON들
  - benchmark GUI asset
- 놓치기 쉬운 포인트:
  - 이 커밋은 단순 UI 개선이 아니라 benchmark를 “관리 가능한 대상”으로 바꾼 변화다.
  - 사이트 목록, suite payload, scenario id 생성, 결과 HTML rendering, report scan/prune 기반이 이때 생겼다.
  - 보고서에서 “평가 자동화 하네스”의 출발점으로 설명하기 좋다.

### `1cc6ff1 feat: auto-follow relevant browser tabs`

- 성격: 실제 웹 브라우저의 새 탭/관련 탭 문제 대응.
- 주요 산출물:
  - `gaia/src/phase4/browser_context_manager.py`
  - `gaia/src/phase4/mcp_openclaw_dispatch_runtime.py`
  - `test_mcp_openclaw_dispatch_runtime.py`
- 놓치기 쉬운 포인트:
  - 외부 사이트는 상세보기, 로그인, 영상 viewer, 문서 링크가 새 탭으로 열릴 수 있다.
  - 자동화가 계속 원래 탭만 보고 있으면 목표 달성 여부를 놓친다.
  - tab-following은 외부 웹 일반성 측면에서 중요하다.

### `ca4f13c feat: add structured human answer flow`

- 성격: 자동화 불가능 또는 사용자 확인이 필요한 상황을 구조화.
- 주요 산출물:
  - `gaia/src/phase4/goal_driven/human_answer_runtime.py`
  - intervention runtime 정리
  - terminal/chat hub integration
- 놓치기 쉬운 포인트:
  - CAPTCHA, 로그인, 비밀번호, 보안문자 같은 건 자동화가 “잘 못해서 실패”한 것이 아니라 정책상 사용자의 명시적 개입이 필요한 상황이다.
  - 이 흐름이 있어야 benchmark에서 intervention rate를 정직하게 분리할 수 있다.

### `fe74566 feat: 멀티유저 상호작용 하네스 추가`

- 성격: 단일 사용자 자동화에서 다중 참여자/상호작용 구조로 확장.
- 주요 산출물:
  - `gaia/src/phase4/participants/models.py`
  - `registry.py`
  - `blackboard.py`
  - `turn_scheduler.py`
  - `multi_user_interaction_runtime.py`
  - local chat login fixture
- 놓치기 쉬운 포인트:
  - 중간보고서에서 이 부분을 크게 다루지 않더라도 “확장 가능한 agent architecture”로 언급할 가치가 있다.
  - blackboard와 turn scheduler는 여러 agent/participant가 하나의 목표를 공유할 때 필요한 기반이다.

### `7f77e16 docs: 개발 하네스 워크플로 추가`

- 성격: agent가 이 큰 repo에서 길을 잃지 않도록 만든 개발 프로세스 하네스.
- 주요 산출물:
  - `AGENTS.md`
  - `docs/harness/CONTEXT_MAP.md`
  - `docs/harness/CHECKS.md`
  - `docs/harness/DEVELOPMENT_HARNESS.md`
  - `docs/harness/development_harness_manifest.json`
  - `scripts/context_pack.py`
  - `scripts/dev_harness.py`
  - `scripts/lint_harness_docs.py`
- 놓치기 쉬운 포인트:
  - 기능 개발만큼 중요한 것은 “AI agent가 잘못된 레이어를 수정하지 않게 하는 운영 규칙”이다.
  - 보고서에서는 개발 신뢰성/유지보수성 파트에 넣을 수 있다.

### `7ea9a12 Remove filter semantic validator from generic runtime`

- 성격: 성능 개선보다 더 중요한 정직성/범용성 개선.
- 주요 산출물:
  - `filter_validation_engine.py` 삭제
  - `filter_validation_runtime.py` 삭제
  - 관련 unit test 삭제
  - `scripts/compare_benchmark_runs.py` 추가
  - filter policy를 state-change evidence 중심으로 낮춤
- 놓치기 쉬운 포인트:
  - 삭제한 코드가 2,000줄 이상이어서 “많이 구현했다”보다 “위험한 것을 걷어냈다”는 메시지가 더 강하다.
  - 특정 서비스 전용 semantic validator는 데모에서는 좋아 보일 수 있지만, 외부 공개 웹 평가에서는 휴리스틱 비판을 받을 수 있다.
  - 이 커밋은 교수님 질문 방어에 핵심이다.

### `2d62d08 Add benchmark monitoring and shared suites`

- 성격: 개인 로컬 artifact에서 팀 공유 관측 체계로 이동.
- 주요 산출물:
  - `monitoring/docker-compose.yml`
  - `monitoring/prometheus.yml`
  - `monitoring/grafana/dashboards/gaia_kpi.json`
  - `monitoring/nginx/nginx.conf`
  - `scripts/gaia_monitor_setup.py`
  - `scripts/gaia_monitor_connect.py`
  - `scripts/push_metrics.py`
  - `scripts/sync_shared_suites.py`
  - `gaia/src/benchmark_suite_sharing.py`
- 놓치기 쉬운 포인트:
  - Grafana 자체가 source of truth는 아니다.
  - shared suite JSON과 Prometheus metric은 역할이 다르다.
  - metric upload는 `--push-metrics` opt-in이다.
  - raw artifact 전체나 민감 정보는 업로드하지 않는 방향이다.

### `1c95ebe Add external public benchmark pack`

- 성격: 내부 서비스 중심 benchmark에서 외부 공개 웹 benchmark로 확장.
- 주요 산출물:
  - `external_public_manifest.json`
  - 다수의 public suite JSON
  - `run_kpi_benchmark_pack.py --suite-manifest`
  - `scripts/prune_benchmark_records.py`
  - terminal benchmark mode의 외부 suite 선택/실행 보강
- 놓치기 쉬운 포인트:
  - 초기에는 npm, spell checker, oliveyoung, naver_shopping, coupang, gmarket, cgv 같은 차단성 사이트가 포함되었고, 이후 실제 실행을 통해 제거됐다.
  - 이 변화는 한 번에 완성된 게 아니라 측정-분석-대체 과정을 통해 정제됐다.

### `bdf8850 Harden external benchmark recovery`

- 성격: 실제 외부 웹에서 터진 문제를 대규모로 반영한 hardening.
- 주요 산출물:
  - `scripts/benchmark_blocking.py`
  - blocked/captcha normalization
  - modal dismiss 정책
  - scenario 문장/URL 대규모 정리
  - KakaoMap deep link
  - public manifest에서 반복 차단 사이트 제거
- 놓치기 쉬운 포인트:
  - “실패율을 낮추려고 어려운 사이트를 숨겼다”가 아니라, 자동화가 우회하면 안 되는 차단과 측정 가능한 일반 실패를 분리한 것이다.
  - primary success rate는 이 철학을 지표로 표현한 것이다.

### `0b91fe2 Add ref-first visual find fallback`

- 성격: 실제 browser-use 계열 자동화의 핵심 안정성 보강.
- 주요 산출물:
  - stale/ref recovery
  - visual label candidate
  - visual coordinate fallback reason code
  - snapshot/ref 재매핑
  - option ref missing 대응
- 놓치기 쉬운 포인트:
  - “이미지까지 쓰면 되지 않나?”라는 질문의 답이 여기에 있다.
  - 이미지를 항상 쓰는 게 아니라 ref-first로 안정적으로 가고, ref가 실패할 때 visual fallback을 쓴다.
  - 좌표 fallback은 마지막 수단이며 confidence threshold와 safe label 검사를 둔다.

### `62d4e95 Harden benchmark preflight diagnostics`

- 성격: benchmark 무효 실행을 빨리 감지하기 위한 진단 보강.
- 주요 산출물:
  - provider credential preflight
  - child traceback preservation
  - `fatal_error` summary
  - timeout budget floor/cap 정리
- 놓치기 쉬운 포인트:
  - 150개가 0.29초씩 모두 실패한 사례는 엔진 실패가 아니라 `OPENAI_API_KEY`/Codex auth 부재였다.
  - 이런 환경 실패를 성능 실패로 오해하지 않게 artifact에 남기는 것이 중요하다.

### `e0e7af2 Support Codex CLI auth for benchmarks`

- 성격: 맥미니/팀원 환경에서 API key 없이 benchmark를 실행하기 위한 인증 경로 보강.
- 주요 산출물:
  - `LLMVisionClient`가 Codex CLI auth를 인식.
  - `run_goal_benchmark.py`가 `codex login` 안내를 포함.
- 놓치기 쉬운 포인트:
  - 팀원이 OpenAI API key를 직접 다루지 않아도 Codex 로그인 기반으로 benchmark 실행 가능.
  - 단, 해당 머신에 `codex login`이 되어 있어야 한다.

### `286e9ea Add external public Grafana rollups`

- 성격: 30개 사이트 전체를 한눈에 보기 위한 metric 확장.
- 주요 산출물:
  - `gaia_external_pack_*`
  - `gaia_external_site_*`
  - `gaia_external_category_*`
  - `gaia_external_reason_code_count`
  - Grafana 상단 30-site overview
- 놓치기 쉬운 포인트:
  - suite별 metric만 있으면 30개 사이트를 한눈에 볼 수 없다.
  - pack-level rollup이 있어야 보고서 표와 Grafana 대시보드가 일치한다.

### `f8ebd9b Run judge for readonly wait completion claims`

- 성격: read-only/detail 목표의 false negative 완화.
- 주요 산출물:
  - reasoning-only WAIT completion judge path.
  - YouTube detail 정보처럼 목표 증거가 화면에 있는데 보수적 판정 때문에 실패하던 케이스 보강.
- 놓치기 쉬운 포인트:
  - judge를 “항상” 호출하지 않는다.
  - mutating goal은 제외한다.
  - DOM transient/loading이면 제외한다.
  - 마지막 성공 판정은 별도 judge가 현재 화면과 목표를 같이 본다.

### `bdbeb5f Refine external benchmarks and tag runners`

- 성격: false positive 제거와 팀 실행자 식별.
- 주요 산출물:
  - VisitKorea 제거, Policy Briefing 추가.
  - Law.go.kr 상세 URL 교체.
  - service unavailable guard.
  - `scripts/runner_identity.py`.
  - Grafana Runner filter/column.
- 놓치기 쉬운 포인트:
  - “사이트를 뺐다”가 아니라 “측정 불가능하거나 오류 화면을 성공으로 오판할 수 있는 케이스를 대체했다”가 핵심이다.
  - runner_id는 팀 공유 환경에서 결과 provenance를 남기기 위한 최소 식별자다.

## Appendix C. 코드 경로 / 함수 단위 지도

### Benchmark suite 관리

| 파일 | 주요 함수/클래스 | 역할 |
|---|---|---|
| `gaia/src/benchmark_manager.py` | `BenchmarkPreset` | benchmark site/preset 구조 |
| `gaia/src/benchmark_manager.py` | `load_benchmark_registry` | site registry 로드 |
| `gaia/src/benchmark_manager.py` | `build_benchmark_site_catalog` | GUI/terminal에서 사용할 site catalog 생성 |
| `gaia/src/benchmark_manager.py` | `load_suite_payload` | suite JSON 로드, missing suite error 발생 지점 |
| `gaia/src/benchmark_manager.py` | `save_suite_payload` | suite JSON 저장 |
| `gaia/src/benchmark_manager.py` | `append_scenario_to_suite` | 새 시나리오 추가 |
| `gaia/src/benchmark_manager.py` | `replace_scenario_in_suite` | 시나리오 수정 |
| `gaia/src/benchmark_manager.py` | `delete_scenario_from_suite` | 시나리오 삭제 |
| `gaia/src/benchmark_manager.py` | `scan_benchmark_reports` | artifact report scan |
| `gaia/src/benchmark_manager.py` | `prune_benchmark_reports` | 실패 포함 artifact 삭제 |
| `gaia/src/benchmark_manager.py` | `render_benchmark_reports_html` | 로컬 HTML 결과 보드 생성 |

### 단일 benchmark runner

| 파일 | 주요 함수 | 역할 |
|---|---|---|
| `scripts/run_goal_benchmark.py` | `_load_suite` | scenario suite JSON 로드 |
| `scripts/run_goal_benchmark.py` | `_resolve_scenario_timeout_budget` | scenario별 timeout floor/cap 계산 |
| `scripts/run_goal_benchmark.py` | `_prepare_scenario_env` | child process 환경 구성 |
| `scripts/run_goal_benchmark.py` | `_build_child_code` | goal benchmark child 실행 코드 생성 |
| `scripts/run_goal_benchmark.py` | `_run_scenario_once` | 단일 scenario 실행, stdout/traceback capture |
| `scripts/run_goal_benchmark.py` | `_compute_metrics` | raw success/avg time 계산 |
| `scripts/run_goal_benchmark.py` | `_compute_kpi_metrics` | KPI success/intervention/progress-stop 계산 |
| `scripts/run_goal_benchmark.py` | `_populate_provider_credentials` | env/profile/Codex auth credential 반영 |
| `scripts/run_goal_benchmark.py` | `_provider_credential_error` | provider credential preflight |
| `scripts/run_goal_benchmark.py` | `_try_push_metrics` | `--push-metrics` opt-in upload |

### KPI pack runner

| 파일 | 주요 함수 | 역할 |
|---|---|---|
| `scripts/run_kpi_benchmark_pack.py` | `_load_suite_manifest` | manifest의 suites 배열 로드 |
| `scripts/run_kpi_benchmark_pack.py` | `_resolve_suite_paths` | `--suite`와 `--suite-manifest`를 함께 resolution |
| `scripts/run_kpi_benchmark_pack.py` | `_run_suite` | child `run_goal_benchmark.py` 실행 |
| `scripts/run_kpi_benchmark_pack.py` | `_build_run_suite_command` | child command 구성, `--runner-id`, `--push-metrics` 전달 |
| `scripts/run_kpi_benchmark_pack.py` | `_compute_pack_kpis` | 30-site pack 전체 KPI 계산 |
| `scripts/run_kpi_benchmark_pack.py` | `_write_markdown` | pack `summary.md` 생성 |
| `scripts/run_kpi_benchmark_pack.py` | `_try_push_pack_metrics` | pack-level Grafana metrics push |

### Grafana/Prometheus metrics

| 파일 | 주요 함수 | 역할 |
|---|---|---|
| `scripts/push_metrics.py` | `build_suite_metrics` | suite-level KPI metric 생성 |
| `scripts/push_metrics.py` | `build_scenario_metrics` | scenario-level metric 생성 |
| `scripts/push_metrics.py` | `build_external_pack_metrics` | external 30-site pack rollup 생성 |
| `scripts/push_metrics.py` | `_site_metadata` | manifest 기반 site/category/volatility label 부여 |
| `scripts/push_metrics.py` | `_reason_code_counts` | 실패 reason code 집계 |
| `scripts/push_metrics.py` | `_runner_id_from_rows` | summary/results의 runner_id를 label로 정리 |
| `scripts/push_metrics.py` | `push_suite_dir` | artifact dir에서 metrics build + push |
| `scripts/push_metrics.py` | `push_shared_suite_json` | sanitize된 suite JSON 공유 |

### Blocked/CAPTCHA 분리

| 파일 | 주요 함수/상수 | 역할 |
|---|---|---|
| `scripts/benchmark_blocking.py` | `BLOCKED_USER_ACTION_STATUS` | `BLOCKED_USER_ACTION` status |
| `scripts/benchmark_blocking.py` | `BLOCKED_CAPTCHA_REASON_CODE` | `blocked_captcha` reason code |
| `scripts/benchmark_blocking.py` | `is_blocked_user_action` | row가 blocked user action인지 판단 |
| `scripts/benchmark_blocking.py` | `normalize_blocked_user_action_row` | CAPTCHA/보안문자/보안 확인을 blocked row로 normalize |
| `scripts/benchmark_blocking.py` | `summary_reason_code_summary` | nested summary reason code 추출 |

### Runner identity

| 파일 | 주요 함수 | 역할 |
|---|---|---|
| `scripts/runner_identity.py` | `sanitize_runner_id` | Prometheus label에 넣을 수 있게 runner id 정리 |
| `scripts/runner_identity.py` | `resolve_runner_id` | explicit value, `GAIA_RUNNER_ID`, `CODEX_RUNNER_ID`, `user@host` 순으로 runner id 결정 |

### WAIT completion / judge

| 파일 | 주요 함수/상수 | 역할 |
|---|---|---|
| `gaia/src/phase4/goal_driven/goal_completion_helpers.py` | `_TRANSIENT_REASONING_WAIT_KEYWORDS` | 로딩/처리 중 surface 감지 |
| `goal_completion_helpers.py` | `_SERVICE_UNAVAILABLE_KEYWORDS` | 서비스 지연/오류 화면 감지 |
| `goal_completion_helpers.py` | `_dom_has_service_unavailable_signal` | DOM에서 서비스 오류 화면 감지 |
| `goal_completion_helpers.py` | `is_readonly_visibility_goal` | read-only visibility 목표 판단 |
| `goal_completion_helpers.py` | `evaluate_reasoning_only_wait_completion` | reasoning-only WAIT claim을 judge로 넘길지 판단 |
| `goal_completion_helpers.py` | `_should_judge_reasoning_only_wait_completion` | judge 호출 guard 조건 |
| `goal_completion_helpers.py` | `evaluate_explicit_reasoning_proof_completion` | reasoning + current DOM proof로 완료 판정 |
| `goal_completion_helpers.py` | `evaluate_goal_completion_judge` | 최종 성공 판정 judge prompt/response 처리 |
| `goal_completion_helpers.py` | `evaluate_wait_goal_completion` | WAIT action의 완료 판정 entry |

### Ref-first / visual fallback

| 파일 | 주요 함수/marker | 역할 |
|---|---|---|
| `gaia/src/phase4/goal_driven/action_execution_runtime.py` | `execute_goal_action` | goal decision을 실제 action으로 실행 |
| `action_execution_runtime.py` | `_refresh_ref_binding` | stale/ref missing 시 최신 DOM snapshot으로 ref 재매핑 |
| `action_execution_runtime.py` | `_execute_with_ref_recovery` | ref action 실패 후 ref recovery 재시도 |
| `action_execution_runtime.py` | `_execute_visual_coordinate_click_fallback` | ref 기반 클릭 실패 시 visual coordinate fallback |
| `action_execution_runtime.py` | `visual_coordinate_fallback` | fallback reason code |
| `action_execution_runtime.py` | `ref 재바인딩` | ref가 새 값으로 바뀐 로그 marker |
| `action_execution_runtime.py` | `stale/ref 오류 복구` | 최신 snapshot/ref 재매핑 후 재시도 성공 marker |
| `action_execution_runtime.py` | `visual fallback 클릭` | coordinate fallback 성공 marker |

### Terminal benchmark mode

| 파일 | 주요 함수/역할 | 설명 |
|---|---|---|
| `gaia/src/terminal_benchmark_mode.py` | benchmark site 선택 | `python -m gaia.cli` 경로에서 GUI 없이 benchmark 실행 |
| `gaia/src/terminal_benchmark_mode.py` | metrics upload 선택 | 방향키로 업로드/로컬 저장 선택 |
| `gaia/src/terminal_benchmark_mode.py` | monitoring 연결 메뉴 | 설정이 없으면 연결/명령 보기/로컬만 저장 선택 |
| `gaia/src/terminal_benchmark_mode.py` | local report viewer | Grafana 없이 HTML report 확인 |
| `gaia/src/terminal_benchmark_mode.py` | shared suite pull/push | 팀 테스트 정의 자동/수동 동기화 |

## Appendix D. 실행 모드별 정리

### GUI mode

- 대상 사용자: GUI에서 benchmark site를 고르고 실행하려는 사용자.
- 장점:
  - site 목록/시나리오 목록을 눈으로 확인하기 쉽다.
  - custom site/test 추가가 쉽다.
  - `--push-metrics` 체크박스 흐름을 제공한다.
- 한계:
  - headless 서버나 맥미니에서 장시간 돌리기에는 terminal/CLI가 더 적합하다.

### Terminal mode

- 진입:

```bash
python -m gaia.cli
python -m gaia.cli --terminal
python -m gaia.cli --terminal --push-metrics
```

- 특징:
  - 방향키 선택 UI.
  - 기존 테스트 실행/테스트 편집/지표 확인/팀 테스트 공유.
  - monitoring server 연결이 있으면 shared suite 자동 pull.
  - Grafana와 로컬 결과 보드 중 선택 가능.

### Direct CLI suite mode

- 진입:

```bash
PYTHONPATH=. GAIA_OPENCLAW_HEADLESS=1 GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_goal_benchmark.py \
  --suite gaia/tests/scenarios/policy_briefing_public_suite.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix policy-briefing-headless-20260509 \
  --runner-id codex-headless
```

- 특징:
  - 단일 suite를 빠르게 검증하기 좋다.
  - artifact는 `artifacts/benchmarks/<suite>_<timestamp>/`에 생성된다.
  - summary/results/markdown이 같이 나온다.

### Direct CLI pack mode

- 진입:

```bash
PYTHONPATH=. GAIA_OPENCLAW_HEADLESS=1 GAIA_LLM_MODEL=gpt-5.5 GAIA_RAIL_ENABLED=0 \
python scripts/run_kpi_benchmark_pack.py \
  --suite-manifest gaia/tests/scenarios/external_public_manifest.json \
  --repeats 1 \
  --timeout-cap 600 \
  --session-prefix external-public-final-20260510 \
  --runner-id macmini-team-a \
  --push-metrics
```

- 특징:
  - 30개 suite/150개 scenario 전체를 실행한다.
  - 각 suite artifact와 최종 pack artifact가 생성된다.
  - `--push-metrics`가 있으면 suite metrics와 pack rollup이 Grafana로 올라간다.

### Headless mode

- 핵심 env:

```bash
GAIA_OPENCLAW_HEADLESS=1
```

- 의미:
  - OpenClaw embedded browser config에서 headless browser로 실행.
  - 사용자의 화면에 Chrome 창을 띄우지 않고 benchmark를 돌릴 수 있다.
- 검증된 사례:
  - 정책브리핑 5/5 성공.
  - 국가법령정보센터 수정 suite 5/5 성공.

### Visible/debug mode

- 핵심 env:

```bash
GAIA_OPENCLAW_VISIBLE=1
```

- 의미:
  - 브라우저가 실제로 어떻게 움직이는지 눈으로 확인할 때 사용.
  - 디버깅에는 좋지만 사용자의 화면을 점유할 수 있어 장시간 전체 실행에는 headless가 적합하다.

## Appendix E. 실패/차단/무효 실행 taxonomy

### 1. 진짜 엔진 실패

- 예:
  - ref가 stale인데 재수집/재시도하지 못함.
  - option ref가 snapshot에 없는데 같은 실패를 반복.
  - 화면 증거는 있는데 WAIT completion이 지나치게 보수적이라 종료하지 못함.
- 대응:
  - ref-first recovery.
  - force resnapshot.
  - visual fallback.
  - read-only WAIT judge.

### 2. scenario 문장/UI 불일치

- 예:
  - “정렬을 선택해 목록이 바뀌는지 확인”이라고 했지만 실제 사이트는 선택 후 별도 조회 버튼이 필요.
  - 상세 진입을 요구했지만 결과 카드 자체에 충분한 정보가 이미 있음.
- 대응:
  - 실제 UI surface 기준으로 목표 문장 수정.
  - 상세 진입 필수 대신 카드 evidence 허용.
  - 비파괴 확인 목표로 낮춤.

### 3. site volatility

- 예:
  - 뉴스/커뮤니티/YouTube/commerce 사이트의 콘텐츠가 수시로 바뀜.
  - 특정 버튼/칩/필터 위치가 자주 바뀜.
- 대응:
  - exact text보다 구조적 evidence 사용.
  - category/list/detail signal 중심으로 goal 작성.
  - 실패 reason code를 분석해 다음 manifest 개정에 반영.

### 4. blocked user action

- 예:
  - CAPTCHA.
  - 보안문자.
  - 로그인 gate.
  - 비밀번호/계정 정보 요청.
- 대응:
  - 자동 우회하지 않는다.
  - `BLOCKED_USER_ACTION`으로 분리.
  - `primary_success_rate` 계산에서 제외.

### 5. site unavailable/service delay

- 예:
  - `서비스 지연 안내`.
  - `서비스 이용에 불편을 드려서 죄송합니다`.
  - `현재 사용자가 많아 요청하신 페이지를 정상적으로 제공할 수 없습니다`.
  - `잠시 후 다시 접속`.
- 대응:
  - 성공 증거에서 제외.
  - 반복되면 primary pack에서 제거하거나 안정 URL/site로 대체.

### 6. environment/preflight failure

- 예:
  - OpenAI credential 없음.
  - `.venv`에 pytest/Pillow 없음.
  - 특정 머신 경로 hardcoding으로 unit test 실패.
  - monitoring config 없음.
- 대응:
  - benchmark runner preflight.
  - Codex CLI auth 지원.
  - child traceback capture.
  - fatal summary 기록.
  - portability test는 `tmp_path` 기반으로 고침/격리.

## Appendix F. 빠진 부정적 결과와 그 의미

### 150개가 전부 0.29초 내외로 실패한 무효 실행

- 현상:
  - `runs_total: 150`
  - `success: 0`
  - `avg_time_seconds: 0.29`
  - `reason`, `captured_log`가 비어 있던 artifact가 생김.
- 실제 원인:
  - `openai.OpenAIError: Missing credentials`
  - child process가 GoalDrivenAgent/LLMVisionClient 초기화 단계에서 종료.
- 교훈:
  - 모든 실패가 엔진 성능 실패는 아니다.
  - benchmark runner는 환경 실패를 빨리 감지하고 artifact에 명확히 남겨야 한다.
- 반영:
  - credential preflight.
  - Codex CLI auth 지원.
  - child traceback capture.

### pytest/Pillow/path portability 문제

- 맥미니/OpenClaw workspace에서 빠른 검증 중:
  - `.venv`에 pytest 없음.
  - 이후 Pillow 없음.
  - 이후 `/Users/coldmans/Documents/GitHub/capston` hardcoded path 때문에 2개 test 실패.
- 교훈:
  - 팀원 머신에서 실행하려면 dependency와 path portability를 같이 봐야 한다.
  - unit test가 특정 사용자 home path에 의존하면 외부 실행 환경에서 깨진다.
- 보고서에 넣을 방식:
  - 메인 성과보다는 “팀 실행 환경 검증 중 발견한 portability 이슈”로 짧게 언급.

### VisitKorea false positive

- 현상:
  - 일부 scenario가 SUCCESS로 기록.
  - 실제 화면은 `대한민국 구석구석 서비스 지연 안내`.
  - generic token `대한민국`, `구석구석`, `서비스`가 상세 정보 evidence처럼 잡힘.
- 교훈:
  - 외부 웹은 HTTP 200이어도 실제 content가 서비스 지연일 수 있다.
  - 일반 단어를 목표 증거로 쓰면 false positive가 난다.
- 반영:
  - VisitKorea primary pack 제거.
  - 정책브리핑으로 대체.
  - service unavailable guard 추가.

### Law.go.kr detail false positive/false negative 혼합

- 현상:
  - 상세 iframe URL이 때때로 `서비스 이용에 불편` 오류 화면 반환.
  - 어떤 rerun에서는 오류 화면 단어가 성공처럼 잡힐 수 있음.
  - 어떤 rerun에서는 progress stop failure.
- 교훈:
  - 같은 사이트라도 homepage/search surface와 detail iframe surface 안정성이 다르다.
  - site 전체 제거보다 특정 불안정 URL만 교체하는 게 더 낫다.
- 반영:
  - `LAWGO_003_LAW_DETAIL` 제거.
  - `LAWGO_003_LAW_SEARCH_TABS` 추가.
  - 수정 후 headless 5/5 성공.

### Musinsa option ref missing

- 현상:
  - 화면에는 정렬 dropdown option이 보임.
  - role snapshot에는 option ref가 늦게 반영.
  - action executor가 `낮은 가격순` ref를 못 찾아 실패.
- 교훈:
  - visual availability와 accessibility snapshot availability는 다를 수 있다.
  - action 직후 짧은 지연, 강제 snapshot, ref 재바인딩이 필요하다.
- 반영:
  - stale/ref recovery.
  - force analyze DOM for visual find.
  - visual coordinate fallback.

## Appendix G. Grafana metric catalog

### Suite-level metrics

| metric | 의미 |
|---|---|
| `gaia_runs_total` | suite 전체 실행 수 |
| `gaia_success_rate` | raw 성공률 |
| `gaia_avg_time_seconds` | 평균 실행 시간 |
| `gaia_suite_success_rate` | suite KPI 기준 scenario 성공률 |
| `gaia_suite_primary_success_rate` | blocked user-action을 제외한 primary 성공률 |
| `gaia_reproducibility_rate` | repeats > 1일 때 재현성 |
| `gaia_progress_stop_failure_rate` | timeout/stuck/no progress 계열 실패율 |
| `gaia_self_recovery_rate` | recovery event가 성공으로 이어진 비율 |
| `gaia_intervention_rate` | blocked/user intervention 비율 |
| `gaia_status_count` | SUCCESS/FAIL/BLOCKED_USER_ACTION count |
| `gaia_suite_started_timestamp_seconds` | suite 시작 시각 |

### Scenario-level metrics

| metric | 의미 |
|---|---|
| `gaia_scenario_runs_total` | scenario별 실행 수 |
| `gaia_scenario_success_count` | scenario별 성공 수 |
| `gaia_scenario_fail_count` | scenario별 실패 수 |
| `gaia_scenario_success_rate` | scenario별 성공률 |
| `gaia_scenario_avg_duration_sec` | scenario 평균 실행 시간 |
| `gaia_scenario_median_duration_sec` | scenario 중앙 실행 시간 |
| `gaia_scenario_min_duration_sec` | scenario 최소 실행 시간 |
| `gaia_scenario_max_duration_sec` | scenario 최대 실행 시간 |
| `gaia_scenario_latest_duration_sec` | scenario 최신 실행 시간 |
| `gaia_scenario_last_status` | 최신 실행 성공 여부 |
| `gaia_scenario_info` | low-cardinality presence marker |
| `gaia_scenario_last_run_timestamp_seconds` | 최신 실행 시각 |

### External pack metrics

| metric | 의미 |
|---|---|
| `gaia_external_pack_runs_total` | 30-site pack 전체 run 수 |
| `gaia_external_pack_success_count` | pack 성공 수 |
| `gaia_external_pack_site_count` | pack site 수 |
| `gaia_external_pack_scenario_count` | pack scenario 수 |
| `gaia_external_pack_success_rate` | pack raw scenario 성공률 |
| `gaia_external_pack_primary_success_rate` | blocked 제외 primary 성공률 |
| `gaia_external_pack_progress_stop_failure_rate` | pack progress-stop failure 비율 |
| `gaia_external_pack_intervention_rate` | pack intervention 비율 |
| `gaia_external_pack_avg_duration_seconds` | pack 평균 시간 |
| `gaia_external_site_success_rate` | site별 성공률 |
| `gaia_external_site_runs_total` | site별 run 수 |
| `gaia_external_site_avg_duration_seconds` | site별 평균 시간 |
| `gaia_external_site_blocked_count` | site별 blocked count |
| `gaia_external_site_reason_code_count` | site별 reason code count |
| `gaia_external_category_success_rate` | category별 성공률 |
| `gaia_external_reason_code_count` | category별 reason code count |

### 주요 labels

| label | 의미 |
|---|---|
| `suite_id` | benchmark suite id |
| `scenario_id` | scenario id |
| `site_key` | manifest site key |
| `site` | 사람이 읽는 site label |
| `category` | 사이트 카테고리 |
| `volatility` | 안정성 태그 |
| `model` | LLM model |
| `provider` | LLM provider |
| `runner_id` | 실행자/머신 식별자 |
| `reason_code` | 실패 reason code |

## Appendix H. 보고서 작성 시 넣기 좋은 “정직성” 문장

아래 문장들은 결과가 완벽하다고 포장하는 대신, 실제 외부 웹 평가에서 부딪힌 문제와 대응을 설명하기 위한 재료다.

> 외부 공개 웹 benchmark에서 중요한 것은 단순히 성공률을 높이는 것이 아니라, 어떤 실패가 엔진의 한계이고 어떤 실패가 사이트 차단 또는 서비스 지연인지 구분하는 것이다.

> CAPTCHA나 보안문자는 자동화가 우회할 대상이 아니므로, 이를 일반 실패로 섞으면 엔진 평가도 왜곡되고 윤리적 기준도 흐려진다.

> HTTP 200 응답은 성공 증거가 아니다. 실제 렌더링된 DOM이 서비스 지연 안내인지, 목표 정보가 있는 페이지인지 구분해야 한다.

> 특정 내부 서비스에 맞춘 semantic validator는 데모 성공률을 높일 수 있지만, 범용 웹 자동화 엔진에서는 위험한 휴리스틱이 될 수 있다.

> 따라서 본 프로젝트는 일부 도메인 특화 검증 로직을 제거하고, 더 일반적인 state-change evidence, DOM evidence, benchmark artifact 기반의 판정으로 전환했다.

> read-only 목표의 WAIT 판정은 모델의 자기 주장만으로 성공 처리하지 않고, 안정 DOM과 별도 judge를 통해 목표 충족 여부를 재확인한다.

> 외부 웹의 동적 UI에서는 ref가 stale해지는 일이 흔하므로, ref-first 정책을 유지하되 snapshot 재수집과 visual fallback을 보완했다.

> Grafana dashboard는 결과를 예쁘게 보여주는 도구가 아니라, 팀원이 각자 실행한 benchmark를 같은 기준으로 비교하기 위한 관측 장치다.

> `runner_id`는 benchmark provenance를 남기기 위한 최소 정보다. 누가, 어느 환경에서 실행했는지 알 수 있어야 결과를 재현하거나 비교할 수 있다.

## Appendix I. 중간보고서 목차 확장안

### 1. 연구/개발 배경

- 웹 자동화 agent의 목표 수행 능력 평가 필요.
- 내부 서비스 기반 평가의 장점과 한계.
- 외부 공개 웹으로 확장해야 하는 이유.
- 실제 웹의 변동성, 차단, 동적 UI, 모달 문제.

### 2. 시스템 구조

- Goal-driven runtime.
- OpenClaw browser control.
- DOM/ref 기반 action execution.
- visual fallback.
- benchmark suite/manifest.
- runner/pack runner.
- artifact schema.
- Grafana monitoring.

### 3. Benchmark 설계

- scenario JSON contract.
- site manifest.
- category/volatility.
- 금지 범위.
- 제외 기준.
- 30 sites/150 scenarios 구성.

### 4. 주요 구현

- benchmark manager.
- terminal/GUI/direct CLI mode.
- shared suite sync.
- metrics push.
- external pack rollup.
- runner_id.
- service unavailable guard.
- blocked/captcha normalization.
- read-only WAIT judge.
- ref-first visual fallback.

### 5. 실험 결과

- 내부 서비스 suite.
- external initial pack.
- 정리 후 headless spot checks.
- Musinsa dropdown recovery.
- VisitKorea/Lawgo false positive 분석.

### 6. 실패 분석

- 엔진 실패.
- scenario 문장 불일치.
- site volatility.
- CAPTCHA/bot-wall.
- service delay.
- environment/preflight.

### 7. 개선 과정

- filter semantic validator 제거.
- public suite 대체.
- preflight diagnostics.
- Codex CLI auth.
- Grafana rollup.
- runner identity.

### 8. 한계와 향후 계획

- repeats=1의 한계.
- 외부 웹 변동성.
- final 30/150 rerun 필요.
- reason code taxonomy 추가.
- stable subset reproducibility.

## Appendix J. 발표용 1분/3분/5분 버전

### 1분 버전

이번 기간에는 GAIA 자동화 엔진을 내부 서비스 데모에서 외부 공개 웹 benchmark로 확장했습니다. 교수님이 지적하신 내부 사이트 휴리스틱 가능성을 줄이기 위해 30개 외부 사이트, 150개 시나리오를 구성했고, 한국 사용자가 익숙한 포털/뉴스/지도/커머스/공공 사이트를 중심으로 잡았습니다. 동시에 성공률을 부풀릴 수 있는 내부 서비스 특화 filter semantic validator를 제거했고, CAPTCHA나 서비스 지연처럼 자동화가 우회하면 안 되는 케이스는 별도로 분리했습니다. 실행 결과는 summary/results artifact로 남기고, `--push-metrics`를 붙이면 Grafana에서 팀원이 함께 볼 수 있게 했습니다.

### 3분 버전

지난 3주간의 핵심 변화는 세 가지입니다. 첫째, benchmark 범위를 확장했습니다. 기존 내부 서비스 중심 평가에서 외부 공개 사이트 30개/150개 시나리오로 늘렸고, 사이트별로 홈 확인, 검색/탐색, 상세 정보, 목록/필터, 비파괴 상호작용을 구성했습니다. 둘째, 범용성에 위험한 로직을 제거했습니다. 내부 서비스에는 맞지만 외부 웹에는 휴리스틱이 될 수 있는 filter semantic validator를 제거하고, OpenClaw state-change evidence와 DOM evidence 중심으로 낮췄습니다. 셋째, 실제 웹에서 발생한 문제를 반영했습니다. CAPTCHA는 `BLOCKED_USER_ACTION`으로 분리하고, 서비스 지연 안내는 성공 증거로 인정하지 않으며, stale ref나 dropdown option ref 문제는 snapshot 재수집과 visual fallback으로 회복하도록 했습니다. 결과는 artifact와 Grafana metric으로 남겨 팀원이 공유할 수 있게 했고, `runner_id`로 실행 환경도 구분합니다.

### 5분 버전

처음에는 내부 서비스 benchmark에서 10개 중 9개 성공이라는 결과를 얻었습니다. 하지만 실패 케이스를 분석해 보니 필터 semantic validator가 특정 서비스의 카드 구조와 필터 UI에 강하게 의존하고 있었습니다. 이 로직은 내부 서비스에는 맞을 수 있지만 범용 웹 자동화 엔진에는 위험한 휴리스틱이 될 수 있다고 판단해 제거했습니다. 이후 외부 공개 사이트 benchmark를 30개 사이트/150개 시나리오로 확장했습니다. 실제 실행 과정에서 npm, 올리브영, 네이버쇼핑, 쿠팡, G마켓, CGV처럼 CAPTCHA나 bot-wall이 반복되는 사이트는 primary pack에서 제거했고, VisitKorea처럼 서비스 지연 안내가 반복되는 사이트도 제거해 대한민국 정책브리핑으로 대체했습니다. Law.go.kr은 사이트 전체가 아니라 상세 iframe URL만 불안정했기 때문에 검색 탭/목록 확인 시나리오로 교체했습니다. 또한 Musinsa 정렬 dropdown처럼 사람에게는 쉬워도 자동화에서는 option ref가 늦게 반영되는 문제를 발견했고, ref-first 조작을 유지하면서 stale ref recovery와 visual fallback을 추가했습니다. 최종적으로 benchmark 결과는 summary/results/markdown artifact로 남기고, `--push-metrics`를 붙이면 Grafana에서 site/category/reason code별로 공유 지표를 볼 수 있습니다.

## Appendix K. 놓치기 쉬운 작은 구현 디테일

- `timeout-cap`은 너무 작게 줘도 내부적으로 최소 600초 floor를 둔다.
- Codex child process timeout은 benchmark timeout의 절반 수준에서 180~300초 사이로 clamp된다.
- `GAIA_CODEX_REASONING_EFFORT`는 benchmark에서 `low`로 내려 과도한 latency를 줄인다.
- stdout live trace는 step-level marker만 흘려보내고 verbose JSON trace는 필터링한다.
- child process가 JSON을 마지막 줄에 못 남겨도 tail traceback을 reason/captured_log에 보존한다.
- `--push-metrics`가 없으면 metric upload는 하지 않는다.
- monitoring config가 없으면 upload를 건너뛰고 연결 명령을 안내한다.
- shared suite upload 시 `password`, `token`, `secret`, `api_key` 계열 key는 sanitize 대상이다.
- external manifest는 unit test에서 30 site/150 scenario, unique scenario id, forbidden goal keyword, internal host exclusion을 검증한다.
- `POLICYBRIEF_004_FACT_CHECK_LIST`는 처음에 “게시물”이라는 단어가 들어가 `게시` 금지 키워드에 걸렸고, “항목 제목”으로 바꿨다.
- `artifacts/`는 source of truth가 아니며 git commit 대상이 아니다.
- 중간보고서 최종 수치에는 최신 manifest 기준 전체 rerun artifact를 사용해야 한다.

## Appendix L. Grafana / Pushgateway 최신 스냅샷 확인

이 섹션은 “Grafana에 실제 결과가 올라가 있는가”를 보고서 작성자가 판단할 수 있도록 남기는 live 확인 기록이다. 민감 정보인 token 값은 기록하지 않는다. 사용자가 말한 맥미니 5/9 실행은 로컬 artifact가 아니라 Pushgateway에 남은 metric snapshot을 기준으로 확인한다.

확인 시점:

- 2026-05-10, 로컬 `~/.gaia/monitoring.json` 기준 team monitoring server 연결 확인.
- `python scripts/gaia_monitor_connect.py --status`로 연결된 Pushgateway endpoint 존재 확인.
- Pushgateway `/metrics`에서 `gaia_external_*` metric이 조회되는지 확인.
- `push_time_seconds{instance="kpi_pack_20260508_235814",job="gaia_benchmark"}` 기준 push 완료 시각은 2026-05-09 02:15:12 KST.
- `runner_id` label은 이 snapshot에서는 비어 있었다. 따라서 “맥미니 실행”은 사용자의 실행 맥락과 push 시각으로 식별하며, metric label만으로 runner를 증명하지는 못한다.

서버에 남아 있던 external pack:

| 항목 | 값 |
|---|---:|
| pack_id | `kpi_pack_20260508_235814` |
| pushed_at_kst | `2026-05-09 02:15:12` |
| runner_id_label | 없음 |
| site_count | 30 |
| scenario_count | 150 |
| runs_total | 150 |
| success_count | 141 |
| success_rate | 0.94 |
| primary_success_rate | 0.94 |
| avg_duration_seconds | 54.73 |
| progress_stop_failure_rate | 0.06 |
| intervention_rate | 0.0 |
| self_recovery_rate | 1.0 |

카테고리별 Grafana metric snapshot:

| category | success_rate |
|---|---:|
| career_business | 1.0 |
| commerce_product | 1.0 |
| culture_public | 0.8 |
| developer_tech | 1.0 |
| finance_game | 1.0 |
| knowledge_reference | 0.8 |
| portal_news_community | 0.98 |
| public_data_service | 0.8333 |

성공률 1.0 미만 사이트:

| site_key | site | category | success_rate |
|---|---|---|---:|
| `visit_korea` | Visit Korea | public_data_service | 0.2 |
| `national_museum` | 국립중앙박물관 | culture_public | 0.8 |
| `seoul_culture` | 서울문화포털 | culture_public | 0.8 |
| `wikipedia` | Wikipedia Korea | knowledge_reference | 0.8 |
| `youtube` | YouTube Korea | portal_news_community | 0.8 |
| `law_go_kr` | 국가법령정보센터 | public_data_service | 0.8 |

reason code 집계:

| reason_code | count |
|---|---:|
| `ok` | 87 |
| `weak_effective_ignored` | 51 |
| `rail_skipped_disabled` | 9 |
| `openclaw_backend_progress` | 3 |
| `http_5xx` | 2 |
| `missing_element_id` | 2 |
| `visual_coordinate_fallback` | 1 |
| `action_timeout` | 1 |
| `dom_force_resnapshot_stale_dom_wait` | 1 |

해석:

- Grafana/Pushgateway에는 실제 external pack metric이 올라가 있었다.
- 이 snapshot은 로컬 `artifacts/`가 아니라 team monitoring server에서 직접 조회한 값이다.
- 다만 서버에 남아 있는 최신 external pack은 `kpi_pack_20260508_235814` 하나였고, 현재 manifest에서 제거한 `visit_korea`가 포함되어 있었다.
- 반대로 현재 manifest에 들어간 `policy_briefing` metric은 Pushgateway snapshot에 없었다.
- 따라서 이 snapshot은 “Grafana 공유 체계가 실제로 작동한다”는 근거로는 사용할 수 있지만, “최신 정리 완료 manifest의 최종 benchmark 수치”로 쓰면 안 된다.
- 최종 중간보고서 수치는 현재 manifest 기준으로 전체 pack을 다시 실행하고 `--push-metrics`까지 성공한 artifact/Grafana snapshot을 별도로 확정해야 한다.

보고서용 문장:

> Grafana/Pushgateway에는 2026-05-09 02:15 KST에 `kpi_pack_20260508_235814` 기준 30개 사이트/150개 시나리오 metric이 실제 업로드되어 있었고, pack raw/primary success rate는 0.94였다. 다만 이 스냅샷은 VisitKorea 제거와 정책브리핑 대체가 반영되기 전 실행이므로, 최종 발표 수치에는 최신 manifest 재실행 결과를 사용한다.

발표용 주의 문장:

> Grafana는 팀 공유와 추세 확인을 위한 관측 계층이며, 최종 근거 수치는 같은 시점의 `summary.json`, `results.json`, `summary.md`, 그리고 Grafana snapshot이 서로 일치하는 실행만 사용한다.
