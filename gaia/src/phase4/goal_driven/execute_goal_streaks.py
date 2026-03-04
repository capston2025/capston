from __future__ import annotations

import time
from typing import Any, Dict, List

from .models import ActionDecision, ActionType, GoalResult, StepResult, TestGoal


def _policy_int(agent: Any, key: str, default: int) -> int:
    cfg = getattr(agent, "_loop_policy", {})
    if isinstance(cfg, dict):
        try:
            return max(0, int(cfg.get(key, default)))
        except Exception:
            return max(0, int(default))
    return max(0, int(default))


def _emit_reason(agent: Any, code: str) -> None:
    if not code:
        return
    recorder = getattr(agent, "_record_reason_code", None)
    if callable(recorder):
        recorder(code)


def _action_signature(decision: ActionDecision) -> str:
    element_id = int(decision.element_id) if decision.element_id is not None else -1
    value = str(decision.value or "").strip().lower()
    if len(value) > 48:
        value = value[:48]
    return f"{decision.action.value}:{element_id}:{value}"


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
    scroll_streak_limit = max(1, _policy_int(agent, "scroll_streak_limit", 3))
    same_intent_soft_fail_limit = max(1, _policy_int(agent, "same_intent_soft_fail_limit", 3))
    no_progress_context_shift_min = _policy_int(agent, "no_progress_context_shift_min", 2)
    ineffective_action_shift_limit = max(1, _policy_int(agent, "ineffective_action_shift_limit", 3))
    ineffective_action_stop_limit = max(2, _policy_int(agent, "ineffective_action_stop_limit", 8))

    sig_history = list(getattr(agent, "_loop_action_signature_history", []) or [])
    sig_history.append(_action_signature(decision))
    if len(sig_history) > 10:
        sig_history = sig_history[-10:]
    setattr(agent, "_loop_action_signature_history", sig_history)

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

    if scroll_streak >= scroll_streak_limit:
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

    if (
        agent._success_click_intent_streak >= same_intent_soft_fail_limit
        and agent._no_progress_counter >= no_progress_context_shift_min
    ):
        agent._log("🧭 동일 클릭 의도 반복 감지: 단계 전환 CTA 탐색으로 전환합니다.")
        force_context_shift = True

    if (
        ineffective_action_streak >= ineffective_action_shift_limit
        and agent._no_progress_counter >= no_progress_context_shift_min
    ):
        force_context_shift = True
        _emit_reason(agent, "loop_ineffective_shift")

    if len(sig_history) >= 4 and agent._no_progress_counter >= no_progress_context_shift_min:
        a, b, c, d = sig_history[-4:]
        if a == c and b == d and a != b:
            agent._log("🧭 액션 진동(ABAB) 감지: 강제 컨텍스트 전환을 적용합니다.")
            force_context_shift = True
            _emit_reason(agent, "loop_oscillation_action_abab")
    if len(sig_history) >= 5 and agent._no_progress_counter >= no_progress_context_shift_min:
        tail = sig_history[-5:]
        if len(set(tail)) == 1:
            agent._log("🧭 동일 액션 반복 루프 감지: 강제 컨텍스트 전환을 적용합니다.")
            force_context_shift = True
            _emit_reason(agent, "loop_repeat_same_action")

    if ineffective_action_streak >= ineffective_action_stop_limit:
        _emit_reason(agent, "loop_ineffective_stop")
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
