from gaia.src.phase4.goal_driven.goal_completion_helpers import evaluate_goal_target_completion
from gaia.src.phase4.goal_driven.models import DOMElement, TestGoal


class _CompletionAgent:
    def __init__(self) -> None:
        self._goal_constraints = {
            "mutation_direction": "increase",
            "require_no_navigation": True,
            "current_view_only": True,
        }
        self._goal_semantics = None
        self._last_snapshot_evidence = {}

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _run_goal_policy_closer(*, goal, dom_elements):
        return None

    @staticmethod
    def _goal_destination_terms(goal) -> list[str]:
        return []

    @staticmethod
    def _goal_target_terms(goal) -> list[str]:
        return ["Apple", "Store"]

    @staticmethod
    def _goal_quoted_terms(goal) -> list[str]:
        return []

    @staticmethod
    def _goal_text_blob(goal) -> str:
        return " ".join(
            [
                str(getattr(goal, "name", "") or ""),
                str(getattr(goal, "description", "") or ""),
                " ".join(str(item or "") for item in (getattr(goal, "success_criteria", None) or [])),
            ]
        )

    @staticmethod
    def _estimate_goal_metric_from_dom(dom_elements):
        return None


def test_goal_target_completion_skips_readonly_visibility_goal() -> None:
    agent = _CompletionAgent()
    goal = TestGoal(
        id="readonly-1",
        name="현재 Apple Store 홈 화면 확인",
        description="현재 Apple Store 홈 화면에서 iPhone 링크가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
        expected_signals=["text_visible", "cta_visible"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="iPhone",
            aria_label="iPhone",
            context_text="Apple Store 홈",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None
