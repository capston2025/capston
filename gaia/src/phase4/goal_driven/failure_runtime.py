from __future__ import annotations

import time
from typing import List, Optional

from .phase_constraints import build_constraint_failure_reason as build_constraint_failure_reason_impl
from .models import GoalResult, StepResult, TestGoal


def record_reason_code(agent, code: Optional[str]) -> None:
    key = str(code or "").strip()
    if not key:
        return
    counts = agent._reason_code_counts if isinstance(agent._reason_code_counts, dict) else {}
    counts[key] = int(counts.get(key, 0)) + 1
    agent._reason_code_counts = counts


def build_constraint_failure_reason(agent) -> Optional[str]:
    return build_constraint_failure_reason_impl(
        agent._goal_constraints,
        agent._goal_metric_value,
    )


def build_failure_result(
    agent,
    *,
    goal: TestGoal,
    steps: List[StepResult],
    step_count: int,
    start_time: float,
    reason: str,
) -> GoalResult:
    agent._log(f"❌ {reason}")
    result = GoalResult(
        goal_id=goal.id,
        goal_name=goal.name,
        success=False,
        steps_taken=steps,
        total_steps=step_count,
        final_reason=reason,
        duration_seconds=time.time() - start_time,
    )
    agent._record_goal_summary(
        goal=goal,
        status="failed",
        reason=reason,
        step_count=step_count,
        duration_seconds=result.duration_seconds,
    )
    return result
