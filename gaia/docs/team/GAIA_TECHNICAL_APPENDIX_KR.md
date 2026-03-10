# GAIA Technical Appendix

발표 Q&A에서 바로 꺼내 설명할 수 있도록, 현재 코드 기준으로 `MCP Host + Playwright -> DOM 추출 -> snapshot/ref 구조화 -> Agent 내부 매핑 -> LLM 프롬프트 -> browser_act 실행 결과` 흐름을 정리한 부록이다.

## 0. 처음 읽는 사람을 위한 진입점

이 문서는 구현 세부사항까지 포함한 기술 부록이다. 처음 보는 사람이 바로 `dom_ref`, `ref_id`, `selector`, `reason_code`부터 읽기 시작하면 구조를 놓치기 쉽다. 이 문서는 아래 순서로 읽는 것이 가장 안전하다.

### 0.1 먼저 이해해야 하는 문제

브라우저 자동화에서 가장 흔한 문제는 아래 3가지다.

- 화면에 같은 이름의 버튼이 여러 개 있다.
- LLM이 선택한 버튼이 실제 실행 시점에는 이미 stale일 수 있다.
- 클릭 API가 성공해도, 실제 상태 변화가 없을 수 있다.

GAIA의 핵심 구조는 이 3가지를 동시에 풀기 위해 설계되어 있다.

### 0.2 핵심 아이디어 3개만 먼저 잡기

- LLM은 사람이 읽기 쉬운 `element_id`를 고른다.
- 실제 실행은 `snapshot_id + ref_id` 계약으로 한다.
- 실행 결과는 클릭 성공 여부가 아니라 `state_change + reason_code`로 검증한다.

처음 읽는 사람은 이 세 줄만 먼저 이해한 뒤 나머지 절을 읽으면 된다.

### 0.3 이 문서에서 답하려는 질문

이 부록은 주로 아래 질문에 답한다.

- Playwright는 어디까지 하고, MCP Host는 무엇을 추가로 하는가
- DOM 요소 1개는 어떤 record로 저장되는가
- 왜 `selector`만으로 실행하지 않고 `snapshot_id + ref_id`를 쓰는가
- 같은 `담기` 버튼이 여러 개 있을 때 어떻게 구분하는가
- 액션 뒤에 무엇을 보고 성공/실패를 판정하는가

### 0.4 추천 읽기 순서

처음 보는 사람은 아래 순서로 읽는 것을 권장한다.

1. `1. 한 줄 구조`
2. `2. 책임 분리`
3. `3. snapshot 생성 흐름`
4. `7. snapshot 저장 구조`
5. `12. 실제 실행 요청 형식`
6. `15. state_change는 무엇을 보나`
7. 이후 필요한 세부 절만 선택

즉 이 문서는 처음부터 끝까지 순서대로 정독하기보다, 핵심 실행 경로를 먼저 잡고 세부 구현으로 내려가는 방식이 더 잘 맞는다.

## 1. 한 줄 구조

`Playwright가 현재 브라우저 DOM/상태를 수집 -> MCP Host가 snapshot_id/ref_id를 부여해 구조화 -> Agent가 이를 element_id 기반 LLM 입력으로 재구성 -> LLM이 action + element_id를 결정 -> Agent가 element_id를 snapshot_id + ref_id로 해석 -> MCP Host가 실제 액션을 수행하고 state_change/reason_code를 반환`

### 1.1 용어 사전

- `selector`
  - 현재 DOM에 다시 질의할 때 쓰는 문자열 힌트
- `full_selector`
  - iframe 문맥까지 포함한 selector 힌트
- `dom_ref`
  - page context의 특정 DOM 노드를 추적하기 위한 synthetic ref
- `ref_id`
  - snapshot 안의 특정 element record를 가리키는 실행용 키
- `snapshot_id`
  - 하나의 snapshot record 전체를 가리키는 묶음 ID
- `element_id`
  - Agent가 LLM 프롬프트에서 보여주는 선택용 번호표

이 문서에서는 위 의미로 용어를 고정해서 사용한다.

### 1.2 설계 원칙과 구현 신호를 구분해서 읽기

이 문서는 아래 두 층을 구분해서 읽는 것이 중요하다.

- **설계 계약**
  - `snapshot_id + ref_id` 기반 실행
  - Host가 실행 검증과 recovery gate를 담당
  - Agent는 LLM 선택용 표현(`element_id`)을 구성
- **현재 구현 신호**
  - selector/full_selector 힌트
  - semantic container 추출
  - token/context 기반 DOM 정렬
  - class naming, CTA 힌트, role_ref 같은 보조 신호

즉 이 문서에서 예시로 보이는 문자열 패턴이나 naming rule은 전부 시스템의 외부 계약이 아니라,
현재 구현이 사용하는 내부 신호 집합으로 이해하는 것이 정확하다.

---

## 2. 책임 분리

### 2.1 Playwright

- 실제 브라우저 제어
- 페이지/프레임 DOM 접근
- 클릭, 입력, 스크롤, 스크린샷 수행

### 2.2 MCP Host

- Playwright를 호출해 DOM/상태 수집
- `snapshot_id`, `ref_id`, `elements_by_ref` 생성
- 실행 계약 검증
- stale/scope 검증
- `state_change`, `reason_code`, `attempt_logs` 반환

### 2.3 GoalDrivenAgent

- 현재 목표와 최근 실행 이력 관리
- snapshot 결과를 `DOMElement(id=element_id, ...)`로 재구성
- LLM 프롬프트 생성
- `element_id -> ref_id` 매핑 후 실행 요청
- 결과를 읽어 다음 스텝 의사결정 / recovery / semantic validation 수행

---

## 3. snapshot 생성 흐름

Agent는 `/execute`로 아래 요청을 보낸다.

```json
{
  "action": "browser_snapshot",
  "params": {
    "session_id": "...",
    "url": "..."
  }
}
```

MCP Host는 `snapshot_page()`에서 현재 페이지를 연 뒤 `analyze_page_elements()`를 호출한다.

수집 순서:

1. 메인 프레임 + iframe 순회
2. shadow DOM root까지 확장 스캔
3. input/textarea/select, button/link, pagination, clickable, semantic 요소 수집
4. 각 요소의 actionability 계산
5. 중복 제거 및 signal score 기반 trimming
6. 탭/프레임 인덱스를 반영해 `ref_id` 부여
7. `elements_by_ref`와 snapshot record 저장

---

## 4. DOM 추출 기준

`analyze_page_elements()`는 단순 버튼만 보는 것이 아니라 아래 계열을 같이 수집한다.

### 4.1 input/select 계열

- `input`, `textarea`, `select`
- placeholder, name, type, aria-label, title
- `select`는 `options`와 `selected_value`까지 수집

### 4.2 button/link 계열

- `button`
- `a[href]`
- `role=button`, `role=link`, `role=tab`, `role=menuitem` 등
- 아이콘형 버튼은 `aria-label`, `title`, `svg aria-label`까지 참고

### 4.3 pagination/navigation 시그널

