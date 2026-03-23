from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal


def is_verification_style_goal(agent, goal: TestGoal) -> bool:
    text = agent._normalize_text(
        " ".join(
            [
                str(goal.name or ""),
                str(goal.description or ""),
                " ".join(str(item or "") for item in (goal.success_criteria or [])),
            ]
        )
    )
    if not text:
        return False

    verify_hints = (
        "검증",
        "확인",
        "작동",
        "동작",
        "되는지",
        "정상",
        "기능",
        "verify",
        "validation",
        "check",
        "works",
        "working",
    )
    operation_hints = (
        "클릭해",
        "눌러",
        "입력해",
        "채워",
        "작성해",
        "제출해",
        "저장해",
        "선택해",
        "실행해",
        "추가해",
        "삭제해",
        "제거해",
        "비우",
        "담기",
        "담아",
        "등록해",
        "login해",
        "로그인해",
        "회원가입해",
        "purchase",
        "submit",
        "clear",
        "remove",
        "click",
        "fill",
        "type",
        "select",
        "press",
    )
    entity_hints = (
        "회원가입",
        "로그인",
        "signup",
        "register",
        "login",
        "결제",
        "구매",
        "checkout",
        "purchase",
    )
    visibility_hints = (
        "보이는지",
        "표시",
        "노출",
        "존재",
        "있는지",
        "열려있는지",
        "링크",
        "버튼",
        "이미",
        "현재",
        "visible",
        "shown",
        "exists",
        "present",
    )
    has_verify_hint = any(hint in text for hint in verify_hints)
    has_operation_hint = any(hint in text for hint in operation_hints)
    has_entity_hint = any(hint in text for hint in entity_hints)
    has_visibility_hint = any(hint in text for hint in visibility_hints)
    if not has_verify_hint:
        return False
    if has_operation_hint:
        return False
    if has_entity_hint and not has_visibility_hint:
        return False
    return True


def is_filter_style_goal(agent, goal: TestGoal) -> bool:
    text = agent._normalize_text(
        " ".join(
            [
                str(goal.name or ""),
                str(goal.description or ""),
                " ".join(str(item or "") for item in (goal.success_criteria or [])),
            ]
        )
    )
    if not text:
        return False
    if not is_verification_style_goal(agent, goal):
        return False
    explicit_filter_hints = (
        "필터",
        "filter",
        "학점",
        "credit",
        "정렬",
        "sort",
    )
    category_like_hints = (
        "분류",
        "category",
    )
    readonly_verification_hints = (
        "현재",
        "이미",
        "추가 조작 없이",
        "보이는지",
        "표시",
        "존재",
        "확인",
        "visible",
        "already",
        "without interaction",
    )
    if any(hint in text for hint in category_like_hints) and not any(
        hint in text for hint in explicit_filter_hints
    ):
        return False
    if any(hint in text for hint in readonly_verification_hints) and not any(
        hint in text for hint in explicit_filter_hints
    ):
        return False
    filter_hints = (
        *explicit_filter_hints,
        "분류",
        "category",
    )
    return any(hint in text for hint in filter_hints)


def can_finish_by_verification_transition(
    agent,
    *,
    goal: TestGoal,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    state_change: Optional[Dict[str, Any]],
    before_dom_count: int,
    after_dom_count: int,
) -> bool:
    if not (success and changed):
        return False
    if decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.NAVIGATE, ActionType.SELECT}:
        return False
    if not is_verification_style_goal(agent, goal):
        return False
    if is_filter_style_goal(agent, goal):
        return False
    goal_text = agent._normalize_text(
        " ".join(
            [
                str(goal.name or ""),
                str(goal.description or ""),
                " ".join(str(item or "") for item in (goal.success_criteria or [])),
            ]
        )
    )
    has_close_hint = any(token in goal_text for token in ("닫", "close", "x 버튼", "x버튼", "dismiss"))
    has_list_hint = any(token in goal_text for token in ("목록", "list", "게시판", "게시글", "board", "row"))
    if has_close_hint and has_list_hint:
        return False
    if agent._is_collect_constraint_unmet():
        return False
    if agent._goal_constraints.get("collect_min") is not None:
        return False
    if agent._goal_constraints.get("apply_target") is not None:
        return False

    require_no_navigation = bool(agent._goal_constraints.get("require_no_navigation"))
    if not isinstance(state_change, dict):
        if require_no_navigation:
            return False
        return after_dom_count != before_dom_count

    if require_no_navigation and bool(state_change.get("url_changed")):
        return False

    transition_keys = (
        "url_changed",
        "dom_changed",
        "modal_state_changed",
        "modal_count_changed",
        "backdrop_count_changed",
        "dialog_count_changed",
        "status_text_changed",
        "auth_state_changed",
        "text_digest_changed",
        "interactive_count_changed",
        "list_count_changed",
    )
    return any(bool(state_change.get(key)) for key in transition_keys) or (
        after_dom_count != before_dom_count
    )


