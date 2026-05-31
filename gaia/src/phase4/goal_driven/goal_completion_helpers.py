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
_SOFT_SERVICE_UNAVAILABLE_KEYWORDS = (
    "서비스 지연 안내",
    "서비스 이용에 불편",
    "정상적으로 제공할 수 없습니다",
    "요청하신 페이지를 정상적으로 제공할 수 없습니다",
    "현재 사용자가 많아",
    "잠시 후 다시 접속",
    "잠시 후 다시 시도",
    "접속이 원활하지 않습니다",
    "일시적으로 사용할 수 없습니다",
)
_HARD_SERVICE_UNAVAILABLE_KEYWORDS = (
    "access denied",
    "502 bad gateway",
    "bad gateway",
    "504 gateway timeout",
    "gateway timeout",
    "cloudflare",
    "just a moment",
    "checking your browser",
    "verify you are human",
    "cf-challenge",
    "captcha",
    "service unavailable",
    "temporarily unavailable",
    "too many requests",
    "page not found",
    "404 not found",
    "페이지를 찾을 수 없습니다",
    "ret9999",
    "시스템 오류 발생",
)
_SERVICE_UNAVAILABLE_KEYWORDS = _SOFT_SERVICE_UNAVAILABLE_KEYWORDS + _HARD_SERVICE_UNAVAILABLE_KEYWORDS


_CAPTCHA_LEGAL_DISCLOSURE_HINTS = (
    "protected by recaptcha",
    "protected by hcaptcha",
    "privacy policy",
    "terms of service",
    "google privacy policy",
    "hcaptcha privacy policy",
)
_CAPTCHA_ACTIVE_CHALLENGE_HINTS = (
    "verify you are human",
    "checking your browser",
    "cf-challenge",
    "cloudflare",
    "access denied",
    "보안 확인",
    "보안문자",
    "자동화된 접근",
    "비정상 접근",
)


def _is_captcha_legal_disclosure(normalized_blob: str) -> bool:
    if not normalized_blob:
        return False
    has_disclosure = any(hint in normalized_blob for hint in _CAPTCHA_LEGAL_DISCLOSURE_HINTS)
    if not has_disclosure:
        return False
    return not any(hint in normalized_blob for hint in _CAPTCHA_ACTIVE_CHALLENGE_HINTS)


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


def _service_unavailable_blobs(agent, dom_elements: List[DOMElement]) -> List[str]:
    blobs: List[str] = []
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
        if blob:
            blobs.append(blob)
    evidence = getattr(agent, "_last_snapshot_evidence", None)
    if isinstance(evidence, dict):
        evidence_values: List[Any] = [
            evidence.get("text_digest"),
            evidence.get("live_texts"),
            evidence.get("frame_texts"),
        ]
        for value in evidence_values:
            if isinstance(value, list):
                blobs.extend(agent._normalize_text(item) for item in value if str(item or "").strip())
            elif str(value or "").strip():
                blobs.append(agent._normalize_text(value))
    exec_result = getattr(agent, "_last_exec_result", None)
    state_change = getattr(exec_result, "state_change", None)
    if isinstance(state_change, dict):
        blobs.append(agent._normalize_text(state_change.get("inspection_summary")))
        inspection = state_change.get("inspection")
        if isinstance(inspection, dict):
            blobs.append(agent._normalize_text(inspection.get("title")))
            blobs.append(agent._normalize_text(inspection.get("bodyText")))
            for frame in list(inspection.get("frames") or [])[:8]:
                if isinstance(frame, dict):
                    blobs.append(agent._normalize_text(frame.get("title")))
                    blobs.append(agent._normalize_text(frame.get("bodyText")))
    blobs = [blob for blob in blobs if str(blob or "").strip()]
    return blobs


def detect_service_unavailable_state(agent, dom_elements: List[DOMElement]) -> Optional[Dict[str, Any]]:
    blobs = _service_unavailable_blobs(agent, dom_elements)
    for token in _HARD_SERVICE_UNAVAILABLE_KEYWORDS:
        normalized_token = agent._normalize_text(token)
        if normalized_token and any(
            normalized_token in blob
            and not (normalized_token == "captcha" and _is_captcha_legal_disclosure(blob))
            for blob in blobs
        ):
            return {
                "hard": True,
                "matched": token,
                "reason": f"외부 서비스 오류/차단 화면이 표시되었습니다: {token}",
            }
    for token in _SOFT_SERVICE_UNAVAILABLE_KEYWORDS:
        normalized_token = agent._normalize_text(token)
        if normalized_token and any(normalized_token in blob for blob in blobs):
            return {
                "hard": False,
                "matched": token,
                "reason": f"외부 서비스 지연 안내 화면이 표시되었습니다: {token}",
            }
    return None


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
    if decision.action not in {ActionType.WAIT, ActionType.INSPECT}:
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


