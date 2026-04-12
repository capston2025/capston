from __future__ import annotations

from gaia.src.phase4.goal_driven.goal_constraint_runtime import enforce_goal_constraints_on_decision
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType


class _FakeSelf:
    def __init__(self) -> None:
        self._last_snapshot_evidence = {}
        self._runtime_phase = ""
        self._goal_constraints = {"collect_min": 1, "metric_label": "과목"}
        self._goal_metric_value = 0
        self._no_progress_counter = 0
        self._element_full_selectors = {}
        self._element_selectors = {}
        self._recent_click_element_ids = []
        self._logs = []

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _is_login_gate(dom_elements: list[object]) -> bool:
        return False

    @staticmethod
    def _is_collect_constraint_unmet() -> bool:
        return True

    @staticmethod
    def _is_navigational_href(_value: object) -> bool:
        return False

    @staticmethod
    def _contains_close_hint(_value: object) -> bool:
        return False

    @staticmethod
    def _goal_overlap_score(*_args: object) -> float:
        return 1.0

    @staticmethod
    def _loop_policy_value(_key: str, default: int) -> int:
        return default

    def _log(self, message: str) -> None:
        self._logs.append(message)

    @staticmethod
    def _pick_collect_element(_dom_elements: list[object]) -> tuple[int, str] | None:
        return 2, "목표 제약상 수집 단계 유지: 담기 | 이미 눌렀던 CTA보다 새 수집 후보를 우선"


def test_enforce_goal_constraints_keeps_achieved_wait_decision_before_collect_gate():
    fake = _FakeSelf()
    decision = ActionDecision(
        action=ActionType.WAIT,
        value="1000",
        reasoning="현재 내 시간표에 목표 과목 행이 직접 보여 이미 검증이 끝났습니다.",
        confidence=0.99,
        is_goal_achieved=True,
        goal_achievement_reason="재추가 반영 확인",
    )

    result = enforce_goal_constraints_on_decision(fake, decision, [])

    assert result.is_goal_achieved is True
    assert result.goal_achievement_reason == "재추가 반영 확인"
    assert result.action == ActionType.WAIT


def test_enforce_goal_constraints_keeps_original_collect_click_without_substitution() -> None:
    fake = _FakeSelf()
    fake._goal_constraints = {"collect_min": 3, "metric_label": "과목"}
    fake._goal_metric_value = 1
    fake._recent_click_element_ids = [1]
    decision = ActionDecision(
        action=ActionType.CLICK,
        element_id=1,
        ref_id="e67",
        reasoning="두 번째 과목을 추가하기 위해 담기 버튼을 누른다.",
        confidence=0.8,
        is_goal_achieved=False,
    )
    dom = [
        type(
            "El",
            (),
            {
                "id": 1,
                "text": "담기",
                "aria_label": "담기",
                "title": "담기",
                "href": "",
                "tag": "button",
                "type": "",
            },
        )(),
        type(
            "El",
            (),
            {
                "id": 2,
                "text": "담기",
                "aria_label": "담기",
                "title": "담기",
                "href": "",
                "tag": "button",
                "type": "",
            },
        )(),
    ]

    result = enforce_goal_constraints_on_decision(fake, decision, dom)

    assert result.action == ActionType.CLICK
    assert result.element_id == 1
    assert result.ref_id == "e67"
    assert result.reasoning == "두 번째 과목을 추가하기 위해 담기 버튼을 누른다."
