from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .execute_goal_progress import evaluate_post_action_progress
from .goal_policy_phase_runtime import advance_goal_policy_phase
from .models import ActionType, TestGoal
from .wrapper_trace_runtime import dump_wrapper_trace, serialize_dom_elements, thin_wrapper_enabled, wrapper_mode_name


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
    filter_goal_active: bool,
    filter_semantic_attempts: int,
    filter_semantic_attempt_limit: int,
    filter_semantic_max_cases: int,
    filter_semantic_current_only: bool,
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
            "filter_semantic_attempts": filter_semantic_attempts,
            "scroll_streak": scroll_streak,
            "ineffective_action_streak": ineffective_action_streak,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "post_dom": post_dom,
            "changed": changed,
            "state_change": state_change,
        }

    if filter_goal_active and decision.action == ActionType.SELECT and bool(success):
        filter_semantic_attempts += 1
        selected_value_hint = str(decision.value or "").strip()
        if agent._filter_validation_contract is None:
            try:
                agent._filter_validation_contract = agent._build_filter_validation_contract(
                    goal=goal,
                    dom_elements=post_dom if isinstance(post_dom, list) and post_dom else dom_elements,
                )
            except Exception as contract_exc:
                agent._log(f"⚠️ 필터 검증 계약 생성 실패: {contract_exc}")
                agent._filter_validation_contract = None
        semantic_report = agent.run_filter_semantic_validation(
            goal_text=goal.description,
            max_pages=2,
            max_cases=filter_semantic_max_cases,
            use_current_selection_only=filter_semantic_current_only,
            forced_selected_value=selected_value_hint,
            validation_contract=(
                agent._filter_validation_contract
                if isinstance(agent._filter_validation_contract, dict)
                else None
            ),
        )
        if isinstance(semantic_report, dict):
            agent._last_filter_semantic_report = semantic_report
            rc_summary = semantic_report.get("reason_code_summary")
            if isinstance(rc_summary, dict):
                for code, count in rc_summary.items():
                    try:
                        repeats = int(count)
                    except Exception:
                        repeats = 0
                    repeats = max(0, min(repeats, 50))
                    for _ in range(repeats):
                        agent._record_reason_code(str(code))

            summary = semantic_report.get("summary")
            summary_dict = summary if isinstance(summary, dict) else {}
            strict_failed = bool(summary_dict.get("strict_failed"))
            goal_satisfied = bool(summary_dict.get("goal_satisfied", semantic_report.get("success")))

            if strict_failed:
                failed_mandatory = int(summary_dict.get("failed_mandatory_checks") or 0)
                reason = f"필터 의미 검증 실패: 필수 체크 실패 {failed_mandatory}건"
                agent._log(f"❌ {reason}")
                return {
                    "terminal_result": agent._build_failure_result(
                        goal=goal,
                        steps=steps,
                        step_count=step_count,
                        start_time=start_time,
                        reason=reason,
                    ),
                    "filter_semantic_attempts": filter_semantic_attempts,
                    "scroll_streak": scroll_streak,
                    "ineffective_action_streak": ineffective_action_streak,
                    "force_context_shift": force_context_shift,
                    "context_shift_fail_streak": context_shift_fail_streak,
                    "context_shift_cooldown": context_shift_cooldown,
                    "post_dom": post_dom,
                    "changed": changed,
                    "state_change": state_change,
                }

            if goal_satisfied:
                passed_checks = int(summary_dict.get("passed_checks") or 0)
                total_checks = int(summary_dict.get("total_checks") or 0)
                success_reason = f"필터 의미 검증 통과 ({passed_checks}/{total_checks})"
                agent._log(f"✅ {success_reason}")
                result = agent._build_success_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=success_reason,
                )
                return {
                    "terminal_result": result,
                    "filter_semantic_attempts": filter_semantic_attempts,
                    "scroll_streak": scroll_streak,
                    "ineffective_action_streak": ineffective_action_streak,
                    "force_context_shift": force_context_shift,
                    "context_shift_fail_streak": context_shift_fail_streak,
                    "context_shift_cooldown": context_shift_cooldown,
                    "post_dom": post_dom,
                    "changed": changed,
                    "state_change": state_change,
                }
            else:
                required_count = int(summary_dict.get("required_option_count") or 0)
                covered_count = int(summary_dict.get("covered_option_count") or 0)
                agent._log(
                    "🧪 필터 의미 검증 진행 중: "
                    f"옵션 커버리지 {covered_count}/{required_count}"
                )
                missing_options = semantic_report.get("missing_required_options")
                if isinstance(missing_options, list) and missing_options:
                    labels: List[str] = []
                    for row in missing_options[:6]:
                        if not isinstance(row, dict):
                            continue
                        label = str(row.get("text") or row.get("value") or "").strip()
                        if label:
                            labels.append(label)
                    if labels:
                        agent._action_feedback.append(
                            "아직 검증되지 않은 필터 옵션: " + ", ".join(labels)
                        )
                        if len(agent._action_feedback) > 10:
                            agent._action_feedback = agent._action_feedback[-10:]

        if filter_semantic_attempts >= filter_semantic_attempt_limit:
            reason = (
                "필터 의미 검증 결과를 확보하지 못해 중단합니다. "
                f"(select 시도 {filter_semantic_attempts}회)"
            )
            agent._log(f"❌ {reason}")
            return {
                "terminal_result": agent._build_failure_result(
                    goal=goal,
                    steps=steps,
                    step_count=step_count,
                    start_time=start_time,
                    reason=reason,
                ),
                "filter_semantic_attempts": filter_semantic_attempts,
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
    else:
        setattr(agent, "_discovery_no_progress_streak", 0)
        agent._no_progress_counter += 1
        if weak_only:
            agent._weak_progress_streak += 1
        else:
            agent._weak_progress_streak = 0

    master_orchestrator.record_progress(
        changed=changed,
        signal={
            "reason_code": agent._last_exec_result.reason_code if agent._last_exec_result else "unknown",
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
        reason_code=agent._last_exec_result.reason_code if agent._last_exec_result else None,
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
    reason_code = agent._last_exec_result.reason_code if agent._last_exec_result else "unknown"
    if (
        agentic_wrapper_mode
        and login_gate_visible
        and decision.action == ActionType.CLICK
        and reason_code in {"not_found", "not_actionable", "action_timeout", "no_state_change"}
    ):
        agent._action_feedback.append(
            "로그인/인증 surface가 열려 있어 배경 CTA가 차단됐습니다. "
            "같은 '바로 추가'를 반복하지 말고 현재 인증 surface 내부의 입력/제출 요소를 우선 선택하세요."
        )
        if len(agent._action_feedback) > 10:
            agent._action_feedback = agent._action_feedback[-10:]
    if bool(success and changed):
        agent._overlay_intercept_pending = False
    elif reason_code in {"not_actionable", "no_state_change"} and agent._error_indicates_overlay_intercept(error):
        agent._overlay_intercept_pending = True
        agent._record_reason_code("overlay_intercept_detected")
    ref_used = agent._last_exec_result.ref_id_used if agent._last_exec_result else ""
    agent._track_ref_outcome(
        ref_id=ref_used,
        reason_code=reason_code,
        success=success,
        changed=changed,
    )
    if (
        login_gate_visible
        and decision.action == ActionType.CLICK
        and reason_code in {"no_state_change", "not_actionable"}
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
                "filter_semantic_attempts": filter_semantic_attempts,
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
        "filter_semantic_attempts": filter_semantic_attempts,
        "scroll_streak": scroll_streak,
        "ineffective_action_streak": ineffective_action_streak,
        "force_context_shift": force_context_shift,
        "context_shift_fail_streak": context_shift_fail_streak,
        "context_shift_cooldown": context_shift_cooldown,
        "post_dom": post_dom,
        "changed": changed,
        "state_change": state_change,
    }
