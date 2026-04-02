from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import ActionDecision, ActionType, DOMElement, TestGoal
from .policies.filter import filter_goal_requires_semantic_validation


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
    has_visibility_hint = any(hint in text for hint in visibility_hints)
    if not has_verify_hint:
        return False
    if has_operation_hint:
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


def _goal_expected_signals(goal: TestGoal) -> List[str]:
    signals: List[str] = []
    direct = getattr(goal, "expected_signals", None)
    if isinstance(direct, list):
        for item in direct:
            token = str(item or "").strip().lower()
            if token and token not in signals:
                signals.append(token)
    test_data = getattr(goal, "test_data", None)
    if isinstance(test_data, dict):
        raw = test_data.get("harness_expected_signals")
        if isinstance(raw, list):
            for item in raw:
                token = str(item or "").strip().lower()
                if token and token not in signals:
                    signals.append(token)
    return signals


def _has_dom_transition_signal(state_change: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(state_change, dict):
        return False
    return any(
        bool(state_change.get(key))
        for key in (
            "dom_changed",
            "text_digest_changed",
            "status_text_changed",
            "interactive_count_changed",
            "list_count_changed",
        )
    )


def _recent_signal_entries(agent: Any) -> List[Dict[str, Any]]:
    raw = getattr(agent, "_recent_signal_history", None)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)][-12:]