def _month_day_pairs_from_text(text: str) -> List[tuple[int, int]]:
    pairs: List[tuple[int, int]] = []
    for month, day in re.findall(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", str(text or "")):
        try:
            pairs.append((int(month), int(day)))
        except ValueError:
            continue
    for month, day in re.findall(r"(?<!\d)(\d{1,2})\s*[.]\s*(\d{1,2})(?!\d)", str(text or "")):
        try:
            pairs.append((int(month), int(day)))
        except ValueError:
            continue
    unique: List[tuple[int, int]] = []
    for pair in pairs:
        if pair not in unique and 1 <= pair[0] <= 12 and 1 <= pair[1] <= 31:
            unique.append(pair)
    return unique


def _disabled_date_surface_has_goal_anchor(agent, goal: TestGoal, visible_blob: str) -> bool:
    visible_no_space = re.sub(r"\s+", "", agent._normalize_text(visible_blob))
    if not visible_no_space:
        return False
    raw_terms: List[str] = []
    for getter_name in ("_goal_quoted_terms", "_goal_target_terms", "_goal_destination_terms"):
        getter = getattr(agent, getter_name, None)
        if callable(getter):
            try:
                raw_terms.extend(str(item or "") for item in (getter(goal) or []))
            except Exception:
                pass
    raw_terms.extend(str(item or "") for item in extract_goal_query_tokens(agent, goal))
    stop_terms = {
        "예매",
        "화면",
        "확인",
        "지난",
        "과거",
        "날짜",
        "상영시간",
        "상영시간이",
        "클릭",
        "클릭되지",
        "않는지",
        "비활성",
        "disabled",
        "선택",
        "불가",
    }
    for term in raw_terms:
        normalized = agent._normalize_text(term)
        compact = re.sub(r"\s+", "", normalized)
        if (
            len(compact) < 2
            or compact in stop_terms
            or re.fullmatch(r"\d+", compact)
            or re.search(r"\d+\s*월|\d+\s*일", normalized)
        ):
            continue
        if compact in visible_no_space:
            return True
    return False


def evaluate_disabled_unavailable_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    if not any(
        token in goal_blob
        for token in (
            "클릭되지",
            "비활성",
            "disabled",
            "선택 불가",
            "클릭 불가",
            "이동할 수 없",
        )
    ):
        return None

    visible_elements = [
        el for el in list(dom_elements or [])
        if bool(getattr(el, "is_visible", True))
    ]
    if not visible_elements:
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
            for el in visible_elements
        )
    )
    if not visible_blob:
        return None

    surface_tokens = ("예매", "상영시간", "날짜", "시간", "옵션", "필터", "버튼")
    if not any(token in visible_blob for token in surface_tokens):
        return None

    goal_date_pairs = _month_day_pairs_from_text(
        str(getattr(goal, "description", "") or "") + " " + str(getattr(goal, "name", "") or "")
    )
    visible_date_pairs = _month_day_pairs_from_text(visible_blob)
    if goal_date_pairs and visible_date_pairs:
        earliest_visible_date = min(visible_date_pairs)
        target_date_before_visible_range = any(pair < earliest_visible_date for pair in goal_date_pairs)
        if (
            target_date_before_visible_range
            and "예매" in visible_blob
            and any(token in visible_blob for token in ("오늘", "날짜", "상영시간"))
            and _disabled_date_surface_has_goal_anchor(agent, goal, visible_blob)
        ):
            return "현재 날짜 선택 UI가 목표 과거 날짜 이후 범위만 제공해 목표 날짜 선택지가 클릭되지 않음을 확인했습니다."

    recent_feedback_blob = agent._normalize_text(
        " ".join(
            str(item or "")
            for item in list(getattr(agent, "_action_feedback", []) or [])[-6:]
        )
        + " "
        + str(getattr(getattr(agent, "_last_exec_result", None), "error", "") or "")
    )
    recent_not_actionable = any(
        token in recent_feedback_blob
        for token in (
            "not_actionable",
            "not found or not visible",
            "not found",
            "not visible",
            "disabled",
            "비활성",
        )
    )

    disabled_controls: List[DOMElement] = [
        el
        for el in visible_elements
        if not bool(getattr(el, "is_enabled", True))
        and str(getattr(el, "role", "") or "").strip().lower() in {"button", "link", "tab", "option", "radio"}
    ]
    disabled_blob = agent._normalize_text(
        " ".join(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                ]
            )
            for el in disabled_controls
        )
    )
    has_disabled_evidence = (
        bool(disabled_controls)
        or "disabled" in visible_blob
        or "비활성" in visible_blob
        or recent_not_actionable
    )
    if not has_disabled_evidence:
        return None

    date_terms = [
        re.sub(r"\s+", "", match)
        for match in re.findall(r"\d+\s*월\s*\d+\s*일", str(getattr(goal, "description", "") or "") + " " + str(getattr(goal, "name", "") or ""))
    ]
    normalized_visible_no_space = re.sub(r"\s+", "", visible_blob)
    date_missing = bool(date_terms) and any(term not in normalized_visible_no_space for term in date_terms)
    past_navigation_blocked = "이전" in disabled_blob or "이전" in visible_blob and "disabled" in visible_blob
    disabled_time_evidence = bool(re.search(r"\b\d{1,2}:\d{2}\b", disabled_blob or visible_blob))

    if recent_not_actionable and any(token in visible_blob for token in ("예매", "상영시간", "날짜", "시간")):
        return "최근 목표 조건의 비활성/선택 불가 대상 클릭이 not_actionable으로 실패했고 현재 검증 화면이 유지되어 클릭되지 않음을 확인했습니다."
    if date_terms and not (date_missing and (past_navigation_blocked or disabled_time_evidence)):
        return None

    if disabled_time_evidence:
        return "현재 화면에서 목표 조건의 시간/옵션이 disabled 상태로 표시되어 클릭되지 않음을 확인했습니다."
    if date_missing and past_navigation_blocked:
        return "현재 화면에서 목표 날짜가 제공되지 않고 이전 이동도 disabled라 과거 선택지가 클릭되지 않음을 확인했습니다."
    return "현재 화면에서 목표 선택지가 disabled/비활성 상태라 클릭되지 않음을 확인했습니다."