- next/prev, pagination, page, chevron, arrow 등 텍스트/클래스/aria signal

### 4.4 semantic/textual signal

- `tr`, `td`, `li`, `article`
- `role=row`, `role=cell`, `role=listitem`
- `data-testid`, `data-test`, `data-qa`
- row/item/card/list 관련 class

즉 실행 후보는 인터랙티브 요소 중심이지만, 검증에 필요한 semantic/text signal도 같이 수집한다.

---

## 5. 요소 1개가 저장되는 형태

MCP Host가 수집한 raw element는 대략 아래 필드를 가진다.

```json
{
  "tag": "button", // 실제 HTML 태그명
  "dom_ref": "gaia-button-...", // MCP/호스트가 원본 DOM 노드를 추적하려고 붙인 내부 참조 ID
  "selector": "text=로그인", // 현재 컨텍스트 안에서 이 요소를 다시 찾기 위한 짧은 선택자
  "full_selector": "iframe[name=\"...\"] >>> text=로그인", // iframe 경로까지 포함한 전체 선택자
  "text": "로그인", // 화면에서 읽힌 대표 텍스트
  "attributes": {
    "role": "button", // 접근성 역할(이 요소가 무엇으로 동작하는지)
    "aria-label": "", // 화면 텍스트 외에 접근성 이름으로 쓰이는 라벨
    "title": "", // HTML title 속성 값
    "gaia-visible-strict": "true", // 도구 기준으로 실제로 보인다고 판단한 원본 플래그
    "gaia-actionable": "true", // 도구 기준으로 클릭/입력 등 액션 가능하다고 본 원본 플래그
    "gaia-disabled": "false", // 도구 기준으로 비활성 상태가 아니라고 본 원본 플래그
    "options": [], // 선택형 요소일 때 가능한 옵션 목록
    "selected_value": "" // 선택형 요소일 때 현재 선택된 값
  },
  "bounding_box": {
    "x": 100, // 뷰포트 기준 좌상단 X 좌표
    "y": 220, // 뷰포트 기준 좌상단 Y 좌표
    "width": 120, // 요소 너비(px)
    "height": 36, // 요소 높이(px)
    "center_x": 160, // 요소 중앙의 X 좌표
    "center_y": 238 // 요소 중앙의 Y 좌표
  },
  "element_type": "button", // 도구가 정규화한 요소 종류
  "actionable": true, // 요약된 액션 가능 여부
  "visible_strict": true, // 요약된 엄격 visible 여부
  "frame_index": 0, // 이 요소가 속한 프레임 번호
  "frame_name": "main", // 이 요소가 속한 프레임 이름
  "is_main_frame": true, // 메인 프레임 소속인지 여부
  "ref_id": "t0-f0-e14", // 현재 snapshot 안에서 이 요소를 가리키는 실행용 참조 ID
  "scope": {
    "tab_index": 0, // 이 요소를 찾은 브라우저 탭 번호
    "frame_index": 0, // 이 요소를 찾은 프레임 번호
    "is_main_frame": true // 이 탐색 범위가 메인 프레임인지 여부
  }
}

```

중요한 점:

- `selector`는 보조 메타데이터다.
- 실행 계약은 `snapshot_id + ref_id`다.
- 실제 locator 복구에는 `dom_ref`, role-ref hint, selector hint가 함께 쓰일 수 있다.
- `dom_ref`는 웹페이지가 원래 갖고 있던 표준 ID가 아니라, MCP Host가 Playwright로 페이지 안 JS를 실행해 붙인 synthetic ref다.

---

## 6. actionability는 어떻게 계산하나

MCP Host는 요소별로 아래를 계산한다.

- `display !== none`
- `visibility !== hidden`
- `opacity > 0.02`
- `pointer-events !== none`
- bounding box 크기 존재
- disabled / aria-disabled 여부
- viewport 내부 여부

그리고 아래 필드를 만든다.

- `visible`
- `actionable`
- `disabled`
- `onViewport`
- `pointerEvents`
- `opacity`

즉 단순 DOM 존재 여부가 아니라, “현재 상호작용 후보인지”까지 snapshot 단계에서 태깅한다.

중요한 점:

- 이 값들은 raw element를 조립하는 **같은 추출 루프 안에서** 계산된다.
- 즉 `assignDomRef(el)`로 `dom_ref`를 붙이는 단계와 actionability 계산은 별도 후처리 단계가 아니라 같은 snapshot 생성 흐름 안에 있다.
- 다만 `dom_ref`는 식별용 필드이고, `actionable`/`visible`은 상태 판정 필드다.

---

## 7. snapshot 저장 구조

MCP Host는 요소 수집 후 다음 형태의 snapshot record를 세션에 저장한다.

```json
{
  "snapshot_id": "session:epoch:domhash",
  "session_id": "...",
  "url": "...",
  "tab_index": 0,
  "dom_hash": "...",
  "epoch": 3,
  "captured_at": 1234567890,
  "elements_by_ref": {
    "t0-f0-e14": { "...element meta..." }
  },
  "context_snapshot": { "...context summary..." }
}
```

그리고 `session.snapshots[snapshot_id]`에 저장한다.

핵심:

- `ref_id`는 “질의 문자열”이 아니라 snapshot 안의 레코드 키다.
- 그래서 Host는 `snapshot_id + ref_id`로 과거에 기록한 정확한 항목을 직접 조회할 수 있다.

여기서 용어를 안전하게 풀면:

- `session_id`
  - 현재 브라우저/MCP 세션을 가리키는 식별자
- `epoch`
  - 같은 세션 안에서 snapshot을 몇 번째로 떴는지 나타내는 증가 카운터
- `dom_hash`
  - 현재 URL + 수집된 요소 목록을 바탕으로 만든 DOM 상태 fingerprint
- `snapshot_id`
  - `session_id:epoch:domhash`
  - “이번 캡처 묶음 전체”를 가리키는 ID

즉 `snapshot_id`는 개별 요소 ID가 아니라 snapshot 전체 레코드의 이름이다.

### 7.0 왜 `dom_ref` 기준 dedupe가 필요한가

수집 단계에서는 같은 원본 DOM 요소가 중복 record로 들어올 가능성이 있다.

대표 경우:

- 태그 기반 수집과 role 기반 수집이 겹칠 때
- 인터랙티브 수집과 semantic 보강 수집이 같은 요소를 다시 만질 때
- 하나의 요소가 여러 추출 경로 조건을 동시에 만족할 때

그래서 Host는 `dom_ref` 기준으로 dedupe를 수행한다.

- `dedupe = deduplicate = 중복 제거`
- 의미: 같은 DOM 노드에서 나온 중복 element record를 하나로 합치는 과정
- 결과: 정보가 더 풍부한 record 하나만 남기고 snapshot 메타를 계속 조립한다

### 7.1 `dom_ref`, `ref_id`, `snapshot_id`는 각각 어디 레벨의 ID인가

- `snapshot_id`
  - snapshot record 전체를 가리키는 ID
  - 의미: “언제, 어떤 DOM 상태를 기준으로 수집했는가”
