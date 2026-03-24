from __future__ import annotations

import os
import time
from typing import List, Optional

from .goal_policy_phase_runtime import goal_phase_intent
from .models import ActionDecision, ActionType, DOMElement
from .parsing import parse_multi_values, parse_wait_payload
from .runtime import ActionExecResult
from .exploration_ui_runtime import is_mcp_transport_error, recover_mcp_host
from gaia.src.phase4.browser_error_utils import add_no_retry_hint, extract_reason_fields
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

    agent._last_exec_result = None

    selector = None
    full_selector = None
    ref_id = str(getattr(decision, "ref_id", "") or "").strip() or None
    requires_ref = decision.action in {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.HOVER,
        ActionType.SELECT,
    }
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
    selected_element = None
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
    element_actions = {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.HOVER,
        ActionType.SELECT,
    }
    retriable_reason_codes = {
        "snapshot_not_found",
        "stale_snapshot",
        "ref_required",
        "not_found",
        "ambiguous_ref_target",
    }

    def _refresh_ref_binding() -> None:
        nonlocal selector, full_selector, ref_id
        _ = agent._analyze_dom()
        selector_to_ref = getattr(agent, "_selector_to_ref_id", {}) or {}
        if decision.element_id is not None:
            selector = agent._element_selectors.get(decision.element_id) or selector
            full_selector = agent._element_full_selectors.get(decision.element_id) or full_selector
            ref_id = ref_id or agent._element_ref_ids.get(decision.element_id) or ref_id
        if not ref_id:
            for candidate in (full_selector, selector):
                if candidate:
                    mapped_ref = selector_to_ref.get(candidate)
                    if mapped_ref:
                        ref_id = mapped_ref
                        break

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
            and agent._last_exec_result.reason_code in retriable_reason_codes
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
                _remember_auth_fill()
            return ok, err

        if decision.action == ActionType.PRESS:
            ok, err = _execute_with_ref_recovery("press", action_value=decision.value or "Enter")
            if ok:
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
            return _execute_with_ref_recovery("select", action_value=decision.value)

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
        if action == "wait":
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
            return ActionExecResult(
                success=True,
                effective=True,
                reason_code="ok",
                reason="ok",
                state_change=data.get("state_change") if isinstance(data.get("state_change"), dict) else {},
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
