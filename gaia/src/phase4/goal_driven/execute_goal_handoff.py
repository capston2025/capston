from __future__ import annotations

from typing import Any, Dict

from .models import TestGoal


def handle_master_handoff(
    *,
    agent: Any,
    goal: TestGoal,
    master_directive: Any,
    context_shift_fail_streak: int,
    context_shift_cooldown: int,
    force_context_shift: bool,
) -> Dict[str, Any]:
    aborted = False
    abort_reason = ""

    if master_directive.kind == "handoff" and master_directive.reason == "auth_required":
        agent._handoff_state = {
            "kind": "auth_required",
            "phase": agent._runtime_phase,
            "url": goal.start_url,
        }

    if master_directive.kind == "handoff" and master_directive.reason == "no_progress":
        no_progress_count = int(
            (master_directive.handoff_payload or {}).get("count")
            or agent._no_progress_counter
            or 0
        )
        agent._handoff_state = {
            "kind": "no_progress",
            "phase": agent._runtime_phase,
            "url": goal.start_url,
            "count": no_progress_count,
        }
        callback_resp = agent._request_user_intervention(
            {
                "kind": "no_progress",
                "goal_name": goal.name,
                "goal_description": goal.description,
                "phase": agent._runtime_phase,
                "question": (
                    f"상태 변화가 {no_progress_count}회 연속으로 감지되지 않았습니다. "
                    "추가 지시(예: 우선할 버튼/필터/입력값)를 제공하거나 proceed=true로 계속하세요."
                ),
                "fields": ["instruction", "proceed"],
            }
        )
        if isinstance(callback_resp, dict):
            action = str(callback_resp.get("action") or "").strip().lower()
            reason_code = str(callback_resp.get("reason_code") or "").strip().lower()
            proceed = agent._to_bool(callback_resp.get("proceed"), default=True)
            instruction = str(callback_resp.get("instruction") or "").strip()
            if instruction:
                agent._action_feedback.append(f"사용자 추가 지시: {instruction}")
                if len(agent._action_feedback) > 10:
                    agent._action_feedback = agent._action_feedback[-10:]
            explicit_cancel = (
                action == "cancel"
                and reason_code not in {
                    "",
                    "user_intervention_missing",
                    "intervention_timeout",
                    "clarification_timeout",
                }
            )
            if not proceed and explicit_cancel:
                aborted = True
                abort_reason = "사용자 요청으로 실행을 중단했습니다."

        if not aborted:
            if context_shift_fail_streak >= 3 or context_shift_cooldown > 0:
                force_context_shift = False
                agent._action_feedback.append(
                    "컨텍스트 전환이 연속 실패해 일반 LLM 액션으로 복귀합니다."
                )
                if len(agent._action_feedback) > 10:
                    agent._action_feedback = agent._action_feedback[-10:]
            else:
                force_context_shift = True

    return {
        "aborted": aborted,
        "abort_reason": abort_reason,
        "force_context_shift": force_context_shift,
    }