def _quantity_term_forms(value: str) -> List[str]:
    cleaned = re.sub(r"\s+", "", str(value or "").strip())
    if not cleaned:
        return []
    forms = [cleaned]
    if cleaned.endswith("입"):
        forms.append(cleaned[:-1])
    elif cleaned.endswith("개"):
        forms.append(f"{cleaned}입")
    return list(dict.fromkeys(forms))


def _variant_target_quantity_terms(goal_blob_raw: str) -> List[str]:
    terms: List[str] = []
    for pattern in (
        r"(\d+\s*개(?:입)?)\s*(?:클릭|선택|누르|눌렀|했을\s*때|했을때)",
        r"(?:클릭|선택|누르|눌렀|했을\s*때|했을때)[^\d]{0,12}(\d+\s*개(?:입)?)",
    ):
        for match in re.findall(pattern, goal_blob_raw):
            for form in _quantity_term_forms(str(match or "")):
                if form not in terms:
                    terms.append(form)
    return terms


def evaluate_variant_price_image_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    goal_blob_raw = agent._goal_text_blob(goal)
    goal_blob = agent._normalize_text(goal_blob_raw)
    if not goal_blob:
        return None
    if not any(token in goal_blob for token in ("대표이미지", "대표 이미지", "상품이미지", "상품 이미지", "product image")):
        return None
    if not any(token in goal_blob for token in ("가격", "금액", "price")):
        return None
    if not any(token in goal_blob for token in ("달라", "변경", "바뀌", "비교", "옵션", "variant")):
        return None

    target_terms = _variant_target_quantity_terms(str(goal_blob_raw or ""))
    if not target_terms:
        return None

    visible_elements = [el for el in list(dom_elements or []) if bool(getattr(el, "is_visible", True))]
    if not visible_elements:
        return None

    def _el_blob(el: DOMElement) -> str:
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

    def _is_actionable(el: DOMElement) -> bool:
        role = str(getattr(el, "role", "") or "").strip().lower()
        tag = str(getattr(el, "tag", "") or "").strip().lower()
        return bool(getattr(el, "is_enabled", True)) and (
            role in {"button", "link", "tab", "option", "radio"} or tag in {"button", "a", "option"}
        )

    visible_blob = agent._normalize_text(" ".join(_el_blob(el) for el in visible_elements))
    current_surface_blob = agent._normalize_text(
        " ".join(_el_blob(el) for el in visible_elements if not _is_actionable(el))
    )
    if not visible_blob or not current_surface_blob:
        return None

    target_seen_on_current_surface = any(term and term in current_surface_blob for term in target_terms)
    if not target_seen_on_current_surface:
        return None

    recent_parts: List[str] = []
    for attr in ("_action_history", "_action_feedback", "_persistent_state_memory", "_text_evidence_memory"):
        value = getattr(agent, attr, None)
        if isinstance(value, list):
            recent_parts.extend(str(item or "") for item in value[-8:])
        elif value:
            recent_parts.append(str(value))
    recent_blob = agent._normalize_text(" ".join(recent_parts))

    last_selected = getattr(agent, "_last_action_selected_element", None)
    selected_blob = ""
    if last_selected is not None:
        selected_blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(last_selected, "text", "") or ""),
                    str(getattr(last_selected, "aria_label", "") or ""),
                    str(getattr(last_selected, "title", "") or ""),
                    str(getattr(last_selected, "role_ref_name", "") or ""),
                    str(getattr(last_selected, "selected_value", "") or ""),
                ]
            )
        )
    target_was_recently_selected = any(term and term in selected_blob for term in target_terms)
    if not target_was_recently_selected:
        target_was_recently_selected = any(term and term in recent_blob for term in target_terms)
    if not target_was_recently_selected:
        return None

    all_quantity_forms = []
    for match in re.findall(r"\d+\s*개(?:입)?", str(goal_blob_raw or "")):
        all_quantity_forms.extend(_quantity_term_forms(match))
    comparison_terms = [term for term in dict.fromkeys(all_quantity_forms) if term not in set(target_terms)]
    if comparison_terms and not any(term and term in recent_blob for term in comparison_terms):
        baseline_terms: List[str] = []
        for match in re.findall(r"\d+\s*개(?:입)?", recent_blob):
            baseline_terms.extend(_quantity_term_forms(match))
        baseline_terms = [
            term for term in dict.fromkeys(baseline_terms)
            if term not in set(target_terms) and term not in set(comparison_terms)
        ]
        if not baseline_terms:
            return None

    has_price = bool(re.search(r"\d[\d,]*\s*원", visible_blob)) or "price" in visible_blob or "가격" in visible_blob
    if not has_price:
        return None

    has_image = False
    for el in visible_elements:
        role = str(getattr(el, "role", "") or "").strip().lower()
        tag = str(getattr(el, "tag", "") or "").strip().lower()
        blob = _el_blob(el)
        if role in {"img", "image"} or tag == "img":
            has_image = True
            break
        if any(token in blob for token in ("product image", "대표이미지", "대표 이미지", "상품이미지", "상품 이미지")):
            has_image = True
            break
    if not has_image:
        return None

    target_label = target_terms[0]
    return (
        f"현재 상품 상세 surface에서 선택 수량({target_label}), 가격, 대표이미지 영역이 함께 확인되어 "
        "옵션 변경 확인 목표를 완료로 판정했습니다."
    )


