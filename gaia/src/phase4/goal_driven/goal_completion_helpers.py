from __future__ import annotations

import json
import os
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


def _has_empty_state_signal(agent, text: str) -> bool:
    norm = agent._normalize_text(text)
    if not norm:
        return False
    if any(token in norm for token in ("비어", "empty", "없음", "없어요", "none", "nothing", "no items", "no results")):
        return True
    return bool(re.search(r"(?:^|\s)0\s*(?:개|건|items?|results?|selected)?(?:\s|$)", norm))


def _has_aggregate_state_signal(agent, text: str) -> bool:
    norm = agent._normalize_text(text)
    if not norm:
        return False
    if any(token in norm for token in ("총", "total", "count", "item", "items", "selected", "selection", "합계", "summary")):
        return True
    return bool(re.search(r"(?:총|total)\s*\d", norm))


def _has_direction_surface_signal(agent, text: str, *, direction: str) -> bool:
    norm = agent._normalize_text(text)
    if not norm:
        return False
    if direction == "increase":
        return any(token in norm for token in ("추가", "담", "넣", "added", "saved", "selected", "created"))
    if direction == "decrease":
        return any(token in norm for token in ("삭제", "제거", "remove", "removed", "minus", "감소", "decreased"))
    if direction == "clear":
        return _has_empty_state_signal(agent, norm) or any(
            token in norm for token in ("clear", "cleared", "삭제", "제거", "비우")
        )
    return False


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
    if is_readonly_visibility_goal(agent, goal):
        return None
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
                if direction == "increase" and (
                    _has_direction_surface_signal(agent, blob, direction="increase")
                    or _has_aggregate_state_signal(agent, blob)
                ):
                    positive_surface_match = True
                if direction == "decrease" and _has_direction_surface_signal(agent, blob, direction="decrease"):
                    positive_surface_match = True
                if direction == "clear" and _has_empty_state_signal(agent, blob):
                    positive_surface_match = True
                break
        if norm_term and norm_term in evidence_blob:
            matches.append(term)
            if context_terms and any(ctx and ctx in evidence_blob for ctx in context_terms):
                contextual_match = True
            if direction == "increase" and (
                _has_direction_surface_signal(agent, evidence_blob, direction="increase")
                or _has_aggregate_state_signal(agent, evidence_blob)
            ):
                positive_surface_match = True
            if direction == "decrease" and _has_direction_surface_signal(agent, evidence_blob, direction="decrease"):
                positive_surface_match = True
            if direction == "clear" and _has_empty_state_signal(agent, evidence_blob):
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
    if _has_aggregate_state_signal(agent, page_blob):
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
    if dom_elements:
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
    else:
        visible_blob = ""

    result_goal_tokens = ("응답", "결과", "결과물", "response", "result", "answer", "reply", "output", "출력")
    loading_reason_tokens = (
        "생각 중",
        "로딩",
        "loading",
        "spinner",
        "generating",
        "작성 중",
        "응답을 생성",
        "기다려",
        "대기",
    )
    quoted_reasoning_matches = [
        (phrase, agent._normalize_text(phrase))
        for phrase in re.findall(r"[\"']([^\"']{4,})[\"']", reasoning_raw)
        if str(phrase or "").strip()
    ]
    distinct_result_quotes = [
        phrase
        for phrase, normalized in quoted_reasoning_matches
        if normalized
        and normalized not in normalized_targets
        and normalized in visible_blob
        and not any(token in normalized for token in loading_reason_tokens)
    ]
    if (
        distinct_result_quotes
        and visible_blob
        and any(token in agent._normalize_text(agent._goal_text_blob(goal)) or token in reasoning_blob for token in result_goal_tokens)
        and not any(token in reasoning_blob for token in loading_reason_tokens)
        and any(norm and norm in visible_blob for norm in normalized_targets)
    ):
        quoted_result = _truncate_completion_text(distinct_result_quotes[0], 80)
        return f"입력 내용과 구분되는 결과 본문(\"{quoted_result}\")이 현재 화면에 직접 보여 목표를 완료로 판정했습니다."

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
        semantic_filter_tokens = ("맞게", "의미", "semantic", "일치", "consisten")
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
        "increase": ("추가", "담", "넣", "added", "saved", "selected", "created"),
        "decrease": ("삭제", "제거", "remove", "removed", "감소", "decreased"),
        "clear": ("clear", "cleared", "비어", "empty", "없음", "없어요"),
    }.get(direction, ())
    has_direction = any(token in reasoning_blob for token in direction_tokens)
    if not has_direction:
        if direction == "clear":
            has_direction = _has_empty_state_signal(agent, reasoning_blob)
        elif direction == "increase":
            has_direction = _has_aggregate_state_signal(agent, reasoning_blob)
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