- `ref_id`
  - snapshot 안의 특정 element record 키
  - 의미: “그 snapshot 안에서 몇 번째 실행 대상 항목인가”
- `dom_ref`
  - page context의 실제 DOM 노드에 붙인 synthetic ref
  - 의미: “그 element record가 원래 어떤 DOM 노드에서 추출됐는가”

즉 구조는 아래처럼 보는 것이 가장 정확하다.

```text
live DOM node
-> dom_ref 부여
-> element meta 생성
-> ref_id 부여
-> snapshot_id 아래 elements_by_ref에 저장
```

### 7.2 `dom_ref`는 누가 만들고, 어디서 쓰나

- 생성 주체는 MCP Host다.
- 다만 Host가 직접 문자열만 만드는 것이 아니라, Playwright의 `page.evaluate(...)` 계열 호출로 페이지 안 JS를 실행해 `assignDomRef(el)`를 붙인다.
- 따라서 “페이지 주입 JS”와 “Playwright가 한다”는 말은 충돌하지 않는다.
  - 페이지 안에서 실제 `dom_ref`를 붙이는 코드는 JS
  - 그 JS를 실행시키는 주체는 Playwright
  - 그 Playwright를 호출하는 상위 레이어는 MCP Host

실제 사용 시점은 2군데다.

1. snapshot 생성 단계
   - raw element를 만들 때 `dom_ref`를 함께 수집
   - 중복 요소 제거, semantic container 연결, context snapshot 구성에 사용

2. 실행 단계
   - Host가 `snapshot_id + ref_id`로 element meta를 찾음
   - 그 meta 안의 `dom_ref`를 읽어 `[data-gaia-dom-ref="..."]` locator를 재구성함
   - 이후 실제 Playwright locator로 resolve해서 click/fill 등을 수행함

즉 실행 흐름은 아래와 같다.

```text
ref_id
-> elements_by_ref[ref_id]
-> dom_ref 확인
-> [data-gaia-dom-ref="..."] locator 생성
-> Playwright locator resolve
-> 실제 액션 실행
```

주의할 점:

- `dom_ref`가 있다고 해서 과거의 live DOM node 객체를 영구 저장하는 것은 아니다.
- 일반적으로 남는 것은 snapshot 시점의 element metadata와 `dom_ref` 문자열이다.
- DOM이 리렌더로 교체되면 예전 `dom_ref`는 stale해질 수 있고, 이때는 resnapshot 후 새 `ref_id`/`dom_ref`로 다시 해석해야 한다.

---

## 8. Agent가 snapshot을 받아서 하는 일

Agent는 `_analyze_dom()`에서 `browser_snapshot` 결과를 받아:

- `_active_snapshot_id`
- `_active_dom_hash`
- `_active_snapshot_epoch`
- `_element_selectors`
- `_element_full_selectors`
- `_element_ref_ids`
- `_selector_to_ref_id`
- `_element_scopes`

를 갱신한다.

그다음 raw element를 `DOMElement(id=idx, ...)`로 바꾼다.

여기서의 `id=idx`가 LLM에게 보여주는 `element_id`다.

즉:

- `ref_id`는 Host 발급 실행용 ID
- `element_id`는 Agent가 다시 붙인 LLM 선택용 번호

---

## 9. LLM에게 실제로 무엇을 보내나

`_decide_next_action()`은 아래 정보를 프롬프트에 묶어 보낸다.

### 9.1 목표 문맥

- goal name
- goal description
- priority
- success criteria
- failure criteria
- keywords
- test_data

### 9.2 실행 문맥

- current phase
- recent action history
- recent action feedback
- repeated click ids
- memory context

### 9.3 현재 화면 정보

- `_format_dom_for_llm()`로 만든 `element_id` 기반 DOM 요약
- 필요 시 screenshot

### 9.4 응답 schema

LLM은 아래 JSON만 반환하도록 강제된다.

```json
{
  "action": "click | fill | press | scroll | wait | select",
  "element_id": 12,
  "value": "optional",
  "reasoning": "이 액션을 선택한 이유",
  "confidence": 0.84,
  "is_goal_achieved": false,
  "goal_achievement_reason": null
}
```

즉 LLM은 `ref_id`를 직접 고르지 않는다.

---

## 10. LLM이 보는 DOM 요약 포맷

LLM은 숫자만 보는 것이 아니라, `element_id`가 붙은 요약된 요소 레코드를 본다.

예시 개념:

```text
[12] <button> "로그인" role=button enabled
[13] <select> "학점" options=[1학점, 2학점, 3학점]
[14] <div> "컴퓨터공학과" role=row
```

즉:

- `element_id`는 번호표
- 실제 선택 근거는 text / role / type / options / context_text / container_name 같은 요약 정보

실제 코드 기준으로는 아래처럼 더 많은 컨텍스트가 한 줄에 붙을 수 있다.

```text
[41] <button> "담기" role=button container="컴퓨터네트워크" container-role="article" context="컴퓨터네트워크 3학점 전공필수" actions=[상세보기 | 담기 | 시간표 보기] role_ref=button(name="담기", nth=2)
```

즉 LLM은 단순히 `[41]`만 고르는 것이 아니라:

- 버튼 자체 텍스트
- 가까운 카드/row의 대표 이름
- 그 카드의 압축 텍스트
- 같은 카드 안의 sibling action labels
- role 기반 recovery hint

를 같이 보고 선택한다.

---

## 10.1 DOM 트리를 그대로 보내는가

아니다.

현재 메인 경로는 `완전한 DOM tree`를 LLM에 그대로 전달하지 않는다. 대신 아래 방식을 쓴다.

1. Host가 raw DOM/semantic signal을 수집
2. 각 요소마다 `가장 가까운 semantic container`를 찾음
3. 그 container의 이름과 문맥 텍스트를 요소 속성에 주입
4. Agent는 이걸 `flat list + contextual attributes` 형태로 LLM에 전달

즉 구조는:

- 전송 형식: tree 전체
- 실제 구현: `flat records with contextual enrichment`

이 방식을 쓰는 이유:

- 프롬프트 길이를 줄일 수 있음
- LLM이 긴 DOM tree를 파싱하는 부담이 줄어듦
- “비슷한 버튼 여러 개”를 카드/row 단위 문맥으로 분리할 수 있음

---

## 10.2 semantic container는 어떻게 잡나

Host는 각 요소에 대해 `nearestSemanticContainer()`를 찾는다.

container 후보 기준은 아래 **구조 신호 우선 + 약한 naming signal 보조**로 읽는 것이 맞다.

- tag가 `li`, `tr`, `article`, `section`
- role이 `listitem`, `row`, `article`, `region`, `group`
- 약한 class 기반 구조 신호가 있는 경우
  - 예: `card`, `item`, `row`, `list`, `result`, `product` 같은 naming pattern

즉 완전한 DOM 트리 전체가 아니라, “이 버튼이 속한 카드/행/상품/과목 묶음”을 찾는 방식이다.

중요:

