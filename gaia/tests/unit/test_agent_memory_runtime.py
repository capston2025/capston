from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.agent_memory_runtime import record_action_feedback
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType


class _FakeAgent:
    def __init__(self) -> None:
        self._last_exec_result = SimpleNamespace(reason_code="ok")
        self._action_feedback: list[str] = []
        self._last_goal_completion_source = "expected_signals"
        self.reason_codes: list[str] = []
        self.intent_updates: list[dict[str, object]] = []

    def _record_reason_code(self, code: str) -> None:
        self.reason_codes.append(code)

    def _update_intent_stats(self, **kwargs) -> None:
        self.intent_updates.append(dict(kwargs))


def test_record_action_feedback_appends_structured_run_state_ledger() -> None:
    agent = _FakeAgent()
    decision = ActionDecision(
        action=ActionType.CLICK,
        element_id=7,
        value="적용하기",
        reasoning="필터를 적용한다.",
    )

    record_action_feedback(
        agent,
        step_number=3,
        decision=decision,
        success=True,
        changed=True,
        error=None,
        reason_code="ok",
        state_change={
            "effective": True,
            "dom_changed": True,
            "text_digest_changed": True,
            "ignored": False,
        },
        intent_key="click:apply",
    )

    assert agent.reason_codes == ["ok"]
    assert len(agent._run_state_ledger) == 1
    entry = agent._run_state_ledger[0]
    assert entry["step"] == 3
    assert entry["action"] == "click"
    assert entry["element_id"] == 7
    assert entry["value"] == "적용하기"
    assert entry["changed"] is True
    assert entry["completion_source"] == "expected_signals"
    assert entry["dom_changed"] is True
    assert "text_digest_changed" in entry["state_keys"]
