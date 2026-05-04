"""Phase 1: ParticipantRegistry 골격 테스트 (라우팅 + bootstrap 하위 호환)."""

from __future__ import annotations

import pytest

from gaia.src.phase4.participants.models import (
    ContextMode,
    Message,
    MessageKind,
    ParticipantSpec,
    TurnPolicySpec,
    WakeCondition,
    WakeConditionKind,
)
from gaia.src.phase4.participants.registry import ParticipantRegistry


def test_bootstrap_with_empty_specs_creates_default_participant() -> None:
    reg = ParticipantRegistry.bootstrap(
        specs=[],
        default_start_url="https://example.com",
        default_test_data={"email": "x@x.com"},
    )
    assert list(reg.participants.keys()) == ["default"]
    assert reg.is_multi() is False
    default = reg.get("default")
    assert default.spec.start_url == "https://example.com"
    assert default.spec.test_data == {"email": "x@x.com"}
    assert reg.active_participant_id == "default"


def test_bootstrap_inherits_default_start_url_when_spec_unset() -> None:
    specs = [
        ParticipantSpec(id="alice"),
        ParticipantSpec(id="bob", start_url="https://b.example.com"),
    ]
    reg = ParticipantRegistry.bootstrap(
        specs=specs,
        default_start_url="https://a.example.com",
    )
    assert reg.get("alice").spec.start_url == "https://a.example.com"
    assert reg.get("bob").spec.start_url == "https://b.example.com"
    assert reg.is_multi() is True


def test_bootstrap_merges_test_data_with_spec_winning() -> None:
    specs = [ParticipantSpec(id="alice", test_data={"email": "alice@x"})]
    reg = ParticipantRegistry.bootstrap(
        specs=specs,
        default_test_data={"email": "default@x", "shared": True},
    )
    data = reg.get("alice").spec.test_data
    assert data["email"] == "alice@x"
    assert data["shared"] is True


def test_bootstrap_rejects_duplicate_ids() -> None:
    specs = [ParticipantSpec(id="alice"), ParticipantSpec(id="alice")]
    with pytest.raises(ValueError):
        ParticipantRegistry.bootstrap(specs=specs)


def test_deliver_routes_direct_message_and_wakes_recipient() -> None:
    reg = ParticipantRegistry.bootstrap(
        specs=[ParticipantSpec(id="alice"), ParticipantSpec(id="bob")]
    )
    # 두 참여자 모두 한 번씩 ready 소진
    reg.scheduler.next_participant()
    reg.scheduler.next_participant()

    reg.scheduler.mark_idle(
        "bob",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.INBOX_MESSAGE,
                from_participant="alice",
            )
        ],
    )

    msg = Message(sender="alice", recipient="bob", kind=MessageKind.MSG)
    woken = reg.deliver(msg)

    assert woken == ["bob"]
    assert list(reg.get("bob").inbox) == [msg]


def test_deliver_broadcast_excludes_sender() -> None:
    reg = ParticipantRegistry.bootstrap(
        specs=[
            ParticipantSpec(id="alice"),
            ParticipantSpec(id="bob"),
            ParticipantSpec(id="carol"),
        ]
    )
    msg = Message(sender="alice", recipient="*")
    reg.deliver(msg)
    assert len(reg.get("alice").inbox) == 0
    assert len(reg.get("bob").inbox) == 1
    assert len(reg.get("carol").inbox) == 1


def test_deliver_unknown_recipient_raises() -> None:
    reg = ParticipantRegistry.bootstrap(specs=[ParticipantSpec(id="alice")])
    with pytest.raises(KeyError):
        reg.deliver(Message(sender="alice", recipient="ghost"))


def test_post_blackboard_writes_and_wakes_subscribers() -> None:
    reg = ParticipantRegistry.bootstrap(
        specs=[ParticipantSpec(id="alice"), ParticipantSpec(id="bob")]
    )
    reg.scheduler.next_participant()
    reg.scheduler.next_participant()

    reg.scheduler.mark_idle(
        "bob",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.BLACKBOARD_KEY,
                blackboard_key="logged_in",
            )
        ],
    )

    woken = reg.post_blackboard("alice", "logged_in", value=True, step=1)
    assert woken == ["bob"]
    entries = reg.blackboard.read_recent(limit=5)
    assert len(entries) == 1
    assert entries[0].key == "logged_in"


def test_set_active_validates_id() -> None:
    reg = ParticipantRegistry.bootstrap(
        specs=[ParticipantSpec(id="alice"), ParticipantSpec(id="bob")]
    )
    reg.set_active("bob")
    assert reg.active_participant_id == "bob"
    with pytest.raises(KeyError):
        reg.set_active("ghost")


def test_default_context_mode_and_turn_policy() -> None:
    reg = ParticipantRegistry.bootstrap(specs=[])
    assert reg.context_mode is ContextMode.ISOLATED
    assert isinstance(reg.turn_policy, TurnPolicySpec)


def test_display_name_resolver_falls_back_to_id() -> None:
    reg = ParticipantRegistry.bootstrap(
        specs=[ParticipantSpec(id="alice", display_name="Alice The Buyer")]
    )
    resolver = reg.display_name_resolver()
    assert resolver("alice") == "Alice The Buyer"
    # 알 수 없는 id는 그대로 반환
    assert resolver("ghost") == "ghost"
