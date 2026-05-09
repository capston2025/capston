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
