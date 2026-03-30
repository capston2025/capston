from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .goal_policy_helpers import build_goal_policy_evidence_bundle
from .goal_replanning_runtime import sync_goal_replanning_state
from .models import DOMElement


def goal_phase_intent(phase: str) -> str:
    phase_norm = str(phase or "").strip().lower()
    if not phase_norm:
        return "mutate"
    if phase_norm == "handle_auth_or_block":
        return "auth"
    if phase_norm.startswith("precheck"):
        return "evidence_only"
    if phase_norm.startswith("verify"):
        return "evidence_only"
    if phase_norm.startswith("reveal"):
        return "reveal"
    return "mutate"


def _auth_ui_still_present(dom_elements: Optional[List[DOMElement]]) -> bool:
    if not isinstance(dom_elements, list) or not dom_elements:
        return False
    has_auth_input = False
    has_submit_control = False
    for element in dom_elements:
        if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
            continue
        tag = str(getattr(element, "tag", "") or "").lower()
        role = str(getattr(element, "role", "") or "").lower()
        blob = " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "placeholder", "") or ""),
                str(getattr(element, "title", "") or ""),
                str(getattr(element, "type", "") or ""),
            ]
        ).lower()
        if tag in {"input", "textarea"} and any(
            token in blob for token in ("password", "비밀번호", "username", "email", "이메일", "아이디", "user")
        ):
            has_auth_input = True
        if (role in {"button", "link"} or tag in {"button", "a"}) and any(
            token in blob for token in ("로그인", "login", "sign in", "signin", "continue", "submit")
        ):
            has_submit_control = True
    return has_auth_input and has_submit_control


def _auth_surface_signature(dom_elements: Optional[List[DOMElement]]) -> str:
    if not isinstance(dom_elements, list) or not dom_elements:
        return ""
    parts: List[str] = []
    for element in dom_elements:
        if not bool(getattr(element, "is_visible", True)) or not bool(getattr(element, "is_enabled", True)):
            continue
        tag = str(getattr(element, "tag", "") or "").lower()
        role = str(getattr(element, "role", "") or "").lower()
        blob = " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "placeholder", "") or ""),
                str(getattr(element, "title", "") or ""),
                str(getattr(element, "type", "") or ""),
            ]
        ).lower()
        is_auth_input = tag in {"input", "textarea"} and any(
            token in blob for token in ("password", "비밀번호", "username", "email", "이메일", "아이디", "user")
        )
        is_submit_control = (role in {"button", "link"} or tag in {"button", "a"}) and any(
            token in blob for token in ("로그인", "login", "sign in", "signin", "continue", "submit")
        )
        if not (is_auth_input or is_submit_control):
            continue
        parts.append(
            "|".join(
                [
                    str(getattr(element, "container_ref_id", "") or ""),
                    str(getattr(element, "ref_id", "") or ""),
                    tag,
                    role,
                    blob,
                ]
            )
        )
    return "\n".join(sorted(set(parts)))


def _is_discovery_control(decision: Any, dom_elements: Optional[List[DOMElement]]) -> bool:
    if not isinstance(dom_elements, list) or not dom_elements:
        return False
    action_value = str(getattr(getattr(decision, "action", None), "value", "") or "").strip().lower()
    if action_value not in {"fill", "click", "press", "select"}:
        return False
    element_id = getattr(decision, "element_id", None)
    if not isinstance(element_id, int):
        return False
    element = next((candidate for candidate in dom_elements if getattr(candidate, "id", None) == element_id), None)
    if element is None and 0 <= element_id < len(dom_elements):
        element = dom_elements[element_id]
    if element is None and 0 < element_id <= len(dom_elements):
        element = dom_elements[element_id - 1]
    if element is None:
        return False
    blob = " ".join(
        [
            str(getattr(element, "text", "") or ""),
            str(getattr(element, "aria_label", "") or ""),
            str(getattr(element, "placeholder", "") or ""),
            str(getattr(element, "title", "") or ""),
            str(getattr(element, "type", "") or ""),
            str(getattr(element, "role", "") or ""),
            str(getattr(element, "container_name", "") or ""),
            str(getattr(element, "context_text", "") or ""),
        ]
    ).lower()
    return any(token in blob for token in ("검색", "search", "query", "find", "filter", "필터"))


