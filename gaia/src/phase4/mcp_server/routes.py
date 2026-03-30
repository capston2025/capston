"""MCP host route and execute dispatch helpers."""
from __future__ import annotations

import asyncio
import traceback
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import HTTPException, WebSocket
from fastapi.websockets import WebSocketDisconnect

from gaia.src.phase4.mcp_server.action_aliases import ACTION_ALIASES, LEGACY_ACTIONS_NOT_NEEDING_SELECTOR
from gaia.src.phase4.mcp_browser.handlers import build_browser_handlers
from gaia.src.phase4.mcp_server.error_converter import to_ai_friendly_error
from gaia.src.phase4.mcp_server.openclaw_protocol import is_element_action, legacy_selector_forbidden


BrowserHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
HandlerSpec = Tuple[str, str]


HANDLER_SPECS: Sequence[HandlerSpec] = (
    ("browser_start", "_browser_start"),
    ("browser_install", "_browser_install"),
    ("browser_profiles", "_browser_profiles"),
    ("browser_tabs", "_browser_tabs"),
    ("browser_tabs_open", "_browser_tabs_open"),
    ("browser_tabs_focus", "_browser_tabs_focus"),
    ("browser_tabs_close", "_browser_tabs_close"),
    ("browser_tabs_action", "_browser_tabs_action"),
    ("browser_snapshot", "_browser_snapshot"),
    ("browser_act", "_browser_act"),
    ("browser_wait", "_browser_wait"),
    ("browser_screenshot", "_browser_screenshot"),
    ("browser_pdf", "_browser_pdf"),
    ("browser_console_get", "_browser_console_get"),
    ("browser_errors_get", "_browser_errors_get"),
    ("browser_requests_get", "_browser_requests_get"),
    ("browser_response_body", "_browser_response_body"),
    ("browser_trace_start", "_browser_trace_start"),
    ("browser_trace_stop", "_browser_trace_stop"),
    ("browser_highlight", "_browser_highlight"),
    ("browser_dialog_arm", "_browser_dialog_arm"),
    ("browser_file_chooser_arm", "_browser_file_chooser_arm"),
    ("browser_download_wait", "_browser_download_wait"),
    ("browser_state", "_browser_state"),
    ("browser_env", "_browser_env"),
)


