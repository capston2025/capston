from __future__ import annotations

from typing import List, Optional

from .goal_completion_helpers import (
    evaluate_destination_region_completion,
    evaluate_explicit_reasoning_proof_completion,
    evaluate_goal_target_completion,
    evaluate_readonly_visibility_completion,
    is_readonly_visibility_goal,
)
from .models import ActionDecision, ActionType, DOMElement, TestGoal


def goal_text_blob(agent_cls, goal: TestGoal) -> str:
    fields = [goal.name, goal.description]
    fields.extend(str(x) for x in (goal.success_criteria or []))
    return " ".join(agent_cls._normalize_text(x) for x in fields if x)


def goal_mentions_signup(agent_cls, goal: TestGoal) -> bool:
    blob = goal_text_blob(agent_cls, goal)
    signup_keywords = (
        "회원가입",
        "가입",
        "sign up",
        "signup",
        "register",
        "registration",
        "계정 생성",
    )
    return any(keyword in blob for keyword in signup_keywords)


def dom_contains_any_hint(agent_cls, dom_elements: List[DOMElement], keywords: tuple[str, ...]) -> bool:
    for el in dom_elements:
        fields = [
            el.text,
            el.placeholder,
            el.aria_label,
            getattr(el, "title", None),
        ]
        for field in fields:
            normalized = agent_cls._normalize_text(field)
            if not normalized:
                continue
            if any(keyword in normalized for keyword in keywords):
                return True
    return False


def has_signup_completion_evidence(agent_cls, dom_elements: List[DOMElement]) -> bool:
    completion_hints = (
        "회원가입 완료",
        "가입 완료",
        "가입되었습니다",
        "가입이 완료",
        "환영합니다",
        "welcome",
        "로그아웃",
        "마이페이지",
        "프로필",
    )
    return dom_contains_any_hint(agent_cls, dom_elements, completion_hints)


def has_recent_transition_completion_proof(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    state_change: dict,
    achieved_signals: List[str],
) -> Optional[str]:
    if not isinstance(state_change, dict) or not state_change:
        return None
    mutation_direction = str(agent._goal_constraints.get("mutation_direction") or "").strip().lower()
    require_state_change = bool(agent._goal_constraints.get("require_state_change"))
    if not (
        mutation_direction in {"increase", "decrease", "clear"}
        or require_state_change
        or bool(achieved_signals)
    ):
        return None

    strong_transition_keys = (
        "auth_state_changed",
        "url_changed",
        "dom_changed",
        "modal_state_changed",
        "dialog_count_changed",
        "text_digest_changed",
        "status_text_changed",
        "interactive_count_changed",
        "list_count_changed",
        "target_value_changed",
        "target_value_matches",
    )
    triggered = [key for key in strong_transition_keys if bool(state_change.get(key))]
    if not triggered:
        return None

    rationale = str(decision.goal_achievement_reason or decision.reasoning or "").strip()
    if not rationale:
        return None

    if achieved_signals:
        return (
            "최근 상태 전환과 contract signal이 확인되어 현재 DOM의 최종 형태와 무관하게 "
            "목표 완료로 판정했습니다."
        )
    return "최근 상태 전환이 확인되어 현재 DOM의 최종 형태와 무관하게 목표 완료로 판정했습니다."


def validate_goal_achievement_claim(
    agent,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> tuple[bool, Optional[str]]:
    if not decision.is_goal_achieved:
        return True, None

    expected_signals = [
        str(item or "").strip().lower()
        for item in list(getattr(goal, "expected_signals", []) or [])
        if str(item or "").strip()
    ]
    achieved: list[str] = []
    if expected_signals:
        from .goal_verification_helpers import derive_achieved_signals

        last_state_change = (
            dict(getattr(getattr(agent, "_last_exec_result", None), "state_change", {}) or {})
            if getattr(agent, "_last_exec_result", None) is not None
            else {}
        )
        achieved = derive_achieved_signals(
            agent,
            goal=goal,
            state_change=last_state_change,
            dom_elements=dom_elements,
        )
        missing = [signal for signal in expected_signals if signal not in achieved]
    else:
        missing = []

    wait_contract_override = False
    if decision.action == ActionType.WAIT:
        wait_proof = evaluate_goal_target_completion(
            agent,
            goal=goal,
            dom_elements=dom_elements,
        )
        if not wait_proof:
            wait_proof = evaluate_destination_region_completion(
                agent,
                goal=goal,
                dom_elements=dom_elements,
            )
        if not wait_proof:
            wait_proof = evaluate_readonly_visibility_completion(
                agent,
                goal=goal,
                decision=decision,
                dom_elements=dom_elements,
            )
        if not wait_proof:
            wait_proof = evaluate_explicit_reasoning_proof_completion(
                agent,
                goal=goal,
                decision=decision,
                dom_elements=dom_elements,
            )
        if not wait_proof:
            wait_proof = has_recent_transition_completion_proof(
                agent,
                goal=goal,
                decision=decision,
                state_change=last_state_change if expected_signals else (
                    dict(getattr(getattr(agent, "_last_exec_result", None), "state_change", {}) or {})
                    if getattr(agent, "_last_exec_result", None) is not None
                    else {}
                ),
                achieved_signals=achieved,
            )
            wait_contract_override = bool(wait_proof)
        if not wait_proof:
            if expected_signals:
                if missing:
                    return (
                        False,
                        "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다.",
                    )
                wait_proof = "expected_signals"
            else:
                return (
                    False,
                    "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다.",
                )

    if goal_mentions_signup(agent.__class__, goal):
        if not has_signup_completion_evidence(agent.__class__, dom_elements):
            return (
                False,
                "회원가입 목표는 화면 진입만으로 성공으로 보지 않습니다. "
                "회원가입 제출 및 완료 신호가 필요합니다.",
            )

    if missing and not (
        decision.action == ActionType.WAIT
        and (
            is_readonly_visibility_goal(agent, goal)
            or wait_contract_override
        )
    ):
        return (
            False,
            "goal contract signal 미충족: " + ", ".join(missing),
        )

    constraint_reason = agent._constraint_failure_reason()
    if constraint_reason:
        return False, constraint_reason

    return True, None