def _decision_selected_element(decision: Any, dom_elements: Optional[List[DOMElement]]) -> Optional[DOMElement]:
    if not isinstance(dom_elements, list) or not dom_elements:
        return None
    element_id = getattr(decision, "element_id", None)
    if not isinstance(element_id, int):
        return None
    element = next((candidate for candidate in dom_elements if getattr(candidate, "id", None) == element_id), None)
    if element is None and 0 <= element_id < len(dom_elements):
        element = dom_elements[element_id]
    if element is None and 0 < element_id <= len(dom_elements):
        element = dom_elements[element_id - 1]
    return element


def _looks_like_target_destination_element(agent: Any, element: Optional[DOMElement], semantics: Any) -> bool:
    if element is None or semantics is None:
        return False
    normalize = getattr(agent, "_normalize_text", None)
    if not callable(normalize):
        return False
    target_terms = [
        normalize(term)
        for term in (getattr(semantics, "target_terms", None) or [])
        if str(term or "").strip()
    ]
    destination_terms = [
        normalize(term)
        for term in (getattr(semantics, "destination_terms", None) or [])
        if str(term or "").strip()
    ]
    self_blob = normalize(
        " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "title", "") or ""),
            ]
        )
    )
    context_blob = normalize(
        " ".join(
            [
                str(getattr(element, "container_name", "") or ""),
                str(getattr(element, "container_role", "") or ""),
                str(getattr(element, "context_text", "") or ""),
            ]
        )
    )
    target_hit = any(term and (term in self_blob or term in context_blob) for term in target_terms)
    destination_hit = any(term and term in context_blob for term in destination_terms) or any(
        token in context_blob for token in ("시간표", "내 시간표", "미배정", "온라인", "요일", "교시")
    )
    return bool(target_hit and destination_hit)


def _set_goal_precheck_state(agent: Any, result: str) -> None:
    result_norm = str(result or "").strip().lower()
    precheck_done = result_norm in {"present", "absent"}
    setattr(agent, "_goal_plan_precheck_done", precheck_done)
    setattr(agent, "_goal_plan_precheck_result", result_norm if precheck_done else "")
    state = getattr(agent, "_goal_state_cache", None)
    if not isinstance(state, dict):
        return
    proof = state.setdefault("proof", {})
    proof["precheck_present"] = result_norm == "present"
    proof["precheck_absent"] = result_norm == "absent"
    state["updated_at"] = time.time()


def _reset_goal_precheck_state(agent: Any) -> None:
    _set_goal_precheck_state(agent, "")


def _contains_membership_present_hint(agent: Any, value: Optional[str]) -> bool:
    normalize = getattr(agent, "_normalize_text", None)
    if not callable(normalize):
        text = str(value or "").strip().lower()
    else:
        text = normalize(value)
    if not text:
        return False
    exact_hints = (
        "이미 시간표에 추가된",
        "이미 추가된 과목",
        "이미 추가되어",
        "이미 추가 되어",
        "이미 담긴",
        "이미 담겨",
        "이미 반영된",
        "이미 등록된",
        "이미 선택된",
        "already added",
        "already in your",
        "already selected",
        "already saved",
    )
    if any(hint in text for hint in exact_hints):
        return True
    has_already = any(token in text for token in ("이미", "already"))
    has_addish = any(token in text for token in ("추가", "담", "반영", "등록", "selected", "added", "saved"))
    has_destination = any(
        token in text
        for token in ("시간표", "목록", "리스트", "장바구니", "위시리스트", "timetable", "list", "cart", "wishlist")
    )
    return bool(has_already and has_addish and has_destination)


