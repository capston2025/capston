from __future__ import annotations

import json

from gaia.src.phase4.goal_driven.decision_parsing_runtime import parse_decision
from gaia.src.phase4.goal_driven.models import ActionType


class _FakeAgent:
    def _log(self, msg: str) -> None:
        pass


def test_parse_normal_click():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "click",
        "ref_id": "e42",
        "reasoning": "click button",
        "confidence": 0.9,
        "is_goal_achieved": False,
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.CLICK
    assert d.ref_id == "e42"
    assert d.is_goal_achieved is False


def test_parse_action_none_maps_to_wait():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "none",
        "reasoning": "goal achieved",
        "confidence": 1.0,
        "is_goal_achieved": True,
        "goal_achievement_reason": "all done",
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.WAIT
    assert d.is_goal_achieved is True
    assert d.goal_achievement_reason == "all done"


def test_parse_action_done_maps_to_wait():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "done",
        "reasoning": "finished",
        "is_goal_achieved": True,
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.WAIT
    assert d.is_goal_achieved is True


def test_parse_action_complete_maps_to_wait():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "complete",
        "reasoning": "finished",
        "is_goal_achieved": True,
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.WAIT
    assert d.is_goal_achieved is True


def test_parse_action_empty_string_maps_to_wait():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "",
        "reasoning": "no action",
        "is_goal_achieved": True,
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.WAIT
    assert d.is_goal_achieved is True


def test_parse_unknown_action_maps_to_wait():
    """등록되지 않은 임의 action도 wait로 매핑된다."""
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "explode",
        "reasoning": "unknown",
        "is_goal_achieved": False,
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.WAIT
    assert d.is_goal_achieved is False


def test_parse_verify_alias():
    agent = _FakeAgent()
    resp = json.dumps({"action": "verify", "reasoning": "checking"})
    d = parse_decision(agent, resp)
    assert d.action == ActionType.WAIT


def test_parse_empty_response():
    agent = _FakeAgent()
    d = parse_decision(agent, "")
    assert d.action == ActionType.WAIT
    assert d.confidence == 0.0


def test_parse_invalid_json_preserves_goal_achieved():
    """JSON은 유효하지만 다른 ValueError 발생 시에도 is_goal_achieved를 보존한다."""
    agent = _FakeAgent()
    d = parse_decision(agent, "not json at all")
    assert d.action == ActionType.WAIT
    assert d.confidence == 0.0


def test_parse_markdown_wrapped_json():
    agent = _FakeAgent()
    resp = '```json\n{"action": "click", "ref_id": "e1", "reasoning": "test"}\n```'
    d = parse_decision(agent, resp)
    assert d.action == ActionType.CLICK
    assert d.ref_id == "e1"


def test_parse_select_with_list_value():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "select",
        "ref_id": "e5",
        "value": ["option1", "option2"],
        "reasoning": "selecting",
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.SELECT
    assert "option1" in d.value


def test_parse_type_action_preserves_target_and_value():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "type",
        "ref_id": "e10",
        "value": "jangboss02@gmail.com",
        "reasoning": "event-driven recipient input needs keyboard input",
    })

    d = parse_decision(agent, resp)

    assert d.action == ActionType.TYPE
    assert d.ref_id == "e10"
    assert d.value == "jangboss02@gmail.com"


def test_parse_preserves_llm_requested_text_evidence_contract():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "inspect",
        "value": "read current news cards",
        "reasoning": "현재 화면에는 기사 목록 카드가 있어 텍스트 evidence 수집이 필요하다.",
        "collect_text_evidence": True,
        "text_evidence_reason": "기사 목록 1~15의 제목/언론사/시간/요약 수집",
        "text_evidence_focus": ["제목", "언론사", "시간", "요약"],
    })

    d = parse_decision(agent, resp)

    assert d.action == ActionType.INSPECT
    assert d.collect_text_evidence is True
    assert d.text_evidence_reason == "기사 목록 1~15의 제목/언론사/시간/요약 수집"
    assert d.text_evidence_focus == ["제목", "언론사", "시간", "요약"]


def test_parse_inspect_action_allows_missing_ref():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "inspect",
        "value": "check active input and committed token state",
        "reasoning": "DOM summary is not enough to know whether input was committed",
    })

    d = parse_decision(agent, resp)

    assert d.action == ActionType.INSPECT
    assert d.ref_id is None
    assert d.element_id is None
    assert "active input" in d.value


def test_parse_input_alias_maps_to_type():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "input",
        "ref_id": "e11",
        "value": "hello",
    })

    d = parse_decision(agent, resp)

    assert d.action == ActionType.TYPE
    assert d.ref_id == "e11"
    assert d.value == "hello"


def test_parse_switch_alias_maps_to_focus_and_uses_value():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "switch",
        "value": 2,
        "reasoning": "move into popup",
    })
    d = parse_decision(agent, resp)
    assert d.action == ActionType.FOCUS
    assert d.value == "2"
    assert d.ref_id is None


def test_parse_preserves_multi_user_participant_plan_contract():
    agent = _FakeAgent()
    resp = json.dumps({
        "action": "wait",
        "value": {"time_ms": 700},
        "reasoning": "need a second user to verify message delivery",
        "participant_id": "sender",
        "next_participant": "receiver",
        "participant_plan": {
            "skill": "multi_user_interaction",
            "required": True,
            "reason": "채팅 수신 여부는 sender와 receiver가 모두 필요하다.",
            "participants": [
                {"id": "sender", "role": "sender", "display_name": "Sender"},
                {"id": "receiver", "role": "receiver", "display_name": "Receiver"},
            ],
            "credential_requests": [
                {"participant_id": "sender", "fields": ["username", "password"]},
                {"participant_id": "receiver", "fields": ["username", "password"]},
            ],
            "expected_events": ["message_sent", "message_received"],
        },
        "blackboard_event": "message_sent",
        "blackboard_payload": {"text_present": True},
        "turn_control": {
            "status": "wait_for",
            "wait_for": [
                {"kind": "blackboard_key", "blackboard_key": "message_received"}
            ],
            "reason": "sender waits until receiver confirms the message",
        },
    })

    d = parse_decision(agent, resp)

    assert d.action == ActionType.WAIT
    assert d.participant_id == "sender"
    assert d.next_participant == "receiver"
    assert d.participant_plan is not None
    assert d.participant_plan.required is True
    assert [p.id for p in d.participant_plan.participants] == ["sender", "receiver"]
    assert d.participant_plan.credential_requests[0].participant_id == "sender"
    assert d.blackboard_event == "message_sent"
    assert d.blackboard_payload == {"text_present": True}
    assert d.turn_control is not None
    assert d.turn_control.status == "wait_for"
    assert d.turn_control.wait_for[0].blackboard_key == "message_received"