def _recent_state_changes(agent: Any, state_change: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    if isinstance(state_change, dict):
        changes.append(state_change)
    for item in _recent_signal_entries(agent):
        change = item.get("state_change")
        if isinstance(change, dict):
            changes.append(change)
    return changes


def _has_any_state_signal(state_changes: List[Dict[str, Any]], *keys: str) -> bool:
    return any(bool(change.get(key)) for change in state_changes for key in keys)


def _is_actionable_dom_element(el: DOMElement) -> bool:
    role = str(getattr(el, "role", "") or "").strip().lower()
    tag = str(getattr(el, "tag", "") or "").strip().lower()
    return bool(getattr(el, "is_enabled", True)) and (
        role in {"button", "link", "tab"} or tag in {"button", "a"}
    )


def _visibility_goal_query_tokens(agent: Any, goal: TestGoal) -> List[str]:
    stop_tokens = {
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
    tokens: List[str] = []
    tokens.extend(str(item or "").strip() for item in (agent._goal_quoted_terms(goal) or []) if str(item or "").strip())
    tokens.extend(str(item or "").strip() for item in (agent._goal_target_terms(goal) or []) if str(item or "").strip())
    tokens.extend(str(item or "").strip() for item in extract_goal_query_tokens(agent, goal) if str(item or "").strip())

    unique: List[str] = []
    seen = set()
    normalized_stops = {agent._normalize_text(item) for item in stop_tokens}
    for token in tokens:
        normalized = agent._normalize_text(token)
        if not normalized or normalized in normalized_stops:
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append(token)
    return unique


def _has_visibility_dom_signal(agent: Any, goal: TestGoal, dom_elements: List[DOMElement], *, actionable_only: bool) -> bool:
    tokens = _visibility_goal_query_tokens(agent, goal)
    if not tokens:
        return False
    for el in dom_elements:
        if not bool(getattr(el, "is_visible", True)):
            continue
        if actionable_only and not _is_actionable_dom_element(el):
            continue
        blob = agent._normalize_text(
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
        if any(agent._normalize_text(token) in blob for token in tokens):
            return True
    return False


def _select_entry_matches_element(agent: Any, item: Dict[str, Any], el: DOMElement) -> bool:
    def _norm(value: Any) -> str:
        try:
            return agent._normalize_text(value)
        except Exception:
            return str(value or "").strip().lower()

    entry_ref = str(item.get("ref_id") or "").strip()
    if entry_ref and str(getattr(el, "ref_id", "") or "").strip() == entry_ref:
        return True

    entry_role_ref = _norm(item.get("role_ref_name"))
    entry_container = _norm(item.get("container_name"))
    entry_context = _norm(item.get("context_text"))
    current_role_ref = _norm(getattr(el, "role_ref_name", ""))
    current_container = _norm(getattr(el, "container_name", ""))
    current_context = _norm(getattr(el, "context_text", ""))

    if entry_role_ref and current_role_ref and entry_role_ref != current_role_ref:
        return False
    if entry_container and current_container and entry_container != current_container:
        return False
    if entry_context and current_context and entry_context != current_context:
        return False
    return bool(entry_role_ref or entry_container or entry_context)


def _selection_reflected_for_entry(agent: Any, item: Dict[str, Any], dom_elements: List[DOMElement]) -> Dict[str, bool]:
    if not isinstance(dom_elements, list) or not dom_elements:
        return {"matched": False, "reflected": False, "changed": False}

    def _norm(value: Any) -> str:
        try:
            return agent._normalize_text(value)
        except Exception:
            return str(value or "").strip().lower()

    expected_value = str(item.get("expected_value") or "").strip()
    expected_norm = _norm(expected_value)
    previous_norm = _norm(item.get("previous_selected_value"))
    for el in dom_elements:
        tag = _norm(getattr(el, "tag", ""))
        role = _norm(getattr(el, "role", ""))
        if tag != "select" and role not in {"combobox", "listbox"}:
            continue
        if not _select_entry_matches_element(agent, item, el):
            continue
        selected_norm = _norm(getattr(el, "selected_value", ""))
        reflected = bool(selected_norm and selected_norm == expected_norm)
        if not reflected and expected_norm:
            text_norm = _norm(getattr(el, "text", ""))
            reflected = bool(text_norm and expected_norm in text_norm)
        return {
            "matched": True,
            "reflected": reflected,
            "changed": bool(reflected and previous_norm and previous_norm != expected_norm),
        }
    return {"matched": False, "reflected": False, "changed": False}


def _persistent_control_assessment(agent: Any, dom_elements: List[DOMElement]) -> Dict[str, bool]:
    memory = getattr(agent, "_persistent_state_memory", None)
    if not isinstance(memory, list) or not isinstance(dom_elements, list):
        return {
            "target_value_matches": False,
            "target_value_changed": False,
            "selection_reflected": False,
            "persistence_verified": False,
            "persistence_broken": False,
            "persistence_evaluated": False,
        }

    def _norm(value: Any) -> str:
        try:
            return agent._normalize_text(value)
        except Exception:
            return str(value or "").strip().lower()

    try:
        from .filter_validation_engine import _collect_result_rows
        row_texts = list(_collect_result_rows(dom_elements)[:24])
    except Exception:
        row_texts = []

    assessment = {
        "target_value_matches": False,
        "target_value_changed": False,
        "selection_reflected": False,
        "persistence_verified": False,
        "persistence_broken": False,
        "persistence_evaluated": False,
    }
    for item in reversed(memory[-6:]):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        expected_value = str(item.get("expected_value") or "").strip()
        if not expected_value:
            continue
        if kind == "select":
            reflected = _selection_reflected_for_entry(agent, item, dom_elements)
            if reflected["reflected"]:
                assessment["target_value_matches"] = True
                assessment["selection_reflected"] = True
                assessment["persistence_verified"] = True
                assessment["persistence_evaluated"] = True
            if reflected["changed"]:
                assessment["target_value_changed"] = True
            elif reflected["matched"]:
                assessment["persistence_evaluated"] = True
                assessment["persistence_broken"] = not reflected["reflected"]
        elif kind == "fill":
            tokens = [
                _norm(token)
                for token in (item.get("tokens") if isinstance(item.get("tokens"), list) else [])
                if _norm(token)
            ]
            probe_tokens = tokens[:2]
            if not probe_tokens:
                continue
            matched_rows = 0
            for row_text in row_texts:
                row_norm = _norm(row_text)
                if all(token in row_norm for token in probe_tokens):
                    matched_rows += 1
            if matched_rows > 0:
                assessment["persistence_verified"] = True
                assessment["persistence_evaluated"] = True
            elif row_texts:
                assessment["persistence_broken"] = True
                assessment["persistence_evaluated"] = True
    return assessment


def _filter_result_consistency_verified(agent: Any) -> bool:
    report = getattr(agent, "_last_filter_semantic_report", None)
    if not isinstance(report, dict):
        return False
    if bool(report.get("success")):
        return True
    summary = report.get("summary")
    if isinstance(summary, dict):
        return bool(summary.get("goal_satisfied"))
    return False


def _has_pagination_advance_signal(agent: Any, state_changes: List[Dict[str, Any]]) -> bool:
    history = _recent_signal_entries(agent)
    for item in reversed(history):
        if not bool(item.get("pagination_candidate")):
            continue
        change = item.get("state_change")
        if not isinstance(change, dict):
            continue
        if _has_dom_transition_signal(change) or bool(change.get("url_changed")):
            return True
    return any(bool(change.get("url_changed")) for change in state_changes)


def derive_achieved_signals(
    agent: Any,
    *,
    goal: TestGoal,
    state_change: Optional[Dict[str, Any]],
    dom_elements: Optional[List[DOMElement]] = None,
) -> List[str]:
    expected_signals = _goal_expected_signals(goal)
    if not expected_signals:
        return []
    dom = dom_elements if isinstance(dom_elements, list) else []
    state_changes = _recent_state_changes(agent, state_change)
    persistent = _persistent_control_assessment(agent, dom)
    pagination_advanced = _has_pagination_advance_signal(agent, state_changes)
    achieved: List[str] = []
    for signal in expected_signals:
        normalized = str(signal or "").strip().lower()
        ok = False
        if normalized in {"target_value_changed", "selection_reflected", "target_value_matches"}:
            if normalized == "target_value_changed":
                ok = _has_any_state_signal(state_changes, "target_value_changed") or bool(
                    persistent.get("target_value_changed")
                )
            elif normalized == "selection_reflected":
                ok = _has_any_state_signal(state_changes, "target_value_matches") or bool(
                    persistent.get("selection_reflected")
                )
            else:
                ok = _has_any_state_signal(state_changes, "target_value_changed", "target_value_matches") or bool(
                    persistent.get("target_value_matches")
                )
        elif normalized in {"dom_changed", "text_changed"}:
            ok = any(_has_dom_transition_signal(change) for change in state_changes)
        elif normalized == "text_visible":
            ok = _has_visibility_dom_signal(agent, goal, dom, actionable_only=False)
        elif normalized == "cta_visible":
            ok = _has_visibility_dom_signal(agent, goal, dom, actionable_only=True)
        elif normalized in {"url_change", "url_changed"}:
            ok = _has_any_state_signal(state_changes, "url_changed")
        elif normalized in {"pagination_advanced", "page_advanced"}:
            ok = bool(pagination_advanced)
        elif normalized == "persistence_verified":
            ok = bool(
                (pagination_advanced or _has_any_state_signal(state_changes, "url_changed"))
                and bool(persistent.get("persistence_verified"))
            )
        elif normalized in {"persistence_evaluated", "state_persistence_evaluated"}:
            ok = bool(
                (pagination_advanced or _has_any_state_signal(state_changes, "url_changed"))
                and (
                    bool(persistent.get("persistence_verified"))
                    or bool(persistent.get("persistence_broken"))
                    or bool(persistent.get("persistence_evaluated"))
                )
            )
        elif normalized == "result_consistency":
            ok = _filter_result_consistency_verified(agent)
        if ok and normalized not in achieved:
            achieved.append(normalized)
    return achieved


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
    post_dom: Optional[List[DOMElement]] = None,
) -> bool:
    if not (success and changed):
        return False
    if decision.action not in {ActionType.CLICK, ActionType.PRESS, ActionType.NAVIGATE, ActionType.SELECT}:
        return False
    if not is_verification_style_goal(agent, goal):
        return False
    filter_style_goal = is_filter_style_goal(agent, goal)
    filter_transition_allowed = filter_style_goal and not filter_goal_requires_semantic_validation(agent)
    if filter_style_goal and not filter_transition_allowed:
        return False
    expected_signals = _goal_expected_signals(goal)
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

    if expected_signals:
        achieved = set(
            derive_achieved_signals(
                agent,
                goal=goal,
                state_change=state_change,
                dom_elements=post_dom if isinstance(post_dom, list) else [],
            )
        )
        return all(signal in achieved for signal in expected_signals)

    if filter_transition_allowed:
        transition_keys = (
            "text_digest_changed",
            "status_text_changed",
            "interactive_count_changed",
            "list_count_changed",
            "target_value_changed",
            "target_value_matches",
        )
        return any(bool(state_change.get(key)) for key in transition_keys) or (
            after_dom_count != before_dom_count
        )

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
    goal_constraints = getattr(agent, "_goal_constraints", {}) or {}
    mutation_direction = str(goal_constraints.get("mutation_direction") or "").strip().lower()
    if mutation_direction in {"increase", "decrease", "clear"}:
        return None
    if goal_constraints.get("collect_min") is not None:
        return None
    if goal_constraints.get("apply_target") is not None:
        return None
    if bool(goal_constraints.get("require_state_change")):
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
