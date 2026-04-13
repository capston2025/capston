from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.execute_goal_progress import _evaluate_post_action_judge_completion
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_constraints = {"mutation_direction": "clear"}
        self._goal_semantics = SimpleNamespace(goal_kind="clear_list", mutate_required=True)
        self._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 화면의 zero-state가 직접 보여 목표가 완료되었습니다.",
  "confidence": 0.95
}
""".strip()
        self._persistent_state_memory = []
        self._action_history = []
        self._action_feedback = []
        self._last_exec_result = None

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _goal_quoted_terms(_goal: object) -> list[str]:
        return []

    @staticmethod
    def _goal_target_terms(_goal: object) -> list[str]:
        return ["위시리스트"]

    @staticmethod
    def _goal_destination_terms(_goal: object) -> list[str]:
        return []

    def _call_llm_text_only(self, _prompt: str) -> str:
        return self._judge_response

    def _format_dom_for_llm(self, elements: list[DOMElement]) -> str:
        return "\n".join(str(getattr(item, "text", "") or "") for item in elements)


def test_post_action_judge_completion_uses_judge_for_changed_clear_flow() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="위시리스트 비우기",
        description="모든 담은 과목을 비운 뒤 빈 상태를 확인",
        success_criteria=["빈 상태 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e949",
        reasoning="마지막 삭제 버튼을 눌렀습니다.",
        confidence=0.88,
        is_goal_achieved=False,
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="div",
            role="status",
            text="담은 과목이 없어요.",
            context_text="empty state",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = _evaluate_post_action_judge_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        post_dom=post_dom,
    )

    assert reason == "현재 화면의 zero-state가 직접 보여 목표가 완료되었습니다."
