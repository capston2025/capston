from __future__ import annotations

import re
from typing import List, Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal
from .goal_verification_helpers import extract_goal_query_tokens, is_filter_style_goal


_READONLY_VISIBILITY_STOP_TOKENS = {
    "현재",
    "메인",
    "화면",
    "페이지",
    "버튼",
    "유도",
    "cta",
    "이미",
    "추가",
    "조작",
    "없이",
    "확인",
    "종료",
    "보이는지",
    "보임",
    "표시",
    "노출",
    "존재",
    "있는지",
    "already",
    "visible",
    "present",
    "current",
    "screen",
    "page",
    "button",
}


def _is_actionable_element(el: DOMElement) -> bool:
    role = str(getattr(el, "role", "") or "").strip().lower()
    tag = str(getattr(el, "tag", "") or "").strip().lower()
    return bool(getattr(el, "is_enabled", True)) and (
        role in {"button", "link", "tab"} or tag in {"button", "a"}
    )


def _element_visibility_blob(agent, el: DOMElement) -> str:
    return agent._normalize_text(
        " ".join(
            [
                str(getattr(el, "text", "") or ""),
                str(getattr(el, "aria_label", "") or ""),
                str(getattr(el, "title", None) or ""),
                str(getattr(el, "role_ref_name", None) or ""),
                str(getattr(el, "container_name", None) or ""),
                str(getattr(el, "context_text", None) or ""),
            ]
        )
    )


def _readonly_visibility_query_tokens(agent, goal: TestGoal) -> List[str]:
    tokens: List[str] = []
    tokens.extend(str(item or "").strip() for item in (agent._goal_quoted_terms(goal) or []) if str(item or "").strip())
    tokens.extend(str(item or "").strip() for item in (agent._goal_target_terms(goal) or []) if str(item or "").strip())
    tokens.extend(str(item or "").strip() for item in extract_goal_query_tokens(agent, goal) if str(item or "").strip())

    unique: List[str] = []
    seen = set()
    for token in tokens:
        normalized = agent._normalize_text(token)
        if not normalized or normalized in {agent._normalize_text(item) for item in _READONLY_VISIBILITY_STOP_TOKENS}:
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append(token)
    return unique


def is_readonly_visibility_goal(agent, goal: TestGoal) -> bool:
    expected_signals = {
        str(item or "").strip().lower()
        for item in list(getattr(goal, "expected_signals", []) or [])
        if str(item or "").strip()
    }
    if expected_signals & {"text_visible", "cta_visible"}:
        return True

    if agent._goal_constraints.get("collect_min") is not None:
        return False
    if agent._goal_constraints.get("apply_target") is not None:
        return False
    direction = str(agent._goal_constraints.get("mutation_direction") or "").strip().lower()
    if direction in {"increase", "decrease", "clear"}:
        return False

    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    visibility_tokens = ("보이는지", "표시", "노출", "존재", "visible", "shown", "present")
    passive_tokens = ("추가 조작 없이", "현재 화면", "현재 메인 화면", "already visible", "without interaction")
    return bool(
        any(token in goal_blob for token in visibility_tokens)
        and (
            any(token in goal_blob for token in passive_tokens)
            or bool(agent._goal_constraints.get("current_view_only"))
            or bool(agent._goal_constraints.get("require_no_navigation"))
        )
    )


