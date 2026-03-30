from gaia.src.phase4.goal_driven.goal_verification_helpers import (
    can_finish_by_verification_transition,
    derive_achieved_signals,
)
from gaia.src.phase4.goal_driven.models import (
    ActionDecision,
    ActionType,
    DOMElement,
    TestGoal as GoalModel,
)


class _VerificationAgent:
    def __init__(self) -> None:
        self._goal_constraints = {}
        self._active_goal_text = ""

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _is_collect_constraint_unmet() -> bool:
        return False

    @staticmethod
    def _goal_quoted_terms(goal: object) -> list[str]:
        return []

    @staticmethod
    def _goal_target_terms(goal: object) -> list[str]:
        return []


def test_nonsemantic_filter_goal_can_finish_on_strong_result_transition() -> None:
    agent = _VerificationAgent()
    goal = GoalModel(
        id="G1",
        name="구분 필터 결과 변경",
        description="구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        expected_signals=["target_value_changed", "dom_changed"],
    )
    agent._active_goal_text = f"{goal.name} {goal.description}"
    decision = ActionDecision(action=ActionType.SELECT, value="교양", reasoning="필터를 적용한다.")

    allowed = can_finish_by_verification_transition(
        agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        state_change={"text_digest_changed": True, "list_count_changed": True},
        before_dom_count=120,
        after_dom_count=95,
        post_dom=[],
    )

    assert allowed is False


def test_nonsemantic_filter_goal_does_not_finish_on_dom_noise_only() -> None:
    agent = _VerificationAgent()
    goal = GoalModel(
        id="G2",
        name="구분 필터 결과 변경",
        description="구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        expected_signals=["target_value_changed", "dom_changed"],
    )
    agent._active_goal_text = f"{goal.name} {goal.description}"
    decision = ActionDecision(action=ActionType.SELECT, value="교양", reasoning="필터를 적용한다.")

    allowed = can_finish_by_verification_transition(
        agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        state_change={"dom_changed": True},
        before_dom_count=120,
        after_dom_count=120,
        post_dom=[],
    )

    assert allowed is False


def test_nonsemantic_filter_goal_finishes_when_expected_signals_are_all_met() -> None:
    agent = _VerificationAgent()
    goal = GoalModel(
        id="G2B",
        name="구분 필터 결과 변경",
        description="구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        expected_signals=["target_value_changed", "dom_changed"],
    )
    agent._active_goal_text = f"{goal.name} {goal.description}"
    decision = ActionDecision(action=ActionType.SELECT, value="교양", reasoning="필터를 적용한다.")

    allowed = can_finish_by_verification_transition(
        agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        state_change={"target_value_changed": True, "text_digest_changed": True},
        before_dom_count=120,
        after_dom_count=95,
        post_dom=[],
    )

    assert allowed is True


def test_nonsemantic_filter_goal_finishes_when_selection_is_reflected_in_dom_memory() -> None:
    agent = _VerificationAgent()
    agent._persistent_state_memory = [
        {
            "kind": "select",
            "expected_value": "교양",
            "previous_selected_value": "전체",
            "ref_id": "e31",
            "role_ref_name": "전체",
            "container_name": "검색 결과",
            "context_text": "검색 결과 | 필터",
        }
    ]
    goal = GoalModel(
        id="G2C",
        name="구분 필터 결과 변경",
        description="구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        expected_signals=["target_value_changed", "dom_changed"],
    )
    agent._active_goal_text = f"{goal.name} {goal.description}"
    decision = ActionDecision(action=ActionType.SELECT, value="교양", reasoning="필터를 적용한다.")
    post_dom = [
        DOMElement(
            id=31,
            ref_id="e31",
            tag="select",
            role="combobox",
            text="구분",
            selected_value="교양",
            role_ref_name="전체",
            container_name="검색 결과",
            context_text="검색 결과 | 필터",
        )
    ]

    allowed = can_finish_by_verification_transition(
        agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        state_change={"text_digest_changed": True},
        before_dom_count=120,
        after_dom_count=95,
        post_dom=post_dom,
    )

    assert allowed is True


