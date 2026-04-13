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