def evaluate_readonly_visibility_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if decision.action != ActionType.WAIT:
        return None
    if not dom_elements:
        return None
    if not is_readonly_visibility_goal(agent, goal):
        return None

    expected_signals = {
        str(item or "").strip().lower()
        for item in list(getattr(goal, "expected_signals", []) or [])
        if str(item or "").strip()
    }
    requires_cta = "cta_visible" in expected_signals
    requires_text = "text_visible" in expected_signals or not requires_cta
    query_tokens = _readonly_visibility_query_tokens(agent, goal)
    if not query_tokens:
        return None

    matched_text: List[str] = []
    matched_cta: List[str] = []
    for el in dom_elements:
        if not bool(getattr(el, "is_visible", True)):
            continue
        blob = _element_visibility_blob(agent, el)
        if not blob:
            continue
        for token in query_tokens:
            norm = agent._normalize_text(token)
            if not norm or norm not in blob:
                continue
            matched_text.append(token)
            if _is_actionable_element(el):
                matched_cta.append(token)

    if requires_text and not matched_text:
        reasoning_blob = agent._normalize_text(str(getattr(decision, "reasoning", None) or ""))
        negative_tokens = (
            "보이지 않",
            "보이지않",
            "없습니다",
            "없음",
            "확인되지 않",
            "확인되지않",
            "not visible",
            "not shown",
            "not present",
            "missing",
        )
        matched_reasoning_tokens = [
            token
            for token in query_tokens
            if (norm := agent._normalize_text(token)) and norm in reasoning_blob
        ]
        if matched_reasoning_tokens and any(token in reasoning_blob for token in negative_tokens):
            evidence = ", ".join(dict.fromkeys(matched_reasoning_tokens[:3]))
            return (
                f"현재 화면 증거와 모델 판단상 목표 관련 CTA/텍스트({evidence})가 "
                "현재 surface에 보이지 않는 것이 확인되어 관찰 목표를 완료로 판정했습니다."
            )
        return None
    if requires_cta and not matched_cta:
        return None

    evidence = ", ".join(dict.fromkeys((matched_cta or matched_text)[:3]))
    if requires_cta:
        return f"현재 화면에서 목표 관련 CTA({evidence})가 직접 보여 추가 조작 없이 목표를 완료로 판정했습니다."
    return f"현재 화면에서 목표 관련 텍스트({evidence})가 직접 보여 추가 조작 없이 목표를 완료로 판정했습니다."


def evaluate_destination_region_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    target_terms = agent._goal_target_terms(goal)
    destination_terms = agent._goal_destination_terms(goal)
    if not target_terms or not destination_terms:
        return None
    norm_targets = [agent._normalize_text(term) for term in target_terms if str(term).strip()]
    norm_destinations = [agent._normalize_text(term) for term in destination_terms if str(term).strip()]
    if not norm_targets or not norm_destinations:
        return None

    def _element_blob(el: DOMElement) -> str:
        labels = getattr(el, "group_action_labels", None) or []
        if isinstance(labels, list):
            label_blob = " ".join(str(x or "") for x in labels if str(x or "").strip())
        else:
            label_blob = ""
        return agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", None) or ""),
                    str(getattr(el, "container_name", None) or ""),
                    str(getattr(el, "container_role", None) or ""),
                    str(getattr(el, "context_text", None) or ""),
                    label_blob,
                ]
            )
        )

    region_match = False
    matched_terms: List[str] = []
    page_destination_anchor = False
    target_container_refs: set[str] = set()
    target_context_blobs: List[str] = []
    remove_like_container_refs: set[str] = set()
    remove_like_context_blobs: List[str] = []
    for el in dom_elements:
        if not bool(getattr(el, "is_visible", True)):
            continue
        blob = _element_blob(el)
        if not blob:
            continue
        has_destination = any(dest and dest in blob for dest in norm_destinations)
        has_target = any(term and term in blob for term in norm_targets)
        if has_destination:
            page_destination_anchor = True
        container_ref = str(getattr(el, "container_ref_id", "") or "").strip()
        context_blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "container_name", None) or ""),
                    str(getattr(el, "container_role", None) or ""),
                    str(getattr(el, "context_text", None) or ""),
                ]
            )
        )
        actionable = bool(getattr(el, "is_enabled", True)) and (
            str(getattr(el, "role", "") or "").strip().lower() in {"button", "link", "tab"}
            or str(getattr(el, "tag", "") or "").strip().lower() in {"button", "a"}
        )
        remove_like = any(token in blob for token in ("삭제", "제거", "remove", "delete", "clear", "비우"))
        if has_destination and has_target:
            region_match = True
            matched_terms.extend(
                term for term, norm in zip(target_terms, norm_targets) if norm and norm in blob
            )
            break
        if has_target:
            matched_terms.extend(
                term for term, norm in zip(target_terms, norm_targets) if norm and norm in blob
            )
            if container_ref:
                target_container_refs.add(container_ref)
            if context_blob:
                target_context_blobs.append(context_blob)
        if actionable and remove_like:
            if container_ref:
                remove_like_container_refs.add(container_ref)
            if context_blob:
                remove_like_context_blobs.append(context_blob)

    if not region_match:
        shared_container = bool(target_container_refs and remove_like_container_refs and (target_container_refs & remove_like_container_refs))
        shared_context = any(
            target_ctx and remove_ctx and (target_ctx == remove_ctx or target_ctx in remove_ctx or remove_ctx in target_ctx)
            for target_ctx in target_context_blobs
            for remove_ctx in remove_like_context_blobs
        )
        if not (page_destination_anchor and matched_terms and (shared_container or shared_context)):
            return None
        region_match = True

    unique = ", ".join(dict.fromkeys(matched_terms[:3] or target_terms[:3]))
    destinations = ", ".join(dict.fromkeys(destination_terms[:2]))
    return f"목표 대상({unique})이 목적지 영역({destinations}) 안에서 확인되어 목표를 완료로 판정했습니다."