def _truncate_completion_text(value: object, limit: int = 180) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _parse_wait_judge_response(raw: object) -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("```json"):
        text = text[len("```json") :].strip()
    elif text.startswith("```"):
        text = text[len("```") :].strip()
    if text.endswith("```"):
        text = text[:-3].strip()

    candidates = [text]
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _goal_completion_judge_enabled() -> bool:
    raw = str(os.getenv("GAIA_ENABLE_GENERIC_WAIT_JUDGE", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def evaluate_goal_completion_judge(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if not _goal_completion_judge_enabled():
        return None
    if not hasattr(agent, "_call_llm_text_only"):
        return None
    if not bool(getattr(decision, "is_goal_achieved", False)) and not str(
        getattr(decision, "goal_achievement_reason", "") or ""
    ).strip():
        return None
    visible_elements = [el for el in (dom_elements or []) if bool(getattr(el, "is_visible", True))]
    if not visible_elements:
        return None

    dom_lines: List[str] = []
    for el in visible_elements:
        parts: List[str] = []
        role = str(getattr(el, "role", "") or "").strip()
        tag = str(getattr(el, "tag", "") or "").strip()
        text = _truncate_completion_text(getattr(el, "text", ""), 180)
        aria = _truncate_completion_text(getattr(el, "aria_label", ""), 140)
        context = _truncate_completion_text(getattr(el, "context_text", ""), 160)
        if role:
            parts.append(f"role={role}")
        if tag:
            parts.append(f"tag={tag}")
        if text:
            parts.append(f'text="{text}"')
        if aria and aria != text:
            parts.append(f'aria="{aria}"')
        if context:
            parts.append(f'context="{context}"')
        if parts:
            dom_lines.append("- " + " | ".join(parts))
    formatted_dom = ""
    formatter = getattr(agent, "_format_dom_for_llm", None)
    if callable(formatter):
        try:
            formatted_dom = str(formatter(list(dom_elements or [])) or "").strip()
        except Exception:
            formatted_dom = ""
    if len(formatted_dom) > 12000:
        formatted_dom = formatted_dom[:12000].rstrip() + "\n... (truncated)"

    if not dom_lines and not formatted_dom:
        return None

    recent_action_history = [
        str(item or "").strip()
        for item in list(getattr(agent, "_action_history", []) or [])[-6:]
        if str(item or "").strip()
    ]
    recent_action_feedback = [
        str(item or "").strip()
        for item in list(getattr(agent, "_action_feedback", []) or [])[-6:]
        if str(item or "").strip()
    ]
    expected_signals = [
        str(item or "").strip()
        for item in list(getattr(goal, "expected_signals", []) or [])
        if str(item or "").strip()
    ]
    quoted_terms = [
        str(item or "").strip()
        for item in (agent._goal_quoted_terms(goal) or [])
        if str(item or "").strip()
    ]
    target_terms = [
        str(item or "").strip()
        for item in (agent._goal_target_terms(goal) or [])
        if str(item or "").strip()
    ]

    recent_state_change = (
        dict(getattr(getattr(agent, "_last_exec_result", None), "state_change", {}) or {})
        if getattr(agent, "_last_exec_result", None) is not None
        else {}
    )
    state_change_summary = {
        key: value
        for key, value in recent_state_change.items()
        if key
        in {
            "dom_changed",
            "text_digest_changed",
            "status_text_changed",
            "interactive_count_changed",
            "list_count_changed",
            "url_changed",
            "target_value_changed",
            "target_value_matches",
        }
        and bool(value)
    }
    fill_memory = []
    for item in list(getattr(agent, "_persistent_state_memory", []) or [])[-4:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "").strip().lower() != "fill":
            continue
        value = str(item.get("expected_value") or "").strip()
        if not value:
            continue
        fill_memory.append(
            {
                "value": value,
                "context_text": str(item.get("context_text") or "").strip(),
                "container_name": str(item.get("container_name") or "").strip(),
            }
        )

    prompt = f"""너는 웹 자동화의 최종 성공 판정 judge다.
actor의 완료 주장을 그대로 믿지 말고, 현재 DOM과 최근 행동 증거를 보고 독립적으로 판정하라.

목표:
{str(getattr(goal, "name", "") or "").strip()}

설명:
{str(getattr(goal, "description", "") or "").strip()}

성공 조건:
{json.dumps(list(getattr(goal, "success_criteria", []) or []), ensure_ascii=False)}

quoted_terms:
{json.dumps(quoted_terms, ensure_ascii=False)}

target_terms:
{json.dumps(target_terms, ensure_ascii=False)}

expected_signals:
{json.dumps(expected_signals, ensure_ascii=False)}

모델의 기존 완료 주장:
{str(getattr(decision, "goal_achievement_reason", "") or getattr(decision, "reasoning", "") or "").strip()}

현재 actor가 고른 action:
{str(getattr(decision, "action", "") or "").strip()}

최근 액션 기록:
{json.dumps(recent_action_history, ensure_ascii=False)}

최근 액션 피드백:
{json.dumps(recent_action_feedback, ensure_ascii=False)}

최근 상태 변화:
{json.dumps(state_change_summary, ensure_ascii=False)}

최근 fill 메모리:
{json.dumps(fill_memory, ensure_ascii=False)}

현재 DOM:
{formatted_dom or "(없음)"}

현재 visible DOM 요약:
{chr(10).join(dom_lines)}

판정 규칙:
- 현재 화면에 직접 보이는 증거만 믿어라.
- `expected_signals`는 참고 정보일 뿐이고, 부재만으로 자동 실패 처리하지 마라.
- 추측하지 마라. 애매하면 success=false.
- 응답형/결과형 UI에서는 사용자가 입력한 내용이 전송되었고, 그 뒤의 결과 본문/응답이 별도 surface에 보이면 success=true다.
- 로딩/생각중/스피너만 보이면 success=false다.
- 회원가입/로그인 goal은 단순 폼 노출이나 화면 진입만으로 success가 아니다.

JSON만 출력:
{{
  "success": true | false,
  "blocked": true | false,
  "reason": "한 문장 근거",
  "confidence": 0.0
}}"""

    try:
        raw = agent._call_llm_text_only(prompt)
    except Exception:
        setattr(
            agent,
            "_last_goal_completion_judge",
            {"prompt": prompt, "raw_response": "", "parsed": {}, "error": "llm_call_failed"},
        )
        return None

    parsed = _parse_wait_judge_response(raw)
    setattr(
        agent,
        "_last_goal_completion_judge",
        {"prompt": prompt, "raw_response": str(raw or ""), "parsed": parsed if isinstance(parsed, dict) else {}},
    )
    if not isinstance(parsed, dict):
        return None
    success_raw = parsed.get("success")
    blocked_raw = parsed.get("blocked")
    success = success_raw is True or str(success_raw).strip().lower() == "true"
    blocked = blocked_raw is True or str(blocked_raw).strip().lower() == "true"
    if not success or blocked:
        return None
    reason = str(parsed.get("reason") or "").strip()
    if reason:
        return reason
    return "현재 화면의 직접적인 결과 증거를 바탕으로 목표가 완료된 것으로 판정했습니다."


def evaluate_generic_wait_judge_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if decision.action != ActionType.WAIT:
        return None
    return evaluate_goal_completion_judge(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom_elements,
    )


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
    destination_reason = evaluate_destination_region_completion(
        agent,
        goal=goal,
        dom_elements=dom_elements,
    )
    if destination_reason:
        return destination_reason
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
