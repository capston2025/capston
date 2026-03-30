# GAIA 전체 로직 구조 설명서 


---

## 0. 이 문서 한 줄 요약

GAIA는 **사람의 목표 문장 또는 기획서(PRD)**를 받아서, **LLM(두뇌)**이 다음 행동을 결정하고, **Playwright(손과 눈)**가 브라우저를 실제 조작하고 관찰하면서, **snapshot/ref 기반 계약**으로 안전하게 실행·검증·복구하는 테스트 에이전트 시스템이다.

처음 읽는 사람은 이 문서를 먼저 읽고, `dom_ref`, `ref_id`, `selector`, `semantic container` 같은 구현 세부사항이 필요해질 때 기술 부록 [GAIA_TECHNICAL_APPENDIX_KR.md](/Users/coldmans/Documents/GitHub/capston/gaia/docs/team/GAIA_TECHNICAL_APPENDIX_KR.md) 로 내려가는 순서를 권장한다.

---

## 1. 먼저, 정말 쉬운 비유로 이해하기

GAIA를 사람으로 비유하면 아래와 같다.

1. 사용자가 "학점 필터링 검증해줘" 또는 "이 기획서 기준으로 테스트해줘"라고 말한다.
2. LLM은 "그럼 어떤 목표를 먼저 검증해야 하는지"를 **계획**한다.
3. Playwright는 실제 브라우저에서 버튼 클릭, 입력, 선택, 스크롤을 수행한다.
4. MCP Host는 "진짜로 상태가 변했는지"를 **증거 기반으로 검사**한다.
5. 바뀌었으면 성공, 안 바뀌었으면 재시도/대체 액션/사용자 개입 요청을 결정한다.

핵심은 단순 클릭 봇이 아니라,

- 클릭 전에 **LLM이 현재 화면의 요소를 `element_id`로 고르고, 실행 직전에 이를 `snapshot_id + ref_id`로 확정**,
- 클릭 후 **정말 상태가 변했는지 검증(state_change/reason_code)**,
- 실패하면 **복구 체인** 또는 **사용자 개입(/resume, steering)**

을 한다는 점이다.

---

## 2. 핵심 용어 사전 


### 2.1 시스템 용어

- `LLM`
  - 사람처럼 문장을 읽고 다음 행동을 결정하는 모델.
  - GAIA에서 "어떤 액션을 할지" 또는 "어떤 테스트 목표를 만들지"를 JSON으로 응답한다.

- `Playwright`
  - 실제 브라우저 자동화 엔진.
  - 클릭, 입력, 스크롤, 스냅샷, 스크린샷을 수행한다.

- `MCP Host`
  - GAIA의 브라우저 실행 서버(백엔드).
  - `/execute` API로 액션 요청을 받아 Playwright를 호출한다.

- `GoalDrivenAgent`
  - 목표 달성 중심 에이전트.
  - 목표를 보고 단계별 액션을 계획/실행/검증한다.

- `ExploratoryAgent`
  - 탐색 중심 에이전트.
  - 페이지를 넓게 돌아다니며 요소를 점검한다.

- `Validation Rail`
  - 메인 실행이 끝난 뒤, 별도 경로(Playwright rail)로 결과를 다시 확인하는 검증 레일.
  - "에이전트가 성공이라고 말한 결과"를 독립적으로 재검증한다.

### 2.2 Snapshot/Ref 용어

- `snapshot`
  - 특정 시점의 페이지 상태 캡처.
  - 요소 목록, URL, DOM hash, 증거를 포함한다.

- `snapshot_id`
  - 스냅샷의 고유 ID.
  - "이 시점의 화면"을 정확히 가리키는 키.

- `ref`
  - 스냅샷 안에서 특정 요소를 가리키는 참조값.

- `ref_id`
  - 요소 고유 참조 ID.
  - 예: `t0-f0-e14` (탭/프레임/요소 인덱스)

- `Ref-only 정책`
  - 임의 CSS selector 문자열로 막 클릭하지 않고,
  - `snapshot_id + ref_id`가 있어야 액션 수행.
  - 재현성과 디버깅성이 올라간다.

### 2.3 실행/검증 용어

- `state_change`
  - 액션 전/후에 무엇이 바뀌었는지 기록한 구조.

- `reason_code`
  - 결과를 짧은 코드로 분류한 값.
  - 예: `ok`, `not_actionable`, `no_state_change`, `stale_snapshot`, `modal_not_open`

- `attempt_logs`
  - 한 액션 안에서 시도한 경로를 단계별로 기록한 로그.

