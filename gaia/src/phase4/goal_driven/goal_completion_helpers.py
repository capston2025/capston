from __future__ import annotations

from typing import List, Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal


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

    evidence = agent._last_snapshot_evidence if isinstance(agent._last_snapshot_evidence, dict) else {}
    evidence_fragments: List[str] = []
    text_digest = str(evidence.get("text_digest") or "").strip()
    if text_digest:
        evidence_fragments.append(text_digest)
    live_texts = evidence.get("live_texts") if isinstance(evidence.get("live_texts"), list) else []
    evidence_fragments.extend(str(item or "").strip() for item in live_texts[:12] if str(item or "").strip())
    evidence_blob = agent._normalize_text(" ".join(evidence_fragments))

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
    for el in dom_elements:
        if not bool(getattr(el, "is_visible", True)):
            continue
        blob = _element_blob(el)
        if not blob:
            continue
        has_destination = any(dest and dest in blob for dest in norm_destinations)
        has_target = any(term and term in blob for term in norm_targets)
        if has_destination and has_target:
            region_match = True
            matched_terms.extend(
                term for term, norm in zip(target_terms, norm_targets) if norm and norm in blob
            )
            break

    if not region_match and evidence_blob:
        if any(dest and dest in evidence_blob for dest in norm_destinations) and any(
            term and term in evidence_blob for term in norm_targets
        ):
            region_match = True
            matched_terms.extend(
                term for term, norm in zip(target_terms, norm_targets) if norm and norm in evidence_blob
            )

    if not region_match:
        return None

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
    destination_reason = evaluate_destination_region_completion(agent, goal=goal, dom_elements=dom_elements)
    if destination_reason:
        return destination_reason
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
        return None

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
        target_reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom_elements)
        if target_reason:
            return target_reason
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
    return None
