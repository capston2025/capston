from __future__ import annotations

import time
from typing import Any, Dict, List

from .models import ActionDecision, ActionType, GoalResult, StepResult, TestGoal


def update_action_streaks_and_loops(
    *,
    agent: Any,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    click_intent_key: str,
    scroll_streak: int,
    ineffective_action_streak: int,
    force_context_shift: bool,
    context_shift_fail_streak: int,
    context_shift_cooldown: int,
    steps: List[StepResult],
    step_count: int,
    start_time: float,
) -> Dict[str, Any]:
    terminal_result: GoalResult | None = None

    if decision.action in {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.NAVIGATE,
        ActionType.SCROLL,
    }:
        if success and changed:
            ineffective_action_streak = 0
            context_shift_fail_streak = 0
            context_shift_cooldown = 0
        else:
            ineffective_action_streak += 1
    else:
        ineffective_action_streak = 0

    if scroll_streak >= 3:
        agent._log("🧭 스크롤이 연속 선택되어 컨텍스트 전환을 강제합니다.")
        force_context_shift = True
        scroll_streak = 0

    if decision.action == ActionType.CLICK:
        if click_intent_key and (not success or not changed):
            if click_intent_key == agent._last_success_click_intent:
                agent._success_click_intent_streak += 1
            else:
                agent._last_success_click_intent = click_intent_key
                agent._success_click_intent_streak = 1
        elif click_intent_key and success and changed:
            agent._last_success_click_intent = click_intent_key
            agent._success_click_intent_streak = 0
        else:
            agent._success_click_intent_streak = 0
    elif decision.action in {
        ActionType.CLICK,
        ActionType.SCROLL,
        ActionType.NAVIGATE,
        ActionType.PRESS,
    }:
        agent._last_success_click_intent = ""
        agent._success_click_intent_streak = 0

    if agent._success_click_intent_streak >= 3 and agent._no_progress_counter >= 2:
        agent._log("🧭 동일 클릭 의도 반복 감지: 단계 전환 CTA 탐색으로 전환합니다.")
        force_context_shift = True

    if ineffective_action_streak >= 3 and agent._no_progress_counter >= 2:
        force_context_shift = True

    if ineffective_action_streak >= 8:
        terminal_result = agent._build_failure_result(
            goal=goal,
            steps=steps,
            step_count=step_count,
            start_time=start_time,
            reason=(
                "무효 액션이 장시간 반복되어 중단했습니다. "
                "컨텍스트 전환(페이지/탭/필터) 시도 후에도 상태 변화가 없습니다."
            ),
        )

    if terminal_result is None:
        time.sleep(0.5)

    return {
        "scroll_streak": scroll_streak,
        "ineffective_action_streak": ineffective_action_streak,
        "force_context_shift": force_context_shift,
        "context_shift_fail_streak": context_shift_fail_streak,
        "context_shift_cooldown": context_shift_cooldown,
        "terminal_result": terminal_result,
    }
