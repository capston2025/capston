from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .goal_verification_helpers import extract_goal_query_tokens
from .media_playback_helpers import (
    collect_visible_play_controls,
    describe_play_control,
    dom_has_media_player_surface,
    goal_requires_media_playback,
)
from .models import ActionDecision, ActionType, DOMElement, TestGoal
from .run_history_runtime import (
    build_run_history_replay_packet_context as build_run_history_replay_packet_context_impl,
    record_run_history_transcript as record_run_history_transcript_impl,
)


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
_TRANSIENT_REASONING_WAIT_KEYWORDS = (
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
_TRANSIENT_REASONING_WAIT_PERCENT = re.compile(r"\b\d{1,3}\s*%")
_SERVICE_UNAVAILABLE_KEYWORDS = (
    "서비스 지연 안내",
    "서비스 이용에 불편",
    "정상적으로 제공할 수 없습니다",
    "요청하신 페이지를 정상적으로 제공할 수 없습니다",
    "현재 사용자가 많아",
    "잠시 후 다시 접속",
    "잠시 후 다시 시도",
    "접속이 원활하지 않습니다",
    "일시적으로 사용할 수 없습니다",
    "access denied",
    "service unavailable",
    "temporarily unavailable",
    "too many requests",
)


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


def _dom_has_reasoning_wait_transient_signals(agent, dom_elements: List[DOMElement]) -> bool:
    for el in list(dom_elements or [])[:180]:
        blob = agent._normalize_text(
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
        role = agent._normalize_text(getattr(el, "role", ""))
        tag = agent._normalize_text(getattr(el, "tag", ""))
        if any(token in blob for token in _TRANSIENT_REASONING_WAIT_KEYWORDS):
            return True
        if _TRANSIENT_REASONING_WAIT_PERCENT.search(blob) and any(
            token in blob for token in ("진행", "progress", "로딩", "생성", "처리", "업데이트", "apply", "save")
        ):
            return True
        if role in {"progressbar", "status", "alert", "timer"} or tag == "progress":
            if any(token in blob for token in ("생각", "loading", "로딩", "progress", "진행", "generating", "processing")):
                return True
    return False


def _text_has_service_unavailable_signal(agent, text: object) -> bool:
    blob = agent._normalize_text(text)
    return bool(blob and any(token in blob for token in _SERVICE_UNAVAILABLE_KEYWORDS))


def _dom_has_service_unavailable_signal(agent, dom_elements: List[DOMElement]) -> bool:
    for el in list(dom_elements or [])[:180]:
        blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", "") or ""),
                    str(getattr(el, "placeholder", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                    str(getattr(el, "container_name", "") or ""),
                ]
            )
        )
        if _text_has_service_unavailable_signal(agent, blob):
            return True
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


def requires_explicit_submission_completion(agent, goal: TestGoal) -> bool:
    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    if not goal_blob:
        return False
    submission_tokens = (
        "발송",
        "전송",
        "보내기",
        "보낸메일",
        "보낸 메일",
        "메일쓰기",
        "메일 쓰기",
        "submit",
        "send",
        "sent",
    )
    completion_tokens = (
        "완료",
        "성공",
        "확인",
        "보낸메일함",
        "보낸 메일함",
        "sent",
    )
    return any(token in goal_blob for token in submission_tokens) and any(
        token in goal_blob for token in completion_tokens
    )


def requires_interactive_state_change_completion(agent, goal: TestGoal) -> bool:
    """Visibility of the control itself is not enough for filter/sort/select goals."""

    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    if not goal_blob:
        return False
    action_tokens = (
        "필터",
        "정렬",
        "선택",
        "적용",
        "전환",
        "변경",
        "바뀌",
        "반영",
        "옵션",
        "filter",
        "sort",
        "select",
        "apply",
        "change",
        "switch",
    )
    result_tokens = (
        "결과",
        "목록",
        "리스트",
        "상위",
        "카드",
        "표",
        "순서",
        "result",
        "list",
        "card",
        "table",
        "order",
    )
    return any(token in goal_blob for token in action_tokens) and any(
        token in goal_blob for token in result_tokens
    )


def _goal_requests_payment_presubmit(agent, goal: TestGoal) -> bool:
    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    if not goal_blob:
        return False
    payment_tokens = (
        "결제",
        "결재",
        "payment",
        "pay",
        "checkout",
    )
    boundary_tokens = (
        "직전",
        "전까지",
        "전 단계",
        "이전 단계",
        "누르지",
        "누르지 않",
        "누르기 전",
        "버튼이 보이",
        "버튼 표시",
        "결제창",
        "결제 창",
        "주문/결제 화면",
        "주문 결제 화면",
        "before payment",
        "before paying",
        "before checkout",
        "payment page",
        "checkout page",
        "pre-submit",
    )
    completion_tokens = (
        "결제 완료",
        "결제 성공",
        "결제를 완료",
        "결제하기를 눌",
        "결제 버튼을 눌",
        "결제까지 완료",
        "구매 완료",
        "주문 완료",
        "complete payment",
        "payment complete",
        "complete purchase",
        "place order",
    )
    if any(token in goal_blob for token in completion_tokens) and not any(
        token in goal_blob for token in boundary_tokens
    ):
        return False
    return any(token in goal_blob for token in payment_tokens) and any(
        token in goal_blob for token in boundary_tokens
    )


def _payment_presubmit_blob(agent, el: DOMElement) -> str:
    labels = getattr(el, "group_action_labels", None) or []
    label_blob = " ".join(str(item or "") for item in labels if str(item or "").strip()) if isinstance(labels, list) else ""
    return agent._normalize_text(
        " ".join(
            [
                str(getattr(el, "text", "") or ""),
                str(getattr(el, "aria_label", "") or ""),
                str(getattr(el, "title", "") or ""),
                str(getattr(el, "role_ref_name", "") or ""),
                str(getattr(el, "placeholder", "") or ""),
                str(getattr(el, "container_name", "") or ""),
                str(getattr(el, "container_role", "") or ""),
                str(getattr(el, "context_text", "") or ""),
                label_blob,
            ]
        )
    )


def evaluate_payment_presubmit_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    if not _goal_requests_payment_presubmit(agent, goal):
        return None
    if not dom_elements:
        return None

    visible_blobs: List[str] = []
    payment_cta_blobs: List[str] = []
    for el in list(dom_elements or [])[:220]:
        if not bool(getattr(el, "is_visible", True)):
            continue
        blob = _payment_presubmit_blob(agent, el)
        if not blob:
            continue
        visible_blobs.append(blob)
        role = agent._normalize_text(getattr(el, "role", ""))
        tag = agent._normalize_text(getattr(el, "tag", ""))
        actionable = bool(getattr(el, "is_enabled", True)) and (
            role in {"button", "link"} or tag in {"button", "a", "input"}
        )
        if actionable and any(
            token in blob
            for token in (
                "결제하기",
                "결제 하기",
                "결제 버튼",
                "pay now",
                "make payment",
                "submit payment",
            )
        ):
            payment_cta_blobs.append(blob)

    if not visible_blobs or not payment_cta_blobs:
        return None
    page_blob = " ".join(visible_blobs)
    post_payment_tokens = (
        "결제 완료",
        "결제가 완료",
        "주문 완료",
        "구매 완료",
        "결제 성공",
        "영수증",
        "receipt",
        "payment complete",
        "order complete",
        "thank you for your order",
    )
    if any(token in page_blob for token in post_payment_tokens):
        return None
    checkout_surface_tokens = (
        "주문/결제",
        "주문 결제",
        "주문서",
        "주문 확인",
        "네이버페이 주문",
        "결제수단",
        "결제 수단",
        "배송지",
        "배송 정보",
        "주문상품",
        "주문 상품",
        "총 결제",
        "결제금액",
        "결제 금액",
        "최종 결제",
        "checkout",
        "payment method",
        "shipping",
        "billing",
        "order summary",
        "order total",
    )
    if not any(token in page_blob for token in checkout_surface_tokens):
        return None
    return "최종 결제 실행 버튼이 보이는 주문/결제 화면에 도달해 결제 직전 상태로 목표를 완료로 판정했습니다."


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
    registry = getattr(agent, "_participant_registry", None)
    if bool(getattr(registry, "is_multi", lambda: False)()):
        return None
    policy_reason = agent._run_goal_policy_closer(goal=goal, dom_elements=dom_elements)
    if policy_reason:
        return policy_reason
    payment_presubmit_reason = evaluate_payment_presubmit_completion(
        agent,
        goal=goal,
        dom_elements=dom_elements,
    )
    if payment_presubmit_reason:
        return payment_presubmit_reason
    return None


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
    if _should_judge_reasoning_only_wait_completion(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=list(dom_elements or []),
    ):
        synthetic_decision = decision.model_copy(
            update={
                "confidence": max(float(decision.confidence or 0.0), 0.75),
                "is_goal_achieved": True,
                "goal_achievement_reason": (
                    str(decision.goal_achievement_reason or "").strip()
                    or str(decision.reasoning or "").strip()
                    or "WAIT reasoning이 현재 화면 기준 목표 완료를 주장했습니다."
                ),
            }
        )
        return evaluate_goal_completion_judge(
            agent,
            goal=goal,
            decision=synthetic_decision,
            dom_elements=list(dom_elements or []),
        )
    return None


def evaluate_repeated_stop_completion_judge(
    agent,
    *,
    goal: TestGoal,
    decision: Optional[ActionDecision] = None,
    dom_elements: Optional[List[DOMElement]] = None,
    stop_reason: str = "",
) -> Optional[str]:
    reason_blob = str(stop_reason or "").strip()
    if not reason_blob:
        return None
    repeated_stop_tokens = (
        "화면 상태가 반복",
        "동일 액션이 반복",
    )
    if not any(token in reason_blob for token in repeated_stop_tokens):
        return None
    elements = list(dom_elements or [])
    if (
        not elements
        or _dom_has_reasoning_wait_transient_signals(agent, elements)
        or _dom_has_service_unavailable_signal(agent, elements)
    ):
        return None

    base_decision = decision or ActionDecision(
        action=ActionType.WAIT,
        reasoning=reason_blob,
        confidence=0.0,
    )
    synthetic_decision = base_decision.model_copy(
        update={
            "confidence": max(float(getattr(base_decision, "confidence", 0.0) or 0.0), 0.72),
            "is_goal_achieved": True,
            "goal_achievement_reason": (
                "반복 중단 직전 최종 판정 요청입니다. "
                "현재 DOM이 목표 성공 조건을 직접 만족하는 경우에만 success=true로 판정하세요. "
                f"반복 중단 사유: {reason_blob}"
            ),
        }
    )
    return evaluate_goal_completion_judge(
        agent,
        goal=goal,
        decision=synthetic_decision,
        dom_elements=elements,
    )


def _text_evidence_memory_limit() -> int:
    raw_value = str(os.getenv("GAIA_TEXT_EVIDENCE_MEMORY_LIMIT", "8") or "8").strip()
    try:
        value = int(raw_value)
    except Exception:
        return 8
    return max(1, min(value, 20))


def _text_evidence_line_limit() -> int:
    raw_value = str(os.getenv("GAIA_TEXT_EVIDENCE_LINE_LIMIT", "24") or "24").strip()
    try:
        value = int(raw_value)
    except Exception:
        return 24
    return max(4, min(value, 80))


def _compact_text_evidence(value: object, *, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _append_unique_text_line(lines: List[str], seen: set[str], line: object, *, limit: int = 500) -> None:
    text = _compact_text_evidence(line, limit=limit)
    if not text:
        return
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    lines.append(text)


def _snapshot_text_evidence_lines(agent) -> List[str]:
    evidence = getattr(agent, "_last_snapshot_evidence", None)
    if not isinstance(evidence, dict):
        return []
    lines: List[str] = []
    seen: set[str] = set()
    block_line_count = 0
    blocks = evidence.get("dom_text_blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            meta_parts = [
                str(block.get("section") or "").strip(),
                str(block.get("role") or block.get("tag") or "").strip(),
            ]
            meta = " / ".join(part for part in meta_parts if part)
            line = f"{text} [{meta}]" if meta else text
            _append_unique_text_line(lines, seen, line, limit=700)
            block_line_count = len(lines)
    if block_line_count > 0:
        return lines
    live_texts = evidence.get("live_texts")
    if isinstance(live_texts, list):
        for item in live_texts:
            _append_unique_text_line(lines, seen, item, limit=500)
    digest = str(evidence.get("text_digest") or "").strip()
    if digest:
        _append_unique_text_line(lines, seen, digest, limit=900)
    return lines


def _dom_text_evidence_lines(dom_elements: List[DOMElement]) -> List[str]:
    lines: List[str] = []
    seen: set[str] = set()
    for el in list(dom_elements or []):
        if not bool(getattr(el, "is_visible", True)):
            continue
        parts = [
            str(getattr(el, "container_name", "") or "").strip(),
            str(getattr(el, "text", "") or "").strip(),
            str(getattr(el, "aria_label", "") or "").strip(),
            str(getattr(el, "title", "") or "").strip(),
            str(getattr(el, "context_text", "") or "").strip(),
        ]
        labels = getattr(el, "group_action_labels", None)
        if isinstance(labels, list):
            parts.extend(str(item or "").strip() for item in labels if str(item or "").strip())
        text = " | ".join(part for part in parts if part)
        if len(text) < 8:
            continue
        _append_unique_text_line(lines, seen, text, limit=500)
    return lines


def record_llm_requested_text_evidence(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if not bool(getattr(decision, "collect_text_evidence", False)):
        return None
    line_limit = _text_evidence_line_limit()
    snapshot_lines = _snapshot_text_evidence_lines(agent)
    if snapshot_lines:
        lines = snapshot_lines[:line_limit]
    else:
        lines = []
        seen: set[str] = set()
        for line in _dom_text_evidence_lines(list(dom_elements or [])):
            _append_unique_text_line(lines, seen, line, limit=500)
            if len(lines) >= line_limit:
                break
    if not lines:
        return None

    memory = getattr(agent, "_text_evidence_memory", None)
    if not isinstance(memory, list):
        memory = []
    entry: Dict[str, Any] = {
        "goal_id": str(getattr(goal, "id", "") or ""),
        "snapshot_id": str(getattr(agent, "_active_snapshot_id", "") or ""),
        "url": str(getattr(agent, "_active_url", "") or ""),
        "reason": str(getattr(decision, "text_evidence_reason", "") or getattr(decision, "reasoning", "") or "").strip(),
        "focus": [
            str(item or "").strip()
            for item in list(getattr(decision, "text_evidence_focus", []) or [])
            if str(item or "").strip()
        ][:8],
        "lines": lines[:line_limit],
    }
    memory.append(entry)
    setattr(agent, "_text_evidence_memory", memory[-_text_evidence_memory_limit():])
    return f"텍스트 evidence {len(entry['lines'])}개 블록 수집"


def build_text_evidence_memory_block(agent, *, max_entries: int = 5, max_lines_per_entry: int = 12) -> str:
    memory = getattr(agent, "_text_evidence_memory", None)
    if not isinstance(memory, list) or not memory:
        return ""
    lines = ["## 누적 텍스트 evidence (LLM 요청 수집)"]
    for entry_index, raw_entry in enumerate(memory[-max_entries:], start=1):
        if not isinstance(raw_entry, dict):
            continue
        reason = _compact_text_evidence(raw_entry.get("reason"), limit=160)
        focus = [
            _compact_text_evidence(item, limit=80)
            for item in list(raw_entry.get("focus") or [])
            if str(item or "").strip()
        ]
        url = _compact_text_evidence(raw_entry.get("url"), limit=120)
        snapshot_id = _compact_text_evidence(raw_entry.get("snapshot_id"), limit=80)
        meta_parts = []
        if snapshot_id:
            meta_parts.append(f"snapshot={snapshot_id}")
        if url:
            meta_parts.append(f"url={url}")
        if focus:
            meta_parts.append("focus=" + ", ".join(focus[:4]))
        if reason:
            meta_parts.append(f"reason={reason}")
        lines.append(f"- capture {entry_index}: " + ("; ".join(meta_parts) if meta_parts else "metadata 없음"))
        entry_lines = [
            _compact_text_evidence(item, limit=700)
            for item in list(raw_entry.get("lines") or [])
            if str(item or "").strip()
        ]
        for text_index, text in enumerate(entry_lines[:max_lines_per_entry], start=1):
            lines.append(f"  - text {text_index}: {text}")
        omitted = len(entry_lines) - max_lines_per_entry
        if omitted > 0:
            lines.append(f"  - ... ({omitted} more text evidence lines omitted)")
    return "\n".join(lines)


def _should_judge_reasoning_only_wait_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> bool:
    if bool(getattr(decision, "is_goal_achieved", False)):
        return False
    if not hasattr(agent, "_call_llm_text_only"):
        return False
    if (
        not dom_elements
        or _dom_has_reasoning_wait_transient_signals(agent, dom_elements)
        or _dom_has_service_unavailable_signal(agent, dom_elements)
    ):
        return False

    direction = str(getattr(agent, "_goal_constraints", {}).get("mutation_direction") or "").strip().lower()
    if direction in {"increase", "decrease", "clear"}:
        return False
    semantics = getattr(agent, "_goal_semantics", None)
    if bool(getattr(semantics, "mutate_required", False)):
        return False

    reasoning_blob = agent._normalize_text(str(getattr(decision, "reasoning", "") or ""))
    if not reasoning_blob:
        return False
    completion_tokens = (
        "충족",
        "완료",
        "이미",
        "보입니다",
        "보인다",
        "보이고",
        "보이는",
        "확인",
        "표시",
        "visible",
        "present",
        "already",
    )
    loading_tokens = (
        "생각 중",
        "로딩",
        "loading",
        "spinner",
        "generating",
        "기다려",
        "대기",
    )
    if not any(token in reasoning_blob for token in completion_tokens):
        return False
    if any(token in reasoning_blob for token in loading_tokens):
        return False

    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    detail_tokens = (
        "상세",
        "정보",
        "제목",
        "채널",
        "조회",
        "설명",
        "본문",
        "요약",
        "가격",
        "저자",
        "출판사",
        "공고",
        "근무지",
        "주소",
        "기간",
        "등급",
        "순위",
        "랭킹",
        "차트",
        "지도",
        "길찾기",
        "경로",
    )
    if not is_readonly_visibility_goal(agent, goal) and not any(token in goal_blob for token in detail_tokens):
        return False

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
    return bool(visible_blob)


def evaluate_explicit_reasoning_proof_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
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


def _build_media_playback_judge_summary(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> str:
    if not goal_requires_media_playback(agent.__class__, goal):
        return ""
    play_controls = collect_visible_play_controls(agent.__class__, dom_elements or [], limit=3)
    if not play_controls:
        return ""

    lines = ["현재 media/player 관련 control:"]
    if dom_has_media_player_surface(agent.__class__, dom_elements or []):
        lines.append("- current surface looks like a media/player viewer.")
    for idx, element in enumerate(play_controls, start=1):
        lines.append(f"- play candidate {idx}: {describe_play_control(element)}")
    lines.append("- 목표가 재생/play/watch/listen을 직접 요구하면 viewer/player surface 진입만으로는 성공이 아닙니다.")
    lines.append("- 현재 action이 위 play/start control 클릭이 아니라면 success=false로 보는 쪽을 우선하세요.")
    lines.append("- 현재 action이 위 play/start control 클릭이라면 그 click 자체가 마지막 단계일 수 있습니다.")
    return "\n".join(lines)


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
    run_history_replay_packet = build_run_history_replay_packet_context_impl(agent, goal=goal)
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
            "new_page_detected",
            "new_page_count",
            "new_page_same_origin_detected",
            "new_page_same_origin_count",
            "new_page_urls",
            "new_page_titles",
            "new_page_kinds",
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
    media_playback_summary = _build_media_playback_judge_summary(
        agent,
        goal=goal,
        dom_elements=list(dom_elements or []),
    )
    text_evidence_memory_block = build_text_evidence_memory_block(agent)

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

세션 continuity replay packet:
{run_history_replay_packet or "(없음)"}

최근 상태 변화:
{json.dumps(state_change_summary, ensure_ascii=False)}

최근 fill 메모리:
{json.dumps(fill_memory, ensure_ascii=False)}

현재 DOM:
{formatted_dom or "(없음)"}

현재 visible DOM 요약:
{chr(10).join(dom_lines)}

{text_evidence_memory_block or "누적 텍스트 evidence: (없음)"}

media/player 보조 관찰:
{media_playback_summary or "(없음)"}

판정 규칙:
- continuity 우선순위는 replay packet(boundary/checklist/attempt digest) -> summary.md -> MEMORY -> retrieval -> compact state 순서다.
- replay packet의 resume checklist와 recent attempt digest를 먼저 읽고, summary.md 안의 Startup Continuity Audit와 Session Start Rules를 그 다음에 읽어라.
- 이전 run 결론은 현재 DOM이 맞을 때만 재사용하라.
- 현재 화면에 직접 보이는 증거만 믿어라.
- `expected_signals`는 참고 정보일 뿐이고, 부재만으로 자동 실패 처리하지 마라.
- 추측하지 마라. 애매하면 success=false.
- 목록/카드/댓글/기사처럼 여러 항목을 읽는 목표에서는 `누적 텍스트 evidence`가 현재 run에서 수집된 직접 증거다.
- 단, 누적 텍스트 evidence에도 필요한 항목/필드가 부족하면 success=false로 두고 부족한 필드를 이유에 적어라.
- 응답형/결과형 UI에서는 사용자가 입력한 내용이 전송되었고, 그 뒤의 결과 본문/응답이 별도 surface에 보이면 success=true다.
- 로딩/생각중/스피너만 보이면 success=false다.
- 회원가입/로그인 goal은 단순 폼 노출이나 화면 진입만으로 success가 아니다.
- 재생/play/watch/listen goal에서는 viewer/player surface 진입만으로 success가 아니다.
- 현재 DOM에 actionable play/start control이 남아 있고 현재 action이 그 control 클릭이 아니라면 success=false를 우선하라.
- 현재 action이 visible play/start control 클릭이라면 그 click 자체가 마지막 단계일 수 있다.

JSON만 출력:
{{
  "success": true | false,
  "blocked": true | false,
  "reason": "한 문장 근거",
  "confidence": 0.0
}}"""

    record_run_history_transcript_impl(
        agent,
        stage="judge_prompt",
        role="user",
        content=prompt,
        metadata={
            "goal_id": getattr(goal, "id", ""),
            "goal_name": getattr(goal, "name", ""),
            "decision_action": str(getattr(decision, "action", "") or "").strip(),
        },
    )
    try:
        raw = agent._call_llm_text_only(prompt)
    except Exception:
        setattr(
            agent,
            "_last_goal_completion_judge",
            {"prompt": prompt, "raw_response": "", "parsed": {}, "error": "llm_call_failed"},
        )
        return None
    record_run_history_transcript_impl(
        agent,
        stage="judge_response",
        role="assistant",
        content=raw,
        metadata={
            "goal_id": getattr(goal, "id", ""),
            "goal_name": getattr(goal, "name", ""),
            "decision_action": str(getattr(decision, "action", "") or "").strip(),
        },
    )

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