def test_semantic_filter_goal_keeps_transition_completion_disabled() -> None:
    agent = _VerificationAgent()
    goal = GoalModel(
        id="G3",
        name="학점 필터 의미 검증",
        description="학점 필터가 실제 결과 과목의 학점과 맞게 동작하는지 의미 검증해줘.",
    )
    agent._active_goal_text = f"{goal.name} {goal.description}"
    decision = ActionDecision(action=ActionType.SELECT, value="1학점", reasoning="학점 필터를 적용한다.")

    allowed = can_finish_by_verification_transition(
        agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        state_change={"text_digest_changed": True, "list_count_changed": True},
        before_dom_count=120,
        after_dom_count=95,
        post_dom=[],
    )

    assert allowed is False


def test_persistence_signal_requires_url_change_and_persisted_state() -> None:
    agent = _VerificationAgent()
    agent._recent_signal_history = [
        {
            "action": "click",
            "pagination_candidate": True,
            "state_change": {"dom_changed": True, "list_count_changed": True},
        }
    ]
    agent._persistent_state_memory = [
        {
            "kind": "fill",
            "expected_value": "포용",
            "tokens": ["포용"],
            "ref_id": "e25",
        }
    ]
    goal = GoalModel(
        id="G4",
        name="페이지네이션 유지 검증",
        description="페이지네이션을 한 번 넘긴 뒤에도 검색 상태가 유지되는지 확인해줘.",
        expected_signals=["pagination_advanced", "persistence_evaluated"],
    )
    achieved = derive_achieved_signals(
        agent,
        goal=goal,
        state_change={"dom_changed": True},
        dom_elements=[],
    )
    assert achieved == ["pagination_advanced"]


def test_persistence_signal_passes_when_rows_still_match_after_url_change() -> None:
    agent = _VerificationAgent()
    agent._recent_signal_history = [
        {
            "action": "click",
            "pagination_candidate": True,
            "state_change": {"dom_changed": True, "list_count_changed": True},
        }
    ]
    agent._persistent_state_memory = [
        {
            "kind": "fill",
            "expected_value": "포용",
            "tokens": ["포용"],
            "ref_id": "e25",
        }
    ]
    goal = GoalModel(
        id="G5",
        name="페이지네이션 유지 검증",
        description="페이지네이션을 한 번 넘긴 뒤에도 검색 상태가 유지되는지 확인해줘.",
        expected_signals=["pagination_advanced", "persistence_evaluated"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            text="(HUSS국립부경대)포용사회와문화탐방1 | 미배정 | 월1,2",
            container_source="openclaw-role-tree",
            container_role="main",
            container_name="검색 결과",
            context_score_hint=10,
        )
    ]
    achieved = derive_achieved_signals(
        agent,
        goal=goal,
        state_change={"dom_changed": True},
        dom_elements=dom,
    )
    assert achieved == ["pagination_advanced", "persistence_evaluated"]


def test_visibility_signals_are_derived_from_visible_dom() -> None:
    agent = _VerificationAgent()
    goal = GoalModel(
        id="G6",
        name="메인 화면 로그인 CTA 확인",
        description="현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
        expected_signals=["text_visible", "cta_visible"],
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

    achieved = derive_achieved_signals(
        agent,
        goal=goal,
        state_change={},
        dom_elements=dom,
    )

    assert achieved == ["text_visible", "cta_visible"]


def test_result_consistency_signal_uses_filter_semantic_report() -> None:
    agent = _VerificationAgent()
    agent._last_filter_semantic_report = {"summary": {"goal_satisfied": True}}
    goal = GoalModel(
        id="G6",
        name="학점 필터 의미 검증",
        description="학점 필터가 실제 결과 과목의 학점과 맞게 동작하는지 의미 검증해줘.",
        expected_signals=["selection_reflected", "result_consistency"],
    )
    agent._persistent_state_memory = [
        {
            "kind": "select",
            "expected_value": "1학점",
            "previous_selected_value": "전체",
            "ref_id": "e31",
        }
    ]
    dom = [
        DOMElement(
            id=31,
            ref_id="e31",
            tag="select",
            role="combobox",
            text="학점",
            selected_value="1학점",
        )
    ]

    achieved = derive_achieved_signals(
        agent,
        goal=goal,
        state_change={},
        dom_elements=dom,
    )

    assert achieved == ["selection_reflected", "result_consistency"]
