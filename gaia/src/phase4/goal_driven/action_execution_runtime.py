from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from .goal_policy_phase_runtime import goal_phase_intent
from .models import ActionDecision, ActionType, DOMElement
from .parsing import parse_multi_values, parse_wait_payload
from .runtime import ActionExecResult
from .exploration_ui_runtime import is_mcp_transport_error, recover_mcp_host
from gaia.src.phase4.browser_error_utils import add_no_retry_hint, extract_reason_fields
from gaia.src.phase4.mcp_page_evidence_runtime import resolve_stale_ref
from gaia.src.phase4.mcp_transport_retry_runtime import execute_mcp_action_with_recovery


def _is_placeholder_wait_text(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in {"", "{}", "[]", "null", "none", "undefined"}


def _execute_request_timeout(agent, request_action: str, action: str) -> tuple[int, int]:
    connect_timeout = 10
    if request_action == "browser_wait":
        default_read_timeout = 120
    elif action in {"click", "press", "goto"}:
        default_read_timeout = 180
    else:
        default_read_timeout = 120
    try:
        read_timeout = int(
            getattr(agent, "_env_int", lambda *_args, **_kwargs: default_read_timeout)(
                "GAIA_MCP_EXECUTE_TIMEOUT_SEC",
                default_read_timeout,
                low=30,
                high=600,
            )
        )
    except Exception:
        read_timeout = default_read_timeout
    return connect_timeout, max(30, int(read_timeout))


def _is_stale_like_timeout(result: Optional[ActionExecResult]) -> bool:
    reason_code = str(getattr(result, "reason_code", "") or "").strip().lower()
    if reason_code != "action_timeout":
        return False
    reason = str(getattr(result, "reason", "") or "")
    lower_reason = reason.lower()
    return any(
        token in lower_reason
        for token in (
            "latest snapshot",
            "최신 snapshot",
            "다시 확인하세요",
            "찾을 수 없거나 표시되지",
            "찾을 수 없거나",
            "not visible",
            "to be visible",
            "waiting for locator",
            "detached from dom",
        )
    )


def _normalized_binding_text(agent, value: object) -> str:
    try:
        return agent._normalize_text(value)
    except Exception:
        return str(value or "").strip().lower()


def _normalized_select_options(agent, element: Optional[DOMElement]) -> List[dict[str, str]]:
    if element is None:
        return []
    normalized: List[dict[str, str]] = []
    for raw_option in list(getattr(element, "options", None) or []):
        if isinstance(raw_option, dict):
            option_value = str(raw_option.get("value") or "").strip()
            option_text = str(raw_option.get("text") or "").strip()
        else:
            option_value = str(raw_option or "").strip()
            option_text = option_value
        if not option_value and not option_text:
            continue
        normalized.append(
            {
                "value": option_value,
                "text": option_text,
                "value_norm": _normalized_binding_text(agent, option_value),
                "text_norm": _normalized_binding_text(agent, option_text),
            }
        )
    return normalized


def _select_option_signature(agent, element: Optional[DOMElement]) -> tuple[str, ...]:
    entries = _normalized_select_options(agent, element)
    signature: List[str] = []
    for entry in entries[:12]:
        token = str(entry.get("text_norm") or entry.get("value_norm") or "").strip()
        if token:
            signature.append(token)
    return tuple(signature)


def _select_values_match_element(agent, element: Optional[DOMElement], values: List[str]) -> bool:
    entries = _normalized_select_options(agent, element)
    if not entries:
        return False
    option_tokens = {
        token
        for entry in entries
        for token in (str(entry.get("value_norm") or "").strip(), str(entry.get("text_norm") or "").strip())
        if token
    }
    desired_tokens = [_normalized_binding_text(agent, value) for value in values if str(value or "").strip()]
    if not desired_tokens:
        return False
    return all(token in option_tokens for token in desired_tokens)


def _find_select_value_candidate(
    agent,
    desired_values: List[str],
    current_element: Optional[DOMElement],
    live_dom: List[DOMElement],
) -> Optional[DOMElement]:
    if not desired_values or not isinstance(live_dom, list):
        return None

    prior_container = _normalized_binding_text(agent, getattr(current_element, "container_name", ""))
    prior_context = _normalized_binding_text(agent, getattr(current_element, "context_text", ""))
    prior_role_ref = _normalized_binding_text(agent, getattr(current_element, "role_ref_name", ""))
    prior_signature = _select_option_signature(agent, current_element)

    best_score = -1.0
    best_element: Optional[DOMElement] = None
    for candidate in live_dom:
        candidate_role = _normalized_binding_text(agent, getattr(candidate, "role", ""))
        candidate_tag = _normalized_binding_text(agent, getattr(candidate, "tag", ""))
        if candidate_role not in {"combobox", "listbox"} and candidate_tag != "select":
            continue
        if not _select_values_match_element(agent, candidate, desired_values):
            continue
        score = 0.0
        if prior_container and prior_container == _normalized_binding_text(agent, getattr(candidate, "container_name", "")):
            score += 2.0
        if prior_context and prior_context == _normalized_binding_text(agent, getattr(candidate, "context_text", "")):
            score += 2.0
        if prior_role_ref and prior_role_ref == _normalized_binding_text(agent, getattr(candidate, "role_ref_name", "")):
            score += 1.0
        candidate_signature = _select_option_signature(agent, candidate)
        if prior_signature and candidate_signature == prior_signature:
            score += 7.0
        elif prior_signature and candidate_signature:
            overlap = len(set(prior_signature).intersection(set(candidate_signature)))
            score += min(3.0, overlap * 0.5)
        if current_element is not None:
            try:
                score += max(0.0, 2.0 - (abs(int(getattr(candidate, "id", -1)) - int(getattr(current_element, "id", -1))) * 0.1))
            except Exception:
                pass
        score += float(len(_normalized_select_options(agent, candidate)) * 0.05)
        if score > best_score:
            best_score = score
            best_element = candidate
    return best_element


def _find_rebound_element(agent, prior_element: Optional[DOMElement], live_dom: List[DOMElement]) -> Optional[DOMElement]:
    if prior_element is None or not isinstance(live_dom, list) or not live_dom:
        return None

    prior_text = _normalized_binding_text(agent, getattr(prior_element, "text", ""))
    prior_aria = _normalized_binding_text(agent, getattr(prior_element, "aria_label", ""))
    prior_title = _normalized_binding_text(agent, getattr(prior_element, "title", ""))
    prior_tag = _normalized_binding_text(agent, getattr(prior_element, "tag", ""))
    prior_role = _normalized_binding_text(agent, getattr(prior_element, "role", ""))
    prior_container = _normalized_binding_text(agent, getattr(prior_element, "container_name", ""))
    prior_context = _normalized_binding_text(agent, getattr(prior_element, "context_text", ""))
    prior_role_ref_role = _normalized_binding_text(agent, getattr(prior_element, "role_ref_role", ""))
    prior_role_ref_name = _normalized_binding_text(agent, getattr(prior_element, "role_ref_name", ""))
    prior_selected_value = _normalized_binding_text(agent, getattr(prior_element, "selected_value", ""))
    prior_option_signature = _select_option_signature(agent, prior_element)
    try:
        prior_role_ref_nth = int(getattr(prior_element, "role_ref_nth", 0) or 0)
    except Exception:
        prior_role_ref_nth = 0

    best_score = -1
    best_element: Optional[DOMElement] = None
    for candidate in live_dom:
        score = 0
        if prior_tag and prior_tag == _normalized_binding_text(agent, getattr(candidate, "tag", "")):
            score += 2
        if prior_role and prior_role == _normalized_binding_text(agent, getattr(candidate, "role", "")):
            score += 3
        candidate_text = _normalized_binding_text(agent, getattr(candidate, "text", ""))
        candidate_aria = _normalized_binding_text(agent, getattr(candidate, "aria_label", ""))
        candidate_title = _normalized_binding_text(agent, getattr(candidate, "title", ""))
        candidate_container = _normalized_binding_text(agent, getattr(candidate, "container_name", ""))
        candidate_context = _normalized_binding_text(agent, getattr(candidate, "context_text", ""))
        candidate_role_ref_role = _normalized_binding_text(agent, getattr(candidate, "role_ref_role", ""))
        candidate_role_ref_name = _normalized_binding_text(agent, getattr(candidate, "role_ref_name", ""))
        candidate_selected_value = _normalized_binding_text(agent, getattr(candidate, "selected_value", ""))
        candidate_option_signature = _select_option_signature(agent, candidate)
        try:
            candidate_role_ref_nth = int(getattr(candidate, "role_ref_nth", 0) or 0)
        except Exception:
            candidate_role_ref_nth = 0

        if prior_text and prior_text == candidate_text:
            score += 5
        if prior_aria and prior_aria == candidate_aria:
            score += 4
        if prior_title and prior_title == candidate_title:
            score += 2
        if prior_container and prior_container == candidate_container:
            score += 4
        if prior_context and prior_context == candidate_context:
            score += 3
        if prior_role_ref_role and prior_role_ref_role == candidate_role_ref_role:
            score += 4
        if prior_role_ref_name and prior_role_ref_name == candidate_role_ref_name:
            score += 5
        if prior_selected_value and prior_selected_value == candidate_selected_value:
            score += 4
        if prior_option_signature and candidate_option_signature == prior_option_signature:
            score += 9
        elif prior_option_signature and candidate_option_signature:
            score += min(4, len(set(prior_option_signature).intersection(set(candidate_option_signature))))
        if (
            prior_role_ref_role
            and prior_role_ref_name
            and prior_role_ref_role == candidate_role_ref_role
            and prior_role_ref_name == candidate_role_ref_name
        ):
            if prior_role_ref_nth == candidate_role_ref_nth:
                score += 6
            else:
                score -= min(abs(candidate_role_ref_nth - prior_role_ref_nth), 3)
        if prior_text and candidate_text and prior_text in candidate_text:
            score += 1

        if score > best_score:
            best_score = score
            best_element = candidate

    if best_score < 7:
        return None
    return best_element


def _snapshot_payload_from_agent(agent) -> dict:
    return {
        "elements_by_ref": dict(getattr(agent, "_last_snapshot_elements_by_ref", {}) or {}),
        "context_snapshot": dict(getattr(agent, "_last_context_snapshot", {}) or {}),
    }


def execute_decision(
    agent,
    decision: ActionDecision,
    dom_elements: List[DOMElement],
) -> tuple[bool, Optional[str]]:
    """결정된 액션 실행"""
    openclaw_agentic_mode = str(
        getattr(agent, "_browser_backend_name", "") or os.getenv("GAIA_BROWSER_BACKEND", "") or ""
    ).strip().lower() == "openclaw"

    def _remember_blockable_intent() -> None:
        if decision.action != ActionType.CLICK or selected_element is None:
            return
        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip()
        if str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase)) == "auth":
            return
        try:
            agent._last_goal_blockable_intent = {
                "action": decision.action.value,
                "ref_id": str(ref_id or ""),
                "text": str(getattr(selected_element, "text", "") or ""),
                "aria_label": str(getattr(selected_element, "aria_label", "") or ""),
                "title": str(getattr(selected_element, "title", "") or ""),
                "container_ref_id": str(getattr(selected_element, "container_ref_id", "") or ""),
                "container_name": str(getattr(selected_element, "container_name", "") or ""),
                "context_text": str(getattr(selected_element, "context_text", "") or ""),
                "role": str(getattr(selected_element, "role", "") or ""),
                "tag": str(getattr(selected_element, "tag", "") or ""),
                "selector": str(selector or ""),
                "full_selector": str(full_selector or ""),
                "reasoning": str(getattr(decision, "reasoning", "") or ""),
            }
            container_ref = str(getattr(selected_element, "container_ref_id", "") or "").strip()
            if container_ref and not openclaw_agentic_mode:
                agent._active_interaction_surface = {
                    "kind": "target",
                    "ref_id": container_ref,
                    "source": "successful-click",
                    "sticky_until": time.time() + 20.0,
                }
                agent._active_scoped_container_ref = container_ref
                agent._surface_reacquire_pending = False
        except Exception:
            pass

    def _remember_auth_submit() -> None:
        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip()
        if str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase)) != "auth":
            return
        loginish = False
        if decision.action == ActionType.CLICK and selected_element is not None:
            try:
                loginish = any(
                    agent._contains_login_hint(field)
                    for field in (
                        getattr(selected_element, "text", None),
                        getattr(selected_element, "aria_label", None),
                        getattr(selected_element, "title", None),
                        selector,
                        full_selector,
                    )
                )
            except Exception:
                loginish = False
        elif decision.action == ActionType.PRESS:
            try:
                pressed = str(getattr(decision, "value", "") or "").strip().lower()
            except Exception:
                pressed = ""
            loginish = pressed in {"enter", "return"}
        if loginish:
            agent._last_auth_submit_at = time.time()
            agent._auth_submit_attempted = True
            agent._auth_submit_attempts = int(getattr(agent, "_auth_submit_attempts", 0) or 0) + 1
            agent._auth_last_planned_fill = None
            pre_auth_surface_ref = str(getattr(agent, "_pre_auth_surface_ref", "") or "").strip()
            if pre_auth_surface_ref:
                agent._active_scoped_container_ref = pre_auth_surface_ref
            agent._surface_reacquire_pending = True

    def _remember_auth_fill() -> None:
        if decision.action != ActionType.FILL or selected_element is None:
            return
        current_phase = str(getattr(agent, "_goal_policy_phase", "") or "").strip()
        auth_context_active = (
            str(getattr(agent, "_goal_phase_intent", "") or goal_phase_intent(current_phase)) == "auth"
            or bool(getattr(agent, "_auth_interrupt_active", False))
            or bool(getattr(agent, "_auth_submit_attempted", False))
        )
        if not auth_context_active:
            return
        try:
            fill_blob = agent._normalize_text(
                " ".join(
                    [
                        str(getattr(selected_element, "text", "") or ""),
                        str(getattr(selected_element, "aria_label", "") or ""),
                        str(getattr(selected_element, "placeholder", "") or ""),
                        str(getattr(selected_element, "title", "") or ""),
                        str(getattr(selected_element, "type", "") or ""),
                        str(selector or ""),
                        str(full_selector or ""),
                    ]
                )
            )
        except Exception:
            fill_blob = ""
        fill_value_norm = agent._normalize_text(str(getattr(decision, "value", "") or ""))
        identifier_like_values = set(getattr(agent, "_auth_identifier_values_norm", set()) or set())
        password_like_value = agent._normalize_text(
            str(getattr(agent, "_auth_password_value_norm", "") or "")
        )
        field_key = (
            str(ref_id or "")
            or str(full_selector or "")
            or str(selector or "")
            or fill_blob
        ).strip()
        field_kind = ""
        if any(token in fill_blob for token in ("password", "비밀번호")):
            field_kind = "password"
            agent._auth_password_done = True
        elif fill_value_norm and password_like_value and fill_value_norm == password_like_value:
            field_kind = "password"
            agent._auth_password_done = True
        elif any(token in fill_blob for token in ("username", "email", "이메일", "아이디", "user")):
            field_kind = "identifier"
            agent._auth_identifier_done = True
        elif fill_value_norm and fill_value_norm in identifier_like_values:
            field_kind = "identifier"
            agent._auth_identifier_done = True
        if field_key and field_kind:
            try:
                memory = getattr(agent, "_auth_fill_memory", None)
                if not isinstance(memory, set):
                    memory = set()
                    agent._auth_fill_memory = memory
                memory.add((field_kind, field_key, fill_value_norm))
            except Exception:
                pass

    def _remember_persistent_control_state() -> None:
        if selected_element is None:
            return
        if decision.action not in {ActionType.FILL, ActionType.SELECT}:
            return
        expected_value = str(getattr(decision, "value", "") or "").strip()
        if not expected_value:
            return
        try:
            tag = agent._normalize_text(str(getattr(selected_element, "tag", "") or ""))
            role = agent._normalize_text(str(getattr(selected_element, "role", "") or ""))
        except Exception:
            tag = str(getattr(selected_element, "tag", "") or "").strip().lower()
            role = str(getattr(selected_element, "role", "") or "").strip().lower()
        if decision.action == ActionType.FILL and tag not in {"input", "textarea"} and role not in {"textbox", "searchbox", "combobox"}:
            return
        if decision.action == ActionType.SELECT and tag != "select" and role not in {"combobox", "listbox"}:
            return

        tokens = []
        for raw_token in expected_value.replace("/", " ").split():
            normalized = _normalized_binding_text(agent, raw_token)
            if normalized and len(normalized) >= 2 and normalized not in tokens:
                tokens.append(normalized)

        entry: Dict[str, Any] = {
            "kind": "select" if decision.action == ActionType.SELECT else "fill",
            "expected_value": expected_value,
            "tokens": tokens[:4],
            "ref_id": str(getattr(selected_element, "ref_id", "") or "").strip(),
            "previous_selected_value": str(getattr(selected_element, "selected_value", "") or "").strip(),
            "tag": str(getattr(selected_element, "tag", "") or "").strip(),
            "role": str(getattr(selected_element, "role", "") or "").strip(),
            "container_name": str(getattr(selected_element, "container_name", "") or "").strip(),
            "role_ref_name": str(getattr(selected_element, "role_ref_name", "") or "").strip(),
            "context_text": str(getattr(selected_element, "context_text", "") or "").strip(),
            "step_ts": time.time(),
        }
        try:
            memory = getattr(agent, "_persistent_state_memory", None)
            if not isinstance(memory, list):
                memory = []
            memory = [
                item for item in memory
                if isinstance(item, dict)
                and (
                    str(item.get("ref_id") or "").strip() != entry["ref_id"]
                    or str(item.get("kind") or "").strip() != entry["kind"]
                )
            ]
            memory.append(entry)
            agent._persistent_state_memory = memory[-8:]
        except Exception:
            pass

    def _annotate_control_state_change() -> None:
        exec_result = getattr(agent, "_last_exec_result", None)
        state_change = getattr(exec_result, "state_change", None)
        if not isinstance(state_change, dict) or selected_element is None:
            return

        desired_values = parse_multi_values(getattr(decision, "value", None))
        desired_norms = [
            _normalized_binding_text(agent, value)
            for value in desired_values
            if str(value or "").strip()
        ]
        if not desired_norms:
            return

        previous_selected = _normalized_binding_text(agent, getattr(selected_element, "selected_value", ""))
        previous_text = _normalized_binding_text(agent, getattr(selected_element, "text", ""))
        previous_role_ref = _normalized_binding_text(agent, getattr(selected_element, "role_ref_name", ""))
        desired_primary = desired_norms[0]

        if decision.action == ActionType.SELECT:
            previous_value = previous_selected or previous_text or previous_role_ref
            if previous_value:
                state_change["target_value_matches"] = previous_value == desired_primary
                state_change["target_value_changed"] = previous_value != desired_primary
            return

        if decision.action == ActionType.FILL:
            typed_value = _normalized_binding_text(agent, getattr(decision, "value", ""))
            if not typed_value:
                return
            previous_value = previous_selected or previous_text
            if previous_value:
                state_change["target_value_matches"] = previous_value == typed_value
                state_change["target_value_changed"] = previous_value != typed_value

    def _remember_recent_signal_event() -> None:
        exec_result = getattr(agent, "_last_exec_result", None)
        state_change = getattr(exec_result, "state_change", None)
        if not isinstance(state_change, dict):
            return
        element_blob = agent._normalize_text(
            " ".join(
                [
                    str(getattr(selected_element, "text", "") or ""),
                    str(getattr(selected_element, "aria_label", "") or ""),
                    str(getattr(selected_element, "title", "") or ""),
                    str(getattr(selected_element, "placeholder", "") or ""),
                    str(getattr(selected_element, "role_ref_name", "") or ""),
                    str(getattr(selected_element, "container_name", "") or ""),
                    str(getattr(selected_element, "context_text", "") or ""),
                ]
            )
        )
        pagination_candidate = bool(
            decision.action == ActionType.CLICK
            and (
                agent._contains_next_pagination_hint(getattr(selected_element, "text", None))
                or agent._contains_next_pagination_hint(getattr(selected_element, "aria_label", None))
                or agent._contains_next_pagination_hint(getattr(selected_element, "title", None))
                or agent._contains_next_pagination_hint(getattr(selected_element, "context_text", None))
                or agent._is_numeric_page_label(getattr(selected_element, "text", None))
                or "pagination" in element_blob
                or "검색 결과" in str(getattr(selected_element, "container_name", "") or "")
            )
        )
        entry: Dict[str, Any] = {
            "action": str(decision.action.value),
            "value": str(getattr(decision, "value", "") or "").strip(),
            "ref_id": str(ref_id or ""),
            "state_change": dict(state_change),
            "pagination_candidate": pagination_candidate,
            "role_ref_name": str(getattr(selected_element, "role_ref_name", "") or "").strip(),
            "text": str(getattr(selected_element, "text", "") or "").strip(),
            "container_name": str(getattr(selected_element, "container_name", "") or "").strip(),
            "context_text": str(getattr(selected_element, "context_text", "") or "").strip(),
            "step_ts": time.time(),
        }
        history = getattr(agent, "_recent_signal_history", None)
        if not isinstance(history, list):
            history = []
        history.append(entry)
        agent._recent_signal_history = history[-12:]

    agent._last_exec_result = None

    selector = None
    full_selector = None
    ref_id = str(getattr(decision, "ref_id", "") or "").strip() or None
    selected_element = None
    requires_ref = decision.action in {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.HOVER,
        ActionType.SELECT,
    }
    if ref_id:
        try:
            selected_element = next(
                (el for el in dom_elements if str(getattr(el, "ref_id", "") or "").strip() == ref_id),
                None,
            )
        except Exception:
            selected_element = None
    if selected_element is None and decision.element_id is not None:
        try:
            selected_element = next((el for el in dom_elements if el.id == decision.element_id), None)
        except Exception:
            selected_element = None
    bound_element_id = (
        int(decision.element_id)
        if decision.element_id is not None
        else int(getattr(selected_element, "id", -1))
        if selected_element is not None and getattr(selected_element, "id", None) is not None
        else None
    )
    if requires_ref and decision.element_id is not None:
        selector = agent._element_selectors.get(decision.element_id)
        full_selector = agent._element_full_selectors.get(decision.element_id)
        ref_id = ref_id or agent._element_ref_ids.get(decision.element_id)
        if not selector and not full_selector and not ref_id:
            agent._last_exec_result = ActionExecResult(
                success=False,
                effective=False,
                reason_code="not_found",
                reason=f"요소 ID {decision.element_id}에 대한 ref/selector를 찾을 수 없음",
            )
            return False, f"요소 ID {decision.element_id}에 대한 ref/selector를 찾을 수 없음"
        if requires_ref and (not ref_id or not agent._active_snapshot_id):
            _ = agent._analyze_dom()
            selector = agent._element_selectors.get(decision.element_id)
            full_selector = agent._element_full_selectors.get(decision.element_id)
            ref_id = ref_id or agent._element_ref_ids.get(decision.element_id)
            if not ref_id:
                selector_to_ref = getattr(agent, "_selector_to_ref_id", {}) or {}
                for candidate in (full_selector, selector):
                    if candidate:
                        mapped_ref = selector_to_ref.get(candidate)
                        if mapped_ref:
                            ref_id = mapped_ref
                            break
            if not ref_id or not agent._active_snapshot_id:
                agent._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="ref_required",
                    reason=(
                        "Ref-only policy: 선택된 요소의 ref_id/snapshot_id가 없습니다. "
                        "최신 snapshot 재수집 후 다시 결정해야 합니다."
                    ),
                )
                return False, agent._last_exec_result.as_error_message()
    if requires_ref and bound_element_id is not None:
        selector = agent._element_selectors.get(bound_element_id) or selector
        full_selector = agent._element_full_selectors.get(bound_element_id) or full_selector
        ref_id = ref_id or agent._element_ref_ids.get(bound_element_id)
    element_actions = {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.HOVER,
        ActionType.SELECT,
    }
    base_retriable_reason_codes = {
        "snapshot_not_found",
        "stale_snapshot",
        "ref_required",
        "ref_stale",
        "not_found",
        "ambiguous_ref_target",
    }

    def _refresh_ref_binding() -> None:
        nonlocal selector, full_selector, ref_id, bound_element_id, selected_element
        previous_ref_id = ref_id
        previous_snapshot_payload = _snapshot_payload_from_agent(agent)
        previous_elements_by_ref = previous_snapshot_payload.get("elements_by_ref") or {}
        previous_meta = None
        if bound_element_id is not None:
            previous_meta = (getattr(agent, "_element_ref_meta_by_id", {}) or {}).get(bound_element_id)
        if previous_meta is None and previous_ref_id:
            candidate_meta = previous_elements_by_ref.get(previous_ref_id)
            if isinstance(candidate_meta, dict):
                previous_meta = candidate_meta
        refreshed_dom = agent._analyze_dom() or []
        selector_to_ref = getattr(agent, "_selector_to_ref_id", {}) or {}
        ref_id = None
        rebound_element = None
        fresh_snapshot_payload = _snapshot_payload_from_agent(agent)
        recovered_meta = None
        if isinstance(previous_meta, dict):
            try:
                recovered_meta = resolve_stale_ref(previous_meta, fresh_snapshot_payload)
            except Exception:
                recovered_meta = None
        if isinstance(recovered_meta, dict):
            recovered_ref = str(recovered_meta.get("ref") or recovered_meta.get("ref_id") or "").strip()
            recovered_selector = str(recovered_meta.get("selector") or "").strip()
            recovered_full_selector = str(recovered_meta.get("full_selector") or "").strip()
            for candidate in refreshed_dom:
                candidate_ref = str(getattr(candidate, "ref_id", "") or "").strip()
                if recovered_ref and candidate_ref == recovered_ref:
                    rebound_element = candidate
                    break
            if rebound_element is None:
                for candidate in refreshed_dom:
                    candidate_id = getattr(candidate, "id", None)
                    if candidate_id is None:
                        continue
                    candidate_selector = (getattr(agent, "_element_selectors", {}) or {}).get(int(candidate_id))
                    candidate_full_selector = (getattr(agent, "_element_full_selectors", {}) or {}).get(int(candidate_id))
                    if recovered_full_selector and candidate_full_selector == recovered_full_selector:
                        rebound_element = candidate
                        break
                    if recovered_selector and candidate_selector == recovered_selector:
                        rebound_element = candidate
                        break
        if rebound_element is None:
            rebound_element = _find_rebound_element(agent, selected_element, refreshed_dom)
        if (
            decision.action == ActionType.SELECT
            and rebound_element is not None
            and decision.value
            and not _select_values_match_element(agent, rebound_element, parse_multi_values(decision.value))
        ):
            repaired = _find_select_value_candidate(
                agent,
                parse_multi_values(decision.value),
                rebound_element,
                refreshed_dom,
            )
            if repaired is not None:
                rebound_element = repaired
        if rebound_element is not None and getattr(rebound_element, "id", None) is not None:
            bound_element_id = int(getattr(rebound_element, "id"))
            selected_element = rebound_element
        if bound_element_id is not None:
            selector = agent._element_selectors.get(bound_element_id) or selector
            full_selector = agent._element_full_selectors.get(bound_element_id) or full_selector
            ref_id = agent._element_ref_ids.get(bound_element_id) or None
            if not ref_id:
                ref_meta = (getattr(agent, "_element_ref_meta_by_id", {}) or {}).get(bound_element_id)
                if isinstance(ref_meta, dict):
                    ref_id = str(ref_meta.get("ref") or ref_meta.get("ref_id") or "").strip() or None
        if not ref_id and rebound_element is not None:
            ref_id = str(getattr(rebound_element, "ref_id", "") or "").strip() or None
        if not ref_id:
            for candidate in (full_selector, selector):
                if candidate:
                    mapped_ref = selector_to_ref.get(candidate)
                    if mapped_ref:
                        ref_id = mapped_ref
                        break
        if ref_id and previous_ref_id and ref_id != previous_ref_id:
            agent._log(f"♻️ ref 재바인딩: {previous_ref_id} -> {ref_id}")

    def _execute_with_ref_recovery(
        action_name: str,
        action_value: Optional[str] = None,
    ) -> tuple[bool, Optional[str]]:
        nonlocal selector, full_selector, ref_id
        agent._last_exec_result = execute_action(
            agent,
            action_name,
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=action_value,
        )
        should_retry = (
            decision.action in element_actions
            and (
                agent._last_exec_result.reason_code in base_retriable_reason_codes
                or _is_stale_like_timeout(agent._last_exec_result)
            )
        )
        if should_retry:
            prev_snapshot = agent._active_snapshot_id
            prev_ref = ref_id or ""
            _refresh_ref_binding()
            if ref_id and agent._active_snapshot_id:
                agent._last_exec_result = execute_action(
                    agent,
                    action_name,
                    selector=selector,
                    full_selector=full_selector,
                    ref_id=ref_id,
                    value=action_value,
                )
                if (
                    agent._last_exec_result.success
                    and agent._last_exec_result.effective
                    and (prev_snapshot != agent._active_snapshot_id or prev_ref != (ref_id or ""))
                ):
                    agent._log("♻️ stale/ref 오류 복구: 최신 snapshot/ref 재매핑 후 재시도 성공")
        return bool(agent._last_exec_result.success and agent._last_exec_result.effective), agent._last_exec_result.as_error_message()

    try:
        if decision.action in {
            ActionType.CLICK,
            ActionType.FILL,
            ActionType.HOVER,
            ActionType.SELECT,
        } and decision.element_id is None and not ref_id:
            agent._last_exec_result = ActionExecResult(
                success=False,
                effective=False,
                reason_code="missing_element_id",
                reason=f"{decision.action.value} 액션에는 ref_id 또는 element_id가 필요함",
            )
            return False, f"{decision.action.value} 액션에는 ref_id 또는 element_id가 필요함"
        if decision.action == ActionType.CLICK and selected_element is not None and not agent._goal_allows_logout():
            logout_fields = [
                selected_element.text,
                selected_element.aria_label,
                selected_element.title,
                selector,
                full_selector,
            ]
            if any(agent._contains_logout_hint(field) for field in logout_fields):
                agent._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="blocked_logout_action",
                    reason="목표와 무관한 로그아웃 액션을 차단했습니다.",
                )
                return False, agent._last_exec_result.as_error_message()
        if (
            not openclaw_agentic_mode
            and decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS}
            and agent._is_ref_temporarily_blocked(ref_id)
        ):
            agent._last_exec_result = ActionExecResult(
                success=False,
                effective=False,
                reason_code="blocked_ref_no_progress",
                reason=(
                    "같은 ref에서 상태 변화 없는 실패가 반복되어 임시 차단했습니다. "
                    "다른 요소/페이지 전환을 시도합니다."
                ),
                ref_id_used=ref_id or "",
            )
            return False, agent._last_exec_result.as_error_message()

        if decision.action == ActionType.CLICK:
            click_value = decision.value
            reasoning_norm = agent._normalize_text(decision.reasoning)
            if any(token in reasoning_norm for token in ("닫", "close", "dismiss", "x 버튼", "우상단 x")):
                click_value = "__close_intent__"
            ok, err = _execute_with_ref_recovery("click", action_value=click_value)
            if ok:
                _remember_recent_signal_event()
                _remember_auth_submit()
                _remember_blockable_intent()
                if getattr(agent, "_pending_resume_element_id", None) == decision.element_id:
                    agent._blocked_intent_resumed = True
                    agent._auth_resume_pending = False
                    agent._pending_resume_element_id = None
            elif (
                not openclaw_agentic_mode
                and (
                selected_element is not None
                and str(getattr(getattr(agent, "_last_exec_result", None), "reason_code", "") or "") == "not_actionable"
                )
            ):
                goal_kind = str(getattr(getattr(agent, "_goal_semantics", None), "goal_kind", "") or "")
                mutation_goal = goal_kind in {"add_to_list", "remove_from_list", "clear_list", "apply_selection"}
                container_ref = str(getattr(selected_element, "container_ref_id", "") or "").strip()
                if mutation_goal and container_ref:
                    agent._active_scoped_container_ref = container_ref
                    agent._active_interaction_surface = {
                        "kind": "target",
                        "ref_id": container_ref,
                        "source": "not-actionable",
                        "sticky_until": time.time() + 10.0,
                    }
                    agent._surface_reacquire_pending = True
                    try:
                        agent._record_reason_code("row_secondary_affordance_scope")
                    except Exception:
                        pass
                if getattr(agent, "_pending_resume_element_id", None) == decision.element_id:
                    agent._blocked_intent_resume_attempts = int(getattr(agent, "_blocked_intent_resume_attempts", 0) or 0) + 1
                    agent._auth_resume_pending = True
                    agent._pending_resume_element_id = None
            return ok, err

        if decision.action == ActionType.FILL:
            if not decision.value:
                agent._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="invalid_input",
                    reason="fill 액션에 value가 필요함",
                )
                return False, "fill 액션에 value가 필요함"
            ok, err = _execute_with_ref_recovery("fill", action_value=decision.value)
            if ok:
                _annotate_control_state_change()
                _remember_recent_signal_event()
                _remember_auth_fill()
                _remember_persistent_control_state()
            return ok, err

        if decision.action == ActionType.FOCUS:
            if not str(decision.value or "").strip():
                agent._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="invalid_input",
                    reason="focus 액션에는 target_id/tab_id value가 필요함",
                )
                return False, "focus 액션에는 target_id/tab_id value가 필요함"
            agent._last_exec_result = execute_action(agent, "focus", value=decision.value)
            return bool(agent._last_exec_result.success and agent._last_exec_result.effective), agent._last_exec_result.as_error_message()

        if decision.action == ActionType.PRESS:
            ok, err = _execute_with_ref_recovery("press", action_value=decision.value or "Enter")
            if ok:
                _remember_recent_signal_event()
                _remember_auth_submit()
            return ok, err

        if decision.action == ActionType.SCROLL:
            return _execute_with_ref_recovery("scroll", action_value=decision.value or "down")

        if decision.action == ActionType.SELECT:
            if not decision.value:
                agent._last_exec_result = ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="invalid_input",
                    reason="select 액션에 value(values)가 필요함",
                )
                return False, "select 액션에 value(values)가 필요함"
            parsed_values = parse_multi_values(decision.value)
            known_select_options = bool(
                _normalized_select_options(agent, selected_element)
                or any(_normalized_select_options(agent, el) for el in dom_elements if isinstance(el, DOMElement))
            )
            if parsed_values and known_select_options and not _select_values_match_element(agent, selected_element, parsed_values):
                repaired = _find_select_value_candidate(agent, parsed_values, selected_element, dom_elements)
                if repaired is None:
                    refreshed_dom = agent._analyze_dom() or []
                    repaired = _find_select_value_candidate(agent, parsed_values, selected_element, refreshed_dom)
                    if repaired is not None:
                        dom_elements = refreshed_dom
                if repaired is not None and getattr(repaired, "id", None) is not None:
                    bound_element_id = int(getattr(repaired, "id"))
                    selected_element = repaired
                    selector = agent._element_selectors.get(bound_element_id) or selector
                    full_selector = agent._element_full_selectors.get(bound_element_id) or full_selector
                    ref_id = agent._element_ref_ids.get(bound_element_id) or str(getattr(repaired, "ref_id", "") or "").strip() or ref_id
                    agent._log(
                        "♻️ select 대상 재바인딩: "
                        f'{str(getattr(decision, "ref_id", "") or ref_id or "")} -> {ref_id or "<none>"} '
                        f'(value={",".join(parsed_values)})'
                    )
                elif known_select_options:
                    agent._last_exec_result = ActionExecResult(
                        success=False,
                        effective=False,
                        reason_code="invalid_select_target",
                        reason=(
                            "선택 값이 현재 combobox의 실제 option 목록에 없습니다. "
                            "같은 snapshot 안에서 호환되는 select를 찾지 못했습니다."
                        ),
                        ref_id_used=ref_id or "",
                    )
                    return False, agent._last_exec_result.as_error_message()
            ok, err = _execute_with_ref_recovery("select", action_value=decision.value)
            if ok:
                _annotate_control_state_change()
                _remember_recent_signal_event()
                _remember_persistent_control_state()
            return ok, err

        if decision.action == ActionType.WAIT:
            wait_value = decision.value
            if wait_value is None or (isinstance(wait_value, str) and not wait_value.strip()):
                wait_value = {"timeMs": 700}
            wait_payload = parse_wait_payload(wait_value)
            if not wait_payload or ("text" in wait_payload and _is_placeholder_wait_text(wait_payload.get("text"))):
                wait_payload = {"time_ms": 700}
            simple_wait_only = bool(wait_payload) and set(wait_payload.keys()).issubset({"time_ms", "timeMs"})
            if simple_wait_only:
                wait_ms = wait_payload.get("time_ms", wait_payload.get("timeMs", 700))
                try:
                    wait_ms = max(0, int(wait_ms))
                except Exception:
                    wait_ms = 700
                time.sleep(min(wait_ms, 1500) / 1000.0)
                agent._last_exec_result = ActionExecResult(
                    success=True,
                    effective=True,
                    reason_code="ok",
                    reason="local_wait",
                    state_change={},
                )
                return bool(agent._last_exec_result.success and agent._last_exec_result.effective), agent._last_exec_result.as_error_message()
            agent._last_exec_result = execute_action(agent, "wait", value=wait_payload)
            return bool(agent._last_exec_result.success and agent._last_exec_result.effective), agent._last_exec_result.as_error_message()

        if decision.action == ActionType.NAVIGATE:
            agent._last_exec_result = execute_action(agent, "goto", url=decision.value)
            return bool(agent._last_exec_result.success and agent._last_exec_result.effective), agent._last_exec_result.as_error_message()

        if decision.action == ActionType.HOVER:
            return _execute_with_ref_recovery("hover")

        agent._last_exec_result = ActionExecResult(
            success=False,
            effective=False,
            reason_code="unsupported_action",
            reason=f"지원하지 않는 액션: {decision.action}",
        )
        return False, f"지원하지 않는 액션: {decision.action}"
    except Exception as exc:
        agent._last_exec_result = ActionExecResult(
            success=False,
            effective=False,
            reason_code="exception",
            reason=str(exc),
        )
        return False, str(exc)


