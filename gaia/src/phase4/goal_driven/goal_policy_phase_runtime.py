from __future__ import annotations

from typing import Any, Dict, List, Optional

from .goal_policy_helpers import build_goal_policy_evidence_bundle
from .models import DOMElement


def derive_goal_policy_event(
    *,
    decision: Any,
    success: bool,
    changed: bool,
    terminal_result: Any = None,
    login_gate_visible: bool = False,
    auth_resume_pending: bool = False,
    auth_submit_attempted: bool = False,
) -> str:
    action_value = str(getattr(getattr(decision, "action", None), "value", "") or "").strip().lower()
    if terminal_result is not None:
        return "terminal"
    if login_gate_visible:
        return "blocked_auth"
    if auth_resume_pending and auth_submit_attempted:
        return "auth_resolved"
    if action_value == "wait":
        return "wait_progress" if changed else "wait_no_progress"
    if success:
        return "action_ok" if changed else "action_no_state_change"
    return "action_failed"


def advance_goal_policy_phase(
    agent: Any,
    *,
    goal: Any,
    decision: Any,
    success: bool,
    changed: bool,
    dom_elements: List[DOMElement],
    post_dom: Optional[List[DOMElement]] = None,
    auth_prompt_visible: bool = False,
    modal_open: bool = False,
    terminal_result: Any = None,
) -> Dict[str, Any]:
    policy = getattr(agent, "_goal_policy", None)
    semantics = getattr(agent, "_goal_semantics", None)
    if policy is None or semantics is None:
        return {}

    effective_dom = post_dom if isinstance(post_dom, list) and post_dom else dom_elements
    evidence = build_goal_policy_evidence_bundle(
        agent,
        goal=goal,
        dom_elements=effective_dom,
        auth_prompt_visible=auth_prompt_visible,
        modal_open=modal_open,
    )
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or policy.initial_phase(semantics))
    event = derive_goal_policy_event(
        decision=decision,
        success=success,
        changed=changed,
        terminal_result=terminal_result,
        login_gate_visible=auth_prompt_visible,
        auth_resume_pending=bool(getattr(agent, "_auth_resume_pending", False)),
        auth_submit_attempted=bool(getattr(agent, "_auth_submit_attempted", False)),
    )
    next_phase = current_phase
    if evidence is not None and hasattr(policy, "next_phase"):
        candidate = str(policy.next_phase(current_phase, event, evidence, policy.budgets()) or current_phase)
        if candidate:
            next_phase = candidate
    setattr(agent, "_goal_policy_phase", next_phase)
    return {
        "event": event,
        "previous_phase": current_phase,
        "current_phase": next_phase,
    }
