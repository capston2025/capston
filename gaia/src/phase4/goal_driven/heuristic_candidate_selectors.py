from __future__ import annotations

from typing import Any, List, Optional, Set

from .models import DOMElement


def is_progress_transition_element(agent: Any, el: Optional[DOMElement]) -> bool:
    if el is None:
        return False
    fields = agent._fields_for_element(el)
    return any(
        agent._contains_progress_cta_hint(f)
        or agent._contains_execute_hint(f)
        or agent._contains_apply_hint(f)
        for f in fields
    )


def pick_collect_element(agent: Any, dom_elements: List[DOMElement]) -> Optional[tuple[int, str]]:
    candidates: List[tuple[float, int, int, str]] = []
    recent_clicks = agent._recent_click_element_ids[-14:]
    for el in dom_elements:
        fields = agent._fields_for_element(el)

        ref_id = agent._element_ref_ids.get(el.id)
        if not ref_id or agent._is_ref_temporarily_blocked(ref_id):
            continue

        role = agent._normalize_text(el.role)
        tag = agent._normalize_text(el.tag)
        if role not in {"button", "link", "menuitem", ""} and tag not in {"button", "a", "input"}:
            continue

        score = 4.5
        score += 2.0 * agent._goal_overlap_score(
            el.text,
            el.aria_label,
            getattr(el, "title", None),
            agent._element_full_selectors.get(el.id),
        )

        repeat_count = recent_clicks.count(el.id)
        if repeat_count > 0:
            score -= min(5.0, 1.6 * repeat_count)

        score += agent._selector_bias_for_fields(fields)
        score += 0.8 * agent._adaptive_intent_bias(agent._candidate_intent_key("click", fields))
        score = agent._clamp_score(score, low=-20.0, high=30.0)
        if score <= 0.5:
            continue

        label = str(el.text or el.aria_label or getattr(el, "title", None) or f"element:{el.id}")
        reason = f"목표 제약상 수집 단계 유지: {label[:60]}"
        candidates.append((score, repeat_count, el.id, reason))

    if not candidates:
        return None
    unseen_candidates = [item for item in candidates if item[1] == 0]
    ranked_candidates = unseen_candidates if unseen_candidates else candidates
    ranked_candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    _, repeat_count, element_id, reason = ranked_candidates[0]
    if repeat_count == 0 and unseen_candidates and len(unseen_candidates) != len(candidates):
        reason += " | 이미 눌렀던 CTA보다 새 수집 후보를 우선"
    return element_id, reason


def pick_collect_context_shift_element(
    agent: Any,
    dom_elements: List[DOMElement],
    used_element_ids: Set[int],
) -> Optional[tuple[int, str, str]]:
    candidates: List[tuple[float, int, str, str]] = []
    recent_clicks = agent._recent_click_element_ids[-12:]
    for el in dom_elements:
        if el.id in used_element_ids:
            continue
        ref_id = agent._element_ref_ids.get(el.id)
        if not ref_id or agent._is_ref_temporarily_blocked(ref_id):
            continue

        fields = agent._fields_for_element(el)
        selector = agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""
        role = agent._normalize_text(el.role)
        tag = agent._normalize_text(el.tag)
        is_navigation_candidate = role in {"tab", "link", "button", "menuitem"} or tag in {"a", "button"}
        if not is_navigation_candidate:
            continue

        normalized_selector = agent._normalize_text(selector)
        text = agent._normalize_text(el.text)
        aria = agent._normalize_text(el.aria_label)
        has_arrow = any(ch in text or ch in aria for ch in ("›", "»", "→", ">"))
        nav_like_selector = any(k in normalized_selector for k in ("page", "pager", "nav", "tab"))
        if not (has_arrow or nav_like_selector):
            continue

        score = 12.0
        if el.id in recent_clicks:
            score -= 2.4
        if has_arrow:
            score += 2.8
        if agent._is_numeric_page_label(el.text) or agent._is_numeric_page_label(el.aria_label) or agent._is_numeric_page_label(getattr(el, "title", None)):
            score -= 3.0
        score += agent._goal_overlap_score(el.text, el.aria_label, getattr(el, "title", None))

        intent_key = agent._candidate_intent_key("click", fields)
        score += agent._adaptive_intent_bias(intent_key)
        score = agent._clamp_score(score, low=-20.0, high=30.0)
        if score <= 1.0:
            continue

        label = str(el.text or el.aria_label or getattr(el, "title", None) or f"element:{el.id}")
        reason = f"수집 정체 복구: 다음/페이지 전환 우선 ({label[:60]})"
        candidates.append((score, el.id, reason, intent_key))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, element_id, reason, intent_key = candidates[0]
    return element_id, reason, intent_key


