from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .models import ActionDecision, DOMElement, TestGoal
from .runtime import ActionExecResult
from gaia.src.phase4.memory.models import MemoryActionRecord, MemorySummaryRecord


def dom_progress_signature(dom_elements: List[DOMElement]) -> str:
    count = len(dom_elements)
    if count < 50:
        bucket = "lt50"
    elif count < 100:
        bucket = "50_99"
    elif count < 150:
        bucket = "100_149"
    elif count < 220:
        bucket = "150_219"
    else:
        bucket = "220p"
    chunks: List[str] = []
    for el in dom_elements[:20]:
        chunks.append(
            f"{el.tag}|{(el.text or '')[:40]}|{el.role or ''}|{el.type or ''}|{el.aria_label or ''}"
        )
    return f"{bucket}#" + "||".join(chunks)


def record_action_feedback(
    agent: Any,
    *,
    step_number: int,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    error: Optional[str],
    reason_code: Optional[str] = None,
    state_change: Optional[Dict[str, Any]] = None,
    intent_key: Optional[str] = None,
) -> None:
    code = reason_code or (agent._last_exec_result.reason_code if agent._last_exec_result else "unknown")
    agent._record_reason_code(str(code or "unknown"))
    agent._update_intent_stats(
        intent_key=intent_key or "",
        success=bool(success),
        changed=bool(changed),
        reason_code=str(code or "unknown"),
    )
    state_info = ""
    if isinstance(state_change, dict) and state_change:
        effective = bool(state_change.get("effective", False))
        state_info = f", effective={effective}"
    feedback = (
        f"Step {step_number}: action={decision.action.value}, "
        f"element_id={decision.element_id}, changed={changed}, success={success}, "
        f"reason_code={code}{state_info}, error={error or 'none'}"
    )
    agent._action_feedback.append(feedback)
    if len(agent._action_feedback) > 10:
        agent._action_feedback = agent._action_feedback[-10:]


def extract_domain(url: Optional[str]) -> str:
    parsed = urlparse(url or "")
    return (parsed.netloc or "").lower()


def build_memory_context(agent: Any, goal: TestGoal) -> str:
    if not agent._memory_store.enabled or not agent._memory_domain:
        agent._memory_selector_bias = {}
        return ""
    hints = agent._memory_retriever.retrieve_lightweight(
        domain=agent._memory_domain,
        goal_text=f"{goal.name} {goal.description}",
        action_history=agent._action_history[-6:],
    )

    bias: Dict[str, float] = {}
    for item in hints:
        selector_hint = agent._normalize_selector_key(str(item.selector_hint or ""))
        if not selector_hint:
            continue
        confidence = max(0.0, min(1.0, float(item.confidence or 0.0)))
        weight = 0.0
        if item.source == "success_pattern":
            weight += 0.7 + (0.6 * confidence)
        elif item.source == "failure_pattern":
            weight -= 0.6 + (0.7 * confidence)
        elif item.source == "recovery":
            if str(item.reason_code or "") in {"no_state_change", "not_actionable", "not_found"}:
                weight -= 0.4
            else:
                weight += 0.2

        if str(item.reason_code or "") in {"no_state_change", "not_actionable", "blocked_ref_no_progress"}:
            weight -= 0.35
        elif str(item.reason_code or "") == "ok":
            weight += 0.2

        merged = float(bias.get(selector_hint, 0.0)) + weight
        bias[selector_hint] = agent._clamp_score(merged, low=-4.0, high=4.0)

    if len(bias) > 40:
        top = sorted(bias.items(), key=lambda kv: abs(kv[1]), reverse=True)[:40]
        agent._memory_selector_bias = dict(top)
    else:
        agent._memory_selector_bias = bias

    return agent._memory_retriever.format_for_prompt(hints)


def record_recovery_hints(agent: Any, goal: TestGoal, reason_code: str) -> None:
    if not agent._memory_store.enabled or not agent._memory_domain:
        return
    hints = agent._memory_retriever.retrieve_recovery(
        domain=agent._memory_domain,
        goal_text=f"{goal.name} {goal.description}",
        reason_code=reason_code,
        limit=3,
    )
    text = agent._memory_retriever.format_for_prompt(hints, max_items=3)
    if not text:
        return
    agent._action_feedback.append(f"Recovery hints ({reason_code}): {text}")
    if len(agent._action_feedback) > 10:
        agent._action_feedback = agent._action_feedback[-10:]


def record_action_memory(
    agent: Any,
    *,
    goal: TestGoal,
    step_number: int,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    error: Optional[str],
) -> None:
    if not agent._memory_store.enabled:
        return
    if agent._memory_episode_id is None:
        return
    exec_result = agent._last_exec_result or ActionExecResult(
        success=success,
        effective=success,
        reason_code="unknown",
        reason=error or "",
    )
    selector = ""
    full_selector = ""
    ref_id = ""
    frame_index: Optional[int] = None
    tab_index: Optional[int] = None
    if decision.element_id is not None:
        selector = agent._element_selectors.get(decision.element_id, "")
        full_selector = agent._element_full_selectors.get(decision.element_id, "")
        ref_id = agent._element_ref_ids.get(decision.element_id, "")
        scope = agent._element_scopes.get(decision.element_id, {})
        if isinstance(scope, dict):
            frame_index = scope.get("frame_index")
            tab_index = scope.get("tab_index")

    try:
        agent._memory_store.record_action(
            MemoryActionRecord(
                episode_id=agent._memory_episode_id,
                domain=agent._memory_domain,
                url=goal.start_url or "",
                step_number=step_number,
                action=decision.action.value,
                selector=selector,
                full_selector=full_selector,
                ref_id=ref_id,
                success=bool(exec_result.success and exec_result.effective),
                effective=bool(exec_result.effective),
                changed=bool(changed),
                reason_code=exec_result.reason_code,
                reason=exec_result.reason or (error or ""),
                snapshot_id=exec_result.snapshot_id_used or agent._active_snapshot_id,
                dom_hash=agent._active_dom_hash,
                epoch=agent._active_snapshot_epoch,
                frame_index=frame_index if isinstance(frame_index, int) else None,
                tab_index=tab_index if isinstance(tab_index, int) else None,
                state_change=exec_result.state_change or {},
                attempt_logs=exec_result.attempt_logs or [],
            )
        )
    except Exception:
        return


def record_goal_summary(
    agent: Any,
    *,
    goal: TestGoal,
    status: str,
    reason: str,
    step_count: int,
    duration_seconds: float,
) -> None:
    if not agent._memory_store.enabled:
        return
    try:
        agent._memory_store.add_dialog_summary(
            MemorySummaryRecord(
                episode_id=agent._memory_episode_id,
                domain=agent._memory_domain,
                command="/test",
                summary=(
                    f"goal={goal.name}, status={status}, steps={step_count}, "
                    f"reason={reason}, duration={duration_seconds:.2f}s"
                ),
                status=status,
                metadata={
                    "goal_id": goal.id,
                    "goal_name": goal.name,
                    "steps": step_count,
                    "reason": reason,
                    "duration_seconds": duration_seconds,
                },
            )
        )
    except Exception:
        return