def _has_membership_present_signal(agent: Any, dom_elements: Optional[List[DOMElement]]) -> bool:
    candidates: List[str] = []
    last_exec = getattr(agent, "_last_exec_result", None)
    if last_exec is not None:
        candidates.extend(
            [
                str(getattr(last_exec, "reason", "") or ""),
                str(getattr(last_exec, "reason_code", "") or ""),
            ]
        )
        last_state_change = getattr(last_exec, "state_change", None)
        if isinstance(last_state_change, dict):
            live_texts = last_state_change.get("live_texts_after")
            if isinstance(live_texts, list):
                candidates.extend(str(item or "") for item in live_texts[:12])
            for key in ("detail", "error", "message", "status_text", "status_text_after"):
                value = last_state_change.get(key)
                if value:
                    candidates.append(str(value))
    for payload in (
        getattr(agent, "_last_backend_post_action_snapshot", None),
        getattr(agent, "_last_snapshot_evidence", None),
        getattr(agent, "_last_backend_trace", None),
    ):
        if not isinstance(payload, dict):
            continue
        live_texts = payload.get("live_texts")
        if isinstance(live_texts, list):
            candidates.extend(str(item or "") for item in live_texts[:12])
        for key in ("detail", "error", "message", "text_digest", "status_text"):
            value = payload.get(key)
            if value:
                candidates.append(str(value))
    if isinstance(dom_elements, list):
        for element in dom_elements[:60]:
            candidates.extend(
                [
                    str(getattr(element, "text", "") or ""),
                    str(getattr(element, "aria_label", "") or ""),
                    str(getattr(element, "title", "") or ""),
                ]
            )
    return any(_contains_membership_present_hint(agent, candidate) for candidate in candidates if str(candidate or "").strip())


def derive_goal_policy_event(
    *,
    decision: Any,
    success: bool,
    changed: bool,
    terminal_result: Any = None,
    login_gate_visible: bool = False,
    auth_submit_attempted: bool = False,
    dom_elements: Optional[List[DOMElement]] = None,
) -> str:
    action_value = str(getattr(getattr(decision, "action", None), "value", "") or "").strip().lower()
    auth_ui_present = _auth_ui_still_present(dom_elements)
    if terminal_result is not None:
        return "terminal"
    if auth_submit_attempted and success and not auth_ui_present:
        return "auth_resolved"
    if login_gate_visible or auth_ui_present:
        return "blocked_auth"
    if action_value == "wait":
        return "wait_progress" if changed else "wait_no_progress"
    if success:
        if _is_discovery_control(decision, dom_elements):
            return "discovery_progress" if changed else "discovery_no_state_change"
        return "action_ok" if changed else "action_no_state_change"
    return "action_failed"


