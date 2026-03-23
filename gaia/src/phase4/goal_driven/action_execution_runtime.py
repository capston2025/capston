from __future__ import annotations

import time
from typing import List, Optional

import requests

from .models import ActionDecision, ActionType, DOMElement
from .parsing import parse_multi_values, parse_wait_payload
from .runtime import ActionExecResult
from .exploration_ui_runtime import is_mcp_transport_error, recover_mcp_host
from gaia.src.phase4.browser_error_utils import add_no_retry_hint, extract_reason_fields


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

    def _remember_blockable_intent() -> None:
        if decision.action != ActionType.CLICK or selected_element is None:
            return
        if str(getattr(agent, "_goal_policy_phase", "") or "").strip() == "handle_auth_or_block":
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
        except Exception:
            pass

    def _remember_auth_submit() -> None:
        if decision.action != ActionType.CLICK or selected_element is None:
            return
        if str(getattr(agent, "_goal_policy_phase", "") or "").strip() != "handle_auth_or_block":
            return
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
        if loginish:
            agent._last_auth_submit_at = time.time()

    def _remember_auth_fill() -> None:
        if decision.action != ActionType.FILL or selected_element is None:
            return
        if str(getattr(agent, "_goal_policy_phase", "") or "").strip() != "handle_auth_or_block":
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
        if any(token in fill_blob for token in ("password", "비밀번호")):
            agent._auth_password_done = True
        elif any(token in fill_blob for token in ("username", "email", "이메일", "아이디", "user")):
            agent._auth_identifier_done = True

    agent._last_exec_result = None

    selector = None
    full_selector = None
    ref_id = None
    requires_ref = decision.action in {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.HOVER,
        ActionType.SCROLL,
        ActionType.SELECT,
    }
    if decision.element_id is not None and requires_ref:
        selector = agent._element_selectors.get(decision.element_id)
        full_selector = agent._element_full_selectors.get(decision.element_id)
        ref_id = agent._element_ref_ids.get(decision.element_id)
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
            ref_id = agent._element_ref_ids.get(decision.element_id)
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
    if decision.element_id is not None:
        try:
            selected_element = next((el for el in dom_elements if el.id == decision.element_id), None)
        except Exception:
            selected_element = None

    element_actions = {
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.PRESS,
        ActionType.HOVER,
        ActionType.SCROLL,
        ActionType.SELECT,
    }
    retriable_reason_codes = {
        "snapshot_not_found",
        "stale_snapshot",
        "ref_required",
        "not_found",
        "ambiguous_ref_target",
        "no_state_change",
        "not_actionable",
    }

    def _refresh_ref_binding() -> None:
        nonlocal selector, full_selector, ref_id
        _ = agent._analyze_dom()
        selector_to_ref = getattr(agent, "_selector_to_ref_id", {}) or {}
        if decision.element_id is not None:
            selector = agent._element_selectors.get(decision.element_id) or selector
            full_selector = agent._element_full_selectors.get(decision.element_id) or full_selector
            ref_id = agent._element_ref_ids.get(decision.element_id) or ref_id
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
        selector_hint = str(full_selector or selector or "")
        value_preview = str(action_value or "")
        if len(value_preview) > 80:
            value_preview = value_preview[:77] + "..."
        try:
            agent._log(
                "🚀 액션 실행 요청: "
                f"action={action_name}, "
                f"snapshot={getattr(agent, '_active_snapshot_id', '')}, "
                f"ref={ref_id or ''}, "
                f"selector_hint={selector_hint}, "
                f"value={value_preview}"
            )
        except Exception:
            pass
        agent._last_exec_result = execute_action(
            agent,
            action_name,
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=action_value,
        )
        try:
            state_change = getattr(agent._last_exec_result, "state_change", None)
            agent._log(
                "✅ 액션 실행 응답: "
                f"action={action_name}, "
                f"success={bool(getattr(agent._last_exec_result, 'success', False))}, "
                f"effective={bool(getattr(agent._last_exec_result, 'effective', False))}, "
                f"reason_code={str(getattr(agent._last_exec_result, 'reason_code', '') or '')}, "
                f"state_change={state_change if isinstance(state_change, dict) else {}}"
            )
        except Exception:
            pass
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
                try:
                    state_change = getattr(agent._last_exec_result, "state_change", None)
                    agent._log(
                        "🔁 액션 재시도 응답: "
                        f"action={action_name}, "
                        f"success={bool(getattr(agent._last_exec_result, 'success', False))}, "
                        f"effective={bool(getattr(agent._last_exec_result, 'effective', False))}, "
                        f"reason_code={str(getattr(agent._last_exec_result, 'reason_code', '') or '')}, "
                        f"state_change={state_change if isinstance(state_change, dict) else {}}"
                    )
                except Exception:
                    pass
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
            ActionType.PRESS,
            ActionType.HOVER,
            ActionType.SELECT,
        } and decision.element_id is None:
            agent._last_exec_result = ActionExecResult(
                success=False,
                effective=False,
                reason_code="missing_element_id",
                reason=f"{decision.action.value} 액션에는 element_id가 필요함",
            )
            return False, f"{decision.action.value} 액션에는 element_id가 필요함"
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
        if decision.action in {ActionType.CLICK, ActionType.FILL, ActionType.PRESS} and agent._is_ref_temporarily_blocked(ref_id):
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
            elif (
                selected_element is not None
                and str(getattr(agent, "_goal_policy_phase", "") or "").strip() in {"reveal_destination_surface", "act_on_target", "verify_removal", "verify_empty"}
                and str(getattr(getattr(agent, "_goal_semantics", None), "goal_kind", "") or "") in {"remove_from_list", "clear_list"}
                and str(getattr(getattr(agent, "_last_exec_result", None), "reason_code", "") or "") == "not_actionable"
            ):
                container_ref = str(getattr(selected_element, "container_ref_id", "") or "").strip()
                if container_ref:
                    agent._active_scoped_container_ref = container_ref
                    try:
                        agent._record_reason_code("row_secondary_affordance_scope")
                    except Exception:
                        pass
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
            return _execute_with_ref_recovery("press", action_value=decision.value or "Enter")

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

    use_ref_protocol = bool(
        ref_id
        and agent._active_snapshot_id
        and action in {"click", "fill", "press", "hover", "scroll", "scrollIntoView", "select"}
    )
    is_element_action = action in {
        "click",
        "fill",
        "press",
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
                "selector": full_selector or selector or "",
                "action": "scroll",
                "value": value,
                "url": url or "",
            }
            request_action = "execute_action"
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
        try:
            value_preview = str(params.get("value", ""))
            if len(value_preview) > 80:
                value_preview = value_preview[:77] + "..."
            agent._log(
                "📡 MCP execute 호출: "
                f"request_action={request_action}, "
                f"action={action}, "
                f"timeout={request_timeout}, "
                f"snapshot={params.get('snapshot_id', '')}, "
                f"ref={params.get('ref_id', '')}, "
                f"selector={params.get('selector_hint', params.get('selector', ''))}, "
                f"value={value_preview}"
            )
        except Exception:
            pass

        def _post_execute():
            return requests.post(
                f"{agent.mcp_host_url}/execute",
                json={"action": request_action, "params": params},
                timeout=request_timeout,
            )

        try:
            response = _post_execute()
        except Exception as exc:
            if (
                is_mcp_transport_error(str(exc))
                and recover_mcp_host(agent, context=f"action:{request_action}")
            ):
                response = _post_execute()
            else:
                raise
        try:
            data = response.json()
        except Exception:
            data = {"error": response.text or "invalid_json_response"}
        try:
            detail_preview = str(data.get("detail") or data.get("error") or "")
            if len(detail_preview) > 160:
                detail_preview = detail_preview[:157] + "..."
            agent._log(
                "📨 MCP execute 응답: "
                f"request_action={request_action}, "
                f"status={response.status_code}, "
                f"success={bool(data.get('success'))}, "
                f"effective={bool(data.get('effective', True))}, "
                f"detail={detail_preview}"
            )
        except Exception:
            pass

        if response.status_code >= 400:
            status_family = "http_4xx" if 400 <= response.status_code < 500 else "http_5xx"
            detail_raw = data.get("detail")
            if isinstance(detail_raw, dict):
                reason_code, detail = extract_reason_fields({"detail": detail_raw}, response.status_code)
            else:
                reason_code = status_family
                detail = str(data.get("detail") or data.get("error") or response.reason or "HTTP error")
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