def _goal_sort_terms(goal_blob: str) -> List[str]:
    candidates = (
        "판매량순",
        "예약가 낮은 순",
        "낮은 가격순",
        "낮은가격순",
        "가격 낮은 순",
        "높은 가격순",
        "가격 높은 순",
        "최신순",
        "추천순",
        "인기순",
        "리뷰순",
        "평점순",
        "랭킹순",
        "sort",
    )
    terms = [term for term in candidates if term in goal_blob]
    return list(dict.fromkeys(terms))


def _goal_search_query_before_search(goal_blob_raw: str) -> str:
    text = str(goal_blob_raw or "")
    match = re.search(r"\s*검색", text)
    if not match:
        return ""
    prefix = text[: match.start()]
    tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9가-힣]+", prefix)
        if token not in {"홈에서", "현재", "검색창", "검색어", "후에", "뒤에"}
    ]
    return re.sub(r"\s+", "", tokens[-1]).strip() if tokens else ""


def evaluate_sort_results_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    goal_blob_raw = agent._goal_text_blob(goal)
    goal_blob = agent._normalize_text(goal_blob_raw)
    if not goal_blob:
        return None
    if not any(token in goal_blob for token in ("정렬", "필터", "sort", "순서")):
        return None
    sort_terms = _goal_sort_terms(goal_blob)
    if not sort_terms:
        return None

    last_selected = getattr(agent, "_last_action_selected_element", None)
    selected_blob = ""
    if last_selected is not None:
        selected_blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(last_selected, "text", "") or ""),
                    str(getattr(last_selected, "aria_label", "") or ""),
                    str(getattr(last_selected, "title", "") or ""),
                    str(getattr(last_selected, "role_ref_name", "") or ""),
                    str(getattr(last_selected, "selected_value", "") or ""),
                ]
            )
        )
    selected_matches_sort = any(term and term in selected_blob for term in sort_terms)

    visible_elements = [el for el in list(dom_elements or []) if bool(getattr(el, "is_visible", True))]
    if not visible_elements:
        return None
    visible_blob = agent._normalize_text(
        " ".join(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", "") or ""),
                    str(getattr(el, "role_ref_name", "") or ""),
                    str(getattr(el, "container_name", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                ]
            )
            for el in visible_elements
        )
    )
    if not visible_blob:
        return None

    active_url = agent._normalize_text(getattr(agent, "_active_url", "") or "")
    has_sort_term_visible = any(term and term in visible_blob for term in sort_terms)
    has_active_sort_signal = any(token in visible_blob for token in ("checked", "active", "selected", "선택됨", "적용"))
    active_url_sort_signal = any(
        token in active_url
        for token in (
            "sort",
            "sorter",
            "order",
            "rank",
            "salecountdesc",
        )
    )
    if selected_blob and not selected_matches_sort and not (has_active_sort_signal or active_url_sort_signal):
        return None
    if not (has_sort_term_visible and (has_active_sort_signal or active_url_sort_signal)):
        return None

    query = _goal_search_query_before_search(str(goal_blob_raw or ""))
    if query and query not in re.sub(r"\s+", "", visible_blob):
        return None

    has_result_surface = any(token in visible_blob for token in ("검색결과", "검색 결과", "상품", "제품", "product", "list"))
    has_ordered_items = bool(re.search(r"(?:^|\D)1(?:\D{1,80})2(?:\D{1,80})3(?:\D|$)", visible_blob))
    has_price_or_product = bool(re.search(r"\d[\d,]*\s*원", visible_blob)) or any(
        token in visible_blob for token in ("상품명", "제품명", "무료배송", "도착", "리뷰")
    )
    if not (has_result_surface and (has_ordered_items or has_price_or_product)):
        return None

    return f"현재 결과 목록에서 {sort_terms[0]} 정렬이 active/checked 상태이고 상품 목록이 표시되어 정렬 확인 목표를 완료로 판정했습니다."


