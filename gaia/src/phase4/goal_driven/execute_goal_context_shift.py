from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Set

from .models import ActionDecision, ActionType, DOMElement, StepResult, TestGoal


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


def _strong_shift_progress(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    strong_keys = (
        "url_changed",
        "modal_state_changed",
        "modal_count_changed",
        "backdrop_count_changed",
        "dialog_count_changed",
        "auth_state_changed",
        "tab_changed",
        "frame_changed",
    )
    return any(bool(state_change.get(key)) for key in strong_keys)


def _is_weak_dom_only_change(
    *,
    before_count: int,
    after_count: int,
    before_signature: Any,
    after_signature: Any,
) -> bool:
    if before_signature == after_signature:
        return True
    count_delta = abs(int(after_count) - int(before_count))
    if count_delta <= 12:
        return True
    return False


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
    context_shift_fail_limit = max(1, _policy_int(agent, "context_shift_fail_limit", 3))
    context_shift_cooldown_steps = _policy_int(agent, "context_shift_cooldown_steps", 4)
    if not force_context_shift:
        setattr(agent, "_forced_context_shift_loop_streak", 0)
        return {
            "continue_loop": False,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "ineffective_action_streak": ineffective_action_streak,
        }
    modal_open_now = bool(
        (getattr(agent, "_last_snapshot_evidence", {}) or {}).get("modal_open")
    )
    if modal_open_now:
        setattr(agent, "_forced_context_shift_loop_streak", 0)
        _emit_reason(agent, "context_shift_blocked_modal_open")
        agent._log("🧭 모달이 열린 상태라 컨텍스트 전환을 중단하고 닫기/상세 상호작용을 우선합니다.")
        return {
            "continue_loop": False,
            "force_context_shift": False,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "ineffective_action_streak": ineffective_action_streak,
        }
    dom_count_history = list(getattr(agent, "_context_shift_dom_count_history", []) or [])
    dom_count_history.append(int(len(dom_elements)))
    if len(dom_count_history) > 8:
        dom_count_history = dom_count_history[-8:]
    setattr(agent, "_context_shift_dom_count_history", dom_count_history)
    if str(os.getenv("GAIA_TRACE_CONTEXT_SHIFT", "")).strip().lower() in {"1", "true", "yes", "on"}:
        agent._log(
            "🧪 context-shift trace: "
            f"dom_count_history={dom_count_history[-4:]}, "
            f"collect_unmet={bool(collect_unmet)}, "
            f"no_progress={int(getattr(agent, '_no_progress_counter', 0))}"
        )
    if len(dom_count_history) >= 4:
        a, b, c, d = dom_count_history[-4:]
        if a == c and b == d and a != b and abs(a - b) <= 20:
            _emit_reason(agent, "context_shift_oscillation_abab")
            agent._log("🧭 컨텍스트 전환 DOM 진동(ABAB) 감지: 전환을 중단하고 직접 상호작용 후보 탐색으로 복귀합니다.")
            return {
                "continue_loop": False,
                "force_context_shift": False,
                "context_shift_fail_streak": context_shift_fail_streak,
                "context_shift_cooldown": context_shift_cooldown_steps,
                "ineffective_action_streak": ineffective_action_streak,
            }
    forced_shift_streak = int(getattr(agent, "_forced_context_shift_loop_streak", 0)) + 1
    setattr(agent, "_forced_context_shift_loop_streak", forced_shift_streak)
    if (not collect_unmet) and forced_shift_streak >= 2:
        _emit_reason(agent, "context_shift_repeat_break")
        agent._log("🧭 컨텍스트 전환 반복 감지: 전환 루프를 중단하고 직접 상호작용 후보 탐색으로 복귀합니다.")
        return {
            "continue_loop": False,
            "force_context_shift": False,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown_steps,
            "ineffective_action_streak": ineffective_action_streak,
        }
    no_progress_context_shift_min = max(1, _policy_int(agent, "no_progress_context_shift_min", 2))
    if (not collect_unmet) and int(getattr(agent, "_no_progress_counter", 0)) < no_progress_context_shift_min:
        setattr(agent, "_forced_context_shift_loop_streak", 0)
        return {
            "continue_loop": False,
            "force_context_shift": False,
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
        after_signature = agent._dom_progress_signature(post_dom) if post_dom else before_signature
        state_change = (
            getattr(agent, "_last_exec_result", None).state_change
            if getattr(agent, "_last_exec_result", None)
            else None
        )
        changed = _strong_shift_progress(state_change)
        if not changed and bool(post_dom):
            weak_change = _is_weak_dom_only_change(
                before_count=len(dom_elements),
                after_count=len(post_dom),
                before_signature=before_signature,
                after_signature=after_signature,
            )
            changed = not weak_change
        agent._record_action_feedback(
            step_number=step_count,
            decision=shift_decision,
            success=success,
            changed=changed,
            error=error,
            reason_code=agent._last_exec_result.reason_code if agent._last_exec_result else None,
            state_change=state_change if isinstance(state_change, dict) else None,
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
            setattr(agent, "_forced_context_shift_loop_streak", 0)
            context_shift_used_elements.clear()
            agent._last_context_shift_intent = ""
            orchestrator.same_dom_count = 0
            context_shift_fail_streak = 0
            context_shift_cooldown = 0
        else:
            context_shift_fail_streak += 1
            if len(context_shift_used_elements) > 20:
                context_shift_used_elements.clear()
            quick_break_limit = 2 if collect_unmet else context_shift_fail_limit
            if context_shift_fail_streak >= max(1, quick_break_limit):
                _emit_reason(agent, "context_shift_fail_exhausted")
                agent._log(
                    "🧭 컨텍스트 전환이 연속 실패해 일반 액션 전략으로 복귀합니다."
                )
                force_context_shift = False
                setattr(agent, "_forced_context_shift_loop_streak", 0)
                context_shift_used_elements.clear()
                agent._last_context_shift_intent = ""
                context_shift_cooldown = context_shift_cooldown_steps
                time.sleep(0.2)
                return {
                    "continue_loop": False,
                    "force_context_shift": force_context_shift,
                    "context_shift_fail_streak": context_shift_fail_streak,
                    "context_shift_cooldown": context_shift_cooldown,
                    "ineffective_action_streak": ineffective_action_streak,
                }
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
        setattr(agent, "_forced_context_shift_loop_streak", 0)
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
        after_signature = agent._dom_progress_signature(post_dom) if post_dom else before_signature
        state_change = (
            getattr(agent, "_last_exec_result", None).state_change
            if getattr(agent, "_last_exec_result", None)
            else None
        )
        changed = _strong_shift_progress(state_change)
        if not changed and bool(post_dom):
            weak_change = _is_weak_dom_only_change(
                before_count=len(dom_elements),
                after_count=len(post_dom),
                before_signature=before_signature,
                after_signature=after_signature,
            )
            changed = not weak_change
        if success and changed:
            context_shift_fail_streak = 0
            force_context_shift = False
            context_shift_cooldown = 0
        else:
            context_shift_fail_streak += 1
            quick_break_limit = 2 if collect_unmet else context_shift_fail_limit
            force_context_shift = context_shift_fail_streak < max(1, quick_break_limit)
            if context_shift_fail_streak >= max(1, quick_break_limit):
                _emit_reason(agent, "context_shift_fail_exhausted")
                context_shift_cooldown = context_shift_cooldown_steps
                return {
                    "continue_loop": False,
                    "force_context_shift": False,
                    "context_shift_fail_streak": context_shift_fail_streak,
                    "context_shift_cooldown": context_shift_cooldown,
                    "ineffective_action_streak": ineffective_action_streak,
                }
        time.sleep(0.3)
        return {
            "continue_loop": True,
            "force_context_shift": force_context_shift,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown,
            "ineffective_action_streak": ineffective_action_streak,
        }

    _emit_reason(agent, "context_shift_no_candidate")
    fallback_scroll_target: Optional[int] = None
    if dom_elements:
        for el in dom_elements:
            ref_id = agent._element_ref_ids.get(el.id)
            if ref_id and not agent._is_ref_temporarily_blocked(ref_id):
                fallback_scroll_target = el.id
                break
    if fallback_scroll_target is not None:
        agent._log("🧭 컨텍스트 전환 후보 부재: 범용 스크롤 fallback으로 상태 변화를 유도합니다.")
        shift_decision = ActionDecision(
            action=ActionType.SCROLL,
            element_id=fallback_scroll_target,
            reasoning="전환 후보 부족으로 범용 스크롤 fallback 수행",
            confidence=0.45,
        )
        step_result, success, error = sub_agent.run_step(
            step_number=step_count,
            step_start=step_start,
            decision=shift_decision,
            dom_elements=dom_elements,
        )
        steps.append(step_result)
        post_dom = agent._analyze_dom()
        after_signature = agent._dom_progress_signature(post_dom) if post_dom else before_signature
        state_change = (
            getattr(agent, "_last_exec_result", None).state_change
            if getattr(agent, "_last_exec_result", None)
            else None
        )
        changed = _strong_shift_progress(state_change)
        if not changed and bool(post_dom):
            weak_change = _is_weak_dom_only_change(
                before_count=len(dom_elements),
                after_count=len(post_dom),
                before_signature=before_signature,
                after_signature=after_signature,
            )
            changed = not weak_change
        agent._record_action_feedback(
            step_number=step_count,
            decision=shift_decision,
            success=success,
            changed=changed,
            error=error,
            reason_code=agent._last_exec_result.reason_code if agent._last_exec_result else "context_shift_no_candidate",
            state_change=state_change if isinstance(state_change, dict) else None,
            intent_key="context_shift:fallback_scroll",
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
            _emit_reason(agent, "context_shift_fallback_scroll_ok")
            return {
                "continue_loop": True,
                "force_context_shift": False,
                "context_shift_fail_streak": 0,
                "context_shift_cooldown": 0,
                "ineffective_action_streak": 0,
            }
        context_shift_fail_streak += 1
        _emit_reason(agent, "context_shift_fallback_scroll_failed")
        return {
            "continue_loop": True,
            "force_context_shift": False,
            "context_shift_fail_streak": context_shift_fail_streak,
            "context_shift_cooldown": context_shift_cooldown_steps,
            "ineffective_action_streak": ineffective_action_streak,
        }

    agent._log("🧭 컨텍스트 전환 후보/스크롤 fallback 모두 없어 기본 LLM 흐름으로 진행합니다.")
    force_context_shift = False
    return {
        "continue_loop": False,
        "force_context_shift": force_context_shift,
        "context_shift_fail_streak": context_shift_fail_streak,
        "context_shift_cooldown": context_shift_cooldown,
        "ineffective_action_streak": ineffective_action_streak,
    }
