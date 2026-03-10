from __future__ import annotations

from typing import Any, Dict, List

from .execute_goal_intervention import handle_login_intervention
from .goal_semantics import extract_goal_semantics
from .models import DOMElement, TestGoal
from .policy_registry import get_goal_policy, run_interrupt_policies


def initialize_goal_policy_runtime(agent: Any, goal: TestGoal) -> None:
    agent._goal_semantics = extract_goal_semantics(
        goal,
        agent._goal_constraints,
        normalize_fn=agent._normalize_text,
        filter_style=agent._is_filter_style_goal(goal),
        verification_style=agent._is_verification_style_goal(goal),
    )
    agent._goal_policy = get_goal_policy(
        agent._goal_semantics.goal_kind if agent._goal_semantics is not None else None
    )
    agent._goal_policy_phase = (
        agent._goal_policy.initial_phase(agent._goal_semantics)
        if agent._goal_policy is not None and agent._goal_semantics is not None
        else ""
    )
    agent._goal_policy_baseline_evidence = None


def resolve_goal_policy_interrupts(
    agent: Any,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
    login_gate_visible: bool,
    has_login_test_data: bool,
    login_intervention_asked: bool,
    modal_open_hint: bool,
) -> Dict[str, Any]:
    policy_evidence = agent._build_goal_policy_evidence_bundle(
        goal=goal,
        dom_elements=dom_elements,
        auth_prompt_visible=login_gate_visible,
        modal_open=modal_open_hint,
    )
    interrupt_result = (
        run_interrupt_policies(agent._goal_semantics, policy_evidence, ctx=agent)
        if agent._goal_semantics is not None and policy_evidence is not None
        else None
    )
    if interrupt_result is not None and getattr(interrupt_result, "policy_name", "") == "auth_interrupt":
        setattr(agent, "_goal_policy_phase", "handle_auth_or_block")
    login_intervention = (
        handle_login_intervention(
            agent=agent,
            goal=goal,
            login_gate_visible=login_gate_visible,
            has_login_test_data=has_login_test_data,
            login_intervention_asked=login_intervention_asked,
        )
        if interrupt_result is not None and getattr(interrupt_result, "policy_name", "") == "auth_interrupt"
        else {
            "has_login_test_data": has_login_test_data,
            "login_intervention_asked": login_intervention_asked,
            "aborted": False,
        }
    )
    return {
        "policy_evidence": policy_evidence,
        "interrupt_result": interrupt_result,
        "login_intervention": login_intervention,
    }
