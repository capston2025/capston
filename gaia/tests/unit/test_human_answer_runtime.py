from __future__ import annotations

import json
from types import SimpleNamespace

from gaia.src.phase4.goal_driven.agent_intervention_runtime import request_goal_clarification
from gaia.src.phase4.goal_driven.execute_goal_intervention import handle_login_intervention
from gaia.src.phase4.goal_driven.human_answer_runtime import (
    parse_human_answer_request,
    request_human_answer,
)
from gaia.src.phase4.goal_driven.models import TestGoal


def _goal(**overrides) -> TestGoal:
    payload = {
        "id": "g1",
        "name": "로그인 후 강의 재생",
        "description": "사이트에 로그인한 뒤 강의 동영상을 재생한다.",
        "success_criteria": ["동영상 재생"],
        "test_data": {},
    }
    payload.update(overrides)
    return TestGoal(**payload)


class _FakeAgent:
    def __init__(self, response=None) -> None:
        self._runtime_phase = "AUTH"
        self._handoff_state = {}
        self._action_feedback = []
        self._recorded_codes = []
        self._response = response

    def _normalize_text(self, value):
        return str(value or "").strip().lower()

    def _has_login_test_data(self, goal):
        data = goal.test_data if isinstance(goal.test_data, dict) else {}
        return bool(data.get("username") and data.get("password"))

    def _request_user_intervention(self, payload):
        self.last_payload = payload
        return self._response

    def _record_reason_code(self, code):
        self._recorded_codes.append(code)

    def _log(self, message):
        self.last_log = message


def test_goal_clarification_does_not_preemptively_request_credentials_for_login_goal() -> None:
    agent = _FakeAgent()
    goal = _goal()

    assert request_goal_clarification(agent, goal) is True
    assert not hasattr(agent, "last_payload")


def test_parse_human_answer_request_accepts_wait_skill_payload() -> None:
    payload = {
        "skill": "human_answer",
        "question": "현재 인증번호가 필요합니다.",
        "fields": ["otp"],
        "sensitive": "false",
    }

    parsed = parse_human_answer_request(json.dumps(payload, ensure_ascii=False))

    assert parsed["kind"] == "human_answer"
    assert parsed["question"] == "현재 인증번호가 필요합니다."
    assert parsed["fields"] == ["otp"]
    assert parsed["sensitive"] is False


def test_request_human_answer_uses_ai_requested_fields_and_merges_response() -> None:
    agent = _FakeAgent(
        response={
            "action": "resume",
            "proceed": True,
            "otp": "123456",
            "instruction": "metadata only",
        }
    )
    goal = _goal()

    ok, reason = request_human_answer(
        agent,
        goal,
        {"question": "OTP를 입력해 주세요.", "fields": ["otp"]},
    )

    assert ok is True
    assert "otp" in reason
    assert goal.test_data["otp"] == "123456"
    assert "instruction" not in goal.test_data
    assert agent.last_payload["fields"] == ["proceed", "manual_done", "otp", "instruction"]


def test_request_human_answer_rejects_partial_required_fields() -> None:
    agent = _FakeAgent(
        response={
            "action": "resume",
            "proceed": True,
            "username": "student01",
        }
    )
    goal = _goal()

    ok, reason = request_human_answer(
        agent,
        goal,
        {"question": "로그인 정보를 입력해 주세요.", "fields": ["username", "password"]},
    )

    assert ok is False
    assert "password" in reason
    assert agent._recorded_codes == ["human_answer_missing"]


def test_login_interrupt_does_not_abort_before_ai_requests_human_answer() -> None:
    agent = _FakeAgent()
    goal = _goal()

    result = handle_login_intervention(
        agent=agent,
        goal=goal,
        login_gate_visible=True,
        has_login_test_data=False,
        login_intervention_asked=False,
    )

    assert result["aborted"] is False
    assert result["login_intervention_asked"] is True
    assert "human_answer skill" in agent._action_feedback[-1]