def build_verification_transition_reason(
    agent,
    *,
    state_change: Optional[Dict[str, Any]],
    before_dom_count: int,
    after_dom_count: int,
) -> str:
    if not isinstance(state_change, dict):
        return "검증형 목표로 판단되어, 액션 후 화면 상태가 변화해 기능 동작을 확인했습니다."

    signals: List[str] = []
    if bool(state_change.get("modal_state_changed")) or bool(state_change.get("dialog_count_changed")):
        signals.append("모달/상세 패널 상태 변화")
    if bool(state_change.get("backdrop_count_changed")):
        signals.append("오버레이(backdrop) 변화")
    if bool(state_change.get("url_changed")):
        signals.append("URL 변화")
    if bool(state_change.get("dom_changed")) or bool(state_change.get("text_digest_changed")):
        signals.append("DOM/본문 변화")
    if bool(state_change.get("interactive_count_changed")) or bool(state_change.get("list_count_changed")):
        signals.append("상호작용/목록 수 변화")
    if not signals and after_dom_count != before_dom_count:
        signals.append(f"DOM 규모 변화({before_dom_count}->{after_dom_count})")

    if not signals:
        return "검증형 목표로 판단되어, 액션 후 상태 변화가 감지되어 기능 동작을 확인했습니다."
    return "검증형 목표로 판단되어, 액션 후 " + ", ".join(signals[:3]) + "가 확인되어 기능 동작으로 판정했습니다."


