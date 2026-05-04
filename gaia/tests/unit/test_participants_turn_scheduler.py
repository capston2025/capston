"""Phase 1: EventDrivenScheduler 단위 테스트."""

from __future__ import annotations

import time

from gaia.src.phase4.participants.models import (
    BlackboardEntry,
    Message,
    MessageKind,
    TurnPolicySpec,
    WakeCondition,
    WakeConditionKind,
)
from gaia.src.phase4.participants.turn_scheduler import EventDrivenScheduler


def test_register_starts_idle_until_explicit_request() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.register("bob")
    assert s.next_participant() is None

    s.request_next("alice")
    assert s.next_participant() == "alice"
    assert s.next_participant() is None


def test_idle_with_inbox_condition_blocks_until_message() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.register("bob")
    # alice가 idle: bob의 메시지를 기다림
    s.mark_idle(
        "alice",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.INBOX_MESSAGE,
                from_participant="bob",
            )
        ],
    )
    assert s.next_participant() is None

    woken = s.on_message(Message(sender="bob", recipient="alice"))
    assert woken == ["alice"]
    assert s.next_participant() == "alice"


def test_idle_with_blackboard_condition() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.register("bob")

    s.mark_idle(
        "bob",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.BLACKBOARD_KEY,
                blackboard_key="message_sent",
            )
        ],
    )
    assert s.next_participant() is None

    irrelevant = BlackboardEntry(participant_id="alice", key="dom_changed")
    woken_none = s.on_blackboard(irrelevant)
    assert woken_none == []

    relevant = BlackboardEntry(participant_id="alice", key="message_sent")
    woken = s.on_blackboard(relevant)
    assert woken == ["bob"]
    assert s.next_participant() == "bob"


def test_request_next_grants_priority() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.register("bob")
    s.register("carol")

    # bob 우선 요청
    s.request_next("bob")
    assert s.next_participant() == "bob"
    # request/event가 없으면 다른 참여자를 자동으로 돌리지 않는다.
    assert s.next_participant() is None


def test_done_excluded_from_scheduling() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.register("bob")
    s.mark_done("alice")
    assert s.next_participant() is None
    s.request_next("bob")
    assert s.next_participant() == "bob"
    assert s.next_participant() is None
    assert s.all_done() is False  # bob은 아직 ready였다가 빠졌고 done 상태가 아님

    s.mark_done("bob")
    assert s.all_done() is True


def test_max_consecutive_turns_forces_yield_only_when_no_progress() -> None:
    policy = TurnPolicySpec(max_consecutive_turns=2)
    s = EventDrivenScheduler(policy=policy)
    s.register("alice")
    s.register("bob")

    # alice가 진척 없이 2번 행동했다면, 다음에는 강제 양보
    s.request_next("alice")
    assert s.next_participant() == "alice"
    s.record_outcome("alice", observation_changed=False)
    s.request_next("alice")
    assert s.next_participant() == "alice"
    s.record_outcome("alice", observation_changed=False)

    s.request_next("alice")
    nxt = s.next_participant()
    assert nxt is None  # round-robin으로 bob에게 넘기지 않는다.


def test_wake_timeout_releases_idle_participant() -> None:
    policy = TurnPolicySpec(wake_timeout_seconds=0.1)
    s = EventDrivenScheduler(policy=policy)
    s.register("alice")

    s.mark_idle(
        "alice",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.INBOX_MESSAGE,
                from_participant="ghost",
            )
        ],
    )
    assert s.next_participant() is None
    time.sleep(0.12)
    assert s.next_participant() == "alice"


def test_immediate_wake_condition_keeps_in_ready() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.mark_idle("alice", wake_conditions=[WakeCondition(kind=WakeConditionKind.IMMEDIATE)])
    # IMMEDIATE면 즉시 다시 ready
    assert s.next_participant() == "alice"


def test_broadcast_message_wakes_only_matching_subscribers() -> None:
    s = EventDrivenScheduler()
    s.register("alice")
    s.register("bob")
    s.register("carol")

    s.mark_idle(
        "bob",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.INBOX_MESSAGE,
                from_participant="alice",
            )
        ],
    )
    s.mark_idle(
        "carol",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.INBOX_MESSAGE,
                from_participant="alice",
            )
        ],
    )
    # carol에게만 매칭되도록 from filter 변경
    s.mark_idle(
        "carol",
        wake_conditions=[
            WakeCondition(
                kind=WakeConditionKind.INBOX_MESSAGE,
                from_participant="zoe",
            )
        ],
    )

    woken = s.on_message(Message(sender="alice", recipient="*"))
    assert "bob" in woken
    assert "carol" not in woken
