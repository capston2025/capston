from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from .models import ActionDecision, ActionType, DOMElement, StepResult, TestGoal


def handle_forced_context_shift(
    *,
    agent: Any,
    goal: TestGoal,
    orchestrator: Any,
    step_count: int,
    step_start: float,
    dom_elements: List[DOMElement],
    before_signature: Any,
    collect_unmet: bool,
    sub_agent: Any,
    steps: List[StepResult],
    context_shift_used_elements: Set[int],
    context_shift_fail_streak: int,
    force_context_shift: bool,
    context_shift_cooldown: int,
    ineffective_action_streak: int,
) -> Dict[str, Any]:
    if not force_context_shift:
        return {
            "continue_loop": False,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "ineffective_action_streak": ineffective_action_streak,
        }

    picked = (
        agent._pick_collect_context_shift_element(dom_elements, context_shift_used_elements)
        if collect_unmet
        else None
    )
    if picked is None:
        picked = agent._pick_context_shift_element(dom_elements, context_shift_used_elements)

    if picked is not None:
        picked_id, picked_reason, picked_intent_key = picked
        context_shift_used_elements.add(picked_id)
        agent._last_context_shift_intent = picked_intent_key
        shift_decision = ActionDecision(
            action=ActionType.CLICK,
            element_id=picked_id,
            reasoning=picked_reason,
            confidence=0.9,
        )
        agent._log("🧭 무효 반복 감지: 페이지/섹션 전환을 우선 시도합니다.")
        step_result, success, error = sub_agent.run_step(
            step_number=step_count,
            step_start=step_start,
            decision=shift_decision,
            dom_elements=dom_elements,
        )
        steps.append(step_result)
        if success:
            agent._action_history.append(
                f"Step {step_count}: {shift_decision.action.value} - {shift_decision.reasoning}"
            )
        else:
            agent._log(f"⚠️ 컨텍스트 전환 실패: {error}")

        post_dom = agent._analyze_dom()
        changed = bool(post_dom) and agent._dom_progress_signature(post_dom) != before_signature
        agent._record_action_feedback(
            step_number=step_count,
            decision=shift_decision,
            success=success,
            changed=changed,
            error=error,
            reason_code=agent._last_exec_result.reason_code if agent._last_exec_result else None,
            state_change=agent._last_exec_result.state_change if agent._last_exec_result else None,
            intent_key=picked_intent_key,
        )
        agent._record_action_memory(
            goal=goal,
            step_number=step_count,
            decision=shift_decision,
            success=success,
            changed=changed,
            error=error,
        )

        if success and changed:
            ineffective_action_streak = 0
            force_context_shift = False
            context_shift_used_elements.clear()
            agent._last_context_shift_intent = ""
            orchestrator.same_dom_count = 0
            context_shift_fail_streak = 0
            context_shift_cooldown = 0
        else:
            context_shift_fail_streak += 1
            if len(context_shift_used_elements) > 20:
                context_shift_used_elements.clear()
            if context_shift_fail_streak >= 3:
                agent._log(
                    "🧭 컨텍스트 전환이 연속 실패해 일반 액션 전략으로 복귀합니다."
                )
                force_context_shift = False
                context_shift_used_elements.clear()
                agent._last_context_shift_intent = ""
                context_shift_cooldown = 4
            else:
                force_context_shift = True
        time.sleep(0.4)
        return {
            "continue_loop": True,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "ineffective_action_streak": ineffective_action_streak,
        }

    if collect_unmet:
        agent._log("🧭 전환 후보 부족: 수집 CTA 노출을 위해 스크롤 전환을 시도합니다.")
        scroll_target_id: Optional[int] = None
        shift_pick = agent._pick_collect_context_shift_element(dom_elements, set())
        if shift_pick is not None:
            scroll_target_id = shift_pick[0]
        elif dom_elements:
            for el in dom_elements:
                ref_id = agent._element_ref_ids.get(el.id)
                if ref_id and not agent._is_ref_temporarily_blocked(ref_id):
                    scroll_target_id = el.id
                    break
        if scroll_target_id is None:
            agent._log("🧭 스크롤 전환 대상(ref)을 찾지 못해 이번 스텝은 대기로 전환합니다.")
            shift_decision = ActionDecision(
                action=ActionType.WAIT,
                reasoning="컨텍스트 전환 대상(ref) 부재로 DOM 재수집 대기",
                confidence=0.45,
            )
        else:
            shift_decision = ActionDecision(
                action=ActionType.SCROLL,
                element_id=scroll_target_id,
                reasoning="수집 목표 미달 상태에서 새 수집 요소 탐색을 위한 스크롤 전환",
                confidence=0.6,
            )
        step_result, success, _error = sub_agent.run_step(
            step_number=step_count,
            step_start=step_start,
            decision=shift_decision,
            dom_elements=dom_elements,
        )
        steps.append(step_result)
        post_dom = agent._analyze_dom()
        changed = bool(post_dom) and agent._dom_progress_signature(post_dom) != before_signature
        if success and changed:
            context_shift_fail_streak = 0
            force_context_shift = False
            context_shift_cooldown = 0
        else:
            context_shift_fail_streak += 1
            force_context_shift = context_shift_fail_streak < 3
            if context_shift_fail_streak >= 3:
                context_shift_cooldown = 4
        time.sleep(0.3)
        return {
            "continue_loop": True,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "ineffective_action_streak": ineffective_action_streak,
        }

    agent._log("🧭 컨텍스트 전환 후보를 찾지 못해 기본 LLM 흐름으로 계속 진행합니다.")
    force_context_shift = False
    return {
        "continue_loop": False,
        "force_context_shift": force_context_shift,
        "context_shift_fail_streak": context_shift_fail_streak,
        "context_shift_cooldown": context_shift_cooldown,
        "ineffective_action_streak": ineffective_action_streak,
    }