- 위 목록은 외부 계약이 아니라 **현재 구현이 사용하는 container 후보 신호 예시**다.
- 설계 원칙은 “가장 가까운 의미 있는 구조 묶음을 찾는다”이고,
- 어떤 naming pattern을 약한 신호로 인정할지는 productionize 시 config/weight 계층으로 분리할 수 있다.

그 후 container 기준으로 아래 문맥을 붙인다.

- `container_name`
  - heading, lead link, strong/title 후보, own accessible name 순으로 대표 이름 추출
  - 즉 현재 버튼이 속한 카드/행의 제목에 가까운 값
- `container_role`
  - container의 role 또는 tag
  - 즉 이 묶음이 카드인지, row인지, section인지 알려주는 종류 정보
- `container_dom_ref`
  - container에도 synthetic dom_ref 부여
  - 즉 버튼 자신이 아니라 “버튼이 속한 카드 자체”를 가리키는 내부 ref
- `container_parent_dom_ref`
  - 상위 semantic container ref
  - 카드가 더 큰 리스트/섹션 아래 있을 때 상위 묶음까지 연결하는 용도
- `context_text`
  - container 전체 텍스트를 2줄 정도로 압축
  - 제목만으로 부족한 메타 정보(학점, 시간, 가격, 상태 등)를 보강하는 요약
- `group_action_labels`
  - 같은 container 안의 버튼/링크 라벨 목록
  - 현재 버튼 하나의 속성이 아니라, 같은 카드 안 sibling CTA 문맥

즉 “담기 버튼이 여러 개 있는데 각 어떤 담기인지”는

- 버튼 자체 text
- 그 버튼이 속한 container 이름
- 그 container의 context text
- 같은 그룹의 sibling actions

를 조합해서 구분한다.

### 10.2.1 리스트가 길게 나열될 때 특정 카드의 `담기`를 어떻게 특정하나

질문을 받으면 아래 순서로 설명하는 것이 가장 정확하다.

1. `담기` 버튼에서 시작한다.
2. 부모 방향으로 올라가며 가장 가까운 semantic container를 찾는다.
3. 첫 번째로 걸린 semantic tag/role 조상, 또는 약한 class 기반 구조 신호가 있는 조상을 현재 버튼의 소속 카드/행 후보로 확정한다.
4. 그 container에서 대표 이름(`container_name`), 문맥 요약(`context_text`), 같은 묶음 CTA 목록(`group_action_labels`)을 추출한다.
5. 이 문맥을 현재 버튼 element record에 같이 주입한다.

즉 이 프로젝트는 좌표 근접성보다 **DOM 조상 관계**로 “이 버튼이 어느 카드에 속하는지”를 먼저 정한다.

예를 들어 DOM이 아래와 비슷하다고 하자.

```html
<article class="course-card">
  <h3>컴퓨터네트워크</h3>
  <p>3학점 전공필수</p>
  <button>상세보기</button>
  <button>담기</button>
  <button>시간표 보기</button>
</article>
```

이 경우 `담기` 버튼은 `article.course-card`에 종속되고, 설명은 아래처럼 만들어질 수 있다.

```text
[41] <button> "담기" role=button container="컴퓨터네트워크" context="컴퓨터네트워크 3학점 전공필수" actions=[상세보기 | 담기 | 시간표 보기]
```

여기서:

- `"담기"`는 현재 요소 자신의 라벨이다.
- `container="컴퓨터네트워크"`는 현재 버튼이 속한 카드 이름이다.
- `context="컴퓨터네트워크 3학점 전공필수"`는 카드 전체 텍스트의 압축본이다.
- `actions=[상세보기 | 담기 | 시간표 보기]`는 현재 버튼 하나의 속성이 아니라, 같은 카드 안 sibling CTA 목록이다.

즉 `상세보기`, `시간표 보기`가 함께 보이는 것은 오류가 아니라,
같은 이름의 `담기` 버튼이 여러 개 있을 때 “어느 카드의 담기인지”를 LLM이 더 잘 구분하도록 주는 컨텍스트다.

---

## 10.3 "담기 버튼이 여러 개"를 어떻게 구분하나

이 질문에 대한 가장 정확한 답은:

`버튼 자체 selector 하나만 보는 게 아니라, 가장 가까운 semantic container의 이름과 문맥을 함께 붙여서 구분합니다.`

예를 들어 과목 카드가 3개 있고 각 카드마다 모두 `담기` 버튼이 있다고 하자.

Host는 각각을 이런 식으로 구조화한다.

```text
[21] <button> "담기" container="운영체제" context="운영체제 3학점 전공필수 월/수 3교시"
[37] <button> "담기" container="컴퓨터네트워크" context="컴퓨터네트워크 3학점 전공필수 화/목 2교시"
[48] <button> "담기" container="데이터베이스" context="데이터베이스 3학점 전공선택 금 1교시"
```

따라서 목표가

- `"컴퓨터네트워크 담기"`
- `"운영체제 위시리스트에 추가"`
- `"3학점 전공필수 과목 담기"`

처럼 오면 Agent는 `_context_score()`에서:

- 목표 토큰과 `text` 일치
- 목표 토큰과 `container_name` 일치
- 목표 토큰과 `context_text` 일치

를 가중치로 계산한다.

실제 가중치는 대략:

- element text 토큰 일치: `1.25`
- container_name 토큰 일치: `2.0`
- context_text 토큰 일치: `0.75`
- goal에 따옴표로 명시한 phrase가 container_name에 직접 포함되면 추가 가산

즉 같은 `담기`라도

- `"운영체제"`라는 키워드가 붙은 버튼
- `"컴퓨터네트워크"`라는 키워드가 붙은 버튼

은 점수가 다르게 나오고, 그중 상위 후보만 LLM 입력 상단에 올라간다.

추가로 Host는 role 기반 fallback 힌트도 별도로 붙인다.

- `role_ref=button(name="담기", nth=2)`
  - 의미: 같은 `role=button` + `name=담기` 조합 중 세 번째 항목
  - `nth`는 0-based 인덱스다.

중요한 점:

- `nth`는 container 내부 순번이 아니다.
- `nth`는 snapshot 전체에서 동일 `role:name` 그룹의 순번이다.
- 따라서 실제 주 구분자는 `container_name` + `context_text` + `group_action_labels`이고,
- `nth`는 stale 복구나 role 기반 재탐색 때 쓰는 보조 힌트라고 설명하는 것이 정확하다.

---

## 10.4 Agent는 어떤 필드를 보고 점수를 매기나

Agent의 `_fields_for_element()`는 아래를 한 묶음으로 사용한다.

- `text`
- `aria_label`
- `placeholder`
- `title`
- `href`
- selector
- `role`
- `tag`
- `type`
- `container_name`
- `container_role`
- `context_text`
- `group_action_labels`
- `role_ref_role`
- `role_ref_name`

즉 버튼 하나를 독립 원자로 보지 않고, “버튼 + 속한 카드 문맥 + recovery hint” 묶음으로 본다.

---

## 10.5 LLM에 전달되기 전 정렬은 어떻게 하나

