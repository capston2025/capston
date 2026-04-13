from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType


def test_fatal_llm_reason_prefers_codex_timeout_message() -> None:
    reason = GoalDrivenAgent._fatal_llm_reason(
        "codex exec failed: codex_exec_timeout:300s"
    )

    assert reason is not None
    assert "제한 시간 안에 끝나지 않았습니다" in reason
    assert "실행 인자/버전 오류" not in reason


def test_looks_like_visual_dom_ref_mismatch_detects_wait_reasoning() -> None:
    reasoning = (
        "스크린샷에서는 좋아요 버튼이 명확하게 보이지만, DOM 정보에는 해당 ref_id가 없습니다. "
        "DOM에 없는 ref를 추측할 수 없어 잠시 기다립니다."
    )

    assert GoalDrivenAgent._looks_like_visual_dom_ref_mismatch(reasoning) is True


def test_retry_decision_after_visual_dom_ref_mismatch_redecides_once(monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("gaia.src.phase4.goal_driven.agent.time.sleep", lambda sec: sleep_calls.append(sec))

    class _FakeAgent:
        def __init__(self) -> None:
            self._action_feedback: list[str] = []
            self.logs: list[str] = []
            self.analyze_count = 0
            self.capture_count = 0
            self.decide_count = 0

        _looks_like_visual_dom_ref_mismatch = staticmethod(GoalDrivenAgent._looks_like_visual_dom_ref_mismatch)

        def _log(self, message: str) -> None:
            self.logs.append(message)

        def _analyze_dom(self):
            self.analyze_count += 1
            return ["fresh-dom"]

        def _capture_screenshot(self):
            self.capture_count += 1
            return "fresh-shot"

        def _decide_next_action(self, *, dom_elements, goal, screenshot, memory_context):
            self.decide_count += 1
            assert dom_elements == ["fresh-dom"]
            assert screenshot == "fresh-shot"
            assert memory_context == "memory"
            return ActionDecision(
                action=ActionType.CLICK,
                ref_id="e777",
                reasoning="재수집 후 좋아요 ref가 보였습니다.",
            )

    agent = _FakeAgent()
    original = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "스크린샷에서는 좋아요 버튼이 명확하게 보이지만, 현재 DOM 요소 목록에는 "
            "해당 ref_id나 element_id가 없습니다."
        ),
    )

    decision, dom_elements, screenshot, retried = GoalDrivenAgent._retry_decision_after_visual_dom_ref_mismatch(
        agent,
        decision=original,
        dom_elements=["old-dom"],
        goal=SimpleNamespace(),
        screenshot="old-shot",
        memory_context="memory",
    )

    assert retried is True
    assert decision.action == ActionType.CLICK
    assert decision.ref_id == "e777"
    assert dom_elements == ["fresh-dom"]
    assert screenshot == "fresh-shot"
    assert sleep_calls == [0.35]
    assert agent.analyze_count == 1
    assert agent.capture_count == 1
    assert agent.decide_count == 1
    assert any("재수집했습니다" in message for message in agent._action_feedback)
