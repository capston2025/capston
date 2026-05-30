from gaia.src.phase4.goal_driven.action_intent_runtime import update_intent_stats
from gaia.src.phase4.goal_driven.models import ActionType
from gaia.src.phase4.goal_driven.post_action_runtime import _post_action_reason_code
from gaia.src.phase4.goal_driven.ref_tracking_runtime import track_ref_outcome


class _Agent:
    def __init__(self) -> None:
        self._intent_stats = {}
        self._ineffective_ref_counts = {}

    def _loop_policy_value(self, _key: str, default: int) -> int:
        return default


def test_pointer_intercepted_counts_as_soft_intent_failure() -> None:
    agent = _Agent()

    update_intent_stats(
        agent,
        intent_key="click:skyview",
        success=False,
        changed=False,
        reason_code="pointer_intercepted",
    )

    assert agent._intent_stats["click:skyview"] == {"ok": 0, "soft_fail": 1, "hard_fail": 0}


def test_pointer_intercepted_marks_ref_temporarily_ineffective() -> None:
    agent = _Agent()

    track_ref_outcome(
        agent,
        ref_id="e133",
        reason_code="pointer_intercepted",
        success=False,
        changed=False,
    )

    assert agent._ineffective_ref_counts["e133"] == 1


def test_successful_mutating_action_without_state_change_is_reported_as_no_state_change() -> None:
    class _Decision:
        action = ActionType.CLICK

    assert (
        _post_action_reason_code(
            decision=_Decision(),
            reason_code="ok",
            success=True,
            changed=False,
        )
        == "no_state_change"
    )


def test_successful_mutating_action_with_deferred_observation_avoids_no_state_change() -> None:
    class _Decision:
        action = ActionType.CLICK

    assert (
        _post_action_reason_code(
            decision=_Decision(),
            reason_code="ok",
            success=True,
            changed=False,
            state_change={"post_action_observation_deferred": True},
        )
        == "observation_deferred"
    )


def test_successful_inspect_without_state_change_keeps_ok_reason() -> None:
    class _Decision:
        action = ActionType.INSPECT

    assert (
        _post_action_reason_code(
            decision=_Decision(),
            reason_code="ok",
            success=True,
            changed=False,
        )
        == "ok"
    )