모든 요소를 다 같은 우선순위로 보내지 않는다.

`_format_dom_for_llm()`는 각 요소에 score를 매겨 상위 `GAIA_LLM_DOM_LIMIT`개만 보낸다.

점수에 반영되는 대표 신호:

- progress CTA 여부
- next/pagination 여부
- login hint 여부
- role/tag 중요도
- selector 기반 pagination/tab 시그널
- 현재 phase에 맞는 action인지
- `_context_score()`
- 최근 반복 클릭 penalty
- adaptive intent bias

즉 `담기` 버튼이 여러 개 있어도 모두 똑같이 취급되는 게 아니라:

- 목표와 맞는 카드 문맥이 있는지
- 지금 phase에서 add-like action이 맞는지
- 최근에 같은 버튼을 반복 클릭했는지

를 합쳐 정렬한다.

---

## 10.6 role_ref는 왜 붙이나

Host는 snapshot 수집 후 `_build_role_refs_from_elements()`로 각 요소에 role 기반 recovery hint를 붙인다.

예:

- `role_ref_role = "button"`
- `role_ref_name = "담기"`
- `role_ref_nth = 2`

이 값은 두 가지에 쓰인다.

1. LLM 입력 설명 보강
2. stale/recovery 시 role 기반 재탐색 힌트

즉 DOM 구조가 조금 바뀌어도

- 같은 role
- 같은 accessible name
- 같은 duplicate index

를 기준으로 다시 찾을 여지를 만든다.

---

## 10.7 "트리 구조를 넣었다"라고 말해도 되나

발표에서는 `완전한 DOM tree를 넣는다`라고 말하면 안 된다.

더 정확한 표현은:

`완전한 DOM tree를 그대로 보내는 대신, 각 요소에 가장 가까운 semantic container의 트리 문맥을 압축해서 붙입니다.`

즉:

- tree 전체 직렬화: 아님
- tree 문맥 압축 사용: 맞음

교수 질문에는 이렇게 답하면 가장 안전하다.

`이 경로는 full DOM tree 직렬화 대신, flat element list에 nearest semantic container 기반 문맥을 주입하는 방식입니다. 그래서 비슷한 CTA가 여러 개 있어도 카드 이름, context text, sibling action labels를 통해 구분합니다.`

---

## 11. 왜 selector를 직접 안 쓰나

핵심 차이는 이렇다.

- `selector`
  - 현재 DOM에 다시 실행해야 하는 질의 문자열
  - 구현 변화에 직접 결합됨
- `ref_id`
  - snapshot 안에 저장된 특정 요소 레코드의 키
  - “그 시점의 그 요소”에 결합됨

즉:

- `snapshot + selector`는 “예전에 이 selector를 봤다”는 기록
- `snapshot + ref_id`는 “예전에 저장한 이 요소 레코드”를 직접 가리킴

그래서 stale 검증, 범위 검증, 디버깅은 `ref_id` 계약이 더 명확하다.

### 11.1 그런데 왜 `selector`, `full_selector`도 같이 남기나

- `ref_id`는 snapshot 로컬 키라서 새 snapshot에서는 그대로 재사용되지 않을 수 있다.
- `dom_ref`도 live DOM이 교체되면 stale해질 수 있다.
- 반면 `selector`, `full_selector`는 “현재 DOM에서 다시 찾기 위한 질의 문자열”이라 디버깅과 fallback에 유리하다.

따라서 이 프로젝트의 역할 분리는 아래처럼 이해하면 된다.

- `snapshot_id + ref_id`
  - 실행 계약
  - stale 검증, 탭/프레임 범위 검증, 재현성의 기준
- `dom_ref`
  - host 내부의 원본 DOM 추적 및 locator 복구 힌트
- `selector`, `full_selector`
  - 사람이 읽기 쉬운 설명값
  - fallback / 재탐색 / 로그 출력용 힌트

즉 selector를 저장하는 이유는 “selector로만 실행하기 위해서”가 아니라, ref-only 실행 계약을 보조하기 위해서다.

---

## 12. 실제 실행 요청 형식

Agent는 `_execute_decision()`에서 `element_id -> ref_id`를 찾은 뒤 `browser_act`를 호출한다.

개념상 요청 payload는 아래와 같다.

```json
{
  "action": "browser_act",
  "params": {
    "session_id": "...",
    "snapshot_id": "...",
    "ref_id": "t0-f0-e14",
    "action": "click",
    "value": null
  }
}
```

실행 전 체크:

- `element_id`가 있는가
- 해당 `ref_id`가 있는가
- `_active_snapshot_id`가 있는가
- stale/없음이면 DOM 재수집 후 재매핑 가능한가

즉 Agent는 실행 전 `ref_required`, `not_found`, `missing_element_id`를 먼저 걸러낸다.

---

## 13. Host의 ref 실행 로직

`execute_ref_action_with_snapshot_impl()`은 아래 순서로 동작한다.

1. `snapshot_id`로 snapshot 조회
2. `elements_by_ref[ref_id]`로 ref meta 조회
3. stale / snapshot_not_found / tab_scope_mismatch / frame_scope_mismatch 검증
4. 필요 시 최신 snapshot을 다시 떠서 stale ref 복구 시도
5. ref meta의 `dom_ref`, selector, role hint로 locator 후보 생성
6. locator를 실제 Playwright locator로 resolve
7. 클릭/입력/hover/scroll/select 수행
8. state_change probe를 반복 수집하며 효과 확인
9. 실패 시 fallback / exception recovery / verify fallback 수행
10. 최종 `success/effective/reason_code/state_change/attempt_logs` 반환

---

## 14. browser_act 성공 응답 형식

성공 시 핵심 응답은 아래와 같다.

```json
{
  "success": true, // 이 액션 호출을 시스템이 최종적으로 성공으로 판정했는지
  "effective": true, // 단순 호출 성공이 아니라 실제로 의미 있는 변화가 있었다고 본 값
  "reason_code": "ok", // 결과를 짧게 분류한 표준 코드
  "reason": "ref action executed and state changed", // reason_code를 사람이 읽기 쉽게 풀어쓴 설명
  "snapshot_id_used": "...", // 이번 실행에서 기준으로 사용한 snapshot ID
  "ref_id_used": "t0-f0-e14", // 실제로 실행 대상으로 사용한 element ref ID
  "stale_recovered": false, // stale snapshot/ref 문제를 복구한 뒤 실행한 케이스인지 여부
  "transport_success": true, // MCP/호출 계층에서 요청 자체는 정상 전달·처리됐는지
  "locator_found": true, // ref를 실제 Playwright locator로 복원하는 데 성공했는지
  "interaction_success": true, // click/fill/select 같은 실제 상호작용 API 호출이 성공했는지
  "state_change": { "...flags..." }, // 액션 전후를 비교해 감지한 상태 변화 요약 구조
  "live_texts": [], // 실행 직후 화면에서 추가로 수집한 실시간 텍스트 증거
  "retry_path": ["1:dom_ref"], // 어떤 경로들로 실행을 시도했는지의 요약 이력
  "attempt_count": 1, // 총 몇 번 시도했는지
  "attempt_logs": [
    {
      "attempt": 1, // 몇 번째 시도인지
      "mode": "dom_ref", // 어떤 locator 복구 모드로 시도했는지
      "selector": "[data-gaia-dom-ref=\"...\"]", // 그 시도에서 실제 사용한 locator/selector
      "frame_index": 0, // 해당 시도가 수행된 프레임 인덱스
      "reason_code": "ok", // 그 개별 시도의 결과 코드
      "state_change": { "...flags..." } // 그 개별 시도에서 감지된 상태 변화
    }
  ],
  "screenshot": "...base64...", // 실행 직후 캡처한 스크린샷 이미지 데이터(base64)
  "current_url": "...", // 액션 직후의 현재 URL
  "tab_id": 0, // 실행이 일어난 브라우저 탭 번호
  "targetId": 0 // 내부 실행 대상 ID 또는 탭/타깃 식별자
}

```