- `effective`
  - "액션이 실질적으로 효과가 있었는가" 판정값.

- `semantic validation`
  - 단순 DOM 변화가 아니라 **의미가 맞는지** 검사하는 검증.
  - 예: 학점 필터를 `1학점`으로 바꿨으면 결과 과목도 실제로 `1학점`인지 본다.

- `final_status`
  - 최종 결과 상태.
  - 예: `SUCCESS`, `FAIL`, `BLOCKED_USER_ACTION`

### 2.4 운영/개입 용어

- `stale`
  - 오래된 참조/스냅샷. 현재 화면과 안 맞는 상태.

- `fallback`
  - 기본 시도 실패 시 대체 전략.
  - 예: close 버튼 ref 실패 시 backdrop 클릭, modal corner 클릭 등.

- `loop guard`
  - 같은 행동 반복을 끊는 안전장치.

- `BLOCKED_USER_ACTION`
  - 로그인, CAPTCHA, 2FA, 권한 허용처럼 사용자가 직접 해야 하는 일이 남은 상태.

- `resume`
  - 사용자가 필요한 처리를 끝낸 뒤 실행을 이어가게 하는 재개 명령.

- `steering`
  - 사용자가 "이 버튼은 누르지 마", "이 목표만 우선해"처럼 자연어로 실행 제약을 넣는 것.

### 2.5 기획서(PRD) 용어

- `PRD`
  - Product Requirements Document. 기획서/요구사항 문서.

- `PRD Bundle`
  - 원본 기획서를 바로 매번 다시 읽지 않고,
  - 한 번 정규화해서 저장한 **재사용 가능한 JSON 번들**.

- `generated_goals`
  - PRD Bundle에서 생성된 테스트 목표 목록.

---

핵심 포인트:

1. 사용자 채널은 여러 개지만 실행 엔진은 하나다.
2. LLM은 "결정"만 하고, 실제 클릭/입력은 Playwright가 한다.
3. 결과는 반드시 `reason_code`, `validation_summary`, `reason_code_summary` 같은 구조화 결과로 남는다.
4. 메인 실행과 별도로 `Validation Rail`이 사후 검증을 수행할 수 있다.

---

## 3. 실행 모드는 몇 가지인가?

현재 GAIA는 3가지 실행 모드를 갖는다.

1. `빠른 목표 실행`
   - 사용자가 자연어로 목표 한 줄을 준다.
   - 예: "학점 필터링 검증해줘"

2. `완전 자율`
   - 에이전트가 페이지를 넓게 탐색하며 테스트 포인트를 찾는다.
   - 주로 exploratory/coverage 측정에 사용.

3. `기획서/번들 실행`
   - 원본 PRD 또는 저장된 PRD Bundle JSON에서 생성된 목표들을 실행한다.

그리고 인터페이스는 3가지다.

1. `CLI`
2. `GUI`
3. `Telegram`

핵심은 **입력 채널은 다르지만, 아래쪽 에이전트/브라우저 엔진은 공용**이라는 점이다.

---

## 4. "Playwright는 LLM이랑 어떻게 연결돼요?" (중요)

직접 연결이 아니라 **에이전트가 중간 다리** 역할을 한다.

### 4.1 연결 구조

1. 에이전트가 MCP Host에 DOM snapshot 수집을 요청한다.
2. MCP Host가 Playwright로 DOM/프레임 상태를 수집하고 snapshot/ref를 구조화한다.
3. 에이전트가 DOM 요약 텍스트(필요 시 스크린샷 포함)를 LLM에 보낸다.
4. LLM은 JSON 액션(`click/select/fill/wait/scroll`) 또는 목표/검증 판단을 돌려준다.
5. 에이전트는 그 액션을 MCP Host로 보내 Playwright로 실행한다.
6. 실행 결과(`effective`, `reason_code`, `state_change`)를 다시 에이전트가 읽는다.
7. 다음 스텝 의사결정 또는 최종 판정에 반영한다.

### 4.2 핵심 원칙

- LLM은 "생각/결정" 담당.
- Playwright는 "손발" 담당.
- MCP Host는 "안전한 실행기" 담당.
- Validation Rail은 "독립 검증자" 담당.

---

## 5. DOM은 어떻게 추출하고 구조화하나요?

DOM은 `mcp_host.py`의 `analyze_page_elements()`에서 수집한다.

### 5.1 수집 방식

