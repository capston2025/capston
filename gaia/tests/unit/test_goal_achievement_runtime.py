from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.goal_achievement_runtime import validate_goal_achievement_claim
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_constraints = {"mutation_direction": "increase"}
        self._persistent_state_memory = []
        self._recent_signal_history = []
        self._last_exec_result = None
        self._consecutive_wait_count = 2
        self._last_filter_semantic_report = None
        self._goal_state_cache = {}
        self._auth_completed_fields = set()
        self._judge_response = ""

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _goal_target_terms(goal: object) -> list[str]:
        return ["포용사회와문화탐방1"]

    @staticmethod
    def _goal_destination_terms(goal: object) -> list[str]:
        return ["시간표", "내 시간표"]

    @staticmethod
    def _goal_quoted_terms(goal: object) -> list[str]:
        return ["포용사회와문화탐방1"]

    @staticmethod
    def _goal_text_blob(goal: object) -> str:
        fields = [getattr(goal, "name", ""), getattr(goal, "description", "")]
        fields.extend(getattr(goal, "success_criteria", []) or [])
        return " ".join(str(field or "").strip() for field in fields if str(field or "").strip()).lower()

    @staticmethod
    def _constraint_failure_reason() -> None:
        return None

    @staticmethod
    def _run_goal_policy_closer(*, goal: object, dom_elements: list[DOMElement]) -> None:
        return None

    def _call_llm_text_only(self, prompt: str) -> str:
        self._last_judge_prompt = prompt
        return self._judge_response

    def _format_dom_for_llm(self, elements: list[DOMElement]) -> str:
        return "\n".join(
            str(getattr(item, "text", "") or "").strip()
            for item in elements
            if str(getattr(item, "text", "") or "").strip()
        )

    def _wait_completion_ready(self, dom_elements: list[DOMElement] | None = None) -> bool:
        from gaia.src.phase4.goal_driven.goal_achievement_runtime import wait_completion_ready

        return wait_completion_ready(self, dom_elements)


