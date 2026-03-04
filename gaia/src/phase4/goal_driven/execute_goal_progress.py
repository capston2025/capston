from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .models import ActionDecision, ActionType, DOMElement, GoalResult, StepResult, TestGoal


def _emit_reason(agent: Any, code: str) -> None:
    if not code:
        return
    recorder = getattr(agent, "_record_reason_code", None)
    if callable(recorder):
        recorder(code)


def _strong_state_progress(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    keys = (
        "url_changed",
        "target_visibility_changed",
        "target_value_changed",
        "target_value_matches",
        "counter_changed",
        "number_tokens_changed",
        "status_text_changed",
        "list_count_changed",
        "interactive_count_changed",
        "modal_count_changed",
        "backdrop_count_changed",
        "dialog_count_changed",
        "modal_state_changed",
        "auth_state_changed",
        "text_digest_changed",
        "nav_detected",
        "popup_detected",
        "dialog_detected",
    )
    return any(bool(state_change.get(key)) for key in keys)


def _is_weak_dom_only_change(
    *,
    before_count: int,
    after_count: int,
    before_signature: Any,
    after_signature: Any,
) -> bool:
    if before_signature == after_signature:
        return True
    if abs(int(after_count) - int(before_count)) <= 12:
        return True
    return False


def evaluate_post_action_progress(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    before_signature: Any,
    dom_elements: List[DOMElement],
    step_count: int,
    steps: List[StepResult],
    start_time: float,
) -> Dict[str, Any]:
    before_evidence = (
        dict(agent._last_snapshot_evidence)
        if isinstance(getattr(agent, "_last_snapshot_evidence", None), dict)
        else {}
    )
    before_modal_open = bool(before_evidence.get("modal_open"))
    decision_reasoning = str(getattr(decision, "reasoning", "") or "").lower()
    decision_close_intent = bool(
        any(
            token in decision_reasoning
            for token in (
                "닫",
                "close",
                "dismiss",
                "종료",
                "x 버튼",
                "우상단 x",
            )
        )
    )
    post_dom = agent._analyze_dom()
    after_evidence = (
        dict(agent._last_snapshot_evidence)
        if isinstance(getattr(agent, "_last_snapshot_evidence", None), dict)
        else {}
    )
    after_modal_open = bool(after_evidence.get("modal_open"))
    if before_modal_open and after_modal_open:
        agent._modal_opened_once = True
    elif (not before_modal_open) and after_modal_open:
        agent._modal_opened_once = True

    refreshed_metric = agent._estimate_goal_metric_from_dom(post_dom) if post_dom else None
    if refreshed_metric is not None:
        agent._goal_metric_value = refreshed_metric
    state_change = agent._last_exec_result.state_change if agent._last_exec_result else None
    close_transition_signal = bool(
        isinstance(state_change, dict)
        and (
            bool(state_change.get("modal_state_changed"))
            or bool(state_change.get("modal_count_changed"))
            or bool(state_change.get("backdrop_count_changed"))
            or bool(state_change.get("dialog_count_changed"))
        )
    )
    changed_by_state = _strong_state_progress(state_change)
    after_signature = agent._dom_progress_signature(post_dom) if post_dom else before_signature
    changed_by_dom = False
    if bool(post_dom) and before_signature != after_signature:
        weak_dom_only = _is_weak_dom_only_change(
            before_count=len(dom_elements),
            after_count=len(post_dom),
            before_signature=before_signature,
            after_signature=after_signature,
        )
        changed_by_dom = not weak_dom_only
    changed = bool(changed_by_state or changed_by_dom)
    if changed_by_state:
        _emit_reason(agent, "progress_state_change")
    elif changed_by_dom:
        _emit_reason(agent, "progress_dom_signature")
    elif (
        bool(success)
        and isinstance(state_change, dict)
        and bool(state_change.get("effective"))
    ):
        # OpenClaw-style guard: weak effective(관측상 약한 변화)는 루프 리셋 신호로 쓰지 않는다.
        _emit_reason(agent, "weak_effective_ignored")
    if decision_close_intent and bool(success):
        # close intent 액션이 실제로 실행됐다면, evidence 지연/누락이 있더라도
        # "모달이 열린 상태를 다루는 흐름"으로 간주해 종료 판정 누락을 줄인다.
        agent._modal_opened_once = True
        if bool(changed):
            agent._close_intent_success_once = True
            if decision.action == ActionType.CLICK:
                agent._close_click_success_once = True
    if (
        bool(getattr(agent, "_modal_opened_once", False))
        and (not after_modal_open)
        and (
            before_modal_open
            or close_transition_signal
            or (decision_close_intent and bool(success) and bool(changed))
        )
    ):
        agent._modal_closed_after_open = True

    if bool(agent._goal_constraints.get("require_no_navigation")) and isinstance(state_change, dict):
        if bool(state_change.get("url_changed")):
            agent._log("🧱 제약 가드: '페이지 이동 없이' 목표라 URL 변경 액션은 진행으로 인정하지 않습니다.")
            changed = False
            start_url = str(goal.start_url or "").strip()
            if start_url:
                agent._log("↩️ 페이지 고정 제약 복구: 시작 URL로 복귀합니다.")
                _ = agent._execute_action("goto", url=start_url)
                time.sleep(0.8)
                recovered_dom = agent._analyze_dom()
                if recovered_dom:
                    post_dom = recovered_dom

    terminal_result: Optional[GoalResult] = None
    if agent._can_finish_by_verification_transition(
        goal=goal,
        decision=decision,
        success=success,
        changed=changed,
        state_change=state_change,
        before_dom_count=len(dom_elements),
        after_dom_count=len(post_dom or []),
    ):
        completion_reason = agent._build_verification_transition_reason(
            state_change=state_change,
            before_dom_count=len(dom_elements),
            after_dom_count=len(post_dom or []),
        )
        agent._log(f"✅ 목표 달성! 이유: {completion_reason}")
        terminal_result = GoalResult(
            goal_id=goal.id,
            goal_name=goal.name,
            success=True,
            steps_taken=steps,
            total_steps=step_count,
            final_reason=completion_reason,
            duration_seconds=time.time() - start_time,
        )
        agent._record_goal_summary(
            goal=goal,
            status="success",
            reason=terminal_result.final_reason,
            step_count=step_count,
            duration_seconds=terminal_result.duration_seconds,
        )
    if terminal_result is None:
        goal_blob = f"{goal.name} {goal.description}".strip().lower()
        close_keywords = ("닫", "close", "x 버튼", "우상단 x", "overlay", "오버레이", "modal", "모달")
        list_keywords = ("목록", "list", "게시판", "게시글", "board", "row")
        x_button_keywords = ("x 버튼", "x버튼", "우상단 x", "닫기 버튼", "close button", "close-btn")
        close_goal = any(token in goal_blob for token in close_keywords)
        list_goal = any(token in goal_blob for token in list_keywords)
        x_button_goal = any(token in goal_blob for token in x_button_keywords)
        has_list_like_dom = any(
            (str(getattr(el, "tag", "") or "").lower() in {"tr", "li", "article", "table", "tbody"})
            or (str(getattr(el, "role", "") or "").lower() in {"row", "listitem", "gridcell", "rowheader", "table", "grid"})
            for el in (post_dom or [])
        )
        close_success_gate = bool(getattr(agent, "_close_intent_success_once", False))
        if x_button_goal:
            close_success_gate = bool(getattr(agent, "_close_click_success_once", False))
        close_step_verified = bool(
            decision.action in {ActionType.CLICK, ActionType.PRESS}
            and decision_close_intent
            and bool(success)
            and bool(changed)
            and (not after_modal_open)
        )
        if (
            close_goal
            and list_goal
            and bool(getattr(agent, "_modal_opened_once", False))
            and bool(getattr(agent, "_modal_closed_after_open", False))
            and close_success_gate
            and (has_list_like_dom or close_step_verified)
        ):
            completion_reason = (
                "상세 오버레이 열기/닫기와 목록 복귀 상태가 모두 확인되어 목표를 완료로 판정했습니다."
            )
            agent._log(f"✅ 목표 달성! 이유: {completion_reason}")
            terminal_result = GoalResult(
                goal_id=goal.id,
                goal_name=goal.name,
                success=True,
                steps_taken=steps,
                total_steps=step_count,
                final_reason=completion_reason,
                duration_seconds=time.time() - start_time,
            )
            agent._record_goal_summary(
                goal=goal,
                status="success",
                reason=terminal_result.final_reason,
                step_count=step_count,
                duration_seconds=terminal_result.duration_seconds,
            )

    return {
        "post_dom": post_dom,
        "state_change": state_change,
        "changed": changed,
        "terminal_result": terminal_result,
    }