def build_registered_browser_handlers(namespace: Mapping[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    missing: list[str] = []
    for public_name, local_name in HANDLER_SPECS:
        fn = namespace.get(local_name)
        if fn is None:
            missing.append(local_name)
            continue
        kwargs[public_name] = fn
    if missing:
        raise KeyError(f"Missing browser handler(s): {', '.join(sorted(missing))}")
    return build_browser_handlers(**kwargs)


async def execute_action_dispatch(
    *,
    request_action: str,
    params: Dict[str, Any],
    session_id: str,
    browser_handlers: Dict[str, BrowserHandler],
    close_session_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    mcp_request_cls: Any,
    handle_legacy_action_fn: Callable[..., Awaitable[Dict[str, Any] | None]],
    execute_simple_action_fn: Callable[..., Awaitable[Dict[str, Any]]],
    browser_act_fn: BrowserHandler,
    browser_console_get_fn: BrowserHandler,
    resolve_session_page_fn: Callable[[str], Awaitable[Any]],
    browser_snapshot_fn: BrowserHandler,
    capture_screenshot_fn: Callable[[str | None, str], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    try:
        action = ACTION_ALIASES.get(request_action, request_action)

        if action in browser_handlers:
            return await browser_handlers[action](params)

        if action == "browser_close":
            close_req = mcp_request_cls(action="close_session", params={"session_id": session_id})
            result = await close_session_fn(close_req)
            result.setdefault("reason_code", "ok" if result.get("success") else "not_found")
            return result

        legacy_result = await handle_legacy_action_fn(
            action=action,
            params=params,
            session_id=session_id,
            execute_simple_action_fn=execute_simple_action_fn,
            browser_act_fn=browser_act_fn,
            browser_console_get_fn=browser_console_get_fn,
            resolve_session_page_fn=resolve_session_page_fn,
            browser_snapshot_fn=browser_snapshot_fn,
            capture_screenshot_fn=capture_screenshot_fn,
        )
        if legacy_result is not None:
            return legacy_result

        raise HTTPException(status_code=400, detail=f"Action '{action}' not supported.")
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("reason_code"):
            raise
        normalized_code = "http_4xx" if 400 <= int(exc.status_code) < 500 else "http_5xx"
        message = str(detail if detail is not None else "HTTP error")
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "reason_code": normalized_code,
                "message": message,
            },
        ) from exc
    except Exception as exc:
        traceback.print_exc()
        friendly_msg = to_ai_friendly_error(exc)
        raise HTTPException(
            status_code=500,
            detail={
                "reason_code": "http_5xx",
                "message": friendly_msg,
            },
        ) from exc


async def dispatch_execute_action_route(
    *,
    request: Any,
    namespace: Mapping[str, Any],
    close_session_fn: Any,
    mcp_request_cls: Any,
    handle_legacy_action_fn: Any,
    execute_simple_action_fn: Any,
    browser_act_fn: Any,
    browser_console_get_fn: Any,
    resolve_session_page_fn: Any,
    browser_snapshot_fn: Any,
    capture_screenshot_fn: Any,
) -> Any:
    params = request.params
    session_id = params.get("session_id", "default")
    browser_handlers = build_registered_browser_handlers(namespace)
    return await execute_action_dispatch(
        request_action=request.action,
        params=params,
        session_id=session_id,
        browser_handlers=browser_handlers,
        close_session_fn=close_session_fn,
        mcp_request_cls=mcp_request_cls,
        handle_legacy_action_fn=handle_legacy_action_fn,
        execute_simple_action_fn=execute_simple_action_fn,
        browser_act_fn=browser_act_fn,
        browser_console_get_fn=browser_console_get_fn,
        resolve_session_page_fn=resolve_session_page_fn,
        browser_snapshot_fn=browser_snapshot_fn,
        capture_screenshot_fn=capture_screenshot_fn,
    )


async def handle_legacy_action(
    *,
    action: str,
    params: Dict[str, Any],
    session_id: str,
    execute_simple_action_fn: Callable[..., Awaitable[Dict[str, Any]]],
    browser_act_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    browser_console_get_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    resolve_session_page_fn: Callable[[str], Awaitable[Any]],
    browser_snapshot_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    capture_screenshot_fn: Callable[[str | None, str], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any] | None:
    """Handle legacy actions. Returns None when action is not legacy."""
    if action == "get_console_logs":
        level = str(params.get("type") or params.get("level") or "")
        limit = int(params.get("limit") or 100)
        data = await browser_console_get_fn(
            {"session_id": session_id, "level": level, "limit": limit}
        )
        return {"success": True, "logs": data.get("items", [])}

    if action == "get_current_url":
        _, page = await resolve_session_page_fn(session_id)
        return {"success": True, "url": page.url}

    if action in {"analyze_page", "snapshot_page"}:
        url = params.get("url")
        return await browser_snapshot_fn({"session_id": session_id, "url": url or ""})

    if action == "capture_screenshot":
        url = params.get("url")
        return await capture_screenshot_fn(url, session_id)

    if action == "execute_action":
        url = params.get("url")
        selector = params.get("selector", "")
        action_type = params.get("action")
        value = params.get("value")
        before_screenshot = params.get("before_screenshot")

        if not action_type:
            raise HTTPException(status_code=400, detail="action is required for 'execute_action'.")

        if is_element_action(action_type) or legacy_selector_forbidden(action_type, selector):
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "legacy_selector_forbidden",
                    "message": (
                        "legacy selector element actions are disabled. "
                        "use browser_snapshot + browser_act(snapshot_id, ref_id)."
                    ),
                },
            )

        if action_type not in LEGACY_ACTIONS_NOT_NEEDING_SELECTOR and not selector:
            raise HTTPException(
                status_code=400,
                detail=f"selector is required for action '{action_type}'.",
            )

        return await execute_simple_action_fn(
            url,
            selector,
            action_type,
            value,
            session_id,
            before_screenshot=before_screenshot,
        )

    if action == "execute_ref_action":
        snapshot_id = params.get("snapshot_id", "")
        ref_id = params.get("ref_id", "")
        action_type = params.get("action", "")
        value = params.get("value")
        url = params.get("url", "")
        tab_id = params.get("tab_id", params.get("targetId"))
        selector_hint = str(params.get("selector_hint", "") or "")
        verify = bool(params.get("verify", True))

        if not snapshot_id:
            raise HTTPException(status_code=400, detail="snapshot_id is required for 'execute_ref_action'.")
        if not ref_id:
            raise HTTPException(status_code=400, detail="ref_id is required for 'execute_ref_action'.")
        if not action_type:
            raise HTTPException(status_code=400, detail="action is required for 'execute_ref_action'.")

        return await browser_act_fn(
            {
                "session_id": session_id,
                "snapshot_id": snapshot_id,
                "ref_id": ref_id,
                "action": action_type,
                "value": value,
                "url": url,
                "tab_id": tab_id,
                "selector_hint": selector_hint,
                "verify": verify,
            }
        )

    if action == "execute_scenario":
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "legacy_selector_forbidden",
                "message": (
                    "execute_scenario legacy selector path is disabled. "
                    "use browser_snapshot + browser_act(snapshot_id, ref_id)."
                ),
            },
        )

    return None


async def close_session_impl(active_sessions: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    if session_id in active_sessions:
        session = active_sessions[session_id]
        try:
            await session.close()
        finally:
            active_sessions.pop(session_id, None)
        return {"success": True, "message": f"Session '{session_id}' closed"}
    return {"success": False, "message": f"Session '{session_id}' not found"}


async def websocket_screencast_loop(
    websocket: WebSocket,
    screencast_subscribers: List[WebSocket],
    get_current_frame: Callable[[], Optional[str]],
    logger: Any,
) -> None:
    await websocket.accept()
    screencast_subscribers.append(websocket)
    logger.info(
        "[WebSocket] New screencast subscriber connected (total: %s)",
        len(screencast_subscribers),
    )
    try:
        while True:
            data = await websocket.receive_text()
            if data == "get_current_frame":
                current = get_current_frame()
                if current:
                    await websocket.send_json(
                        {
                            "type": "screencast_frame",
                            "frame": current,
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
    except WebSocketDisconnect:
        logger.info("[WebSocket] Screencast subscriber disconnected")
    except Exception:
        logger.exception("[WebSocket] Error")
    finally:
        if websocket in screencast_subscribers:
            screencast_subscribers.remove(websocket)
        logger.info("[WebSocket] Subscriber removed (total: %s)", len(screencast_subscribers))


def build_root_payload(
    *,
    playwright_instance: Any,
    active_sessions: Dict[str, Any],
    screencast_subscribers: List[WebSocket],
) -> Dict[str, Any]:
    return {
        "message": "MCP Host is running.",
        "enabled": True,
        "profile": "default",
        "running": bool(playwright_instance),
        "chosenBrowser": "chromium",
        "headless": False,
        "active_sessions": len(active_sessions),
        "screencast_subscribers": len(screencast_subscribers),
        "screencast_active": any(s.screencast_active for s in active_sessions.values()),
    }
