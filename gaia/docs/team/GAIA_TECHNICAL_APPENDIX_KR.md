# GAIA Technical Appendix

발표 Q&A에서 바로 꺼내 설명할 수 있도록, 현재 코드 기준으로 `MCP Host + Playwright -> DOM 추출 -> snapshot/ref 구조화 -> Agent 내부 매핑 -> LLM 프롬프트 -> browser_act 실행 결과` 흐름을 정리한 부록이다.

## 1. 한 줄 구조

`Playwright가 현재 브라우저 DOM/상태를 수집 -> MCP Host가 snapshot_id/ref_id를 부여해 구조화 -> Agent가 이를 element_id 기반 LLM 입력으로 재구성 -> LLM이 action + element_id를 결정 -> Agent가 element_id를 snapshot_id + ref_id로 해석 -> MCP Host가 실제 액션을 수행하고 state_change/reason_code를 반환`

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
  "tag": "button",
  "dom_ref": "gaia-button-...",
  "selector": "text=로그인",
  "full_selector": "iframe[name=\"...\"] >>> text=로그인",
  "text": "로그인",
  "attributes": {
    "role": "button",
    "aria-label": "",
    "title": "",
    "gaia-visible-strict": "true",
    "gaia-actionable": "true",
    "gaia-disabled": "false",
    "options": [],
    "selected_value": ""
  },
  "bounding_box": {
    "x": 100,
    "y": 220,
    "width": 120,
    "height": 36,
    "center_x": 160,
    "center_y": 238
  },
  "element_type": "button",
  "actionable": true,
  "visible_strict": true,
  "frame_index": 0,
  "frame_name": "main",
  "is_main_frame": true,
  "ref_id": "t0-f0-e14",
  "scope": {
    "tab_index": 0,
    "frame_index": 0,
    "is_main_frame": true
  }
}
```

중요한 점:

- `selector`는 보조 메타데이터다.
- 실행 계약은 `snapshot_id + ref_id`다.
- 실제 locator 복구에는 `dom_ref`, role-ref hint, selector hint가 함께 쓰일 수 있다.

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

container 후보 기준:

- tag가 `li`, `tr`, `article`, `section`
- role이 `listitem`, `row`, `article`, `region`, `group`
- class에 `card`, `item`, `row`, `list`, `result`, `product`, `course`, `subject`

즉 완전한 DOM 트리 전체가 아니라, “이 버튼이 속한 카드/행/상품/과목 묶음”을 찾는 방식이다.

그 후 container 기준으로 아래 문맥을 붙인다.

- `container_name`
  - heading, lead link, strong/title 후보, own accessible name 순으로 대표 이름 추출
- `container_role`
  - container의 role 또는 tag
- `container_dom_ref`
  - container에도 synthetic dom_ref 부여
- `container_parent_dom_ref`
  - 상위 semantic container ref
- `context_text`
  - container 전체 텍스트를 2줄 정도로 압축
- `group_action_labels`
  - 같은 container 안의 버튼/링크 라벨 목록

즉 “담기 버튼이 여러 개 있는데 각 어떤 담기인지”는

- 버튼 자체 text
- 그 버튼이 속한 container 이름
- 그 container의 context text
- 같은 그룹의 sibling actions

를 조합해서 구분한다.

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

`현재 구현은 full DOM tree를 그대로 LLM에 전달하지 않고, flat element list에 nearest semantic container 기반 문맥을 주입하는 방식입니다. 그래서 비슷한 CTA가 여러 개 있어도 카드 이름, context text, sibling action labels를 통해 구분합니다.`

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
  "success": true,
  "effective": true,
  "reason_code": "ok",
  "reason": "ref action executed and state changed",
  "snapshot_id_used": "...",
  "ref_id_used": "t0-f0-e14",
  "stale_recovered": false,
  "transport_success": true,
  "locator_found": true,
  "interaction_success": true,
  "state_change": { "...flags..." },
  "live_texts": [],
  "retry_path": ["1:dom_ref"],
  "attempt_count": 1,
  "attempt_logs": [
    {
      "attempt": 1,
      "mode": "dom_ref",
      "selector": "[data-gaia-dom-ref=\"...\"]",
      "frame_index": 0,
      "reason_code": "ok",
      "state_change": { "...flags..." }
    }
  ],
  "screenshot": "...base64...",
  "current_url": "...",
  "tab_id": 0,
  "targetId": 0
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

## 17. recovery는 어떻게 동작하나

recovery는 “LLM이 그냥 다시 생각한다”가 아니다.

현재 구조는:

1. deterministic precheck
2. stale/ref 복구
3. verify fallback
4. context shift
5. 다음 스텝의 LLM 재계획

즉 규칙 기반 가드와 LLM 재계획이 섞여 있다.

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
