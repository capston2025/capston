from __future__ import annotations

import re
from typing import List, Optional

from .goal_completion_helpers import (
    evaluate_goal_completion_judge,
    evaluate_wait_goal_completion,
    is_readonly_visibility_goal,
)
from .media_playback_helpers import collect_visible_play_controls, goal_requires_media_playback
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
        "new_page_detected",
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


_TRANSIENT_WAIT_KEYWORDS = (
    "생각 중",
    "로딩",
    "loading",
    "불러오는 중",
    "처리 중",
    "processing",
    "generating",
    "생성 중",
    "saving",
    "저장 중",
    "applying",
    "적용 중",
    "updating",
    "업데이트 중",
    "progress",
    "진행률",
    "please wait",
    "잠시만",
)
_TRANSIENT_WAIT_PERCENT = re.compile(r"\b\d{1,3}\s*%")


def dom_has_transient_wait_signals(agent_cls, dom_elements: List[DOMElement]) -> bool:
    for el in list(dom_elements or [])[:180]:
        blob = agent_cls._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", "") or ""),
                    str(getattr(el, "placeholder", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                    str(getattr(el, "container_name", "") or ""),
                    str(getattr(el, "class_name", "") or ""),
                ]
            )
        )
        if not blob:
            continue
        role = agent_cls._normalize_text(getattr(el, "role", ""))
        tag = agent_cls._normalize_text(getattr(el, "tag", ""))
        if any(token in blob for token in _TRANSIENT_WAIT_KEYWORDS):
            return True
        if _TRANSIENT_WAIT_PERCENT.search(blob) and any(
            token in blob for token in ("진행", "progress", "로딩", "생성", "처리", "업데이트", "apply", "save")
        ):
            return True
        if role in {"progressbar", "status", "alert", "timer"} or tag in {"progress"}:
            if any(token in blob for token in ("생각", "loading", "로딩", "progress", "진행", "generating", "processing")):
                return True
    return False


def wait_completion_ready(agent, dom_elements: Optional[List[DOMElement]] = None) -> bool:
    wait_count = int(getattr(agent, "_consecutive_wait_count", 0) or 0)
    if wait_count >= 2:
        return True
    if wait_count <= 0:
        return False
    return not dom_has_transient_wait_signals(agent.__class__, list(dom_elements or []))


def validate_goal_achievement_claim(
    agent,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> tuple[bool, Optional[str]]:
    setattr(agent, "_last_goal_completion_source", "")
    if not decision.is_goal_achieved:
        return True, None
    if decision.action == ActionType.WAIT:
        ready = wait_completion_ready(agent, dom_elements)
        if not ready:
            return False, "첫 WAIT는 완료 판정을 내리지 않고 한 번 더 상태 변화를 관찰합니다."
        if goal_requires_media_playback(agent.__class__, goal):
            visible_play_controls = collect_visible_play_controls(agent.__class__, dom_elements or [], limit=3)
            if visible_play_controls:
                return (
                    False,
                    "재생 목표는 현재 player surface에 play/start control이 남아 있으면 완료로 보지 않습니다. 먼저 재생 버튼을 누르세요.",
                )

    expected_signals = [
        str(item or "").strip().lower()
        for item in list(getattr(goal, "expected_signals", []) or [])
        if str(item or "").strip()
    ]
    last_state_change = (
        dict(getattr(getattr(agent, "_last_exec_result", None), "state_change", {}) or {})
        if getattr(agent, "_last_exec_result", None) is not None
        else {}
    )
    achieved: list[str] = []
    if expected_signals:
        from .goal_verification_helpers import derive_achieved_signals

        achieved = derive_achieved_signals(
            agent,
            goal=goal,
            state_change=last_state_change,
            dom_elements=dom_elements,
        )
        missing = [signal for signal in expected_signals if signal not in achieved]
    else:
        missing = []

    wait_fallback_reason: Optional[str] = None
    wait_contract_override = False
    if decision.action == ActionType.WAIT:
        wait_fallback_reason = evaluate_wait_goal_completion(
            agent,
            goal=goal,
            decision=decision,
            dom_elements=dom_elements,
        )
        if not wait_fallback_reason:
            wait_fallback_reason = has_recent_transition_completion_proof(
                agent,
                goal=goal,
                decision=decision,
                state_change=last_state_change,
                achieved_signals=achieved,
            )
        wait_contract_override = bool(wait_fallback_reason) or is_readonly_visibility_goal(agent, goal)

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

    judge_reason = evaluate_goal_completion_judge(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom_elements,
    )
    if judge_reason:
        setattr(agent, "_last_goal_completion_source", "judge")
        decision.goal_achievement_reason = judge_reason
        return True, None

    if decision.action != ActionType.WAIT:
        setattr(agent, "_last_goal_completion_source", "direct")
        return True, None

    if missing and not wait_contract_override:
        return (
            False,
            "goal contract signal 미충족: " + ", ".join(missing),
        )

    if expected_signals and not missing:
        setattr(agent, "_last_goal_completion_source", "expected_signals")
        if not str(decision.goal_achievement_reason or "").strip():
            decision.goal_achievement_reason = "goal contract signal 충족"
        return True, None

    if wait_fallback_reason:
        setattr(agent, "_last_goal_completion_source", "wait_fallback")
        decision.goal_achievement_reason = wait_fallback_reason
        return True, None
    return (
        False,
        "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다.",
    )
