"""Dispatcher for MCP /execute route."""
from __future__ import annotations

import traceback
from typing import Any, Awaitable, Callable, Dict

from fastapi import HTTPException

from gaia.src.phase4.mcp_action_aliases import ACTION_ALIASES


BrowserHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


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
        raise HTTPException(
            status_code=500,
            detail={
                "reason_code": "http_5xx",
                "message": f"{type(exc).__name__}: {exc}",
            },
        ) from exc
