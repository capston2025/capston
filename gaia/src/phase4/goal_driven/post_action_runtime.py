from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .execute_goal_progress import evaluate_post_action_progress
from .goal_policy_phase_runtime import advance_goal_policy_phase
from .models import ActionType, TestGoal
from .wrapper_trace_runtime import dump_wrapper_trace, serialize_dom_elements, thin_wrapper_enabled, wrapper_mode_name


_STATE_MUTATING_ACTIONS = {
    ActionType.CLICK,
    ActionType.FILL,
    ActionType.TYPE,
    ActionType.PRESS,
    ActionType.SELECT,
}


def _observation_deferred(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    return bool(state_change.get("post_action_observation_deferred") or state_change.get("backend_pending_observation"))


def _post_action_reason_code(
    *,
    decision: Any,
    reason_code: str,
    success: bool,
    changed: bool,
    state_change: Optional[Dict[str, Any]] = None,
) -> str:
    normalized = str(reason_code or "unknown")
    if bool(success) and not bool(changed) and normalized == "ok" and _observation_deferred(state_change):
        return "observation_deferred"
    if (
        bool(success)
        and not bool(changed)
        and normalized == "ok"
        and getattr(decision, "action", None) in _STATE_MUTATING_ACTIONS
    ):
        return "no_state_change"
    return normalized


def _has_zero_result_surface(agent: Any, dom_elements: List[Any]) -> bool:
    visible_blob = agent._normalize_text(
        " ".join(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", None) or ""),
                    str(getattr(el, "container_name", None) or ""),
                    str(getattr(el, "context_text", None) or ""),
                ]
            )
            for el in list(dom_elements or [])
            if bool(getattr(el, "is_visible", True))
        )
    )
    if not visible_blob:
        return False
    zero_tokens = ("0개", "0 건", "0건", "없음", "없어요", "no results", "no result", "empty")
    result_tokens = ("결과", "result", "results", "조합", "list", "리스트")
    return any(token in visible_blob for token in zero_tokens) and any(
        token in visible_blob for token in result_tokens
    )


