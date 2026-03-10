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

    if any(hint in goal_text for hint in search_hints) and not (forbid_search_action or current_view_only):
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
                    return ActionDecision(
                        action=ActionType.CLICK,
                        element_id=submit_target.id,
                        value=None,
                        reasoning=f"같은 검색어 `{query_value}` 입력이 이미 끝났으므로 검색 CTA를 바로 실행합니다.",
                        confidence=0.94,
                        is_goal_achieved=False,
                        goal_achievement_reason=None,
                    )
                return ActionDecision(
                    action=ActionType.PRESS,
                    element_id=search_input.id,
                    value="Enter",
                    reasoning=f"같은 검색어 `{query_value}` 입력이 이미 끝났으므로 Enter로 검색을 제출합니다.",
                    confidence=0.93,
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
