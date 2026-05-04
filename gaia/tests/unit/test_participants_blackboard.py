"""Phase 1: Blackboard 단위 테스트 (sync + async subscribe)."""

from __future__ import annotations

import asyncio

from gaia.src.phase4.participants.blackboard import Blackboard


def test_write_and_read_recent() -> None:
    bb = Blackboard()
    bb.write("alice", "step_started", value=1, step=1)
    bb.write("bob", "step_started", value=1, step=1)
    bb.write("alice", "message_sent", value={"text": "hi"}, step=2)

    recent = bb.read_recent(limit=10)
    assert len(recent) == 3
    # 가장 최근부터 정렬
    assert recent[0].key == "message_sent"
    assert recent[-1].key == "step_started"


def test_read_recent_filter_by_participant() -> None:
    bb = Blackboard()
    bb.write("alice", "x", 1)
    bb.write("bob", "x", 2)
    bb.write("alice", "y", 3)

    only_alice = bb.read_recent(participant_id="alice", limit=10)
    assert {e.key for e in only_alice} == {"x", "y"}
    assert all(e.participant_id == "alice" for e in only_alice)


def test_read_recent_filter_by_key() -> None:
    bb = Blackboard()
    bb.write("alice", "ping", 1)
    bb.write("bob", "pong", 2)
    bb.write("alice", "ping", 3)

    pings = bb.read_recent(key="ping", limit=10)
    assert len(pings) == 2
    assert all(e.key == "ping" for e in pings)


def test_latest_returns_most_recent_for_key() -> None:
    bb = Blackboard()
    bb.write("a", "k", 1)
    bb.write("a", "k", 2)
    bb.write("a", "other", 3)
    latest = bb.latest("k")
    assert latest is not None
    assert latest.value == 2

    assert bb.latest("missing") is None


def test_to_prompt_summary_first_person_for_viewer() -> None:
    bb = Blackboard()
    bb.write("alice", "logged_in", step=1)
    bb.write("bob", "logged_in", step=1)
    bb.write("alice", "message_sent", value="hello", step=2)

    summary = bb.to_prompt_summary("alice")
    # alice 자신은 'I' 로 표시되어야 함
    assert "I :: logged_in" in summary
    assert "I :: message_sent" in summary
    # bob은 그대로 'bob'
    assert "bob :: logged_in" in summary


def test_to_prompt_summary_uses_name_resolver() -> None:
    bb = Blackboard()
    bb.write("p_001", "joined", step=1)

    summary = bb.to_prompt_summary(
        "viewer",
        name_resolver=lambda pid: "Bob" if pid == "p_001" else pid,
    )
    assert "Bob :: joined" in summary


def test_to_prompt_summary_empty_when_no_entries() -> None:
    bb = Blackboard()
    assert bb.to_prompt_summary("alice") == ""

def test_wait_for_resolves_immediately_if_predicate_already_satisfied() -> None:
    async def run() -> None:
        bb = Blackboard()
        bb.write("alice", "logged_in")

        entry = await bb.wait_for(lambda e: e.key == "logged_in", timeout=1.0)
        assert entry is not None
        assert entry.key == "logged_in"

    asyncio.run(run())


def test_wait_for_resolves_when_write_arrives() -> None:
    async def run() -> None:
        bb = Blackboard()

        async def writer() -> None:
            await asyncio.sleep(0.05)
            bb.write("alice", "message_sent", value="hi")

        task = asyncio.create_task(writer())
        entry = await bb.wait_for(lambda e: e.key == "message_sent", timeout=2.0)
        await task
        assert entry is not None
        assert entry.value == "hi"

    asyncio.run(run())


def test_wait_for_returns_none_on_timeout() -> None:
    async def run() -> None:
        bb = Blackboard()
        entry = await bb.wait_for(lambda e: e.key == "nope", timeout=0.05)
        assert entry is None

    asyncio.run(run())
