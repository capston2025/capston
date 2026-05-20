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


def test_looks_like_stale_dom_wait_detects_dom_refresh_request() -> None:
    reasoning = (
        "직전 클릭으로 정렬 드롭다운이 열린 것으로 보이지만 현재 제공된 역할 트리에 "
        "낮은 가격순 옵션의 ref_id가 보이지 않습니다. DOM을 한 번 갱신해 실제 옵션 ref를 확인합니다."
    )

    assert GoalDrivenAgent._looks_like_stale_dom_wait_needing_resnapshot(reasoning) is True


def test_retry_decision_after_visual_dom_ref_mismatch_redecides_once(monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("gaia.src.phase4.goal_driven.agent.time.sleep", lambda sec: sleep_calls.append(sec))

    class _FakeAgent:
        def __init__(self) -> None:
            self._action_feedback: list[str] = []
            self.logs: list[str] = []
            self.analyze_count = 0
            self.analyze_force_refresh = False
            self.capture_count = 0
            self.decide_count = 0
            self._dom_cache_generation = 4
            self._dom_analyze_cache = {"key": (4, "s1", "", ""), "elements": ["old-dom"]}
            self._prev_raw_snapshot_text = '- button "이전 옵션" [ref=e1]'
            self.reason_codes: list[str] = []

        _looks_like_visual_dom_ref_mismatch = staticmethod(GoalDrivenAgent._looks_like_visual_dom_ref_mismatch)

        def _log(self, message: str) -> None:
            self.logs.append(message)

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

        def _analyze_dom(self, *, force_refresh: bool = False):
            self.analyze_count += 1
            self.analyze_force_refresh = force_refresh
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
    assert agent.analyze_force_refresh is True
    assert agent._dom_cache_generation == 5
    assert agent._dom_analyze_cache == {}
    assert agent._prev_raw_snapshot_text == ""
    assert agent.reason_codes == ["dom_force_resnapshot_visual_dom_ref_mismatch"]
    assert agent.capture_count == 1
    assert agent.decide_count == 1
    assert any("재수집했습니다" in message for message in agent._action_feedback)


def test_retry_decision_after_text_only_visual_escalation_captures_screenshot(monkeypatch) -> None:
    monkeypatch.setattr("gaia.src.phase4.goal_driven.agent.time.sleep", lambda sec: None)

    class _FakeAgent:
        def __init__(self) -> None:
            self._action_feedback: list[str] = []
            self.logs: list[str] = []
            self.capture_count = 0
            self.decide_screenshots: list[str] = []
            self._dom_cache_generation = 1
            self._dom_analyze_cache = {"key": (1, "s1", "", ""), "elements": ["old-dom"]}
            self._prev_raw_snapshot_text = "old snapshot"
            self.reason_codes: list[str] = []

        def _log(self, message: str) -> None:
            self.logs.append(message)

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

        def _analyze_dom(self, *, force_refresh: bool = False):
            assert force_refresh is True
            return ["fresh-dom"]

        def _capture_screenshot(self):
            self.capture_count += 1
            return "fresh-shot"

        def _decide_next_action(self, *, dom_elements, goal, screenshot, memory_context):
            self.decide_screenshots.append(screenshot)
            return ActionDecision(
                action=ActionType.CLICK,
                ref_id="e9",
                reasoning="화면 확인 후 다음 버튼 ref를 찾았습니다.",
            )

    agent = _FakeAgent()
    original = ActionDecision(
        action=ActionType.WAIT,
        reasoning="DOM 정보만으로는 진행 버튼이 보이는지 확인할 수 없어 화면을 다시 확인하기 위해 대기합니다.",
    )

    decision, dom_elements, screenshot, retried = GoalDrivenAgent._retry_decision_after_visual_dom_ref_mismatch(
        agent,
        decision=original,
        dom_elements=["old-dom"],
        goal=SimpleNamespace(),
        screenshot=None,
        memory_context="memory",
    )

    assert retried is True
    assert decision.action == ActionType.CLICK
    assert dom_elements == ["fresh-dom"]
    assert screenshot == "fresh-shot"
    assert agent.capture_count == 1
    assert agent.decide_screenshots == ["fresh-shot"]
    assert agent.reason_codes == ["dom_force_resnapshot_text_only_visual_escalation"]


def test_retry_decision_after_stale_dom_wait_forces_resnapshot(monkeypatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr("gaia.src.phase4.goal_driven.agent.time.sleep", lambda sec: sleep_calls.append(sec))

    class _FakeAgent:
        def __init__(self) -> None:
            self._action_feedback: list[str] = []
            self.logs: list[str] = []
            self.analyze_kwargs: list[dict[str, bool]] = []
            self._dom_cache_generation = 2
            self._dom_analyze_cache = {"key": (2, "s1", "", ""), "elements": ["stale-dom"]}
            self._prev_raw_snapshot_text = '- generic "낮은 가격순" [ref=e2]'
            self.reason_codes: list[str] = []

        def _log(self, message: str) -> None:
            self.logs.append(message)

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

        def _analyze_dom(self, *, force_refresh: bool = False):
            self.analyze_kwargs.append({"force_refresh": force_refresh})
            return ["fresh-options"]

        def _capture_screenshot(self):
            return "fresh-shot"

        def _decide_next_action(self, *, dom_elements, goal, screenshot, memory_context):
            assert dom_elements == ["fresh-options"]
            return ActionDecision(
                action=ActionType.CLICK,
                ref_id="e222",
                reasoning="강제 재수집 후 낮은 가격순 ref를 찾았습니다.",
            )

    agent = _FakeAgent()
    original = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "현재 제공된 역할 트리는 'DOM 변경 없음'이라 열린 옵션의 ref를 확인할 수 없습니다. "
            "낮은 가격순 옵션 ref를 추측하지 않기 위해 짧게 기다려 새 DOM/옵션 목록을 다시 확인합니다."
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
    assert decision.ref_id == "e222"
    assert dom_elements == ["fresh-options"]
    assert screenshot == "fresh-shot"
    assert sleep_calls == [0.35]
    assert agent.analyze_kwargs == [{"force_refresh": True}]
    assert agent._dom_cache_generation == 3
    assert agent._dom_analyze_cache == {}
    assert agent._prev_raw_snapshot_text == ""
    assert agent.reason_codes == ["dom_force_resnapshot_stale_dom_wait"]