def execute_action(
    agent,
    action: str,
    selector: Optional[str] = None,
    full_selector: Optional[str] = None,
    ref_id: Optional[str] = None,
    value: Optional[str] = None,
    values: Optional[List[str]] = None,
    url: Optional[str] = None,
) -> ActionExecResult:
    """MCP Host를 통해 액션 실행"""
    try:
        agent._dom_cache_generation = int(getattr(agent, "_dom_cache_generation", 0) or 0) + 1
        agent._dom_analyze_cache = {}
    except Exception:
        pass

    use_ref_protocol = bool(
        ref_id
        and agent._active_snapshot_id
        and action in {"click", "fill", "press", "hover", "scroll", "scrollIntoView", "select"}
    )
    is_element_action = action in {
        "click",
        "fill",
        "hover",
        "scrollIntoView",
        "select",
        "dragAndDrop",
        "dragSlider",
    }
    if is_element_action and not use_ref_protocol:
        return ActionExecResult(
            success=False,
            effective=False,
            reason_code="ref_required",
            reason="Ref-only policy: snapshot_id + ref_id가 필요합니다.",
        )

    if use_ref_protocol:
        params = {
            "session_id": agent.session_id,
            "snapshot_id": agent._active_snapshot_id,
            "ref_id": ref_id,
            "action": action,
            "url": url or "",
            "verify": True,
            "selector_hint": full_selector or selector or "",
        }
        if action == "select":
            parsed_values = values or parse_multi_values(value)
            if not parsed_values:
                return ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="invalid_input",
                    reason="select 액션에는 values가 필요합니다.",
                )
            params["values"] = parsed_values
            params["value"] = parsed_values if len(parsed_values) > 1 else parsed_values[0]
        elif value is not None:
            params["value"] = value
        request_action = "browser_act"
    else:
        if action == "focus":
            target_id = str(value or "").strip()
            if not target_id:
                return ActionExecResult(
                    success=False,
                    effective=False,
                    reason_code="invalid_input",
                    reason="focus 액션에는 targetId/tab_id가 필요합니다.",
                )
            params = {
                "session_id": agent.session_id,
                "targetId": target_id,
            }
            request_action = "browser_tabs_focus"
        elif action == "wait":
            wait_payload = parse_wait_payload(value)
            if not wait_payload:
                wait_payload = {"time_ms": 1000}
            if "text" in wait_payload and _is_placeholder_wait_text(wait_payload.get("text")):
                wait_payload = {"time_ms": 1000}
            simple_wait_only = bool(wait_payload) and set(wait_payload.keys()).issubset({"time_ms", "timeMs"})
            if simple_wait_only:
                wait_ms = wait_payload.get("time_ms", wait_payload.get("timeMs", 1000))
                try:
                    wait_ms = max(0, int(wait_ms))
                except Exception:
                    wait_ms = 1000
                params = {
                    "session_id": agent.session_id,
                    "action": "wait",
                    "value": wait_ms,
                    "url": url or "",
                }
                request_action = "browser_act"
            else:
                params = {"session_id": agent.session_id}
                params.update(wait_payload)
                request_action = "browser_wait"
        elif action == "scroll":
            params = {
                "session_id": agent.session_id,
                "action": "scroll",
                "value": value,
                "url": url or "",
            }
            request_action = "browser_act"
        else:
            params = {
                "session_id": agent.session_id,
                "action": action,
                "url": url or "",
                "selector": full_selector or selector or "",
            }
            if value is not None:
                params["value"] = value
            if action == "goto" and url:
                params["value"] = url
            request_action = "browser_act"

    try:
        request_timeout = _execute_request_timeout(agent, request_action, action)

        response = execute_mcp_action_with_recovery(
            raw_base_url=agent.mcp_host_url,
            action=request_action,
            params=params,
            timeout=request_timeout,
            attempts=2,
            is_transport_error=is_mcp_transport_error,
            recover_host=lambda *, context="": recover_mcp_host(agent, context=context),
            context=f"action:{request_action}",
        )
        data = response.payload or {"error": response.text or "invalid_json_response"}

        if response.status_code >= 400:
            status_family = "http_4xx" if 400 <= response.status_code < 500 else "http_5xx"
            detail_raw = data.get("detail")
            if isinstance(detail_raw, dict):
                reason_code, detail = extract_reason_fields({"detail": detail_raw}, response.status_code)
            else:
                reason_code = status_family
                detail = str(data.get("detail") or data.get("error") or response.text or "HTTP error")
            attempt_logs = data.get("attempt_logs") if isinstance(data.get("attempt_logs"), list) else []
            retry_path = data.get("retry_path") if isinstance(data.get("retry_path"), list) else []
            attempt_count = int(data.get("attempt_count") or len(attempt_logs) or 0)
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code=reason_code,
                reason=detail,
                state_change={},
                attempt_logs=attempt_logs,
                retry_path=retry_path,
                attempt_count=attempt_count,
                snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                ref_id_used=str(data.get("ref_id_used") or ""),
            )

        is_success = bool(data.get("success"))
        is_effective = bool(data.get("effective", True))
        backend_trace = data.get("backend_trace") if isinstance(data.get("backend_trace"), dict) else {}
        if backend_trace:
            agent._last_backend_trace = dict(backend_trace)
        backend_snapshot = data.get("post_action_snapshot") if isinstance(data.get("post_action_snapshot"), dict) else {}
        agent._last_backend_post_action_snapshot = dict(backend_snapshot) if backend_snapshot else {}
        attempt_logs = data.get("attempt_logs")
        retry_path = data.get("retry_path")
        attempt_count = int(
            data.get("attempt_count")
            or (len(attempt_logs) if isinstance(attempt_logs, list) else 0)
            or 0
        )
        if is_success and is_effective:
            state_change = data.get("state_change") if isinstance(data.get("state_change"), dict) else {}
            if action == "focus":
                state_change = {
                    **dict(state_change or {}),
                    "backend": "browser_tabs_focus",
                    "backend_progress": True,
                    "focus_changed": True,
                    "focused_target_id": str(data.get("targetId") or data.get("current_tab_id") or ""),
                    "focused_url": str(data.get("current_url") or ""),
                }
            return ActionExecResult(
                success=True,
                effective=True,
                reason_code="ok",
                reason="ok",
                state_change=state_change,
                attempt_logs=attempt_logs if isinstance(attempt_logs, list) else [],
                retry_path=retry_path if isinstance(retry_path, list) else [],
                attempt_count=attempt_count,
                snapshot_id_used=str(data.get("snapshot_id_used") or ""),
                ref_id_used=str(data.get("ref_id_used") or ""),
            )

        reason_code, reason = extract_reason_fields(data, response.status_code)
        if reason_code in {"snapshot_not_found", "stale_snapshot", "ambiguous_ref_target", "ambiguous_selector"}:
            reason = (
                f"{reason} | 최신 snapshot/ref로 다시 시도해야 합니다."
                if reason
                else "최신 snapshot/ref로 다시 시도해야 합니다."
            )
        if isinstance(attempt_logs, list) and attempt_logs:
            reason = f"{reason} (attempts={len(attempt_logs)})"
        return ActionExecResult(
            success=is_success,
            effective=is_effective,
            reason_code=reason_code,
            reason=reason,
            state_change=data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
            attempt_logs=attempt_logs if isinstance(attempt_logs, list) else [],
            retry_path=retry_path if isinstance(retry_path, list) else [],
            attempt_count=attempt_count,
            snapshot_id_used=str(data.get("snapshot_id_used") or ""),
            ref_id_used=str(data.get("ref_id_used") or ""),
        )

    except Exception as exc:
        return ActionExecResult(
            success=False,
            effective=False,
            reason_code="request_exception",
            reason=add_no_retry_hint(str(exc)),
        )
