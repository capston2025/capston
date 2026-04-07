from gaia.src.phase4.goal_driven.goal_verification_helpers import derive_achieved_signals
from gaia.src.phase4.goal_driven.models import (
    DOMElement,
    TestGoal as GoalModel,
)


class _VerificationAgent:
    def __init__(self) -> None:
        self._goal_constraints = {}
        self._active_goal_text = ""
        self._active_url = ""
        self._goal_state_cache = {}
        self._auth_completed_fields = set()
        self._element_full_selectors = {}
        self._element_selectors = {}
        self._reason_codes = []

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

    @staticmethod
    def _tokenize_text(value: object) -> list[str]:
        return [token for token in str(value or "").lower().split() if token]

    def _record_reason_code(self, code: str) -> None:
        self._reason_codes.append(code)


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


def test_auth_completed_signal_is_derived_from_auth_state_transition() -> None:
    agent = _VerificationAgent()
    goal = GoalModel(
        id="G7",
        name="로그인 완료 확인",
        description="로그인 후 인증이 완료되었는지 확인해줘.",
        expected_signals=["auth_completed"],
    )

    achieved = derive_achieved_signals(
        agent,
        goal=goal,
        state_change={"auth_state_changed": True},
        dom_elements=[],
    )

    assert achieved == ["auth_completed"]