def test_validate_goal_achievement_claim_accepts_wait_when_destination_row_is_visible():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목을 바로 추가",
        description="이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인",
        success_criteria=["내 시간표에 포용사회와문화탐방1이 다시 보이는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        value='{"text":"(HUSS국립부경대)포용사회와문화탐방1"}',
        reasoning=(
            "현재 열린 내 시간표 surface 안에 포용사회와문화탐방1의 직접 행이 보이고 "
            "삭제 후 다시 바로 추가까지 수행했으므로 목표를 달성했습니다."
        ),
        confidence=0.98,
        is_goal_achieved=True,
        goal_achievement_reason="내 시간표에 다시 반영됨",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            context_text="내 시간표 | 총 9개 과목 • 25학점 | 시간표에서 제거",
            group_action_labels=["시간표에서 제거"],
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_when_destination_anchor_and_row_action_are_separate():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목을 바로 추가",
        description="이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인",
        success_criteria=["내 시간표에 포용사회와문화탐방1이 다시 보이는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 내 시간표 화면에서 포용사회와문화탐방1 행과 같은 줄의 제거 CTA가 확인되어 목표를 달성했습니다.",
        confidence=0.95,
        is_goal_achieved=True,
        goal_achievement_reason="내 시간표에 다시 반영됨",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="내 시간표",
            aria_label="내 시간표",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            container_ref_id="row-1",
            tag="div",
            role="generic",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            context_text="온라인 / 시간외 과목",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            container_ref_id="row-1",
            tag="button",
            role="button",
            text="제거",
            aria_label="제거",
            context_text="온라인 / 시간외 과목",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_keeps_wait_rejected_for_source_add_row_even_with_page_destination_anchor():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목을 바로 추가",
        description="이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인",
        success_criteria=["내 시간표에 포용사회와문화탐방1이 다시 보이는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 페이지에 내 시간표 앵커가 있고 포용사회와문화탐방1 행이 보여 목표를 달성했다고 판단합니다.",
        confidence=0.72,
        is_goal_achieved=True,
        goal_achievement_reason="반영됨",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="내 시간표",
            aria_label="내 시간표",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            container_ref_id="row-1",
            tag="div",
            role="generic",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            context_text="검색 결과 | 미배정",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            container_ref_id="row-1",
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            context_text="검색 결과 | 미배정",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다."


def test_validate_goal_achievement_claim_accepts_wait_for_generic_search_change_proof():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="검색 결과 변경 검증",
        description="과목 검색창에 키워드를 입력해 검색 결과 목록이 실제로 바뀌는지 검증",
        success_criteria=["검색 결과 목록이 실제로 바뀌는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        value='{"timeMs":1000}',
        reasoning=(
            "현재 화면에서 검색 결과가 변경되어 검색 결과(총 35개 중 20개 표시)와 "
            "디지털 관련 과목들이 표시되고 있으므로 결과 목록 변화가 반영되었다고 판단합니다."
        ),
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="검색 결과 변경 반영",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="검색 결과(총 35개 중 20개 표시)",
            aria_label="검색 결과(총 35개 중 20개 표시)",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="(HUSS)디지털포용과스타트업창업실무",
            aria_label="(HUSS)디지털포용과스타트업창업실무",
            context_text="검색 결과(총 35개 중 20개 표시)",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_for_readonly_visibility_absence_observation():
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": ""}
    goal = SimpleNamespace(
        name="현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인",
        description="추가 조작 없이 종료해줘.",
        success_criteria=["현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인"],
        expected_signals=["text_visible", "cta_visible"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "현재 메인 화면에는 과목 검색과 바로 추가 버튼만 보이고 "
            "로그인 버튼이나 로그인 유도 CTA는 보이지 않습니다. "
            "가시 여부 확인이 끝났으므로 추가 조작 없이 종료합니다."
        ),
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="현재 화면에서 로그인 CTA 미노출 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="과목 검색",
            aria_label="과목 검색",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_rejects_generic_search_wait_without_specific_evidence():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="검색 결과 변경 검증",
        description="과목 검색창에 키워드를 입력해 검색 결과 목록이 실제로 바뀌는지 검증",
        success_criteria=["검색 결과 목록이 실제로 바뀌는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        value='{"timeMs":1000}',
        reasoning="현재 검색 결과 목록이 표시되고 있으므로 변화가 반영된 것으로 판단합니다.",
        confidence=0.7,
        is_goal_achieved=True,
        goal_achievement_reason="검색 결과 표시",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="검색 결과(총 35개 중 20개 표시)",
            aria_label="검색 결과(총 35개 중 20개 표시)",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다."


def test_validate_goal_achievement_claim_accepts_wait_when_expected_signals_are_met() -> None:
    agent = _FakeAgent()
    agent._persistent_state_memory = [
        {
            "kind": "select",
            "expected_value": "전핵",
            "previous_selected_value": "전체",
            "ref_id": "e33",
            "role_ref_name": "전체",
            "container_name": "검색",
            "context_text": "검색 | 전체 | &service",
        }
    ]
    agent._last_exec_result = SimpleNamespace(state_change={"text_digest_changed": True})
    goal = SimpleNamespace(
        name="구분 필터 결과 변경",
        description="구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        success_criteria=["결과 목록이 실제로 바뀌는지 확인"],
        expected_signals=["target_value_changed", "dom_changed"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="필터 선택값과 결과 목록 변화가 모두 확인되어 목표를 달성했습니다.",
        confidence=0.92,
        is_goal_achieved=True,
        goal_achievement_reason="필터 결과 변경 확인",
    )
    dom = [
        DOMElement(
            id=33,
            ref_id="e33",
            tag="select",
            role="combobox",
            text="구분",
            selected_value="전핵",
            role_ref_name="전체",
            container_name="검색",
            context_text="검색 | 전체 | &service",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=716,
            tag="div",
            role="generic",
            text="전핵 | 과목 A",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_rejects_signup_goal_without_completion_signal() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="회원가입 완료 확인",
        description="회원가입이 정상적으로 끝났는지 확인",
        success_criteria=["회원가입 완료 여부 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        reasoning="회원가입 화면이 보이므로 목표를 달성했다고 판단합니다.",
        confidence=0.8,
        is_goal_achieved=True,
        goal_achievement_reason="회원가입 화면 진입",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="회원가입",
            aria_label="회원가입",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "회원가입 목표는 화면 진입만으로 성공으로 보지 않습니다. 회원가입 제출 및 완료 신호가 필요합니다."


def test_validate_goal_achievement_claim_accepts_wait_on_recent_transition_even_when_expected_signals_are_missing() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": "clear"}
    agent._last_exec_result = SimpleNamespace(
        state_change={
            "dom_changed": True,
            "text_digest_changed": True,
        }
    )
    goal = SimpleNamespace(
        name="캡스톤디자인 과목을 추가 후 다시 삭제",
        description="추가한 뒤 삭제까지 끝났는지 확인",
        success_criteria=["추가 후 삭제가 완료되었는지 확인"],
        expected_signals=["post_action_verified", "ui_transition_recorded"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="방금 추가 후 삭제 전환이 반영되었고 현재는 삭제 완료 상태이므로 종료합니다.",
        confidence=0.91,
        is_goal_achieved=True,
        goal_achievement_reason="추가 후 삭제 전환 완료",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="status",
            text="'캡스톤디자인' 삭제 완료",
            context_text="상태 토스트",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_for_readonly_visibility_goal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {
        "require_no_navigation": True,
        "current_view_only": True,
    }
    goal = SimpleNamespace(
        name="메인 화면 로그인 CTA 확인",
        description="현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인하고 추가 조작 없이 종료",
        success_criteria=["로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인"],
        expected_signals=["text_visible", "cta_visible"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 메인 화면 상단에 로그인 버튼이 직접 보이므로 추가 조작 없이 종료합니다.",
        confidence=0.95,
        is_goal_achieved=True,
        goal_achievement_reason="로그인 CTA 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="로그인",
            aria_label="로그인",
            context_text="상단 배너 | 인증",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_via_generic_judge_for_late_response_goal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._judge_response = """
```json
{
  "success": true,
  "blocked": false,
  "reason": "입력한 문장이 전송되었고 그에 대한 응답 본문이 현재 화면에 직접 보여 목표가 완료되었습니다.",
  "confidence": 0.96
}
```
""".strip()
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "입력한 문장은 이미 전송되었고, 현재 화면에 assistant 응답인 "
            "'안녕! 그냥 너랑 대화하려고 기다리고 있었지'가 직접 표시됩니다."
        ),
        confidence=0.93,
        is_goal_achieved=True,
        goal_achievement_reason="응답 본문 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="대화 입력",
            is_visible=True,
            is_enabled=True,
        )
    ]
    for idx in range(2, 47):
        dom.append(
            DOMElement(
                id=idx,
                tag="div",
                role="generic",
                text=f"filler-{idx}",
                context_text="sidebar",
                is_visible=True,
                is_enabled=True,
            )
        )
    dom.append(
        DOMElement(
            id=47,
            ref_id="e319",
            tag="div",
            role="article",
            text="안녕! 그냥 너랑 대화하려고 기다리고 있었지 🙂 너는 지금 뭐 하고 있어?",
            context_text="assistant response",
            is_visible=True,
            is_enabled=True,
        )
    )

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "judge"
    if hasattr(agent, "_last_judge_prompt"):
        assert "assistant response" in agent._last_judge_prompt


def test_validate_goal_achievement_claim_accepts_wait_via_reasoning_result_quote_without_judge() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "이전 단계에서 메시지를 보냈고, 현재 화면에 응답인 "
            "'안녕! 😊 지금 너랑 대화하고 있지 😊 뭐 도와줄까?'가 표시되어 목표가 달성되었습니다."
        ),
        confidence=0.94,
        is_goal_achieved=True,
        goal_achievement_reason="응답 본문 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="내 메시지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="article",
            text="안녕! 😊 지금 너랑 대화하고 있지 😊 뭐 도와줄까?",
            context_text="assistant response",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_defers_first_wait_for_transient_loading_surface() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._consecutive_wait_count = 1
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 화면 증거상 목표가 완료되었습니다.",
  "confidence": 0.95
}
""".strip()
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="응답이 보이기 시작했으니 목표가 끝난 것 같습니다.",
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="응답 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="status",
            role="status",
            text="생각 중",
            context_text="loading surface",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="진행률 16%",
            context_text="progress overlay",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "첫 WAIT는 완료 판정을 내리지 않고 한 번 더 상태 변화를 관찰합니다."
    assert agent._last_goal_completion_source == ""


def test_validate_goal_achievement_claim_allows_first_wait_for_stable_zero_state_surface() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": "clear"}
    agent._consecutive_wait_count = 1
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "삭제 이후 stable zero-state가 직접 확인되어 목표가 완료되었습니다.",
  "confidence": 0.95
}
""".strip()
    goal = SimpleNamespace(
        name="위시리스트 비우기",
        description="로그인 후 위시리스트를 모두 비우고 총 0학점 상태인지 확인해줘.",
        success_criteria=["총 0학점과 empty-state 문구 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 화면에 총 0학점과 빈 위시리스트 상태가 직접 보여 목표가 완료되었습니다.",
        confidence=0.92,
        is_goal_achieved=True,
        goal_achievement_reason="zero-state 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="총 0학점",
            context_text="위시리스트 요약",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="status",
            text="담은 과목이 없어요.",
            context_text="empty state",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "judge"


def test_validate_goal_achievement_claim_rejects_loading_quote_as_result() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning='현재 화면에는 "생각 중" 상태가 표시되어 결과를 생성하고 있습니다.',
        confidence=0.8,
        is_goal_achieved=True,
        goal_achievement_reason="로딩 중",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="내 메시지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="status",
            role="status",
            text="생각 중",
            context_text="loading",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다."


def test_validate_goal_achievement_claim_allows_judge_to_bypass_missing_expected_signals() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 화면 증거상 목표가 완료되었습니다.",
  "confidence": 0.93
}
""".strip()
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
        expected_signals=["response_visible"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="사용자 입력과 응답이 모두 화면에 보여 목표가 달성되었습니다.",
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="응답 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="내 메시지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="article",
            text="안녕! 반가워요.",
            context_text="assistant response",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "judge"