1. 메인 프레임 + iframe 순회.
2. shadow DOM까지 가능한 범위에서 수집.
3. 액션 후보(button, a, input, select 등)를 우선 수집하되, 검증에 필요한 semantic/text 신호(row, cell, listitem, aria-label, title, data-testid, visible text)도 함께 추출.
4. 표시/활성/가림 여부(actionability)를 함께 계산.

즉, "클릭 가능한 것만 본다"기보다는 "액션 후보를 우선 보면서 검증용 텍스트/구조 신호도 같이 본다"에 가깝다.
따라서 단순 정보 표시 텍스트도 DOM/semantic 신호로 잡히는 범위는 검증할 수 있고, 순수 시각 렌더링 결과는 스크린샷 기반 판단이나 semantic validation/validation rail로 보강한다.

### 5.2 select 옵션 처리

`<select>`는 옵션 목록을 같이 수집한다.

- `attributes['options'] = [{value, text}, ...]`
- `attributes['selected_value']`

이 정보가 있어야 LLM이 "무슨 옵션이 가능한지"를 보고 정확한 `select` 액션을 만들 수 있다.

### 5.3 ref 부여

스냅샷 생성 시 각 요소에 `ref_id`를 부여한다.

에이전트는 이 스냅샷을 받은 뒤, LLM에게 보여줄 때는 화면용 번호인 `element_id`를 다시 붙여서 사용한다.

- 예: `t0-f0-e14`
- 같은 스냅샷에서는 안정적인 참조가 된다.
- LLM 선택 단계는 주로 `element_id` 기준이다.
- 실제 실행 계약은 `snapshot_id + ref_id` 기준이다.

### 5.4 정제

수집 후 중복 제거/신호 점수 기반 트리밍을 하여, LLM 프롬프트 길이를 관리한다.

---

## 6. LLM에게 정확히 무엇을 보내나요?

`GoalDrivenAgent._decide_next_action()` 기준으로 보면, 대략 아래 묶음을 전달한다.

1. 목표(goal)와 성공 조건(success criteria)
2. 현재 phase 정보(COLLECT/APPLY/VERIFY 등)
3. 최근 액션 이력(action history)
4. 최근 실패/피드백(feedback, retry 힌트)
5. DOM 요소 목록(요약된 구조, `element_id` 기반 화면 번호)
6. 필요 시 스크린샷(vision 경로)
7. 응답 포맷 제약(JSON schema 유사 규칙)

즉, "현재 상황 + 과거 기록 + 출력 형식"을 같이 준다.

중요한 점은, 이 단계에서 LLM이 직접 `ref_id`를 고르는 것이 아니라는 점이다.
LLM은 현재 화면의 요소 요약 목록에서 `element_id`를 고르고, 에이전트가 실행 직전에 이를 현재 `snapshot_id` 안의 `ref_id`로 해석한다.

### 6.1 왜 `element_id`를 보여주고 `ref_id`는 나중에 푸나요?

여기서 식별자는 두 층으로 나뉜다.

1. `element_id`
   - LLM이 현재 화면에서 고르는 로컬 번호
   - 예: `[12] 로그인 버튼`
   - 사람이 읽고 모델이 고르기 쉬운 짧은 번호다.

2. `ref_id`
   - MCP Host가 snapshot 생성 시 붙이는 실행용 참조값
   - 예: `t0-f0-e12`
   - 실제 브라우저 액션은 `snapshot_id + ref_id` 계약으로만 수행한다.

즉, `element_id`는 선택용 번호표이고, `ref_id`는 실행용 관리번호다.

### 6.2 실제 흐름은 어떻게 이어지나요?

1. MCP Host가 Playwright로 DOM을 수집하고 `snapshot_id`, `ref_id`들을 만든다.
2. 에이전트가 이 목록을 받아 LLM에게는 `[element_id] 텍스트/태그/속성` 형태로 요약해서 보여준다.
3. LLM은 `{"action":"click","element_id":12}`처럼 응답한다.
4. 에이전트가 내부 매핑에서 `element_id -> ref_id`를 찾는다.
5. 실제 실행은 `browser_act(session_id, snapshot_id, ref_id, action)` 형태로 MCP Host에 요청한다.

### 6.3 왜 바로 `ref_id`를 LLM에게 고르게 하지 않나요?

1. `ref_id`는 실행 계약에는 좋지만, LLM이 읽고 선택하기에는 의미가 약하다.
   - `t0-f0-e12`만 보고는 어떤 요소인지 직관적으로 파악하기 어렵다.