def pick_no_navigation_click_candidate(
    agent: Any,
    dom_elements: List[DOMElement],
    *,
    excluded_ids: Optional[Set[int]] = None,
) -> Optional[tuple[int, str]]:
    blocked = excluded_ids or set()
    candidates: List[tuple[float, int, str]] = []
    for el in dom_elements:
        if el.id in blocked:
            continue

        ref_id = agent._element_ref_ids.get(el.id)
        if not ref_id or agent._is_ref_temporarily_blocked(ref_id):
            continue

        if agent._is_navigational_href(el.href):
            continue

        fields = agent._fields_for_element(el)
        field_blob = " ".join(fields).lower()
        score = 2.5
        score += 2.0 * agent._goal_overlap_score(
            el.text,
            el.aria_label,
            getattr(el, "title", None),
        )
        if any(h in field_blob for h in ("detail", "상세", "보기", "view", "open", "expand", "펼치")):
            score += 2.5
        if any(h in field_blob for h in ("modal", "dialog", "overlay", "panel", "sheet", "drawer", "popup")):
            score += 2.0
        if any(h in field_blob for h in ("row", "card", "listitem")):
            score += 1.5
        score += agent._selector_bias_for_fields(fields)
        score += agent._adaptive_intent_bias(agent._candidate_intent_key("click", fields))
        score = agent._clamp_score(score, low=-20.0, high=30.0)
        if score <= 1.0:
            continue

        label = str(el.text or el.aria_label or getattr(el, "title", None) or f"element:{el.id}")
        candidates.append((score, el.id, f"페이지 고정 제약 준수: 비내비게이션 요소 우선 ({label[:60]})"))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, element_id, reason = candidates[0]
    return element_id, reason


def pick_context_target_click_candidate(
    agent: Any,
    dom_elements: List[DOMElement],
    excluded_ids: Optional[Set[int]] = None,
) -> Optional[tuple[int, str]]:
    blocked = excluded_ids or set()
    candidates: List[tuple[float, int, str]] = []
    for el in dom_elements:
        if el.id in blocked or not bool(el.is_visible and el.is_enabled):
            continue
        ref_id = agent._element_ref_ids.get(el.id)
        if not ref_id or agent._is_ref_temporarily_blocked(ref_id):
            continue
        tag = agent._normalize_text(el.tag)
        role = agent._normalize_text(el.role)
        if tag not in {"button", "a", "input"} and role not in {"button", "link", "menuitem"}:
            continue
        context_score = agent._context_score(el)
        if context_score <= 0.0:
            continue
        score = context_score
        if agent._contains_add_like_hint(el.text) or agent._contains_add_like_hint(el.aria_label):
            score += 2.5
        if agent._contains_wishlist_like_hint(el.text) or agent._contains_wishlist_like_hint(el.aria_label):
            score += 1.2
        label = str(el.text or el.aria_label or getattr(el, "container_name", None) or f"element:{el.id}")
        candidates.append((score, el.id, f"현재 화면 타깃 문맥과 가장 잘 맞는 액션을 선택합니다. ({label[:60]})"))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, element_id, reason = candidates[0]
    return element_id, reason


