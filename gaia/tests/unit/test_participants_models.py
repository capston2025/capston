"""Phase 1: participants.models 단위 테스트."""

from __future__ import annotations

from gaia.src.phase4.participants.models import (
    BlackboardEntry,
    ContextMode,
    Message,
    MessageKind,
    ParticipantCredentialRequest,
    ParticipantPlan,
    ParticipantSpec,
    TurnControl,
    TurnControlStatus,
    TurnPolicyKind,
    TurnPolicySpec,
    WakeCondition,
    WakeConditionKind,
)


def test_participant_spec_defaults() -> None:
    spec = ParticipantSpec(id="alice")
    assert spec.id == "alice"
    assert spec.display_name == ""
    assert spec.role == ""
    assert spec.persona == ""
    assert spec.start_url is None
    assert spec.test_data == {}
    assert spec.context_args == {}
    assert spec.resolved_display_name() == "alice"


def test_participant_spec_resolved_display_name_uses_alias() -> None:
    spec = ParticipantSpec(id="alice", display_name="Alice The Buyer")
    assert spec.resolved_display_name() == "Alice The Buyer"


def test_message_broadcast_flag() -> None:
    direct = Message(sender="alice", recipient="bob")
    bcast = Message(sender="alice", recipient="*")
    assert not direct.is_broadcast()
    assert bcast.is_broadcast()


def test_message_kind_default_is_msg() -> None:
    m = Message(sender="alice", recipient="bob")
    assert m.kind is MessageKind.MSG


def test_blackboard_entry_basic() -> None:
    entry = BlackboardEntry(participant_id="alice", key="logged_in", value=True)
    assert entry.participant_id == "alice"
    assert entry.key == "logged_in"
    assert entry.value is True
    assert entry.tags == []


def test_turn_policy_defaults_event_driven() -> None:
    policy = TurnPolicySpec()
    assert policy.kind is TurnPolicyKind.EVENT_DRIVEN
    assert policy.wake_timeout_seconds > 0
    assert policy.max_consecutive_turns >= 1


def test_wake_condition_inbox_match_filter_by_sender() -> None:
    cond = WakeCondition(
        kind=WakeConditionKind.INBOX_MESSAGE,
        from_participant="alice",
    )
    msg_from_alice = Message(sender="alice", recipient="bob")
    msg_from_carol = Message(sender="carol", recipient="bob")
    assert cond.matches_message(msg_from_alice) is True
    assert cond.matches_message(msg_from_carol) is False


def test_wake_condition_inbox_match_filter_by_kind() -> None:
    cond = WakeCondition(
        kind=WakeConditionKind.INBOX_MESSAGE,
        message_kind=MessageKind.SIGNAL,
    )
    sig = Message(sender="a", recipient="b", kind=MessageKind.SIGNAL)
    msg = Message(sender="a", recipient="b", kind=MessageKind.MSG)
    assert cond.matches_message(sig) is True
    assert cond.matches_message(msg) is False


def test_wake_condition_blackboard_key_match() -> None:
    cond = WakeCondition(
        kind=WakeConditionKind.BLACKBOARD_KEY,
        blackboard_key="message_sent",
    )
    yes = BlackboardEntry(participant_id="a", key="message_sent")
    no = BlackboardEntry(participant_id="a", key="dom_changed")
    assert cond.matches_blackboard(yes) is True
    assert cond.matches_blackboard(no) is False


def test_wake_condition_kind_mismatch_is_false() -> None:
    immediate = WakeCondition(kind=WakeConditionKind.IMMEDIATE)
    msg = Message(sender="a", recipient="b")
    entry = BlackboardEntry(participant_id="a", key="x")
    assert immediate.matches_message(msg) is False
    assert immediate.matches_blackboard(entry) is False


def test_context_mode_values() -> None:
    assert ContextMode.ISOLATED.value == "isolated"
    assert ContextMode.SHARED_SYSTEM.value == "shared_system"


def test_participant_plan_defaults_to_explicit_skill_contract() -> None:
    plan = ParticipantPlan(required=True, participants=[ParticipantSpec(id="sender")])

    assert plan.skill == "multi_user_interaction"
    assert plan.required is True
    assert plan.expected_events == [
        "message_sent",
        "message_received",
        "notification_visible",
    ]


def test_participant_credential_request_defaults_username_password() -> None:
    request = ParticipantCredentialRequest(participant_id="receiver")

    assert request.participant_id == "receiver"
    assert request.fields == ["username", "password"]
    assert request.required is True


def test_turn_control_wait_for_blackboard_contract() -> None:
    control = TurnControl(
        status=TurnControlStatus.WAIT_FOR,
        wait_for=[
            WakeCondition(
                kind=WakeConditionKind.BLACKBOARD_KEY,
                blackboard_key="message_sent",
            )
        ],
    )

    assert control.status is TurnControlStatus.WAIT_FOR
    assert control.wait_for[0].blackboard_key == "message_sent"
