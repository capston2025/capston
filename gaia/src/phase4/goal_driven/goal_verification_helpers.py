from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import DOMElement, TestGoal


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


def _has_auth_completion_signal(agent: Any, state_changes: List[Dict[str, Any]]) -> bool:
    if _has_any_state_signal(state_changes, "auth_state_changed"):
        return True
    completed_fields = getattr(agent, "_auth_completed_fields", None)
    if isinstance(completed_fields, set) and completed_fields:
        return True
    if isinstance(completed_fields, list) and completed_fields:
        return True
    return False


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
        elif normalized == "auth_completed":
            ok = _has_auth_completion_signal(agent, state_changes)
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