def _compact_match_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _goal_region_terms(goal_blob_raw: str) -> List[str]:
    terms = re.findall(r"[가-힣]{2,}(?:특별시|광역시|시|군|구|동|읍|면|리)", str(goal_blob_raw or ""))
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))


def evaluate_filter_result_surface_completion(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    goal_blob_raw = agent._goal_text_blob(goal)
    goal_blob = agent._normalize_text(goal_blob_raw)
    if not goal_blob:
        return None
    if "필터" not in goal_blob:
        return None
    if not any(token in goal_blob for token in ("결과", "나타", "표시", "확인")):
        return None

    visible_elements = [el for el in list(dom_elements or []) if bool(getattr(el, "is_visible", True))]
    if not visible_elements:
        return None

    def _el_blob(el: DOMElement) -> str:
        return agent._normalize_text(
            " ".join(
                [
                    str(getattr(el, "text", "") or ""),
                    str(getattr(el, "aria_label", "") or ""),
                    str(getattr(el, "title", "") or ""),
                    str(getattr(el, "role_ref_name", "") or ""),
                    str(getattr(el, "selected_value", "") or ""),
                    str(getattr(el, "class_name", "") or ""),
                    str(getattr(el, "container_name", "") or ""),
                    str(getattr(el, "context_text", "") or ""),
                ]
            )
        )

    visible_blob = agent._normalize_text(" ".join(_el_blob(el) for el in visible_elements))
    compact_visible = _compact_match_text(visible_blob)
    if not visible_blob:
        return None

    region_terms = _goal_region_terms(str(goal_blob_raw or ""))
    if len(region_terms) < 2:
        return None
    if not all(_compact_match_text(term) in compact_visible for term in region_terms):
        return None
    most_specific_region = region_terms[-1]
    region_result_patterns = (
        f"‘{most_specific_region}’ 매물건수",
        f"'{most_specific_region}' 매물건수",
        f'"{most_specific_region}" 매물건수',
        f"{most_specific_region} 매물건수",
    )
    region_summary_committed = any(pattern in visible_blob for pattern in region_result_patterns) or bool(
        re.search(
            re.escape(most_specific_region) + r".{0,16}(?:매물건수|검색결과|검색 결과|결과)",
            visible_blob,
        )
    )
    if not region_summary_committed:
        return None

    expected_signals = [
        str(item or "").strip()
        for item in list(getattr(goal, "expected_signals", []) or [])
        if str(item or "").strip()
    ]
    expected_hits = [
        signal
        for signal in expected_signals
        if agent._normalize_text(signal) in visible_blob
        or _compact_match_text(signal) in compact_visible
    ]
    if expected_signals and len(expected_hits) < min(4, len(expected_signals)):
        return None

    result_summary_tokens = (
        "검색결과",
        "검색 결과",
        "결과",
        "매물건수",
        "상품",
        "제품",
        "목록",
        "list",
    )
    has_result_summary = any(token in visible_blob for token in result_summary_tokens)
    has_count_summary = bool(re.search(r"\d[\d,]*\s*(?:건|개|명|원|%)", visible_blob)) or (
        has_result_summary and bool(re.search(r"\d[\d,]*", visible_blob))
    )
    if not (has_result_summary and has_count_summary):
        return None

    active_or_committed = any(
        token in visible_blob
        for token in (
            "selected",
            "active",
            "checked",
            "선택",
            "적용",
            "매물건수",
            "검색결과",
            "검색 결과",
        )
    )
    if not active_or_committed:
        return None

    matched_label = ", ".join(dict.fromkeys(expected_hits[:4] or region_terms[:4]))
    if not matched_label:
        matched_label = "필터/지역/결과 요약"
    return f"현재 화면에서 {matched_label} 및 결과 요약/건수가 함께 확인되어 필터 결과 확인 목표를 완료로 판정했습니다."


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
    disabled_unavailable_reason = evaluate_disabled_unavailable_completion(
        agent,
        goal=goal,
        dom_elements=dom_elements,
    )
    if disabled_unavailable_reason:
        return disabled_unavailable_reason
    variant_price_image_reason = evaluate_variant_price_image_completion(
        agent,
        goal=goal,
        dom_elements=dom_elements,
    )
    if variant_price_image_reason:
        return variant_price_image_reason
    sort_results_reason = evaluate_sort_results_completion(
        agent,
        goal=goal,
        dom_elements=dom_elements,
    )
    if sort_results_reason:
        return sort_results_reason
    filter_result_reason = evaluate_filter_result_surface_completion(
        agent,
        goal=goal,
        dom_elements=dom_elements,
    )
    if filter_result_reason:
        return filter_result_reason
    return None


def evaluate_reasoning_only_wait_completion(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    dom_elements: Optional[List[DOMElement]] = None,
) -> Optional[str]:
    if decision.action not in {ActionType.WAIT, ActionType.INSPECT}:
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
    reasoning_blob = agent._normalize_text(str(getattr(decision, "reasoning", "") or ""))
    if not reasoning_blob:
        return False
    goal_blob = agent._normalize_text(agent._goal_text_blob(goal))
    disabled_verification_goal = any(
        token in goal_blob or token in reasoning_blob
        for token in (
            "클릭되지",
            "비활성",
            "disabled",
            "선택 불가",
            "이동할 수 없",
            "접근할 수 없",
        )
    )
    semantics = getattr(agent, "_goal_semantics", None)
    if bool(getattr(semantics, "mutate_required", False)) and not disabled_verification_goal:
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
        "기다려야",
        "기다리는",
        "대기 중",
    )
    if not any(token in reasoning_blob for token in completion_tokens):
        return False
    if any(token in reasoning_blob for token in loading_tokens):
        return False

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
        "예매",
        "상영시간",
        "비활성",
        "disabled",
        "클릭되지",
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
