from __future__ import annotations

from typing import Any, Mapping

from gaia.src.phase4.mcp_execute_dispatch import execute_action_dispatch
from gaia.src.phase4.mcp_handler_registry import build_registered_browser_handlers


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
