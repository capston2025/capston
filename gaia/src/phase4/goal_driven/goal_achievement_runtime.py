from __future__ import annotations

from typing import List, Optional

from .goal_completion_helpers import evaluate_goal_target_completion
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
    return any(k in blob for k in signup_keywords)


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
            if any(k in normalized for k in keywords):
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


def validate_goal_achievement_claim(
    agent,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> tuple[bool, Optional[str]]:
    if not decision.is_goal_achieved:
        return True, None

    if decision.action == ActionType.WAIT:
        wait_proof = evaluate_goal_target_completion(
            agent,
            goal=goal,
            dom_elements=dom_elements,
        )
        if not wait_proof:
            return (
                False,
                "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거가 필요합니다.",
            )

    if goal_mentions_signup(agent.__class__, goal):
        if not has_signup_completion_evidence(agent.__class__, dom_elements):
            return (
                False,
                "회원가입 목표는 화면 진입만으로 성공으로 보지 않습니다. "
                "회원가입 제출 및 완료 신호가 필요합니다.",
            )

    constraint_reason = agent._constraint_failure_reason()
    if constraint_reason:
        return False, constraint_reason

    return True, None