2. `element_id`는 현재 화면에서만 쓰는 짧은 번호라 프롬프트가 단순해진다.
   - 모델은 번호와 텍스트 설명만 보고 빠르게 고를 수 있다.

3. 실행 단계는 Host가 검증 가능한 식별자로 분리하는 편이 안전하다.
   - `snapshot_id + ref_id`가 있어야 stale 검증, 프레임/탭 범위 검증, 실행 재현성이 가능하다.

`dom_ref`, `ref_id`, `snapshot_id`, selector의 역할 차이와 Host 내부의 locator 복구 순서는 [GAIA_TECHNICAL_APPENDIX_KR.md](./GAIA_TECHNICAL_APPENDIX_KR.md)에 별도로 정리했다.

한 줄로 요약하면,

`LLM은 element_id로 선택하고, GAIA는 그 선택을 snapshot_id + ref_id 실행 계약으로 변환한다.`

---

## 7. Cache는 어디서 쓰이나요?

1. 프로세스 메모리에 유지되는 런타임 상태
2. 재실행 복원을 위한 로컬 파일 저장
3. 일부 탐색 모드에서만 쓰는 로컬 LLM 응답 재사용


### 7.1 MCP Host 런타임 캐시

- `active_sessions`
  - 엄밀히 말하면 일반적인 캐시라기보다 세션 레지스트리에 가깝다.
  - 현재 살아 있는 브라우저 컨텍스트, 페이지, 세션 상태를 프로세스 메모리에 유지한다.

- `session.snapshots`
  - 세션별 최근 snapshot record를 메모리에 보관한다.
  - 오래된 snapshot은 개수 제한에 따라 잘려 나간다.

- `_page_target_id_cache`
  - 페이지 타겟 식별 최적화용 캐시(약참조 기반).

### 7.2 디스크 세션 포인터

`session_store.py`가 사용자/환경별 세션 포인터를 저장한다.

- 경로 예: `~/.gaia/sessions/*.json`
- 재실행 시 같은 session key에 대응하는 MCP 세션 상태를 복원하는 데 사용한다.
- 이것도 Redis가 아니라 로컬 파일(JSON) 저장이다.

### 7.3 LLM 캐시(탐색 모드 계열)

탐색 계열에는 `artifacts/llm_cache.json`, `artifacts/cache/semantic_llm_cache.json` 같은 로컬 JSON 캐시가 활용될 수 있다.

- 동일하거나 유사한 탐색 프롬프트에 대한 응답을 재사용해 호출 비용과 시간을 줄인다.
- 이 캐시는 모든 실행 모드의 공용 인프라가 아니라, exploratory 계열 로직에서 주로 사용된다.

주의:

- GoalDriven 주요 경로는 snapshot/ref 상태 일관성이 더 핵심이고,
- LLM 캐시는 모드별로 사용 정도가 다르다.
- 따라서 이 문맥의 `cache`는 "분산 캐시 서버"보다 "메모리 상태 + 로컬 재사용 저장"에 가깝다.

---

## 8. Ref-only 정책을 왜 쓰나요?

### 8.1 장점

1. 재현성
   - 같은 snapshot/ref로 같은 액션 재실행이 쉽다.

2. 디버깅 용이
   - 어떤 요소를 눌렀는지 추적 가능.

3. 안전성
   - 랜덤 selector 클릭을 줄인다.

### 8.2 단점/보완

1. 화면이 바뀌면 ref가 stale될 수 있다.
2. 그래서 stale 감지 + resnapshot + 재시도 경로가 필요하다.

---

## 9. Reason Code / Final Status 체계는 왜 중요하나요?

`status=failed` 한 줄로는 원인 분석이 안 된다.

GAIA는 `reason_code`와 `final_status`로 실패를 분류해 운영 가능하게 만든다.

예시 reason_code:

- `ok`
- `not_actionable`
- `no_state_change`
- `stale_snapshot`
- `modal_not_open`
- `http_5xx`
- `filter_result_mismatch`
- `filter_persistence_lost`
- `blocked_timeout`

예시 final_status:

- `SUCCESS`
- `FAIL`
- `BLOCKED_USER_ACTION`

운영 관점에서 이 값들은 KPI, 복구율, 병목 추적의 핵심 지표다.

---

## 10. 사용자가 CLI/GUI/Telegram에서 "학점 필터링 로직을 검증해줘" 입력하면 무슨 일이 벌어지나?

이 섹션이 실제 발표/팀 공유 때 가장 중요

가정:

