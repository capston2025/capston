from __future__ import annotations

import json
from typing import Optional

from .models import ActionDecision, ActionType


_ACTION_ALIASES = {
    "verify": ActionType.WAIT.value,
    "check": ActionType.WAIT.value,
    "validate": ActionType.WAIT.value,
    "confirm": ActionType.WAIT.value,
}


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
        normalized_action = _ACTION_ALIASES.get(action_raw, action_raw)
        raw_value = data.get("value")
        normalized_value: Optional[str]
        if raw_value is None:
            normalized_value = None
        elif isinstance(raw_value, str):
            normalized_value = raw_value
        elif normalized_action in {"wait", "select"} and isinstance(raw_value, (dict, list, int, float, bool)):
            normalized_value = json.dumps(raw_value, ensure_ascii=False)
        else:
            normalized_value = str(raw_value)

        final_action = ActionType(normalized_action or "wait")
        if final_action == ActionType.WAIT and (normalized_value is None or (isinstance(normalized_value, str) and not normalized_value.strip())):
            normalized_value = json.dumps({"time_ms": 700}, ensure_ascii=False)
        final_ref_id = None if final_action == ActionType.WAIT else data.get("ref_id")
        if final_ref_id is not None:
            final_ref_id = str(final_ref_id).strip() or None
        final_element_id = None if final_action == ActionType.WAIT else data.get("element_id")

        return ActionDecision(
            action=final_action,
            ref_id=final_ref_id,
            element_id=final_element_id,
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
