from __future__ import annotations

import re
from typing import Any, List, Optional

from .models import ActionDecision, ActionType, DOMElement, StepResult, TestGoal


def build_deterministic_goal_preplan(
    agent: Any,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
    steps: Optional[List[StepResult]] = None,
) -> Optional[ActionDecision]:
    def _element_goal_blob(element: DOMElement) -> str:
        return agent._normalize_text(
            " ".join(
                [
                    str(element.text or ""),
                    str(element.aria_label or ""),
                    str(getattr(element, "title", None) or ""),
                    str(getattr(element, "container_name", None) or ""),
                    str(getattr(element, "context_text", None) or ""),
                    str(getattr(element, "placeholder", None) or ""),
                    str(getattr(element, "role_ref_name", None) or ""),
                ]
            )
        )

    goal_text = agent._normalize_text(
        " ".join(
            [
                str(goal.name or ""),
                str(goal.description or ""),
                " ".join(str(item or "") for item in (goal.success_criteria or [])),
            ]
        )
    )
    if not goal_text:
        return None

    query_tokens = agent._extract_goal_query_tokens(goal)
    if not query_tokens:
        return None

    search_hints = ("검색", "search", "query", "find")
    open_hints = ("열어", "open", "상세", "detail")
    forbid_search_action = bool(agent._goal_constraints.get("forbid_search_action"))
    current_view_only = bool(agent._goal_constraints.get("current_view_only"))
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip().lower()
    locate_target_search_consumed = bool(getattr(agent, "_locate_target_search_consumed", False))
    target_content_visible = False
    target_actionable_visible = False
    if current_phase not in {"locate_target", "precheck_destination_membership"}:
        locate_target_search_consumed = False
        agent._locate_target_search_consumed = False
    normalized_query_tokens = [agent._normalize_text(token) for token in query_tokens if agent._normalize_text(token)]
    if normalized_query_tokens:
        for el in dom_elements:
            if not bool(el.is_visible):
                continue
            blob = _element_goal_blob(el)
            if not blob:
                continue
            if any(token in blob for token in normalized_query_tokens):
                target_content_visible = True
                tag = agent._normalize_text(el.tag)
                role = agent._normalize_text(getattr(el, "role", None))
                if bool(getattr(el, "is_enabled", True)) and (
                    role in {"button", "link", "option", "menuitem", "tab"}
                    or tag in {"button", "a", "option"}
                    or (tag == "input" and agent._normalize_text(getattr(el, "type", None)) in {"button", "submit", "checkbox", "radio"})
                ):
                    target_actionable_visible = True
                    break

    if target_actionable_visible:
        agent._locate_target_search_consumed = False
        return None

    if target_content_visible:
        agent._locate_target_search_consumed = False
        return None

    should_use_search_preplan = (
        not (forbid_search_action or current_view_only)
        and not target_content_visible
        and not locate_target_search_consumed
        and (
            any(hint in goal_text for hint in search_hints)
            or current_phase in {"locate_target", "precheck_destination_membership"}
        )
    )

    if should_use_search_preplan:
        search_candidates: List[tuple[float, DOMElement]] = []
        for el in dom_elements:
            if not bool(el.is_visible and el.is_enabled):
                continue
            tag = agent._normalize_text(el.tag)
            etype = agent._normalize_text(el.type)
            if tag != "input" and tag != "textarea":
                continue
            score = 0.0
            if etype in {"search", "text"}:
                score += 3.0
            if any(
                token in agent._normalize_text(
                    " ".join(
                        [
                            str(el.placeholder or ""),
                            str(el.aria_label or ""),
                            str(el.text or ""),
                            str(agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""),
                        ]
                    )
                )
                for token in ("검색", "search", "query")
            ):
                score += 4.0
            if score > 0.0:
                search_candidates.append((score, el))
        if search_candidates:
            search_candidates.sort(key=lambda item: item[0], reverse=True)
            query_value = query_tokens[0]
            search_input = search_candidates[0][1]
            last_step = steps[-1] if steps else None
            repeated_fill = bool(
                last_step
                and bool(last_step.success)
                and last_step.action.action == ActionType.FILL
                and last_step.action.element_id == search_input.id
                and agent._normalize_text(str(last_step.action.value or "")) == agent._normalize_text(query_value)
            )
            if repeated_fill:
                submit_candidates: List[tuple[float, DOMElement]] = []
                for el in dom_elements:
                    if not bool(el.is_visible and el.is_enabled):
                        continue
                    tag = agent._normalize_text(el.tag)
                    etype = agent._normalize_text(el.type)
                    if tag not in {"button", "a", "input"}:
                        continue
                    if tag == "input" and etype not in {"submit", "button"}:
                        continue
                    blob = agent._normalize_text(
                        " ".join(
                            [
                                str(el.text or ""),
                                str(el.aria_label or ""),
                                str(getattr(el, "title", None) or ""),
                                str(agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""),
                            ]
                        )
                    )
                    score = 0.0
                    if any(token in blob for token in ("검색", "search", "찾기", "go", "submit")):
                        score += 5.0
                    if tag == "button":
                        score += 1.0
                    if score > 0.0:
                        submit_candidates.append((score, el))
                if submit_candidates:
                    submit_candidates.sort(key=lambda item: item[0], reverse=True)
                    submit_target = submit_candidates[0][1]
                    agent._locate_target_search_consumed = True
                    return ActionDecision(
                        action=ActionType.CLICK,
                        element_id=submit_target.id,
                        value=None,
                        reasoning=f"같은 검색어 `{query_value}` 입력이 이미 끝났으므로 검색 CTA를 바로 실행합니다.",
                        confidence=0.95,
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                agent._locate_target_search_consumed = True
                return ActionDecision(
                    action=ActionType.PRESS,
                    element_id=search_input.id,
                    value="Enter",
                    reasoning=f"같은 검색어 `{query_value}` 입력이 이미 끝났으므로 검색 입력에서 Enter로 직접 제출합니다.",
                    confidence=0.95,
                    is_goal_achieved=False,
                    goal_achievement_reason=None,
                )
            return ActionDecision(
                action=ActionType.FILL,
                element_id=search_input.id,
                value=query_value,
                reasoning=f"목표에 명시된 검색 토큰 `{query_value}`를 검색 입력에 우선 적용합니다.",
                confidence=0.92,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

    if any(hint in goal_text for hint in open_hints):
        candidates: List[tuple[float, DOMElement, str]] = []
        numeric_tokens = [token for token in query_tokens if token.isdigit()]
        for el in dom_elements:
            if not bool(el.is_visible and el.is_enabled):
                continue
            href = str(el.href or "")
            text = str(el.text or "")
            aria = str(el.aria_label or "")
            blob = agent._normalize_text(" ".join([href, text, aria]))
            matched = []
            score = 0.0
            for token in query_tokens:
                norm = agent._normalize_text(token)
                if not norm:
                    continue
                if norm in blob:
                    matched.append(token)
                    score += 3.0
            if not matched:
                continue
            if agent._normalize_text(el.tag) == "a":
                score += 2.0
            if href:
                score += 1.5
            if any(ch.isdigit() for ch in "".join(matched)) and re.search(r"/[a-z]+/\d+", href):
                score += 2.0
            for token in numeric_tokens:
                if re.search(rf"/{re.escape(token)}(?:[/?#]|$)", href):
                    score += 6.0
                elif token in href:
                    score += 2.0
            candidates.append((score, el, ", ".join(matched[:3])))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, element, label = candidates[0]
            return ActionDecision(
                action=ActionType.CLICK,
                element_id=element.id,
                value=None,
                reasoning=f"목표에 명시된 타깃 토큰({label})과 가장 잘 맞는 항목을 직접 엽니다.",
                confidence=0.9,
                is_goal_achieved=False,
                goal_achievement_reason=None,
            )

    return None
