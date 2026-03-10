from __future__ import annotations

import json
from typing import List, Optional

from .exploratory_models import ExplorationDecision, PageState, TestableAction


def decide_next_exploration_action(
    agent,
    page_state: PageState,
    screenshot: Optional[str],
    action_count: int,
) -> ExplorationDecision:
    """LLM에게 다음 탐색 액션 결정 요청"""

    testable_actions = agent._generate_testable_actions(page_state)
    agent._log(f"   - 테스트 가능한 액션: {len(testable_actions)}개")
    if not testable_actions:
        preview = [
            f"{el.tag}:{agent._element_label(el)}"
            for el in page_state.interactive_elements[:10]
        ]
        agent._log(f"   - 요소 샘플: {preview}")

    if not testable_actions:
        if agent.config.test_navigation and agent._action_frontier:
            frontier_action = agent._select_frontier_action(page_state, [])
            if frontier_action:
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=frontier_action,
                    reasoning="BFS 큐에 남은 액션으로 계속 탐색",
                    confidence=0.4,
                )
        return ExplorationDecision(
            should_continue=False,
            reasoning="더 이상 테스트할 요소가 없습니다",
            confidence=1.0,
        )

    if str(agent._runtime_phase or "").upper() == "AUTH":
        def _auth_haystack(action: TestableAction) -> str:
            return str(action.description or "").strip().lower()

        auth_fill_keywords = (
            "아이디",
            "username",
            "user id",
            "email",
            "이메일",
            "password",
            "비밀번호",
            "otp",
            "captcha",
            "인증",
        )
        auth_submit_keywords = ("로그인", "login", "log in", "sign in")
        auth_signup_keywords = ("회원가입", "sign up", "signup", "register")

        auth_fill_actions = [
            a
            for a in testable_actions
            if a.action_type == "fill"
            and any(k in _auth_haystack(a) for k in auth_fill_keywords)
        ]
        auth_fill_actions = [
            a for a in auth_fill_actions if agent._auth_field_needs_input(a, page_state)
        ]
        if auth_fill_actions:
            auth_fill_actions.sort(
                key=lambda x: (
                    agent._auth_field_order(agent._auth_field_bucket(x)),
                    -float(x.priority),
                )
            )
            return ExplorationDecision(
                should_continue=True,
                selected_action=auth_fill_actions[0],
                reasoning="AUTH 단계: 인증 입력 필드 우선",
                confidence=0.9,
            )

        auth_login_clicks = [
            a
            for a in testable_actions
            if a.action_type == "click"
            and any(k in _auth_haystack(a) for k in auth_submit_keywords)
            and not any(k in _auth_haystack(a) for k in auth_signup_keywords)
        ]
        if auth_login_clicks:
            auth_login_clicks.sort(key=lambda x: float(x.priority), reverse=True)
            return ExplorationDecision(
                should_continue=True,
                selected_action=auth_login_clicks[0],
                reasoning="AUTH 단계: 로그인 제출 액션 우선",
                confidence=0.9,
            )

    state_key = agent._state_key(page_state, testable_actions)
    agent._current_state_key = state_key
    visited_actions = agent._state_action_history.get(state_key, set())
    unvisited = [
        action
        for action in testable_actions
        if f"{action.element_id}:{action.action_type}" not in visited_actions
    ]
    if unvisited:
        if agent._has_pending_inputs(page_state):
            fill_actions = [action for action in unvisited if action.action_type == "fill"]
            if fill_actions:
                fill_actions.sort(key=lambda x: x.priority, reverse=True)
                return ExplorationDecision(
                    should_continue=True,
                    selected_action=fill_actions[0],
                    reasoning="미입력 필드 우선 입력",
                    confidence=0.75,
                )
        unvisited_keys = {
            f"{action.element_id}:{action.action_type}" for action in unvisited
        }
        testable_actions = sorted(
            testable_actions,
            key=lambda action: (
                1 if f"{action.element_id}:{action.action_type}" in unvisited_keys else 0,
                float(action.priority),
            ),
            reverse=True,
        )

    if (
        agent.config.test_navigation
        and not agent._has_pending_inputs(page_state)
        and not unvisited
    ):
        frontier_action = agent._select_frontier_action(page_state, testable_actions)
        if frontier_action:
            return ExplorationDecision(
                should_continue=True,
                selected_action=frontier_action,
                reasoning="BFS 탐색: 큐에 등록된 액션 우선 선택",
                confidence=0.6,
            )
        if agent._action_frontier:
            agent._log("ℹ️ BFS 큐는 남아있지만 현재 페이지에서 매칭 실패")

    memory_context = agent._memory_context()
    prompt = build_exploration_prompt(
        agent=agent,
        page_state=page_state,
        testable_actions=testable_actions,
        action_count=action_count,
        memory_context=memory_context,
    )

    try:
        action_signature = agent._action_signature(testable_actions)
        cache_key = agent._get_llm_cache_key(prompt, screenshot, action_signature)
        response_text = agent._llm_cache.get(cache_key)

        if response_text:
            agent._log("🧠 LLM 캐시 hit")
        else:
            semantic_text = agent._semantic_cache_text(page_state, testable_actions)
            response_text = agent._semantic_cache_lookup(semantic_text, action_signature)

        if not response_text:
            if screenshot:
                response_text = agent.llm.analyze_with_vision(prompt, screenshot)
            else:
                response_text = agent._call_llm_text_only(prompt)

            agent._llm_cache[cache_key] = response_text
            if len(agent._llm_cache) > 200:
                agent._llm_cache.pop(next(iter(agent._llm_cache)))
            agent._save_llm_cache()

            semantic_text = agent._semantic_cache_text(page_state, testable_actions)
            agent._semantic_cache_store(semantic_text, response_text, action_signature)

        decision = parse_exploration_decision(agent, response_text, testable_actions)

        if not decision.should_continue and testable_actions:
            fallback_action = sorted(
                testable_actions, key=lambda x: x.priority, reverse=True
            )[0]
            return ExplorationDecision(
                should_continue=True,
                selected_action=fallback_action,
                reasoning="남은 액션이 있어 탐색 지속",
                confidence=0.5,
            )

        return decision

    except Exception as e:
        agent._log(f"LLM 결정 실패: {e}")
        fatal_reason = agent._fatal_llm_reason(str(e))
        if fatal_reason:
            return ExplorationDecision(
                should_continue=False,
                reasoning=fatal_reason,
                confidence=1.0,
            )
        if testable_actions:
            return ExplorationDecision(
                should_continue=True,
                selected_action=testable_actions[0],
                reasoning=f"LLM 오류로 기본 액션 선택: {e}",
                confidence=0.3,
            )
        return ExplorationDecision(
            should_continue=False,
            reasoning="테스트할 요소 없음",
            confidence=1.0,
        )