def handle_post_action_runtime(
    agent,
    *,
    goal: TestGoal,
    decision,
    success: bool,
    error: Optional[str],
    before_signature: Optional[str],
    dom_elements: List[Any],
    steps: List[Any],
    step_count: int,
    start_time: float,
    login_gate_visible: bool,
    has_login_test_data: bool,
    modal_open_hint: bool,
    scroll_streak: int,
    ineffective_action_streak: int,
    force_context_shift: bool,
    context_shift_fail_streak: int,
    context_shift_cooldown: int,
    click_intent_key: str,
    action_intent_key: str,
    master_orchestrator,
) -> Dict[str, Any]:
    post_action_started = time.perf_counter()
    progress_eval = evaluate_post_action_progress(
        agent=agent,
        goal=goal,
        decision=decision,
        success=success,
        before_signature=before_signature,
        dom_elements=dom_elements,
        step_count=step_count,
        steps=steps,
        start_time=start_time,
    )
    post_dom = progress_eval.get("post_dom") or []
    state_change = progress_eval.get("state_change")
    changed = bool(progress_eval.get("changed"))
    if isinstance(state_change, dict):
        changed = bool(changed or agent._state_change_indicates_progress(state_change))
    backend_trace = {}
    if isinstance(state_change, dict) and isinstance(state_change.get("backend_trace"), dict):
        backend_trace = dict(state_change.get("backend_trace") or {})
    elif isinstance(getattr(agent, "_last_backend_trace", None), dict):
        backend_trace = dict(getattr(agent, "_last_backend_trace", None) or {})
    observation_deferred = _observation_deferred(state_change)
    llm_trace = dict(getattr(agent, "_last_llm_trace", None) or {}) if isinstance(getattr(agent, "_last_llm_trace", None), dict) else {}
    terminal_result = progress_eval.get("terminal_result")
    wrapper_mode = wrapper_mode_name(agent)
    agentic_wrapper_mode = thin_wrapper_enabled(agent)
    current_goal_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip()
    current_phase_intent = str(getattr(agent, "_goal_phase_intent", "") or "").strip()
    if agentic_wrapper_mode:
        phase_event = "terminal"
        if terminal_result is None:
            if login_gate_visible:
                phase_event = "blocked_auth"
            elif success and observation_deferred:
                phase_event = "action_observation_deferred"
            elif success:
                phase_event = "action_ok" if changed else "action_no_state_change"
            else:
                phase_event = "action_failed"
        phase_update = {
            "event": phase_event,
            "previous_phase": current_goal_phase,
            "current_phase": current_goal_phase,
            "phase_intent": current_phase_intent,
        }
        previous_goal_phase = current_goal_phase
    else:
        phase_update = advance_goal_policy_phase(
            agent,
            goal=goal,
            decision=decision,
            success=success,
            changed=changed,
            dom_elements=dom_elements,
            post_dom=post_dom if isinstance(post_dom, list) else None,
            auth_prompt_visible=login_gate_visible,
            modal_open=modal_open_hint,
            terminal_result=terminal_result,
        )
        previous_goal_phase = str(phase_update.get("previous_phase") or "").strip()
        current_goal_phase = str(phase_update.get("current_phase") or "").strip()
        current_phase_intent = str(phase_update.get("phase_intent") or current_phase_intent).strip()
    if previous_goal_phase and current_goal_phase and previous_goal_phase != current_goal_phase:
        agent._log(
            f"🧭 goal phase 전환: {previous_goal_phase} -> {current_goal_phase}"
            f" ({phase_update.get('event')})"
        )
    post_action_eval_ms = int((time.perf_counter() - post_action_started) * 1000)
    owner = "gaia_post_action"
    if llm_trace.get("used_llm") and int(llm_trace.get("llm_ms", 0) or 0) >= max(1500, post_action_eval_ms):
        owner = "llm"
    elif isinstance(backend_trace, dict) and str(backend_trace.get("owner") or "").strip():
        owner = str(backend_trace.get("owner") or "")
    step_trace = {
        "phase": current_goal_phase,
        "phase_intent": current_phase_intent,
        "wrapper_mode": wrapper_mode,
        "llm": llm_trace,
        "backend": backend_trace,
        "gaia": {
            "post_action_eval_ms": post_action_eval_ms,
            "phase_event": str(phase_update.get("event") or ""),
            "changed": bool(changed),
        },
        "owner": owner,
    }
    agent._last_step_trace = step_trace
    agent._log(f"🧪 step trace: {step_trace}")
    dump_wrapper_trace(
        agent,
        kind="post_action",
        payload={
            "goal": {
                "id": getattr(goal, "id", ""),
                "name": getattr(goal, "name", ""),
            },
            "decision": decision.model_dump() if hasattr(decision, "model_dump") else str(decision),
            "success": bool(success),
            "error": error,
            "phase_update": dict(phase_update or {}),
            "step_trace": step_trace,
            "changed": bool(changed),
            "state_change": state_change if isinstance(state_change, dict) else {},
            "post_dom": serialize_dom_elements(post_dom if isinstance(post_dom, list) else [], limit=120, agent=agent),
            "agentic_wrapper_mode": bool(agentic_wrapper_mode),
            "wrapper_mode": wrapper_mode,
        },
    )
    if terminal_result is not None:
        return {
            "terminal_result": terminal_result,
            "scroll_streak": scroll_streak,
            "ineffective_action_streak": ineffective_action_streak,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "post_dom": post_dom,
            "changed": changed,
            "state_change": state_change,
        }

    weak_only = (not changed) and agent._state_change_is_weak(state_change)
    if changed:
        agent._progress_counter += 1
        agent._no_progress_counter = 0
        agent._weak_progress_streak = 0
        setattr(agent, "_discovery_no_progress_streak", 0)
    elif observation_deferred:
        setattr(agent, "_discovery_no_progress_streak", 0)
    else:
        setattr(agent, "_discovery_no_progress_streak", 0)
        agent._no_progress_counter += 1
        if weak_only:
            agent._weak_progress_streak += 1
        else:
            agent._weak_progress_streak = 0

    reason_code = agent._last_exec_result.reason_code if agent._last_exec_result else "unknown"
    outcome_reason_code = _post_action_reason_code(
        decision=decision,
        reason_code=reason_code,
        success=success,
        changed=changed,
        state_change=state_change,
    )
    master_orchestrator.record_progress(
        changed=changed,
        signal={
            "reason_code": outcome_reason_code,
            "phase": agent._runtime_phase,
            "step": step_count,
        },
    )
    agent._record_action_feedback(
        step_number=step_count,
        decision=decision,
        success=success,
        changed=changed,
        error=error,
        reason_code=outcome_reason_code,
        state_change=state_change,
        intent_key=action_intent_key,
    )
    agent._record_action_memory(
        goal=goal,
        step_number=step_count,
        decision=decision,
        success=success,
        changed=changed,
        error=error,
    )
    if (
        agentic_wrapper_mode
        and login_gate_visible
        and decision.action == ActionType.CLICK
        and outcome_reason_code in {"not_found", "not_actionable", "action_timeout", "no_state_change"}
    ):
        agent._action_feedback.append(
            "로그인/인증 surface가 열려 있어 배경 CTA가 차단됐습니다. "
            "같은 '바로 추가'를 반복하지 말고 현재 인증 surface 내부의 입력/제출 요소를 우선 선택하세요."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
    if isinstance(state_change, dict) and bool(state_change.get("commit_verification_failed")):
        verification = state_change.get("commit_verification") if isinstance(state_change.get("commit_verification"), dict) else {}
        expected = str(verification.get("expected_range") or "").strip()
        observed = verification.get("observed_ranges")
        observed_text = ", ".join(str(item) for item in observed[:3]) if isinstance(observed, list) else ""
        detail = f"예상 {expected}" if expected else "예상 선택값"
        if observed_text:
            detail += f", 현재 표시 {observed_text}"
        agent._action_feedback.append(
            f"커밋 검증 실패: 적용/확인 후 지속 화면에 {detail}가 반영되지 않았습니다. "
            "다음 단계로 넘어가지 말고 현재 표시값을 기준으로 다시 선택/적용하세요."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
    if bool(success and (changed or observation_deferred)):
        agent._overlay_intercept_pending = False
        force_resnapshot = getattr(agent, "_force_next_dom_resnapshot", None)
        if callable(force_resnapshot):
            try:
                force_resnapshot(reason="post_action_changed" if changed else "post_action_observation_deferred")
            except Exception:
                pass
    elif reason_code == "pointer_intercepted" or (
        reason_code in {"not_actionable", "no_state_change"} and agent._error_indicates_overlay_intercept(error)
    ):
        agent._overlay_intercept_pending = True
        agent._record_reason_code("overlay_intercept_detected")
        pointer_interceptor = state_change.get("pointer_interceptor") if isinstance(state_change, dict) else None
        blocker = ""
        if isinstance(pointer_interceptor, dict):
            blocker = str(pointer_interceptor.get("description") or "").strip()
        if blocker:
            agent._action_feedback.append(
                f"클릭 지점이 {blocker} 오버레이에 가려졌습니다. 같은 ref를 반복하지 말고 전면 오버레이를 먼저 닫거나 우회하세요."
            )
            if len(agent._action_feedback) > 10:
                agent._action_feedback = agent._action_feedback[-10:]
    if (
        reason_code in {"not_found", "ref_stale", "missing_element_id"}
        and _has_zero_result_surface(agent, post_dom)
    ):
        agent._action_feedback.append(
            "전면 zero-result surface가 아직 남아 있습니다. 스크롤로 결과를 찾지 말고, 새 snapshot에서 현재 전면 surface의 닫기/확인 CTA를 다시 찾으세요."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
    ref_used = agent._last_exec_result.ref_id_used if agent._last_exec_result else ""
    agent._track_ref_outcome(
        ref_id=ref_used,
        reason_code=outcome_reason_code,
        success=success,
        changed=changed,
    )
    if (
        login_gate_visible
        and decision.action == ActionType.CLICK
        and outcome_reason_code in {"no_state_change", "not_actionable"}
        and agent._has_duplicate_account_signal(state_change=state_change, dom_elements=post_dom)
    ):
        new_username = agent._rotate_signup_identity(goal)
        if new_username:
            agent._log(
                f"🪪 회원가입 아이디 중복 메시지 감지: username을 `{new_username}`로 갱신 후 재시도합니다."
            )
            agent._action_feedback.append(
                "회원가입 오류 감지: 아이디가 이미 사용 중입니다. username/email을 새 값으로 갱신했으니 아이디 필드부터 다시 입력하세요."
            )
            if len(agent._action_feedback) > 10:
                agent._action_feedback = agent._action_feedback[-10:]
            return {
                "continue_loop": True,
                "scroll_streak": scroll_streak,
                "ineffective_action_streak": 0,
                "force_context_shift": False,
                "context_shift_fail_streak": context_shift_fail_streak,
                "context_shift_cooldown": context_shift_cooldown,
                "post_dom": post_dom,
                "changed": changed,
                "state_change": state_change,
                "terminal_result": None,
            }
    return {
        "terminal_result": terminal_result,
        "continue_loop": False,
        "scroll_streak": scroll_streak,
        "ineffective_action_streak": ineffective_action_streak,
        "force_context_shift": force_context_shift,
        "context_shift_fail_streak": context_shift_fail_streak,
        "context_shift_cooldown": context_shift_cooldown,
        "post_dom": post_dom,
        "changed": changed,
        "state_change": state_change,
    }