실패 시도 비슷한 형식을 가지되:

- `success: false`
- `effective: false`
- `reason_code: stale_snapshot | not_actionable | no_state_change | ...`

가 된다.

---

## 15. state_change는 무엇을 보나

Host는 액션 전/후를 비교해 아래 플래그들을 계산한다.

- `url_changed`
- `dom_changed`
- `target_visibility_changed`
- `target_value_changed`
- `target_value_matches`
- `target_focus_changed`
- `focus_changed`
- `target_checked_changed`
- `target_aria_expanded_changed`
- `target_aria_pressed_changed`
- `target_aria_selected_changed`
- `target_disabled_changed`
- `counter_changed`
- `number_tokens_changed`
- `status_text_changed`
- `list_count_changed`
- `interactive_count_changed`
- `modal_count_changed`
- `backdrop_count_changed`
- `dialog_count_changed`
- `modal_state_changed`
- `auth_state_changed`
- `text_digest_changed`
- `evidence_changed`
- `effective`

즉 “URL 바뀌었나”만 보는 게 아니라, DOM/텍스트/모달/인증/카운터까지 종합적으로 본다.

---

## 16. reason_code는 왜 중요한가

`reason_code`는 단순 success/fail보다 훨씬 구체적인 원인 분류다.

예:

- `ok`
- `snapshot_not_found`
- `stale_snapshot`
- `not_found`
- `not_actionable`
- `no_state_change`
- `modal_not_open`
- `tab_scope_mismatch`
- `frame_scope_mismatch`
- `action_timeout`
- `blocked_ref_no_progress`

이 값 덕분에:

- 왜 실패했는지 설명 가능
- 어떤 recovery를 써야 하는지 분기 가능
- 지표 집계 가능

---

## 17. recovery policy는 어떻게 동작하나

recovery는 “LLM이 그냥 다시 생각한다”가 아니다.

현재 구조는 아래 policy ladder로 이해하는 것이 가장 정확하다.

1. deterministic precheck
2. stale/ref 복구
3. verify fallback
4. context shift
5. 다음 스텝의 LLM 재계획

즉 규칙 기반 가드와 LLM 재계획이 섞여 있는 stateful recovery policy다.

문서에서 recovery를 코드 순서처럼 읽기보다, 아래 책임 분리로 이해하는 편이 좋다.

- **validation gate**
  - stale/scope/not_actionable/no_state_change 같은 실패 유형 분류
- **binding recovery**
  - 최신 snapshot 재수집, stale ref 재매핑
- **interaction fallback**
  - backdrop, modal corner, alternate close, verify fallback
- **context transition**
  - 탭/섹션/페이지/스크롤 전환
- **replan**
  - 위 단계로도 해결되지 않으면 다음 LLM 결정으로 넘어감

즉 recovery는 절차적 나열이라기보다, 실패 유형별로 다른 policy branch가 개입하는 구조다.

대표 복구:

- stale면 최신 snapshot 재수집 후 ref 재매핑
- close 실패면 backdrop / modal corner fallback
- `no_state_change`, `not_actionable` 반복 시 context shift
- overlay intercept면 닫기 후보로 강제 전환

---

## 18. context shift의 정확한 의미

`retry`는 같은 문맥에서 다시 시도하는 것이다.

`context shift`는 현재 문맥이 막혔다고 보고 다른 문맥으로 이동하는 것이다.

예:

- 다른 탭 클릭
- 다른 섹션 열기
- 다음 페이지 이동
- 스크롤 후 새로운 후보 탐색

즉 recovery의 일종이지만, “같은 요소 재시도”가 아니라 “탐색 문맥 전환”이다.

---

## 19. Validation Rail은 어디에 속하나

Validation Rail은 메인 사용자 실행 경로의 핵심 validator가 아니라, 별도의 독립 QA / 벤치마킹 레일이다.

현재 역할:

- smoke/full rail 실행
- 재현성 확인
- 내부 benchmark metric 계산

현재 지원 host도 제한적이다.

따라서 발표에서는:

`Validation Rail은 메인 실행과 별도의 독립 QA 레일`  
이라고 설명하는 것이 정확하다.

---

## 20. 발표 때 가장 안전한 정리 문장

### 20.1 DOM/Host/LLM 관계

`Playwright가 DOM과 브라우저 상태를 수집하고, MCP Host가 이를 snapshot_id/ref_id로 구조화합니다. Agent는 이 snapshot을 element_id 기반 LLM 입력으로 다시 구성하고, LLM은 action + element_id만 결정합니다. 실제 실행은 Agent가 element_id를 snapshot_id + ref_id로 해석해 Host에 요청합니다.`

### 20.2 selector 대신 ref를 쓰는 이유

`selector는 현재 DOM에 다시 질의해야 하는 문자열이고, ref_id는 snapshot 안에 저장된 특정 요소 레코드의 키입니다. 그래서 ref_id 계약이 stale 검증, 디버깅, 재현성에 더 유리합니다.`

### 20.3 검증 로직

`저희는 클릭 성공 자체를 성공으로 보지 않고, state_change와 reason_code를 통해 실제 변화가 있었는지 확인합니다. 변화가 없으면 retry, resnapshot, context shift 같은 recovery로 복구를 시도합니다.`

### 20.4 `ref_id`가 붙기 전까지 생성 순서

면접에서 가장 많이 꼬리 질문이 들어오는 부분은 “정확히 어느 시점에 `dom_ref`, `snapshot_id`, `ref_id`가 생기느냐”이다.

가장 안전한 설명 순서는 아래와 같다.