def evaluate_static_verification_on_current_page(
    agent,
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Optional[str]:
    if not is_verification_style_goal(agent, goal):
        return None
    if is_filter_style_goal(agent, goal):
        return None
    if bool(agent._goal_constraints.get("require_state_change")):
        return None

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

    static_check_hints = (
        "현재",
        "이미",
        "추가 조작 없이",
        "보이는지",
        "노출",
        "표시",
        "존재하는지",
        "열려있는지",
        "확인",
        "visible",
        "already",
        "without interaction",
    )
    if not any(hint in goal_text for hint in static_check_hints):
        return None

    page_fragments: List[str] = [str(agent._active_url or "")]
    for el in dom_elements[:120]:
        page_fragments.extend(
            [
                str(el.text or ""),
                str(el.aria_label or ""),
                str(getattr(el, "title", None) or ""),
                str(el.placeholder or ""),
                str(el.href or ""),
                str(agent._element_full_selectors.get(el.id) or agent._element_selectors.get(el.id) or ""),
            ]
        )
    page_blob = agent._normalize_text(" ".join(fragment for fragment in page_fragments if fragment))

    def _matches_any(*needles: str) -> bool:
        return any(str(needle or "").strip().lower() in page_blob for needle in needles if str(needle or "").strip())

    evidence_labels: List[str] = []
    visible_elements = [el for el in dom_elements if bool(el.is_visible)]
    link_like_count = sum(
        1
        for el in visible_elements
        if str(el.href or "").strip()
        or agent._normalize_text(el.tag) == "a"
        or agent._normalize_text(el.role) == "link"
    )
    collection_like_count = sum(
        1
        for el in visible_elements
        if agent._normalize_text(el.tag) in {"a", "li", "tr", "article"}
        or agent._normalize_text(el.role) in {"row", "listitem"}
    )
    title_like_count = sum(1 for el in visible_elements if len(str(el.text or "").strip()) >= 12)
    has_collection_evidence = bool(link_like_count >= 6 or collection_like_count >= 6 or title_like_count >= 8)

    if any(token in goal_text for token in ("로그인", "login", "sign in", "signin")):
        if not _matches_any("로그인", "login", "sign in", "/login", "signin"):
            return None
        evidence_labels.append("로그인 신호")

    generic_stop_tokens = {
        "현재", "이미", "추가", "조작", "없이", "보이는지", "표시", "존재하는지",
        "열려있는지", "확인", "페이지", "화면", "되는지", "정상", "작동", "검증",
        "상태", "목록이", "목록", "리스트", "테이블", "table", "list", "page",
        "visible", "already", "without", "interaction", "verify", "check", "page",
        "open", "opened", "shown",
    }
    goal_tokens = [token for token in agent._tokenize_text(goal_text) if token not in generic_stop_tokens]
    matched_generic: List[str] = []
    strong_matched = False
    for token in goal_tokens:
        if len(token) < 2:
            continue
        if token in page_blob:
            matched_generic.append(token)
            if token.isdigit() or len(token) >= 4:
                strong_matched = True

    list_like_hints = ("목록", "리스트", "list", "table", "테이블", "랭킹", "게시판", "카테고리", "태그", "분류", "status", "현황")
    detail_like_hints = ("상세", "detail")
    asks_list_like = any(hint in goal_text for hint in list_like_hints)
    asks_detail_like = any(hint in goal_text for hint in detail_like_hints)

    if asks_list_like and not has_collection_evidence:
        return None
    if asks_list_like and has_collection_evidence:
        evidence_labels.append("목록형 구조")

    if asks_detail_like and not (strong_matched or len(matched_generic) >= 2):
        return None
    if asks_detail_like and (strong_matched or len(matched_generic) >= 2):
        evidence_labels.append("상세 토큰 일치")

    if not evidence_labels:
        if strong_matched:
            evidence_labels.append("핵심 토큰 일치")
        elif len(matched_generic) >= 2:
            evidence_labels.append("토큰 일치")
        elif len(matched_generic) >= 1 and has_collection_evidence:
            evidence_labels.append("토큰+목록 구조 일치")
        else:
            return None

    if matched_generic:
        evidence_labels.extend(sorted(dict.fromkeys(matched_generic[:3])))

    agent._record_reason_code("static_verification_pass")
    labels = ", ".join(dict.fromkeys(evidence_labels)) if evidence_labels else "현재 페이지 신호"
    return f"현재 페이지에서 목표 검증 신호를 바로 확인했습니다. ({labels})"


def extract_goal_query_tokens(agent, goal: TestGoal) -> List[str]:
    goal_text = " ".join(
        [
            str(goal.name or ""),
            str(goal.description or ""),
            " ".join(str(item or "") for item in (goal.success_criteria or [])),
        ]
    )
    quoted = re.findall(r"\"([^\"]{2,})\"|'([^']{2,})'", goal_text)
    quoted_tokens = [next((part for part in group if part), "") for group in quoted]
    tokens: List[str] = [token.strip() for token in quoted_tokens if token.strip()]

    for match in re.findall(r"(?<!\d)(\d{3,6})(?!\d)", goal_text):
        tokens.append(str(match))

    for raw in re.findall(r"[0-9A-Za-z가-힣+/#_-]{2,}", goal_text):
        token = str(raw or "").strip()
        low = token.lower()
        if low in {
            "goal", "test", "flow", "step", "steps", "button", "buttons",
            "verify", "check", "validation", "works", "working",
            "filter", "filters", "page", "pages", "screen", "screens",
            "login", "signup", "register", "current", "visible", "already",
            "without", "interaction",
        }:
            continue
        if len(token) >= 2:
            tokens.append(token)

    unique: List[str] = []
    seen = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            unique.append(token)
    return unique
