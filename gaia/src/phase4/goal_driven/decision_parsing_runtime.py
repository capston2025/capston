from __future__ import annotations

import json
from typing import Optional

from .models import ActionDecision, ActionType


def parse_decision(agent, response_text: str) -> ActionDecision:
    """LLM 응답을 ActionDecision으로 파싱"""
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    if not text:
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning="LLM 오류: empty_response_from_model",
            confidence=0.0,
        )

    if not text.startswith("{"):
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            text = text[first:last + 1].strip()

    try:
        data = json.loads(text)
        action_raw = str(data.get("action", "wait")).strip().lower()
        raw_value = data.get("value")
        normalized_value: Optional[str]
        if raw_value is None:
            normalized_value = None
        elif isinstance(raw_value, str):
            normalized_value = raw_value
        elif action_raw in {"wait", "select"} and isinstance(raw_value, (dict, list, int, float, bool)):
            normalized_value = json.dumps(raw_value, ensure_ascii=False)
        else:
            normalized_value = str(raw_value)

        return ActionDecision(
            action=ActionType(data.get("action", "wait")),
            element_id=data.get("element_id"),
            value=normalized_value,
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", 0.5),
            is_goal_achieved=data.get("is_goal_achieved", False),
            goal_achievement_reason=data.get("goal_achievement_reason"),
        )

    except (json.JSONDecodeError, ValueError) as exc:
        agent._log(f"JSON 파싱 실패: {exc}, 응답: {text[:200]}")
        return ActionDecision(
            action=ActionType.WAIT,
            reasoning=f"파싱 오류: {exc}",
            confidence=0.0,
        )
