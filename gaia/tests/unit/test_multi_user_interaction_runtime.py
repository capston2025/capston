from __future__ import annotations

import json
from typing import Any

from gaia.src.phase4.goal_driven import multi_user_interaction_runtime as runtime
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, TestGoal as GoalModel
from gaia.src.phase4.participants.models import (
    ParticipantCredentialRequest,
    ParticipantPlan,
    ParticipantSpec,
    TurnControl,
    TurnControlStatus,
    WakeCondition,
    WakeConditionKind,
)


class _FakeAgent:
    def __init__(self) -> None:
        self.mcp_host_url = "http://mcp.test"
        self.session_id = "base-session"
        self._base_session_id = "base-session"
        self._participant_registry = None
        self._participant_plan = None
        self._active_participant_id = ""
        self._handoff_state: dict[str, Any] = {}
        self.intervention_payload: dict[str, Any] | None = None
        self.reason_codes: list[str] = []

    def _request_user_intervention(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.intervention_payload = payload
        return {
            "proceed": True,
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }

    def _record_reason_code(self, code: str) -> None:
        self.reason_codes.append(code)


def _goal(**overrides: Any) -> GoalModel:
    payload = {
        "id": "TC_CHAT",
        "name": "chat delivery",
        "description": "sender sends a message and receiver sees it",
        "start_url": "https://chat.example.test",
    }
    payload.update(overrides)
    return GoalModel(**payload)


def _plan() -> ParticipantPlan:
    return ParticipantPlan(
        required=True,
        reason="채팅 수신 여부는 두 사용자 세션이 필요하다.",
        participants=[
            ParticipantSpec(id="sender", role="sender", display_name="Sender"),
            ParticipantSpec(id="receiver", role="receiver", display_name="Receiver"),
        ],
        credential_requests=[
            ParticipantCredentialRequest(participant_id="sender"),
            ParticipantCredentialRequest(participant_id="receiver"),
        ],
    )


def test_parse_multi_user_interaction_request_from_wait_value() -> None:
    payload = {
        "skill": "multi_user_interaction",
        "participant_plan": {
            "required": True,
            "participants": [{"id": "sender"}, {"id": "receiver"}],
        },
    }

    plan = runtime.parse_multi_user_interaction_request(json.dumps(payload))

    assert plan is not None
    assert plan.required is True
    assert [p.id for p in plan.participants] == ["sender", "receiver"]


def test_activate_required_false_does_not_boot_registry() -> None:
    agent = _FakeAgent()
    goal = _goal()
    plan = ParticipantPlan(required=False, participants=[ParticipantSpec(id="sender")])

    ok, reason = runtime.activate_multi_user_interaction(agent, goal, plan)

    assert ok is True
    assert "단일 유저 경로" in reason
    assert agent._participant_registry is None
    assert goal.participants == []


def test_activate_requests_scoped_credentials_and_keeps_participant_data_isolated(monkeypatch) -> None:
    created_session_ids: list[str] = []
    created_profile_names: list[str] = []

    def fake_create(_agent: Any, binding: Any) -> bool:
        created_session_ids.append(binding.session_id)
        created_profile_names.append(binding.profile_name)
        return True

    monkeypatch.setattr(runtime, "_create_participant_browser_context", fake_create)
    agent = _FakeAgent()
    goal = _goal()

    ok, reason = runtime.activate_multi_user_interaction(agent, goal, _plan())

    assert ok is True
    assert "multi_user_interaction 활성화" in reason
    assert agent.intervention_payload is not None
    assert agent.intervention_payload["reason_code"] == "multi_user_credentials_required"
    assert "sender_username" in agent.intervention_payload["fields"]
    assert "receiver_password" in agent.intervention_payload["fields"]
    assert set(created_session_ids) == {
        "base-session::participant::sender",
        "base-session::participant::receiver",
    }
    assert len(set(created_profile_names)) == 2
    assert all(name.startswith("gaia-") for name in created_profile_names)
    assert all(":" not in name and "_" not in name for name in created_profile_names)

    sender = next(p for p in goal.participants if p.id == "sender")
    receiver = next(p for p in goal.participants if p.id == "receiver")
    assert sender.test_data == {"username": "sender-user", "password": "sender-pass"}
    assert receiver.test_data == {"username": "receiver-user", "password": "receiver-pass"}
    assert "username" not in goal.test_data
    assert "password" not in goal.test_data
    assert "sender_username" not in goal.test_data
    assert "sender_password" not in goal.test_data
    assert "receiver_username" not in goal.test_data
    assert "receiver_password" not in goal.test_data


def test_create_participant_browser_context_ensures_profile_before_navigation(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_ensure_profile(_raw_base_url: str, *, profile: str, timeout: Any) -> Any:
        calls.append(("profile", {"profile": profile, "timeout": timeout}))

        class _Result:
            status_code = 200
            payload = {"success": True, "profile": profile}

        return _Result()

    def fake_execute(_raw_base_url: str, *, action: str, params: dict[str, Any], timeout: Any) -> Any:
        calls.append((action, dict(params)))

        class _Result:
            status_code = 200
            payload = {"success": True}

        return _Result()

    monkeypatch.setattr(runtime, "ensure_browser_profile", fake_ensure_profile)
    monkeypatch.setattr(runtime, "execute_mcp_action", fake_execute)
    agent = _FakeAgent()
    binding = runtime.ParticipantBrowserBinding(
        participant_id="sender",
        session_id="base::participant::sender",
        profile_name="gaia-test-sender",
        start_url="https://chat.example.test",
    )

    assert runtime._create_participant_browser_context(agent, binding) is True
    assert calls[0] == ("profile", {"profile": "gaia-test-sender", "timeout": (5, 45)})
    assert calls[1][0] == "browser_act"
    assert calls[1][1]["profile"] == "gaia-test-sender"
    assert calls[1][1]["action"] == "goto"


def test_participant_test_data_for_prompt_uses_only_active_participant(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "message_text": "hello receiver",
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True

    runtime.begin_participant_turn(agent)
    sender_data = runtime.participant_test_data_for_prompt(agent, goal)
    assert sender_data["message_text"] == "hello receiver"
    assert sender_data["username"] == "sender-user"
    assert sender_data["password"] == "sender-pass"
    assert sender_data["participant_id"] == "sender"
    assert "receiver_username" not in sender_data

    agent._active_participant_id = "receiver"
    agent._participant_registry.set_active("receiver")
    receiver_data = runtime.participant_test_data_for_prompt(agent, goal)
    assert receiver_data["message_text"] == "hello receiver"
    assert receiver_data["username"] == "receiver-user"
    assert receiver_data["password"] == "receiver-pass"
    assert receiver_data["participant_id"] == "receiver"
    assert "sender_username" not in receiver_data


def test_begin_and_complete_participant_turn_routes_session_and_blackboard(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True

    first = runtime.begin_participant_turn(agent)
    assert first == "sender"
    assert agent.session_id == "base-session::participant::sender"

    decision = ActionDecision(
        action=ActionType.CLICK,
        participant_id="sender",
        reasoning="send the message",
        blackboard_event="message_sent",
        blackboard_payload={"text": "hello"},
        turn_control=TurnControl(status=TurnControlStatus.DONE),
        next_participant="receiver",
    )
    runtime.complete_participant_turn(
        agent,
        decision=decision,
        success=True,
        changed=True,
        step_count=3,
    )

    registry = agent._participant_registry
    assert registry.blackboard.latest("message_sent") is not None
    assert registry.blackboard.latest("message_sent").value["text"] == "hello"

    second = runtime.begin_participant_turn(agent)
    assert second == "receiver"
    assert agent.session_id == "base-session::participant::receiver"

    receiver_decision = ActionDecision(
        action=ActionType.CLICK,
        participant_id="receiver",
        reasoning="receiver replied and hands control back",
        turn_control=TurnControl(status=TurnControlStatus.WAIT_FOR),
        next_participant="sender",
    )
    runtime.complete_participant_turn(
        agent,
        decision=receiver_decision,
        success=True,
        changed=True,
        step_count=4,
    )

    assert runtime.begin_participant_turn(agent) == "sender"


def test_complete_turn_does_not_auto_round_robin_without_handoff(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True

    first = runtime.begin_participant_turn(agent)
    assert first == "sender"

    decision = ActionDecision(
        action=ActionType.CLICK,
        participant_id="sender",
        reasoning="sender clicked something but did not hand off",
    )
    runtime.complete_participant_turn(
        agent,
        decision=decision,
        success=True,
        changed=True,
        step_count=3,
    )

    assert runtime.begin_participant_turn(agent) is None


def test_turn_control_continue_keeps_same_named_participant(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True
    assert runtime.begin_participant_turn(agent) == "sender"

    decision = ActionDecision(
        action=ActionType.FILL,
        participant_id="sender",
        reasoning="sender still needs to finish login",
        turn_control=TurnControl(status=TurnControlStatus.CONTINUE),
    )
    runtime.complete_participant_turn(
        agent,
        decision=decision,
        success=True,
        changed=True,
        step_count=2,
    )

    assert runtime.begin_participant_turn(agent) == "sender"


def test_turn_control_wait_for_blackboard_wakes_named_participant(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True
    assert runtime.begin_participant_turn(agent) == "sender"

    receiver_wait = ActionDecision(
        action=ActionType.WAIT,
        participant_id="receiver",
        reasoning="receiver waits until sender posts message_sent",
        turn_control=TurnControl(
            status=TurnControlStatus.WAIT_FOR,
            wait_for=[
                WakeCondition(
                    kind=WakeConditionKind.BLACKBOARD_KEY,
                    blackboard_key="message_sent",
                )
            ],
        ),
    )
    runtime.complete_participant_turn(
        agent,
        decision=receiver_wait,
        success=True,
        changed=False,
        step_count=2,
    )
    assert runtime.begin_participant_turn(agent) is None

    sender_event = ActionDecision(
        action=ActionType.CLICK,
        participant_id="sender",
        reasoning="sender sent a message",
        blackboard_event="message_sent",
        turn_control=TurnControl(status=TurnControlStatus.DONE),
    )
    runtime.complete_participant_turn(
        agent,
        decision=sender_event,
        success=True,
        changed=True,
        step_count=3,
    )

    assert runtime.begin_participant_turn(agent) == "receiver"


def test_participant_decision_session_temporarily_switches_explicit_participant(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True
    runtime.begin_participant_turn(agent)
    assert agent.session_id == "base-session::participant::sender"

    decision = ActionDecision(
        action=ActionType.WAIT,
        participant_id="receiver",
        reasoning="inspect receiver state",
    )
    with runtime.participant_decision_session(agent, decision):
        assert agent.session_id == "base-session::participant::receiver"
        assert agent._active_participant_id == "receiver"

    assert agent.session_id == "base-session::participant::sender"
    assert agent._active_participant_id == "sender"


def test_participant_decision_session_can_keep_target_active_for_post_action(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True
    runtime.begin_participant_turn(agent)
    assert agent.session_id == "base-session::participant::sender"

    decision = ActionDecision(
        action=ActionType.CLICK,
        participant_id="receiver",
        reasoning="receiver accepts the message notification",
    )
    with runtime.participant_decision_session(agent, decision, restore=False):
        assert agent.session_id == "base-session::participant::receiver"

    assert agent.session_id == "base-session::participant::receiver"
    assert agent._active_participant_id == "receiver"


def test_close_participant_browser_contexts_closes_sessions_and_generated_profiles(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_create_participant_browser_context", lambda _agent, _binding: True)
    closed_sessions: list[str] = []
    deleted_profiles: list[str] = []

    def fake_close(_raw_base_url: str, *, session_id: str, timeout: Any) -> Any:
        closed_sessions.append(session_id)

    def fake_delete(_raw_base_url: str, *, profile: str, timeout: Any) -> Any:
        deleted_profiles.append(profile)

    monkeypatch.setattr(runtime, "close_mcp_session", fake_close)
    monkeypatch.setattr(runtime, "delete_browser_profile", fake_delete)
    agent = _FakeAgent()
    goal = _goal(
        test_data={
            "sender_username": "sender-user",
            "sender_password": "sender-pass",
            "receiver_username": "receiver-user",
            "receiver_password": "receiver-pass",
        }
    )
    ok, _reason = runtime.activate_multi_user_interaction(agent, goal, _plan())
    assert ok is True

    runtime.close_participant_browser_contexts(agent)

    assert set(closed_sessions) == {
        "base-session::participant::sender",
        "base-session::participant::receiver",
    }
    assert len(set(deleted_profiles)) == 2
    assert all(profile.startswith("gaia-") for profile in deleted_profiles)