1. Agent가 MCP Host에 `browser_snapshot`을 요청한다.
2. MCP Host가 Playwright로 현재 브라우저 페이지를 잡는다.
3. MCP Host가 Playwright의 `page.evaluate(...)` 계열 호출로 페이지 안 JS를 실행한다.
4. 페이지 안 JS가 DOM 요소를 순회하며 snapshot 대상 요소를 찾는다.
5. 각 요소마다 `getActionability(el)`로 visible/actionable/disabled/pointer-events/opacity/onViewport를 계산한다.
6. 같은 추출 루프 안에서 `assignDomRef(el)`를 호출해 `dom_ref`를 부여하거나 재사용한다.
7. 같은 시점에 `selector`, `text`, `bounding_box`, `attributes`, `actionable`, `visible_strict`를 합쳐 raw element record를 만든다.
8. 필요하면 `nearestSemanticContainer(el)`로 가장 가까운 semantic container를 찾아 `container_name`, `context_text`, `group_action_labels`, `container_dom_ref`를 주입한다.
9. raw element 목록이 Python 쪽 MCP Host로 반환된다.
10. Host가 `dom_ref` 기준으로 중복 제거를 수행한다.
11. Host가 `tab_index`, `epoch`, `dom_hash`를 계산해 `snapshot_id = session_id:epoch:domhash`를 만든다.
12. 마지막으로 최종 element record 목록에 `ref_id = t{tab_index}-f{frame_index}-e{idx}`를 부여한다.
13. `elements_by_ref[ref_id] = element_record` 형태로 snapshot record에 저장한다.

즉 핵심은:

- `dom_ref`는 raw element 추출 중 생긴다.
- `snapshot_id`와 `ref_id`는 raw element 목록이 Host로 돌아온 뒤 붙는다.
- `ref_id`는 `dom_ref`에 붙는 것이 아니라, `dom_ref`를 포함한 element record 전체에 붙는다.

### 20.5 `assignDomRef`와 actionability의 관계

`assignDomRef(el)`는 현재 DOM 노드에 GAIA 내부 참조값을 부여하거나 이미 있으면 재사용하는 함수다.

안전한 설명:

- `assignDomRef(el)`의 출력은 `"gaia-button-..."` 같은 synthetic ref 문자열이다.
- 이 값은 element record의 `dom_ref` 필드에 들어간다.
- `actionable`이나 `visible` 상태를 포함하는 값은 아니다.

즉 아래처럼 이해하는 것이 맞다.

```text
element record
├─ dom_ref          -> 이 record가 어떤 DOM 노드에서 왔는가
├─ actionable       -> 지금 상호작용 가능한가
├─ visible_strict   -> 실제로 보이는가
└─ ...
```

따라서 `dom_ref`와 `actionable`은 둘 다 snapshot 생성 중 같은 추출 루프에서 계산되지만,
하나는 식별용 필드이고 다른 하나는 상태 판정 필드다.

### 20.6 `snapshot_id`, `epoch`, `dom_hash`, `scope`, `idx` 의미

- `session_id`
  - 현재 브라우저/MCP 세션 식별자
- `epoch`
  - 같은 세션 안에서 snapshot을 몇 번째로 떴는지 나타내는 증가 카운터
- `dom_hash`
  - 현재 URL과 수집된 요소 목록으로 만든 DOM 상태 fingerprint
- `snapshot_id`
  - `session_id:epoch:domhash`
  - snapshot 전체 묶음을 가리키는 ID
- `scope`
  - 이 요소를 어느 탭/프레임 문맥에서 해석해야 하는지 나타내는 실행 범위

`ref_id = t{tab_index}-f{frame_index}-e{idx}`에서:

- `t{tab_index}`
  - 몇 번째 탭인지
- `f{frame_index}`
  - 몇 번째 프레임인지
- `e{idx}`
  - 이번 snapshot의 최종 element record 리스트에서 몇 번째 항목인지

중요:

- `idx`는 DOM의 영구 노드 번호가 아니다.
- `idx`는 dedupe가 끝난 뒤 `enumerate(elements)`로 붙는 최종 리스트 순번이다.
- 따라서 DOM이 변하거나 dedupe 결과가 달라지면 같은 버튼도 다음 snapshot에서 다른 `e{idx}`를 가질 수 있다.

### 20.7 `selector`와 `full_selector`는 정확히 언제 쓰나

실행 계약은 `snapshot_id + ref_id`지만, `selector`와 `full_selector`도 남겨 둔다.

역할은 아래와 같다.

- `selector`
  - 현재 컨텍스트 안에서의 짧은 질의 문자열
- `full_selector`
  - iframe 경로까지 포함한 더 강한 질의 힌트

이 값들은 주로 아래 흐름에서 쓰인다.

1. Agent가 새 snapshot에서 `selector/full_selector -> ref_id` 맵을 다시 만들 때
2. stale ref 복구 시 old/new snapshot 요소를 비교할 때
3. 디버깅/로그/메모리 suggestion에서 사람이 읽을 설명값이 필요할 때

즉 `full_selector`는 보통 “직접 실행용 주키”라기보다,
프레임 문맥까지 포함한 **재매핑/복구 힌트**라고 설명하는 것이 정확하다.

### 20.8 semantic container 로직의 한계와 더 범용적으로 가는 방향

현재 `nearestSemanticContainer()` 구현은 실용적인 휴리스틱이지, 완전 범용 알고리즘은 아니다.

특히 아래 부분은 휴리스틱 성격이 강하다.

- class 이름에 `card|item|row|list|result|product|course|subject` 패턴이 있는지 보는 부분

이 부분은 core execution contract가 아니라, LLM 입력 품질을 높이기 위한 implementation signal로 보는 것이 맞다.

따라서 발표에서는 아래처럼 말하는 것이 안전하다.

`현재 container 판정은 semantic tag/role을 우선 사용하고, class naming은 약한 보조 신호로 사용합니다. productionize한다면 이 신호는 config/weight 계층으로 분리하는 것이 적절합니다.`

더 범용적으로 가려면 보통 아래 방향이 낫다.

1. boolean 판정보다 score 기반 후보 선택으로 바꾸기
2. semantic tag/role에 높은 가중치 부여
3. heading 존재, CTA 밀도, 반복 sibling 구조, 텍스트 블록 밀도 같은 구조 신호 추가
4. page-level wrapper처럼 너무 큰 후보에는 감점
5. class 이름은 약한 보조 신호로만 사용
6. 최종적으로 “가장 가까운 후보”가 아니라 “최고 점수 후보”를 container로 선택

장기적으로는 “semantic/structural scoring + configurable weights” 방식으로 가야 더 범용적이라고 설명할 수 있다.

---

## 21. Playwright 호출부터 검증까지 end-to-end 전체 흐름

발표에서 “Playwright가 정확히 어디서 시작해서 어디서 끝나느냐”를 길게 설명해야 할 때는 아래 순서를 그대로 따라가면 된다.

### 21.1 snapshot 생성 단계

1. Agent가 MCP Host에 `browser_snapshot`을 요청한다.
2. MCP Host가 Playwright로 현재 브라우저 page/frame 객체를 잡는다.
3. MCP Host가 Playwright의 `page.evaluate(...)` 계열 호출로 페이지 안 JS를 실행한다.
4. 페이지 안 JS가 snapshot 대상 요소를 순회한다.
5. 각 요소에 대해 raw element record를 조립한다.
   - `getActionability(el)`로 visible/actionable/disabled/pointer-events/opacity/onViewport 계산
   - `assignDomRef(el)`로 `dom_ref` 생성 또는 재사용
   - `selector`, `text`, `role`, `aria-label`, `title`, `placeholder`, `bounding_box` 추출
   - `frame_index`, `frame_name`, `is_main_frame` 기록
