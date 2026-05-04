"""Phase 1: TestGoal participant extension contract tests."""

from __future__ import annotations

from gaia.src.phase4.goal_driven.models import TestGoal as GoalModel
from gaia.src.phase4.participants.models import (
    ContextMode,
    ParticipantSpec,
    TurnPolicyKind,
    TurnPolicySpec,
)


def _goal(**overrides: object) -> GoalModel:
    payload = {
        "id": "TC_PARTICIPANTS",
        "name": "participant extension",
        "description": "exercise participant-aware goal fields",
    }
    payload.update(overrides)
    return GoalModel(**payload)


def test_test_goal_defaults_preserve_single_participant_compatibility() -> None:
    goal = _goal()

    assert goal.participants == []
    assert goal.turn_policy is None
    assert goal.context_mode is ContextMode.ISOLATED


def test_test_goal_accepts_shared_system_context_toggle() -> None:
    goal = _goal(context_mode="shared_system")

    assert goal.context_mode is ContextMode.SHARED_SYSTEM


def test_test_goal_accepts_event_driven_turn_policy_and_participants() -> None:
    goal = _goal(
        participants=[{"id": "alice"}, ParticipantSpec(id="bob", display_name="Bob")],
        turn_policy={"kind": "event_driven", "wake_timeout_seconds": 1.0},
    )

    assert [p.id for p in goal.participants] == ["alice", "bob"]
    assert isinstance(goal.turn_policy, TurnPolicySpec)
    assert goal.turn_policy.kind is TurnPolicyKind.EVENT_DRIVEN
    assert goal.turn_policy.wake_timeout_seconds == 1.0