- URL은 `https://inuu-timetable.vercel.app/`
- 입력 채널은 세 가지 중 하나다.

### 10.1 입력 채널별 차이

1. `CLI`
   - `gaia/cli.py`가 입력을 받고,
   - `chat_hub.py` -> `terminal.py`로 전달한다.

2. `Telegram`
   - `telegram_bridge.py`가 메시지를 받고,
   - 내부적으로 `chat_hub.py` -> `terminal.py` 경로로 보낸다.

3. `GUI`
   - `gui.controller.py`가 입력창/채팅창의 문장을 받고,
   - GUI worker를 통해 GoalDrivenAgent 또는 ExploratoryAgent를 실행한다.

즉, 앞단 경로는 달라도 **뒤쪽 엔진은 결국 비슷한 구조**를 탄다.

### 10.2 단계 1: 목표 객체 생성

1. 빠른 목표 실행이면 자연어 목표가 바로 goal로 감싸진다.
2. `agent.py`가 goal type을 추론한다.

### 10.3 단계 2: 브라우저 스냅샷 수집

1. 에이전트가 MCP `/execute`에 `browser_snapshot` 요청
2. `mcp_host.py`에서 Playwright로 페이지 상태 수집
3. `analyze_page_elements()`가 DOM 구조화
4. `snapshot_id`, `elements_by_ref` 생성
5. 결과가 에이전트로 돌아옴

### 10.4 단계 3: LLM 의사결정

1. 에이전트가 DOM 요약 문자열 생성 (`[element_id] <tag> ...` 형태)
2. 목표/이력/제약/DOM/스크린샷을 LLM에 전달
3. LLM이 예: `{"action":"select","element_id":4,"value":"1학점"}` 같은 결정 반환

### 10.5 단계 4: 액션 실행 (Ref-only)

1. 현재 활성 snapshot 안에서 `element_id -> ref_id` 매핑
2. MCP `/execute`에 `browser_act` 요청
3. payload에 `session_id`, `snapshot_id`, `ref_id`, `action`, `value` 포함
4. `mcp_ref.action_executor`가 실제 액션 수행

### 10.6 단계 5: 실행 후 검증

1. `state_change` probe 수집
2. `effective` 판정
3. `reason_code` 기록
4. 성공이면 step 완료, 실패면 fallback/retry

### 10.7 단계 6: semantic validation

필터 검증에서는 단순 DOM 변화만 보지 않는다.

1. 필터 선택 상태가 반영되었는지
2. 현재 페이지 결과가 실제로 해당 학점과 일치하는지
3. 페이지네이션이 있으면 다음 페이지에서도 유지되는지
4. 페이지네이션이 없으면 reload 후에도 유지되는지

이 검증을 `filter_validation_engine.py`가 deterministic 하게 수행한다.

### 10.8 단계 7: 최종 리포트 생성

1. `terminal.py` 또는 GUI worker가 validation summary 생성
2. `chat_hub.py` / `telegram_bridge.py` / GUI result card가 결과를 정리
3. 산출물 예시:
   - `final_status`
   - `reason`
   - `validation_summary`
   - `validation_checks`
   - `reason_code_summary`
   - `step_timeline`
   - `attachments`
   - 필요 시 JSON 출력 파일(`--output`) 또는 rail `summary.json`

---

## 11. PRD Bundle 모드에서는 무슨 일이 벌어지나?


### 11.1 왜 Bundle이 필요한가?

원본 기획서를 매번 다시 LLM이 해석하면 결과가 흔들릴 수 있다.

그래서 GAIA는:

1. 원본 기획서(PDF/DOCX/MD/TXT)를 입력받고
2. 정규화된 `PRD Bundle JSON`으로 변환하고
3. 그 번들 안의 `generated_goals`를 기준으로 반복 실행한다.

### 11.2 장점

1. 재현성
   - 같은 bundle이면 같은 목표 집합으로 다시 돌릴 수 있다.

2. 추적성
   - 어떤 요구사항에서 어떤 테스트 goal이 나왔는지 남길 수 있다.

3. 운영성
   - GUI에서 bundle을 열고 바로 실행할 수 있다.

---

## 12. 실패는 어디서 많이 나고, 어떻게 읽어야 하나?

실패 지점은 크게 6곳이다.

1. LLM 출력 파싱 실패
2. ref 실행 실패(`not_actionable`)
3. 화면 변화 감지 실패(`no_state_change`)
4. 세션/스냅샷 불일치(`stale`)
5. 외부 네트워크/API 지연(`http_5xx`/timeout)
6. 사용자 개입 필요(`BLOCKED_USER_ACTION`)