def evaluate_goal_target_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    policy_reason = agent._run_goal_policy_closer(goal=goal, dom_elements=dom_elements)
    if policy_reason:
        return policy_reason
    direction = str(agent._goal_constraints.get("mutation_direction") or "").strip().lower()
    if direction not in {"increase", "decrease", "clear"}:
        return None
    semantics = getattr(agent, "_goal_semantics", None)
    semantic_goal_kind = str(getattr(semantics, "goal_kind", "") or "").strip().lower()
    destination_terms = agent._goal_destination_terms(goal)
    if (
        semantic_goal_kind in {"add_to_list", "remove_from_list", "clear_list", "apply_selection"}
        or bool(destination_terms)
        or bool(getattr(semantics, "mutate_required", False))
    ):
        return None
    target_terms = agent._goal_target_terms(goal)
    if not target_terms:
        return None
    context_terms = [
        agent._normalize_text(str(x))
        for x in (agent._goal_constraints.get("context_terms") or [])
        if str(x).strip()
    ]
    try:
        collect_min = int(agent._goal_constraints.get("collect_min")) if agent._goal_constraints.get("collect_min") is not None else None
    except Exception:
        collect_min = None
    current_metric = agent._estimate_goal_metric_from_dom(dom_elements) if dom_elements else None
    if collect_min is not None and (current_metric is None or float(current_metric) < float(collect_min)):
        return None
    evidence = agent._last_snapshot_evidence if isinstance(agent._last_snapshot_evidence, dict) else {}
    evidence_fragments: List[str] = []
    text_digest = str(evidence.get("text_digest") or "").strip()
    if text_digest:
        evidence_fragments.append(text_digest)
    live_texts = evidence.get("live_texts") if isinstance(evidence.get("live_texts"), list) else []
    for item in live_texts[:8]:
        text = str(item or "").strip()
        if text:
            evidence_fragments.append(text)
    evidence_blob = agent._normalize_text(" ".join(evidence_fragments))
    matches: List[str] = []
    contextual_match = False
    positive_surface_match = False
    aggregate_page_match = False
    for term in target_terms:
        norm_term = agent._normalize_text(term)
        if not norm_term:
            continue
        for el in dom_elements:
            if not bool(el.is_visible):
                continue
            if agent._normalize_text(el.tag) in {"input", "textarea", "select"}:
                continue
            blob = agent._normalize_text(
                " ".join(
                    [
                        str(el.text or ""),
                        str(el.aria_label or ""),
                        str(getattr(el, "title", None) or ""),
                        str(getattr(el, "container_name", None) or ""),
                        str(getattr(el, "context_text", None) or ""),
                    ]
                )
            )
            if norm_term and norm_term in blob:
                matches.append(term)
                if context_terms and any(ctx and ctx in blob for ctx in context_terms):
                    contextual_match = True
                if direction == "increase" and any(
                    token in blob
                    for token in (
                        "추가", "담", "added", "saved", "selected",
                        "위시", "wishlist", "장바구니", "cart",
                        "총", "count", "item", "items", "학점", "credit", "credits",
                    )
                ):
                    positive_surface_match = True
                if direction == "decrease" and any(
                    token in blob
                    for token in ("삭제", "제거", "remove", "removed", "minus", "감소")
                ):
                    positive_surface_match = True
                if direction == "clear" and any(
                    token in blob for token in ("비어", "empty", "없음", "없어요", "0개", "0학점")
                ):
                    positive_surface_match = True
                break
        if norm_term and norm_term in evidence_blob:
            matches.append(term)
            if context_terms and any(ctx and ctx in evidence_blob for ctx in context_terms):
                contextual_match = True
            if direction == "increase" and any(
                token in evidence_blob
                for token in (
                    "추가", "담", "added", "saved", "selected",
                    "위시", "wishlist", "장바구니", "cart",
                    "총", "count", "item", "items", "학점", "credit", "credits",
                )
            ):
                positive_surface_match = True
            if direction == "decrease" and any(
                token in evidence_blob for token in ("삭제", "제거", "remove", "removed", "minus", "감소")
            ):
                positive_surface_match = True
            if direction == "clear" and any(
                token in evidence_blob for token in ("비어", "empty", "없음", "없어요", "0개", "0학점")
            ):
                positive_surface_match = True
    if not matches:
        return None
    page_blob = agent._normalize_text(
        " ".join(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", None) or ""),
                    str(getattr(el, "container_name", None) or ""),
                    str(getattr(el, "context_text", None) or ""),
                ]
            )
            for el in dom_elements
            if bool(getattr(el, "is_visible", True))
        )
    )
    if evidence_blob:
        page_blob = agent._normalize_text(f"{page_blob} {evidence_blob}")
    if context_terms and not contextual_match:
        if any(ctx and ctx in page_blob for ctx in context_terms):
            contextual_match = True
    if any(
        token in page_blob
        for token in (
            "총", "count", "item", "items", "selected", "selection", "학점", "credit", "credits",
            "위시", "wishlist", "장바구니", "cart",
        )
    ):
        aggregate_page_match = True
    if context_terms and not contextual_match and not positive_surface_match and not aggregate_page_match:
        return None
    if not contextual_match and context_terms:
        page_blob = agent._normalize_text(
            " ".join(
                " ".join(
                    [
                        str(getattr(el, "text", "") or ""),
                        str(getattr(el, "aria_label", "") or ""),
                        str(getattr(el, "title", None) or ""),
                        str(getattr(el, "container_name", None) or ""),
                        str(getattr(el, "context_text", None) or ""),
                    ]
                )
                for el in dom_elements
                if bool(getattr(el, "is_visible", True))
            )
        )
        if any(ctx and ctx in page_blob for ctx in context_terms):
            contextual_match = True
    unique = ", ".join(dict.fromkeys(matches[:3]))
    if collect_min is not None:
        metric_label = str(agent._goal_constraints.get("metric_label") or "count")
        return f"목표 대상({unique})이 현재 화면에 보이고 최소 기준 {collect_min}{metric_label}도 충족해 목표를 완료로 판정했습니다."
    return f"목표 대상({unique})이 현재 화면에 보여 목표를 완료로 판정했습니다."


