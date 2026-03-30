import base64
import os
import time
from typing import Any, Dict, List


async def browser_act(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    trace_started_at = time.perf_counter()
    trace_auth_submit_enabled = str(os.getenv("GAIA_TRACE_AUTH_SUBMIT", "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}

    def pick(key: str, default: Any = None) -> Any:
        if key in params:
            return params.get(key)
        if isinstance(payload, dict) and key in payload:
            return payload.get(key)
        return default

    http_exception = ctx["HTTPException"]
    session_id = str(pick("session_id", "default"))
    tab_id = pick("tab_id", pick("targetId"))
    selector_raw = pick("selector")
    selector_provided = bool(str(selector_raw or "").strip())
    raw_action = str(
        (payload.get("kind") if isinstance(payload, dict) else None)
        or params.get("kind")
        or (payload.get("action") if isinstance(payload, dict) else None)
        or params.get("action")
        or ""
    ).strip()
    action = raw_action
    force_double_click = False
    action_lower = action.lower()
    type_submit = False
    if action_lower in {"doubleclick", "dblclick"}:
        action = "click"
        force_double_click = True
    elif action_lower == "type":
        action = "fill"
        type_submit = bool(pick("submit", False))
    elif action_lower == "drag":
        action = "dragAndDrop"
    url = str(pick("url") or "")
    value = pick("value")
    if action == "fill" and value is None:
        text_value = pick("text")
        if text_value is not None:
            value = str(text_value)
    values = pick("values")
    fields = pick("fields")
    verify = bool(pick("verify", True))
    snapshot_id = str(pick("snapshot_id") or pick("snapshotId") or "")
    ref_id = str(pick("ref_id") or pick("refId") or pick("ref") or "")
    selector_hint = str(
        pick("selector_hint")
        or pick("selectorHint")
        or pick("selector")
        or ""
    )
    trace_auth_submit = any(
        token in selector_hint.lower()
        for token in ("로그인", "login", "sign in", "회원가입", "sign up", "register")
    )
    action_options: Dict[str, Any] = {}
    for option_key in ("timeoutMs", "timeout_ms", "doubleClick", "double_click", "button", "modifiers"):
        option_value = pick(option_key)
        if option_value is not None:
            action_options[option_key] = option_value
    if force_double_click:
        action_options["doubleClick"] = True

    if not action:
        raise http_exception(status_code=400, detail="action is required for 'browser_act'.")
    if selector_provided and action != "wait":
        raise http_exception(
            status_code=400,
            detail={
                "reason_code": "legacy_selector_forbidden",
                "message": "'selector' is not supported for /act. Use snapshot refs.",
            },
        )

    evaluate_enabled_raw = str(os.getenv("GAIA_BROWSER_EVALUATE_ENABLED", "true")).strip().lower()
    evaluate_enabled = evaluate_enabled_raw not in {"0", "false", "no", "off"}

    if action == "evaluate":
        eval_expr = pick("fn") if pick("fn") is not None else value
        if eval_expr is None or not str(eval_expr).strip():
            raise http_exception(
                status_code=400,
                detail={
                    "reason_code": "invalid_input",
                    "message": "fn is required for evaluate",
                },
            )
        if not evaluate_enabled:
            raise http_exception(
                status_code=403,
                detail={
                    "reason_code": "not_actionable",
                    "message": (
                        "evaluate is disabled by config (browser.evaluateEnabled=false).\n"
                        "Docs: /gateway/configuration#browser-openclaw-managed-browser"
                    ),
                },
            )
        value = eval_expr

    if action == "resize":
        width = pick("width")
        height = pick("height")
        if width is None or height is None:
            raise http_exception(
                status_code=400,
                detail={
                    "reason_code": "invalid_input",
                    "message": "width and height are required for resize",
                },
            )

    if action == "select" and values is None and (value is None or not str(value).strip()):
        raise http_exception(
            status_code=400,
            detail={
                "reason_code": "invalid_input",
                "message": "ref and values are required for select",
            },
        )

    if action == "fill" and isinstance(fields, list):
        if not snapshot_id:
            raise http_exception(
                status_code=400,
                detail={
                    "reason_code": "ref_required",
                    "message": "snapshot_id is required when using fill fields[]",
                },
            )
        field_results: List[Dict[str, Any]] = []
        for idx, field in enumerate(fields, start=1):
            if not isinstance(field, dict):
                continue
            field_ref = str(field.get("ref") or field.get("refId") or field.get("ref_id") or "")
            if not field_ref:
                raise http_exception(
                    status_code=400,
                    detail={
                        "reason_code": "ref_required",
                        "message": f"fields[{idx}] missing ref/refId",
                    },
                )
            field_type = str(field.get("type") or "text").strip().lower()
            field_value = field.get("value")
            if field_type in {"select", "dropdown"}:
                action_name = "select"
                action_value = field.get("values") if isinstance(field.get("values"), list) else field_value
            elif field_type in {"checkbox", "radio", "toggle", "switch"}:
                action_name = "setChecked"
                action_value = field_value
            else:
                action_name = "fill"
                action_value = "" if field_value is None else str(field_value)
            single_result = await ctx["execute_ref_action_with_snapshot"](
                session_id=session_id,
                snapshot_id=snapshot_id,
                ref_id=field_ref,
                action=action_name,
                value=action_value,
                options=action_options,
                url=url,
                selector_hint=selector_hint,
                verify=verify,
                tab_id=tab_id,
            )
            field_results.append(
                {
                    "index": idx,
                    "ref_id": field_ref,
                    "type": field_type,
                    "action": action_name,
                    "success": bool(single_result.get("success", False)),
                    "effective": bool(single_result.get("effective", False)),
                    "reason_code": str(single_result.get("reason_code") or "unknown_error"),
                    "reason": str(single_result.get("reason") or ""),
                }
            )
            if not bool(single_result.get("success", False)) or not bool(single_result.get("effective", False)):
                return {
                    "success": False,
                    "effective": False,
                    "reason_code": str(single_result.get("reason_code") or "unknown_error"),
                    "reason": str(single_result.get("reason") or "fill fields execution failed"),
                    "fields": field_results,
                    "snapshot_id_used": snapshot_id,
                }
        return {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "reason": "fill fields applied",
            "fields": field_results,
            "snapshot_id_used": snapshot_id,
        }

    if action == "select" and isinstance(values, list):
        normalized_values = [str(item).strip() for item in values if str(item).strip()]
        if normalized_values:
            value = normalized_values if len(normalized_values) > 1 else normalized_values[0]

    if ctx["is_element_action"](action):
        if not snapshot_id or not ref_id:
            raise http_exception(
                status_code=400,
                detail={
                    "reason_code": "ref_required",
                    "message": "snapshot_id + ref_id are required for element actions",
                },
            )
        if trace_auth_submit and trace_auth_submit_enabled:
            print(
                f"[trace_browser_act] start action={action} verify={verify} "
                f"ref_id={ref_id} selector_hint={selector_hint!r}"
            )
        ref_dispatch_started_at = time.perf_counter()
        result = await ctx["execute_ref_action_with_snapshot"](
            session_id=session_id,
            snapshot_id=snapshot_id,
            ref_id=ref_id,
            action=action,
            value=value,
            options=action_options,
            url=url,
            selector_hint=selector_hint,
            verify=verify,
            tab_id=tab_id,
        )
        if trace_auth_submit and trace_auth_submit_enabled:
            print(
                f"[trace_browser_act] ref_dispatch_ms={int((time.perf_counter() - ref_dispatch_started_at) * 1000)} "
                f"success={bool(result.get('success'))} effective={bool(result.get('effective', False))} "
                f"reason_code={result.get('reason_code')}"
            )
        if type_submit and bool(result.get("success")) and bool(result.get("effective")):
            press_result = await ctx["execute_ref_action_with_snapshot"](
                session_id=session_id,
                snapshot_id=snapshot_id,
                ref_id=ref_id,
                action="press",
                value="Enter",
                options=action_options,
                url=url,
                selector_hint=selector_hint,
                verify=verify,
                tab_id=tab_id,
            )
            if not bool(press_result.get("success")) or not bool(press_result.get("effective")):
                return press_result
            result = press_result
        result.setdefault("snapshot_id_used", snapshot_id)
        result.setdefault("ref_id_used", ref_id)
        result.setdefault("retry_path", [])
        result.setdefault("attempt_logs", [])
        result.setdefault("attempt_count", len(result.get("attempt_logs", [])))
        result.setdefault("state_change", {})
        if trace_auth_submit and trace_auth_submit_enabled:
            print(
                f"[trace_browser_act] total_ms={int((time.perf_counter() - trace_started_at) * 1000)} "
                f"return_reason={result.get('reason_code')}"
            )
        return result

    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    if action == "close":
        close_result = await ctx["browser_tabs_close"](
            {
                "session_id": session_id,
                "targetId": tab_id if tab_id is not None else ctx["get_tab_index"](page),
            }
        )
        ok = bool(close_result.get("success"))
        return {
            "success": ok,
            "effective": ok,
            "reason_code": str(close_result.get("reason_code") or ("ok" if ok else "failed")),
            "reason": str(close_result.get("reason") or ("tab closed" if ok else "tab close failed")),
            "state_change": {"effective": ok, "tab_closed": ok},
            "attempt_logs": [],
            "snapshot_id_used": snapshot_id,
            "ref_id_used": ref_id,
            "retry_path": [],
            "attempt_count": 0,
            "current_url": page.url,
            "tab": close_result.get("tab"),
            "tabs": close_result.get("tabs", []),
        }

    if action == "wait":
        wait_payload: Dict[str, Any] = {"session_id": session_id}
        if tab_id is not None:
            wait_payload["tab_id"] = tab_id
        if isinstance(value, dict):
            wait_payload.update(dict(value))
        for key in (
            "selector",
            "selector_state",
            "js",
            "fn",
            "url",
            "load_state",
            "loadState",
            "text",
            "text_gone",
            "textGone",
            "timeout_ms",
            "timeoutMs",
            "time_ms",
            "timeMs",
        ):
            picked = pick(key)
            if picked is not None:
                wait_payload[key] = picked
        if "loadState" in wait_payload and "load_state" not in wait_payload:
            wait_payload["load_state"] = wait_payload.pop("loadState")
        if "textGone" in wait_payload and "text_gone" not in wait_payload:
            wait_payload["text_gone"] = wait_payload.pop("textGone")
        if "timeoutMs" in wait_payload and "timeout_ms" not in wait_payload:
            wait_payload["timeout_ms"] = wait_payload.pop("timeoutMs")
        if "timeMs" in wait_payload and "time_ms" not in wait_payload:
            wait_payload["time_ms"] = wait_payload.pop("timeMs")
        if "fn" in wait_payload and "js" not in wait_payload:
            wait_payload["js"] = wait_payload.pop("fn")

        rich_wait_keys = {"selector", "js", "url", "load_state", "text", "text_gone", "time_ms"}
        if any(wait_payload.get(k) not in (None, "") for k in rich_wait_keys):
            return await ctx["browser_wait"](wait_payload)
        wait_ms: int
        if wait_payload.get("timeout_ms") not in (None, ""):
            try:
                wait_ms = max(0, int(wait_payload.get("timeout_ms")))
            except Exception:
                wait_ms = 500
        elif isinstance(value, (int, str)) and str(value).strip():
            try:
                wait_ms = max(0, int(value))
            except Exception:
                wait_ms = 500
        else:
            wait_ms = 500
        await page.wait_for_timeout(max(0, wait_ms))
        session.current_url = page.url
        screenshot_bytes = await page.screenshot(full_page=False)
        screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
        return {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "reason": "wait completed",
            "state_change": {"effective": True, "wait_ms": wait_ms},
            "attempt_logs": [],
            "snapshot_id_used": snapshot_id,
            "ref_id_used": ref_id,
            "retry_path": [],
            "attempt_count": 0,
            "current_url": session.current_url,
            "tab_id": ctx["get_tab_index"](page),
            "targetId": ctx["get_tab_index"](page),
            "screenshot": screenshot,
        }

    legacy = await ctx["execute_simple_action"](
        url=url,
        selector="",
        action=("setViewport" if action == "resize" else action),
        value=(
            value
            if action != "resize"
            else [pick("width"), pick("height")]
        ) if action != "evaluate" else (pick("fn") if pick("fn") is not None else value),
        session_id=session_id,
        before_screenshot=None,
        action_options=action_options,
    )
    ok = bool(legacy.get("success"))
    reason = str(legacy.get("message") or legacy.get("reason") or "")
    reason_code = str(legacy.get("reason_code") or ("ok" if ok else "failed"))
    effective = bool(legacy.get("effective", ok))
    return {
        "success": ok,
        "effective": effective,
        "reason_code": reason_code,
        "reason": reason or ("ok" if ok else "action_failed"),
        "state_change": {"effective": effective},
        "attempt_logs": [],
        "snapshot_id_used": snapshot_id,
        "ref_id_used": ref_id,
        "retry_path": [],
        "attempt_count": 0,
        "current_url": legacy.get("current_url", page.url),
        "tab_id": ctx["get_tab_index"](page),
        "targetId": ctx["get_tab_index"](page),
        "screenshot": legacy.get("screenshot"),
    }
