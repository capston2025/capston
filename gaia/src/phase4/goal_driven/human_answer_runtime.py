from __future__ import annotations

import json
import time
from typing import Any, Mapping


_SKILL_NAMES = {"human_answer", "request_user_input", "ask_user", "user_answer"}
_BLOCKED_RESPONSE_KEYS = {
    "action",
    "proceed",
    "reason_code",
    "question",
    "fields",
    "kind",
    "skill",
    "manual_done",
    "instruction",
    "instructions",
}


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def parse_human_answer_request(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    raw: Any = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except Exception:
            return {}
    if not isinstance(raw, Mapping):
        return {}

    skill = str(raw.get("skill") or raw.get("kind") or "").strip().lower()
    if skill not in _SKILL_NAMES:
        return {}

    question = str(raw.get("question") or raw.get("prompt") or "").strip()
    fields_raw = raw.get("fields")
    fields: list[str] = []
    if isinstance(fields_raw, list):
        fields = [str(field or "").strip() for field in fields_raw if str(field or "").strip()]
    elif isinstance(fields_raw, str):
        fields = [part.strip() for part in fields_raw.split(",") if part.strip()]
    if not question:
        question = "현재 작업을 계속하려면 사용자의 정답 또는 입력값이 필요합니다."

    return {
        "kind": "human_answer",
        "question": question,
        "fields": fields,
        "reason_code": str(raw.get("reason_code") or "human_answer_required").strip(),
        "sensitive": _to_bool(raw.get("sensitive"), default=True),
        "instructions": str(raw.get("instructions") or "").strip(),
    }


def request_human_answer(agent: Any, goal: Any, request: Mapping[str, Any]) -> tuple[bool, str]:
    fields = [str(field or "").strip() for field in list(request.get("fields") or []) if str(field or "").strip()]
    required_fields = [field for field in fields if field not in _BLOCKED_RESPONSE_KEYS]
    question = str(request.get("question") or "").strip() or "사용자 입력이 필요합니다."
    agent._handoff_state = {
        "kind": "human_answer",
        "phase": getattr(agent, "_runtime_phase", ""),
        "requested": True,
        "fields": fields,
        "timestamp": int(time.time()),
    }
    payload = {
        "kind": "human_answer",
        "goal_name": getattr(goal, "name", ""),
        "goal_description": getattr(goal, "description", ""),
        "question": question,
        "fields": list(dict.fromkeys(["proceed", "manual_done", *fields, "instruction"])),
        "reason_code": str(request.get("reason_code") or "human_answer_required"),
        "instructions": str(request.get("instructions") or ""),
        "sensitive": _to_bool(request.get("sensitive"), default=True),
    }
    response = agent._request_user_intervention(payload)
    if response is None:
        agent._record_reason_code("human_answer_missing")
        return False, "사용자 입력이 필요한 단계지만 응답을 받을 수 없어 중단했습니다."

    if str(response.get("action") or "").strip().lower() in {"cancel", "deny", "no"}:
        agent._record_reason_code(str(response.get("reason_code") or "user_cancelled"))
        return False, "사용자가 필요한 입력 제공을 취소했습니다."
    if not _to_bool(response.get("proceed"), default=True):
        agent._record_reason_code(str(response.get("reason_code") or "user_cancelled"))
        return False, "사용자가 필요한 입력 제공을 취소했습니다."

    if not isinstance(getattr(goal, "test_data", None), dict):
        goal.test_data = {}
    provided: list[str] = []
    for key, value in response.items():
        norm_key = str(key or "").strip()
        if not norm_key or norm_key in _BLOCKED_RESPONSE_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                continue
            goal.test_data[norm_key] = cleaned
        else:
            goal.test_data[norm_key] = value
        provided.append(norm_key)

    if _to_bool(response.get("manual_done"), default=False):
        agent._handoff_state["provided"] = True
        agent._handoff_state["mode"] = "manual_done"
        return True, "사용자가 수동 처리 완료를 전달했습니다."

    missing_fields = [
        field
        for field in required_fields
        if not str(goal.test_data.get(field) or "").strip()
    ]
    if missing_fields:
        agent._record_reason_code("human_answer_missing")
        return False, "요청한 입력 필드가 응답에 포함되지 않았습니다: " + ", ".join(missing_fields)

    agent._handoff_state["provided"] = True
    agent._handoff_state["mode"] = "answered"
    summary = ", ".join(provided) if provided else "proceed"
    return True, f"사용자 입력을 test_data에 반영했습니다: {summary}"