def evaluate_reasoning_only_wait_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if decision.action != ActionType.WAIT:
        return None
    if dom_elements:
        target_reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom_elements)
        if target_reason:
            return target_reason
    return None


def evaluate_explicit_reasoning_proof_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if decision.action != ActionType.WAIT:
        return None
    reasoning_raw = str(getattr(decision, "reasoning", None) or "")
    reasoning_blob = agent._normalize_text(reasoning_raw)
    satisfaction_tokens = ("충족", "완료", "이미", "표시", "반영", "정상", "visible", "present", "already")
    if not any(token in reasoning_blob for token in satisfaction_tokens):
        return None

    targets = agent._goal_quoted_terms(goal) or agent._goal_target_terms(goal)
    normalized_targets = [agent._normalize_text(str(term)) for term in targets if str(term).strip()]
    matched_targets = [term for term, norm in zip(targets, normalized_targets) if norm and norm in reasoning_blob]
    if not matched_targets:
        goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
        change_goal_tokens = (
            "검색",
            "결과",
            "목록",
            "리스트",
            "filter",
            "필터",
            "pagination",
            "페이지",
            "page",
            "정렬",
            "sort",
        )
        semantic_filter_tokens = ("학점", "credit", "맞게", "의미", "semantic", "일치", "consisten")
        reasoning_change_tokens = (
            "변경",
            "변화",
            "바뀌",
            "유지",
            "반영",
            "different",
            "changed",
            "updated",
            "persist",
            "page",
            "result",
            "results",
            "목록",
            "검색 결과",
        )
        if not any(token in goal_blob for token in change_goal_tokens):
            return None
        if is_filter_style_goal(agent, goal) and any(token in goal_blob for token in semantic_filter_tokens):
            return None
        if not any(token in reasoning_blob for token in reasoning_change_tokens):
            return None
        if not dom_elements:
            return None
        visible_blob = agent._normalize_text(
            " ".join(
                " ".join(
                    [
                        str(getattr(el, "text", "") or ""),
                        str(getattr(el, "aria_label", "") or ""),
                        str(getattr(el, "title", None) or ""),
                        str(getattr(el, "container_name", None) or ""),
                        str(getattr(el, "context_text", None) or ""),
                    ]
                )
                for el in dom_elements
                if bool(getattr(el, "is_visible", True))
            )
        )
        reasoning_query_stop_tokens = {
            "검색",
            "결과",
            "목록",
            "리스트",
            "페이지",
            "page",
            "pages",
            "필터",
            "filter",
            "변경",
            "변화",
            "바뀌",
            "반영",
            "유지",
            "실제",
            "확인",
            "검증",
            "changed",
            "updated",
            "result",
            "results",
            "list",
            "visible",
            "present",
            "already",
            "current",
            "표시",
            "표시되고",
            "표시되",
            "판단",
            "판단합니다",
            "확인됩니다",
            "보임",
            "보이고",
            "shown",
            "displayed",
        }
        reasoning_query_tokens = []
        for token in re.findall(r"[0-9A-Za-z가-힣+/#_-]{2,}", reasoning_raw):
            normalized = agent._normalize_text(token)
            if not normalized or normalized in reasoning_query_stop_tokens:
                continue
            reasoning_query_tokens.append(normalized)
        goal_query_tokens = [agent._normalize_text(token) for token in extract_goal_query_tokens(agent, goal)[:8] if str(token).strip()]
        generic_result_tokens = {
            "검색결과",
            "검색",
            "결과",
            "목록",
            "리스트",
            "페이지",
            "필터",
            "정렬",
            "과목",
            "표시",
            "보임",
            "shown",
            "displayed",
            "results",
            "result",
            "list",
            "page",
            "pages",
        }
        page_evidence_tokens = (
            "검색 결과",
            "목록",
            "리스트",
            "이전",
            "다음",
            "페이지",
            "필터",
            "교양",
            "전공",
            "정렬",
        )
        matched_reasoning_tokens = [
            token
            for token in reasoning_query_tokens
            if token
            and token not in generic_result_tokens
            and token not in reasoning_query_stop_tokens
            and (token.isdigit() or len(token) >= 3)
            and token in visible_blob
        ]
        has_page_structure = any(token in visible_blob for token in page_evidence_tokens)
        if not matched_reasoning_tokens:
            return None
        if not has_page_structure and not any(token and token in visible_blob for token in goal_query_tokens):
            return None
        label_tokens = matched_reasoning_tokens[:3] or [token for token in goal_query_tokens[:3] if token in visible_blob]
        label = ", ".join(dict.fromkeys(str(token) for token in label_tokens if str(token).strip()))
        if label:
            return f"모델 판단과 현재 화면 증거상 {label} 관련 상태 변화가 실제로 반영된 것이 확인되어 목표를 완료로 판정했습니다."
        return "모델 판단과 현재 화면 증거상 결과 목록/페이지 상태 변화가 실제로 반영된 것이 확인되어 목표를 완료로 판정했습니다."

    destinations = agent._goal_destination_terms(goal)
    normalized_destinations = [agent._normalize_text(str(term)) for term in destinations if str(term).strip()]
    has_destination = any(term and term in reasoning_blob for term in normalized_destinations)

    direction = str(agent._goal_constraints.get("mutation_direction") or "").strip().lower()
    if not direction:
        goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
        if any(token in goal_blob for token in ("비우", "전체삭제", "전부삭제", "clear", "empty")):
            direction = "clear"
        elif any(token in goal_blob for token in ("삭제", "제거", "remove", "decrease", "줄")):
            direction = "decrease"
        else:
            direction = "increase"
    direction_tokens = {
        "increase": ("추가", "담", "added", "saved", "총", "학점", "count", "item"),
        "decrease": ("삭제", "제거", "remove", "removed", "감소"),
        "clear": ("비어", "empty", "없음", "없어요", "0개", "0학점"),
    }.get(direction, ())
    has_direction = any(token in reasoning_blob for token in direction_tokens)
    if not has_destination and not has_direction:
        return None
    if dom_elements:
        destination_reason = evaluate_destination_region_completion(agent, goal=goal, dom_elements=dom_elements)
        if destination_reason:
            return destination_reason
        visible_blob = agent._normalize_text(
            " ".join(
                " ".join(
                    [
                        str(getattr(el, "text", "") or ""),
                        str(getattr(el, "aria_label", "") or ""),
                        str(getattr(el, "title", None) or ""),
                        str(getattr(el, "container_name", None) or ""),
                        str(getattr(el, "context_text", None) or ""),
                    ]
                )
                for el in dom_elements
                if bool(getattr(el, "is_visible", True))
            )
        )
        if any(norm and norm in visible_blob for norm in normalized_targets):
            unique = ", ".join(dict.fromkeys(matched_targets[:3]))
            if has_destination:
                destinations_label = ", ".join(dict.fromkeys(destinations[:2]))
                return (
                    f"모델 판단과 현재 화면 증거상 목표 대상({unique})이 목적지 영역({destinations_label})에 반영된 것으로 확인되어 목표를 완료로 판정했습니다."
                )
            return f"모델 판단과 현재 화면 증거상 목표 대상({unique})이 반영된 것으로 확인되어 목표를 완료로 판정했습니다."
    return None


def evaluate_wait_goal_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    if decision.action != ActionType.WAIT:
        return None
    target_reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom_elements)
    if target_reason:
        return target_reason
    readonly_reason = evaluate_readonly_visibility_completion(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom_elements,
    )
    if readonly_reason:
        return readonly_reason
    explicit_reason = evaluate_explicit_reasoning_proof_completion(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom_elements,
    )
    if explicit_reason:
        return explicit_reason
    return None
