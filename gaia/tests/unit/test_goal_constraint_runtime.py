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

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _is_login_gate(dom_elements: list[object]) -> bool:
        return False

    @staticmethod
    def _is_collect_constraint_unmet() -> bool:
        return True


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