def build_exploration_prompt(
    agent,
    page_state: PageState,
    testable_actions: List[TestableAction],
    action_count: int,
    memory_context: str = "",
) -> str:
    """탐색 프롬프트 생성"""

    actions_text = "\n".join(
        [
            f"[{i}] {action.action_type.upper()}: {action.description} (우선순위: {action.priority:.2f})"
            for i, action in enumerate(testable_actions[:60])
        ]
    )

    recent_history = (
        "\n".join(agent._action_history[-5:]) if agent._action_history else "없음 (첫 탐색)"
    )

    issues_summary = (
        f"{len(agent._found_issues)}개 이슈 발견" if agent._found_issues else "아직 이슈 없음"
    )

    return f"""당신은 웹 애플리케이션 탐색 테스트 에이전트입니다.
화면의 모든 UI 요소를 자율적으로 탐색하고 테스트하여 버그를 찾는 것이 목표입니다.

## 현재 상황
- URL: {page_state.url}
- 탐색 진행: {action_count}/{agent.config.max_actions} 액션
- 테스트 완료 요소: {len(agent._tested_elements)}개
- 발견된 이슈: {issues_summary}

## 최근 수행한 액션
{recent_history}

## 도메인 실행 기억(KB)
{memory_context or '없음'}

## 테스트 가능한 액션 목록 (우선순위 순)
{actions_text}

## 지시사항
1. **우선순위 고려**: 미테스트 요소를 우선 선택하세요
2. **다양성**: 같은 유형만 계속 테스트하지 말고 다양한 UI 요소를 테스트하세요
3. **탐색 확대**: 방문하지 않은 링크나 새 페이지로 이어질 요소를 우선 선택하세요
4. **외부 링크 제외**: 현재 도메인 밖으로 이동하는 링크는 선택하지 마세요
5. **BFS 탐색**: 새로 발견된 내부 링크는 발견 순서대로 우선 선택하세요
6. **버그 탐지**: 에러 메시지, 깨진 UI, 예상치 못한 동작을 찾으세요
7. **종료 조건**: 더 이상 테스트할 요소가 없거나, 충분히 탐색했다면 should_continue: false

## 입력값 생성 규칙 (fill 액션인 경우)
- **중요**: 화면에 테스트 계정 정보가 보이면 반드시 그 값을 사용하세요!
- 사용자명/아이디 필드: input_values에 "username" 키로 값 지정
- 비밀번호 필드: input_values에 "password" 키로 값 지정
- 이메일 필드: "test.explorer@example.com"
- 일반 텍스트: "Test input"

## 응답 형식 (JSON만, 마크다운 없이)
{{
    "should_continue": true | false,
    "selected_action_index": 액션 인덱스 (0-59, 선택 안 하면 null),
    "input_values": {{"username": "사용자명", "password": "비밀번호"}},  // fill 액션인 경우, 필요한 키만 포함
    "reasoning": "이 액션을 선택한 이유 또는 종료 이유",
    "confidence": 0.0~1.0,
    "expected_outcome": "예상되는 결과"
}}

JSON 응답:"""


def parse_exploration_decision(
    agent,
    response_text: str,
    testable_actions: List[TestableAction],
) -> ExplorationDecision:
    """LLM 응답을 ExplorationDecision으로 파싱"""

    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    if not text:
        return ExplorationDecision(
            should_continue=False,
            reasoning="LLM 오류: empty_response_from_model",
            confidence=0.0,
        )

    if not text.startswith("{"):
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first:last + 1].strip()

    try:
        data = json.loads(text)

        should_continue = data.get("should_continue", True)
        action_index = data.get("selected_action_index")
        selected_action = None

        if action_index is not None and 0 <= action_index < len(testable_actions):
            selected_action = testable_actions[action_index]

        return ExplorationDecision(
            should_continue=should_continue,
            selected_action=selected_action,
            input_values=data.get("input_values", {}),
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", 0.5),
            expected_outcome=data.get("expected_outcome", ""),
        )

    except (json.JSONDecodeError, ValueError) as e:
        agent._log(f"JSON 파싱 실패: {e}")
        if testable_actions:
            return ExplorationDecision(
                should_continue=True,
                selected_action=testable_actions[0],
                reasoning=f"파싱 오류로 기본 액션 선택: {e}",
                confidence=0.3,
            )
        return ExplorationDecision(
            should_continue=False,
            reasoning="파싱 오류 및 액션 없음",
            confidence=0.0,
        )
