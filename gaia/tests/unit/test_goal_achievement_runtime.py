from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.goal_achievement_runtime import validate_goal_achievement_claim
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_constraints = {"mutation_direction": "increase"}

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