def advance_goal_policy_phase(
    agent: Any,
    *,
    goal: Any,
    decision: Any,
    success: bool,
    changed: bool,
    dom_elements: List[DOMElement],
    post_dom: Optional[List[DOMElement]] = None,
    auth_prompt_visible: bool = False,
    modal_open: bool = False,
    terminal_result: Any = None,
) -> Dict[str, Any]:
    policy = getattr(agent, "_goal_policy", None)
    semantics = getattr(agent, "_goal_semantics", None)
    if policy is None or semantics is None:
        return {}

    effective_dom = post_dom if isinstance(post_dom, list) and post_dom else dom_elements
    evidence = build_goal_policy_evidence_bundle(
        agent,
        goal=goal,
        dom_elements=effective_dom,
        auth_prompt_visible=auth_prompt_visible,
        modal_open=modal_open,
    )
    current_phase = str(getattr(agent, "_goal_policy_phase", "") or policy.initial_phase(semantics))
    current_intent = str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase))
    if hasattr(semantics, "requires_pre_action_membership_check"):
        setattr(agent, "_goal_plan_requires_precheck", bool(getattr(semantics, "requires_pre_action_membership_check", False)))
    event = derive_goal_policy_event(
        decision=decision,
        success=success,
        changed=changed,
        terminal_result=terminal_result,
        login_gate_visible=auth_prompt_visible,
        auth_submit_attempted=bool(getattr(agent, "_auth_submit_attempted", False)),
        dom_elements=effective_dom,
    )
    auth_surface_signature = _auth_surface_signature(effective_dom)
    previous_auth_surface_signature = str(getattr(agent, "_last_auth_surface_signature", "") or "")
    if (
        current_phase == "handle_auth_or_block"
        and event == "blocked_auth"
        and bool(getattr(agent, "_auth_submit_attempted", False))
        and auth_surface_signature
        and previous_auth_surface_signature
        and auth_surface_signature != previous_auth_surface_signature
    ):
        event = "auth_progress"
        setattr(agent, "_auth_surface_progressed", True)
        setattr(agent, "_auth_identifier_done", False)
        setattr(agent, "_auth_password_done", False)
        setattr(agent, "_auth_last_planned_fill", None)
    setattr(agent, "_last_auth_surface_signature", auth_surface_signature)
    if event == "blocked_auth" and current_phase != "handle_auth_or_block":
        setattr(agent, "_goal_phase_resume_after_auth", current_phase)
    if str(current_phase).strip().lower() == "precheck_destination_membership":
        destination_anchor_found = bool(getattr(evidence, "derived", {}).get("destination_anchor_found"))
        target_in_destination = bool(getattr(evidence, "current", {}).get("target_in_destination"))
        selected_pre_dom = _decision_selected_element(decision, dom_elements)
        clicked_target_destination = (
            str(getattr(getattr(decision, "action", None), "value", "") or "").strip().lower() == "click"
            and bool(success)
            and bool(changed)
            and _looks_like_target_destination_element(agent, selected_pre_dom, semantics)
        )
        if destination_anchor_found and target_in_destination:
            _set_goal_precheck_state(agent, "present")
            event = "precheck_present"
        elif destination_anchor_found and clicked_target_destination:
            _set_goal_precheck_state(agent, "present")
            event = "precheck_present"
        elif destination_anchor_found:
            _set_goal_precheck_state(agent, "absent")
            event = "precheck_absent"
    last_exec = getattr(agent, "_last_exec_result", None)
    last_state_change = getattr(last_exec, "state_change", None) if last_exec is not None else None
    if (
        str(current_phase).strip().lower() == "locate_target"
        and event == "action_no_state_change"
        and bool(getattr(agent, "_goal_plan_requires_precheck", False))
        and str(getattr(agent, "_goal_phase_intent", "") or current_intent) == "mutate"
        and bool(getattr(last_exec, "success", False))
        and str(getattr(last_exec, "reason_code", "") or "").strip().lower() == "ok"
        and bool((last_state_change or {}).get("backend_effective_only"))
    ):
        _reset_goal_precheck_state(agent)
        event = "possible_present_noop"
    if (
        str(current_phase).strip().lower() == "locate_target"
        and event in {"action_ok", "action_no_state_change"}
        and bool(getattr(agent, "_goal_plan_requires_precheck", False))
        and str(getattr(agent, "_goal_phase_intent", "") or current_intent) == "mutate"
        and str(getattr(semantics, "mutation_direction", "") or "").strip().lower() == "increase"
        and _has_membership_present_signal(agent, effective_dom)
    ):
        _set_goal_precheck_state(agent, "present")
        event = "possible_present_noop"
    if (
        str(current_phase).strip().lower() == "verify_remediation_removal"
        and not bool(getattr(evidence, "current", {}).get("target_in_destination"))
    ):
        setattr(agent, "_goal_plan_remediation_completed", True)
    sync_goal_replanning_state(
        agent,
        goal=goal,
        dom_elements=effective_dom,
        current_phase=current_phase,
        current_intent=current_intent,
        event=event,
    )
    next_phase = current_phase
    if evidence is not None and hasattr(policy, "next_phase"):
        candidate = str(policy.next_phase(current_phase, event, evidence, policy.budgets()) or current_phase)
        if candidate:
            next_phase = candidate
    if event == "auth_resolved":
        setattr(agent, "_auth_resume_pending", False)
        setattr(agent, "_auth_submit_attempted", False)
        setattr(agent, "_last_auth_surface_signature", "")
        setattr(agent, "_auth_surface_progressed", False)
        # auth 이전의 baseline은 stale → 리셋하여 auth 후 상태로 재수집
        setattr(agent, "_goal_policy_baseline_evidence", None)
        if bool(getattr(agent, "_goal_plan_requires_precheck", False)):
            _reset_goal_precheck_state(agent)
        resume_phase = str(getattr(agent, "_goal_phase_resume_after_auth", "") or "").strip()
        if resume_phase:
            next_phase = resume_phase
            setattr(agent, "_goal_phase_resume_after_auth", "")
    if event == "auth_resolved" and bool(getattr(agent, "_goal_plan_requires_precheck", False)):
        next_phase = "precheck_destination_membership"
    setattr(agent, "_goal_policy_phase", next_phase)
    next_intent = goal_phase_intent(next_phase)
    setattr(agent, "_goal_phase_intent", next_intent)
    return {
        "event": event,
        "previous_phase": current_phase,
        "current_phase": next_phase,
        "phase_intent": str(next_intent or ""),
    }