def pick_context_shift_element(
    agent: Any,
    dom_elements: List[DOMElement],
    used_element_ids: Set[int],
) -> Optional[tuple[int, str, str]]:
    agent._context_shift_round += 1
    phase = (agent._runtime_phase or "COLLECT").upper()
    exploration_slot = (agent._context_shift_round % 4) == 0
    collect_unmet = agent._is_collect_constraint_unmet()

    add_candidates_visible = False
    for probe_el in dom_elements:
        probe_fields = [
            str(probe_el.text or "").strip(),
            str(probe_el.aria_label or "").strip(),
            str(probe_el.placeholder or "").strip(),
            str(getattr(probe_el, "title", None) or "").strip(),
            str(agent._element_full_selectors.get(probe_el.id) or agent._element_selectors.get(probe_el.id) or ""),
        ]
        if any(agent._contains_add_like_hint(f) for f in probe_fields):
            add_candidates_visible = True
            break

    candidates: List[tuple[float, int, str, str]] = []
    for el in dom_elements:
        if el.id in used_element_ids:
            continue
        selector = agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""
        text = str(el.text or "").strip()
        aria_label = str(el.aria_label or "").strip()
        title = str(getattr(el, "title", None) or "").strip()
        href = str(el.href or "").strip()
        fields = [
            text,
            aria_label,
            el.placeholder,
            title,
            selector,
            href,
        ]

        has_context_shift = any(agent._contains_context_shift_hint(f) for f in fields)
        has_expand = any(agent._contains_expand_hint(f) for f in fields)
        has_next = any(agent._contains_next_pagination_hint(f) for f in fields)
        has_progress = any(agent._contains_progress_cta_hint(f) for f in fields)
        has_wishlist_like = any(agent._contains_wishlist_like_hint(f) for f in fields)
        has_add_like = any(agent._contains_add_like_hint(f) for f in fields)
        has_configure = any(agent._contains_configure_hint(f) for f in fields)
        has_execute = any(agent._contains_execute_hint(f) for f in fields)
        has_apply = any(agent._contains_apply_hint(f) for f in fields)

        score = 0.0
        if has_context_shift:
            score += 3.5
        if has_next:
            score += 4.5
        if has_progress:
            score += 5.0
        if has_expand:
            score += 0.8

        role = agent._normalize_text(el.role)
        tag = agent._normalize_text(el.tag)
        if role in {"tab", "link", "button", "menuitem"}:
            score += 1.8
        if tag in {"a", "button"}:
            score += 1.2

        normalized_selector = agent._normalize_text(selector)
        if any(k in normalized_selector for k in ("pagination", "pager", "page", "tab", "tabs", "nav")):
            score += 2.2
        if any(k in normalized_selector for k in ("next", "다음", "pager-next", "page-next", "nav-next")):
            score += 2.8
        if any(k in normalized_selector for k in ("prev", "previous", "back", "이전")):
            score -= 5.0
        if any(k in normalized_selector for k in ("active", "current", "selected")):
            score -= 2.0

        is_numeric_page = (
            agent._is_numeric_page_label(text)
            or agent._is_numeric_page_label(aria_label)
            or agent._is_numeric_page_label(title)
        )
        if is_numeric_page and not has_next:
            score -= 3.5

        if phase in {"AUTH", "COLLECT"}:
            if has_progress:
                score += 2.0
            if has_next:
                score += 1.5
            if has_expand and not has_wishlist_like:
                score -= 1.0
        elif phase == "COMPOSE":
            if has_configure:
                score += 2.5
            if has_context_shift:
                score += 1.8
            if has_progress:
                score += 3.0
            if has_add_like:
                score -= 1.5
        elif phase == "APPLY":
            if has_execute or has_progress:
                score += 4.0
            if has_next:
                score += 2.2
            if has_add_like:
                score -= 2.5
        elif phase == "VERIFY":
            if has_apply or has_progress:
                score += 4.5
            if has_add_like:
                score -= 3.5

        if collect_unmet:
            if has_next:
                score += 5.5
            if has_progress or has_execute or has_apply:
                score -= 6.0
            if has_add_like:
                score += 0.8
            if is_numeric_page and not has_next:
                score -= 5.0
            if any(k in normalized_selector for k in ("last", "first", "처음", "마지막")):
                score -= 2.5

        intent_key = agent._candidate_intent_key("click", fields)
        score += agent._adaptive_intent_bias(intent_key)
        score += agent._selector_bias_for_fields(fields)

        if intent_key and intent_key == agent._last_context_shift_intent:
            score -= 3.0

        if exploration_slot:
            score += 0.6
            if has_next or has_progress or has_context_shift:
                score += 1.1

        score = agent._clamp_score(score, low=-20.0, high=25.0)

        if score <= 1.0:
            continue

        label = (el.text or el.aria_label or getattr(el, "title", None) or selector or f"element:{el.id}")
        if has_next:
            reason_core = "페이지네이션 전환"
        elif has_progress:
            reason_core = "단계 전환 CTA"
        elif has_context_shift:
            reason_core = "컨텍스트 전환"
        elif has_expand and not has_wishlist_like:
            reason_core = "콘텐츠 확장"
        else:
            reason_core = "반복 탈출"
        reason = (
            f"{reason_core} 우선 시도: {str(label)[:60]} "
            f"(phase={phase}, score={score:.1f})"
        )
        candidates.append((score, el.id, reason, intent_key))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, element_id, reason, intent_key = candidates[0]
    return element_id, reason, intent_key