6. 필요하면 같은 추출 시점에 `nearestSemanticContainer(el)`를 찾아 container 문맥을 주입한다.
   - `container_name`
   - `container_role`
   - `container_dom_ref`
   - `container_parent_dom_ref`
   - `context_text`
   - `group_action_labels`
7. raw element 목록이 Python 쪽 MCP Host로 반환된다.
8. Host가 `dom_ref` 기준으로 dedupe를 수행한다.
9. Host가 snapshot 단위 메타를 만든다.
   - `tab_index`
   - `epoch`
   - `dom_hash`
   - `snapshot_id = session_id:epoch:domhash`
10. Host가 최종 element record 목록에 `ref_id = t{tab_index}-f{frame_index}-e{idx}`를 부여한다.
11. 이 시점에 `scope = {tab_index, frame_index, is_main_frame}`도 같이 붙인다.
12. Host가 `elements_by_ref[ref_id] = element_record` 형태로 snapshot record를 만들고 세션에 저장한다.

핵심:

- `dom_ref`는 raw element 추출 중 생긴다.
- `snapshot_id`와 `ref_id`는 raw element 목록이 Host로 돌아온 뒤 붙는다.
- `ref_id`는 `dom_ref`에 붙는 것이 아니라, `dom_ref`를 포함한 element record 전체에 붙는다.

### 21.2 Agent가 LLM 입력으로 바꾸는 단계

1. Agent가 snapshot 응답을 받아 내부 캐시를 갱신한다.
   - `_active_snapshot_id`
   - `_element_ref_ids`
   - `_element_selectors`
   - `_element_full_selectors`
   - `_selector_to_ref_id`
   - `_element_scopes`
2. Agent는 raw element를 `DOMElement(id=idx, ...)` 형태로 바꾸고, 이 `id=idx`를 LLM에게 보여줄 `element_id`로 사용한다.
3. Agent는 snapshot 전체를 그대로 넘기지 않고, `flat records with contextual enrichment` 형태의 프롬프트용 요약을 만든다.
4. 이때 LLM이 보게 되는 대표 한 줄은 아래와 비슷하다.

```text
[41] <button> "담기" role=button container="컴퓨터네트워크" container-role="article" context="컴퓨터네트워크 3학점 전공필수" actions=[상세보기 | 담기 | 시간표 보기] role_ref=button(name="담기", nth=2)
```

즉 LLM은 단순히 버튼 text만 보는 것이 아니라:

- 요소 자체 텍스트
- role/type
- container 이름
- context text
- sibling action labels
- role 기반 recovery hint

를 함께 보고 선택한다.

### 21.3 LLM 결정 단계

1. Agent는 목표, 최근 액션, 최근 실패, DOM 요약을 묶어 LLM에 보낸다.
2. LLM은 보통 아래 형태로 응답한다.

```json
{
  "action": "click",
  "element_id": 41,
  "reasoning": "...",
  "confidence": 0.87
}
```

핵심:

- LLM은 `ref_id`를 직접 고르지 않는다.
- LLM은 사람이 읽기 쉬운 `element_id`를 고른다.

### 21.4 실행 전 해석 단계

1. Agent는 선택된 `element_id`를 현재 snapshot 안의 `ref_id`로 해석한다.
2. 이때 기본 경로는 `_element_ref_ids[element_id]`다.
3. 필요하면 `selector/full_selector -> ref_id` 맵도 보조로 사용한다.
4. 최종적으로 Agent는 `browser_act(session_id, snapshot_id, ref_id, action)` 형태로 MCP Host에 요청한다.

즉 실행 계약은 항상 `snapshot_id + ref_id` 기준이다.

### 21.5 Host의 ref 실행 단계

1. Host는 `snapshot_id`로 snapshot record를 찾는다.
2. `elements_by_ref[ref_id]`로 target element meta를 찾는다.
3. stale / snapshot_not_found / tab_scope_mismatch / frame_scope_mismatch를 먼저 검증한다.
4. 필요하면 최신 snapshot을 다시 떠서 stale ref 복구를 시도한다.
5. target meta에서 locator 복구 후보를 만든다.
   - `role_ref`
   - `dom_ref`
   - selector hint
6. 보통 `dom_ref`가 있으면 `[data-gaia-dom-ref="..."]` locator를 구성한다.
7. ambiguous match가 나오면 bbox 중심점으로 가장 가까운 후보를 고른다.
8. 최종 locator를 Playwright locator로 resolve한다.

### 21.6 Playwright 실제 액션 단계

1. Playwright가 click/fill/press/hover/select/scroll 등을 수행한다.
2. 이때 Host는 단순 transport success만 보는 것이 아니라, 실제 페이지 변화가 있었는지까지 확인해야 한다.

### 21.7 검증 단계

Host는 액션 직후 아래 증거를 수집한다.

- URL 변화
- DOM/text 변화
- modal 열림/닫힘 변화
- 인증 상태 변화
- count/badge 변화
- 기타 page evidence probe

이 결과를 종합해 아래를 만든다.

- `success`
- `effective`
- `reason_code`
- `state_change`
- `attempt_logs`
- `retry_path`

중요:

- click API가 성공했다고 해서 곧바로 목표 진행 성공으로 보지 않는다.
- 실제로 상태 변화가 있었는지를 `state_change`와 `reason_code`로 검증한다.

### 21.8 recovery 단계

검증 결과가 좋지 않으면 아래 recovery가 순서대로 개입할 수 있다.

1. deterministic precheck
2. stale/ref 복구
3. verify fallback
4. context shift
5. 다음 step의 LLM 재계획

대표 복구:

- stale이면 최신 snapshot 재수집 후 ref 재매핑
- close 실패면 backdrop / modal corner fallback
- `no_state_change`, `not_actionable` 반복 시 context shift
- overlay intercept면 닫기 후보로 강제 전환

즉 recovery는 “LLM이 그냥 다시 생각하는 것”이 아니라,
규칙 기반 가드 + ref 복구 + fallback + 재계획이 섞인 구조다.

### 21.9 종료 또는 다음 step

- 변화가 유효하면 action history를 갱신하고 다음 step으로 간다.
- 성공 조건을 만족하면 목표를 종료한다.
- 실패 조건이 누적되면 partial/fail로 종료한다.

한 줄 요약:

`Agent가 snapshot을 요청하면 MCP Host가 Playwright로 페이지 안 JS를 실행해 DOM을 raw element record로 구조화하고 snapshot_id/ref_id를 만든다. Agent는 이를 element_id 기반 LLM 입력으로 바꿔 다음 액션을 결정하게 하고, 실행 시에는 다시 snapshot_id + ref_id 계약으로 Host에 요청한다. Host는 Playwright로 실제 액션을 수행한 뒤 state_change/reason_code로 검증하고, 실패하면 stale 복구나 fallback을 거쳐 다음 step으로 이어간다.`
