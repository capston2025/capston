"""Legacy execute action dispatch helpers for MCP host."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from fastapi import HTTPException

from gaia.src.phase4.mcp_action_aliases import LEGACY_ACTIONS_NOT_NEEDING_SELECTOR
from gaia.src.phase4.openclaw_protocol import is_element_action, legacy_selector_forbidden


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

    if action == "analyze_page":
        url = params.get("url")
        return await browser_snapshot_fn({"session_id": session_id, "url": url or ""})

    if action == "snapshot_page":
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
