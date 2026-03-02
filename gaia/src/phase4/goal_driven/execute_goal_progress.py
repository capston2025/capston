from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .models import ActionDecision, DOMElement, GoalResult, StepResult, TestGoal


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
    post_dom = agent._analyze_dom()
    refreshed_metric = agent._estimate_goal_metric_from_dom(post_dom) if post_dom else None
    if refreshed_metric is not None:
        agent._goal_metric_value = refreshed_metric
    state_change = agent._last_exec_result.state_change if agent._last_exec_result else None
    changed_by_state = agent._state_change_indicates_progress(state_change)
    changed_by_dom = bool(post_dom) and agent._dom_progress_signature(post_dom) != before_signature
    changed = bool(changed_by_state or changed_by_dom)

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

    return {
        "post_dom": post_dom,
        "state_change": state_change,
        "changed": changed,
        "terminal_result": terminal_result,
    }