### 12.1 해석 순서(실무)

1. `final_status` 먼저 본다.
2. `reason_code_summary`를 본다.
3. `attempt_logs`에서 어떤 fallback까지 갔는지 본다.
4. `state_change`의 변화 신호를 본다.
5. 필요하면 screenshot/GIF/validation rail artifact를 확인한다.

---

## 13. 로그인/개입(intervention) 흐름

운영 중 사람이 필요한 순간이 있다.

예:

- 로그인 창 등장
- CAPTCHA
- 2FA
- 브라우저 권한 허용
- 결제

현재 설계 원칙:

1. 감지되면 `BLOCKED_USER_ACTION`으로 전환
2. 사용자에게 개입 요청
3. `resume` 또는 GUI/Telegram 채팅으로 재개
4. 일정 시간 내 재개 안 되면 `blocked_timeout`

즉, "모든 걸 자동으로 하겠다"가 아니라, **사람이 필요한 순간은 분리해서 안정적으로 이어간다**가 원칙이다.

---

## 14. Steering은 왜 필요한가?

사용자는 테스트 중간에 아래처럼 개입할 수 있다.

- "바로추가는 누르지 마"
- "위시리스트만 먼저 비워"
- "로그인 끝난 다음에 계속해"

GAIA는 이걸 steering policy로 바꿔 실행 제약으로 사용한다.

핵심 원칙:

1. 금지 규칙은 hard
2. 선호 규칙은 soft
3. infeasible이면 soft부터 1회 완화
4. 그래도 안 되면 사용자 개입 요청

즉, steering은 에이전트를 망가뜨리는 수동 조작이 아니라, **정책형 개입**이다.


---

## 15. Q&A

### Q1. 왜 selector 직접 클릭 안 해?

A. selector 직접 클릭은 빠르지만 재현성과 디버깅성이 떨어진다. GAIA는 snapshot/ref 계약으로 실행 품질을 우선한다.

### Q2. LLM이 다 하는 거 아냐?

A. 아니다. LLM은 결정만 한다. 실제 클릭/입력/검증은 Playwright + MCP Host가 한다.

### Q3. `success`인데 체감상 이상하면?

A. summary만 보지 말고 `validation_checks`, `reason_code_summary`, `step_timeline`, 첨부 이미지/JSON 요약을 같이 봐야 한다.

### Q4. 왜 가끔 loop에 빠져?

A. weak state change를 progress로 잘못 인식하거나, collect gate/phase 정책이 과도하면 반복 전환이 생긴다. loop guard/strong progress 기준 튜닝이 필요하다.

### Q5. GUI/CLI/Telegram 중 뭐가 메인이야?

A. 셋 다 같은 엔진을 쓰는 진입점이다. 상황에 따라 다르게 쓸 뿐, 브라우저 실행/검증 로직은 공용이다.



---

## 16. 부록 A: 실행 흐름을 한 문장으로 끝내기

"사용자 문장이나 PRD를 목표로 바꾼 뒤, 에이전트가 DOM snapshot/ref를 기반으로 LLM 결정을 받고 Playwright 액션을 실행하며, state_change/reason_code와 semantic validation으로 검증·복구·리포팅까지 수행한다."

---

## 17. 부록 B: 팀 발표 때 바로 읽는 1분 스크립트

"GAIA는 사용자 목표나 기획서를 받아서, 먼저 브라우저 화면을 snapshot으로 구조화합니다. 각 요소는 snapshot 내부에서 ref_id로 관리되고, 에이전트는 이를 LLM이 고르기 쉬운 element_id 기반 목록으로 정리해 전달합니다. LLM은 다음 액션을 JSON으로 결정하고, 실행 직전에 에이전트가 그 element_id를 현재 snapshot의 ref_id로 해석합니다. 실행은 MCP Host가 Playwright로 수행하고, 결과는 state_change와 reason_code로 검증합니다. 필터처럼 의미가 중요한 기능은 semantic validation으로 실제 결과가 맞는지 다시 확인하고, 필요하면 Validation Rail로 한 번 더 검증합니다. 실패하면 fallback 또는 사용자 개입 요청으로 복구하고, 최종적으로 GUI 카드·Telegram 요약·첨부·선택적 JSON 출력 형태로 결과를 전달합니다. 즉, GAIA의 차별점은 단순 자동화가 아니라, 재현 가능한 실행 계약과 설명 가능한 검증 체계입니다." 
