from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal


def handle_action_recovery(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    reason_code: str,
    login_gate_visible: bool,
    has_login_test_data: bool,
    post_dom: Optional[List[DOMElement]],
    force_context_shift: bool,
    ineffective_action_streak: int,
) -> Dict[str, Any]:
    if success and changed:
        return {
            "continue_loop": False,
            "force_context_shift": force_context_shift,
            "ineffective_action_streak": ineffective_action_streak,
        }

    agent._record_recovery_hints(goal, reason_code)
    auth_mode = ""
    if isinstance(goal.test_data, dict):
        auth_mode = str(goal.test_data.get("auth_mode") or "").strip().lower()
    is_auth_flow = login_gate_visible and (
        auth_mode in {"signup", "register", "login", "signin"} or has_login_test_data
    )

    if (
        is_auth_flow
        and decision.action == ActionType.CLICK
        and reason_code in {"no_state_change", "not_actionable"}
    ):
        agent._action_feedback.append(
            "인증 모달 제출이 반영되지 않았습니다. 모달 내부 오류/필수 입력값을 확인하고 "
            "같은 모달 안에서 재시도하세요. 페이지/섹션 전환은 금지합니다."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
        time.sleep(0.25)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    if reason_code == "modal_not_open":
        agent._log("🧭 close 대상 모달이 현재 열려있지 않아 재계획합니다.")
        agent._action_feedback.append(
            "닫기 액션 시점에 모달이 열려있지 않았습니다. 최신 화면 기준으로 후보를 다시 수집하고 "
            "닫기 대신 현재 활성 CTA를 선택하세요."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
        _ = agent._analyze_dom()
        time.sleep(0.2)
        return {
            "continue_loop": True,
            "force_context_shift": True,
            "ineffective_action_streak": 0,
        }

    if (
        agent._no_progress_counter >= 2
        and reason_code
        in {
            "no_state_change",
            "not_actionable",
            "ambiguous_ref_target",
            "ambiguous_selector",
            "blocked_ref_no_progress",
            "blocked_logout_action",
        }
        and decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS}
    ):
        force_context_shift = True

    if reason_code in {
        "snapshot_not_found",
        "stale_snapshot",
        "ref_required",
        "ambiguous_ref_target",
        "ambiguous_selector",
        "not_found",
    }:
        agent._log("♻️ snapshot/ref 갱신이 필요해 DOM을 재수집합니다.")
        _ = agent._analyze_dom()
        time.sleep(0.25)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    if reason_code in {"request_exception", "http_5xx"}:
        attempt_count = agent._last_exec_result.attempt_count if agent._last_exec_result else 0
        backoff = min(2.5, 0.6 + (0.25 * max(0, attempt_count)))
        agent._log(
            f"🌐 일시적 통신 오류({reason_code}) 감지: {backoff:.2f}s 대기 후 재시도합니다."
        )
        _ = agent._analyze_dom()
        time.sleep(backoff)
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "ineffective_action_streak": 0,
        }

    return {
        "continue_loop": False,
        "force_context_shift": force_context_shift,
        "ineffective_action_streak": ineffective_action_streak,
    }
