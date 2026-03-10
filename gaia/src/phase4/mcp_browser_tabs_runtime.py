from __future__ import annotations

from typing import Any, Dict


def _pick(params: Dict[str, Any], key: str, default: Any = None) -> Any:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    if key in params:
        return params.get(key)
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return default


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default
    if value is None:
        return default
    return bool(value)


async def browser_start(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(params.get("session_id", "default"))
    url = str(params.get("url") or "")
    tab_id = params.get("tab_id")
    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    if url:
        current = ctx["normalize_url"](page.url)
        target = ctx["normalize_url"](url)
        if current != target:
            await page.goto(url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
    session.current_url = page.url
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": ctx["get_tab_index"](page),
        "targetId": ctx["get_tab_index"](page),
        "current_url": page.url,
    }


async def browser_install(_params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    installed = bool(ctx["playwright_instance"]())
    return {
        "success": installed,
        "reason_code": "ok" if installed else "not_found",
        "installed": installed,
        "message": "Playwright initialized" if installed else "Playwright not initialized",
        "hint": "python -m playwright install chromium",
    }


async def browser_profiles(_params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "reason_code": "ok",
        "profiles": [{"profile_id": "default", "name": "default", "sessions": sorted(ctx["active_sessions"].keys())}],
    }


async def browser_tabs(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    tabs = await ctx["tabs_payload_async"](session, list(page.context.pages))
    current_tab_id = ctx["get_tab_index"](page)
    current_target_id = await ctx["get_page_target_id"](page)
    current_tab_payload = await ctx["tab_payload_async"](session, page, current_tab_id)
    return {
        "success": True,
        "reason_code": "ok",
        "running": True,
        "session_id": session_id,
        "tabs": tabs,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
        "tab": current_tab_payload,
    }


async def browser_tabs_open(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    url = str(_pick(params, "url") or "")
    activate = _as_bool(_pick(params, "activate", True), True)
    session, page = await ctx["resolve_session_page"](session_id)
    context = page.context
    new_page = await context.new_page()
    session.observability.attach_page(new_page)
    if url:
        await new_page.goto(url, timeout=60000)
        try:
            await new_page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
    if activate:
        session.page = new_page
        session.dialog_listener_armed = False
        session.file_chooser_listener_armed = False
    current_page = session.page or new_page
    session.observability.attach_page(current_page)
    tabs = await ctx["tabs_payload_async"](session, list(context.pages))
    current_tab_id = ctx["get_tab_index"](current_page)
    current_target_id = await ctx["get_page_target_id"](current_page)
    opened_tab_payload = await ctx["tab_payload_async"](session, new_page, ctx["get_tab_index"](new_page))
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab": opened_tab_payload,
        "tabs": tabs,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
    }


async def browser_tabs_focus(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    target_id_raw = _pick(params, "targetId", _pick(params, "tab_id", _pick(params, "index")))
    if target_id_raw is None or not str(target_id_raw).strip():
        return ctx["build_error"]("invalid_input", "targetId/tab_id/index is required for tabs.focus")
    try:
        session, focused_page = await ctx["resolve_session_page"](session_id, tab_id=target_id_raw)
    except ctx["HTTPException"] as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        extra: Dict[str, Any] = {}
        if isinstance(detail.get("matches"), list):
            extra["matches"] = detail.get("matches")
        return ctx["build_error"](
            str(detail.get("reason_code") or "not_found"),
            str(detail.get("message") or detail or "tab not found"),
            **extra,
        )
    tabs = await ctx["tabs_payload_async"](session, list(focused_page.context.pages))
    current_tab_id = ctx["get_tab_index"](focused_page)
    current_target_id = await ctx["get_page_target_id"](focused_page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
        "tab": await ctx["tab_payload_async"](session, focused_page, current_tab_id),
        "tabs": tabs,
    }


async def browser_tabs_close(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    pages = list(page.context.pages)
    target_raw = _pick(params, "targetId", _pick(params, "tab_id", _pick(params, "index")))
    if target_raw is None or not str(target_raw).strip():
        target_page = page
    else:
        status, _, resolved_page, matches = await ctx["resolve_page_from_tab_identifier"](
            pages,
            target_raw,
            session.browser,
        )
        if status == "ambiguous":
            return ctx["build_error"]("ambiguous_target_id", "ambiguous target id prefix", matches=matches)
        if status != "ok" or resolved_page is None:
            return ctx["build_error"]("not_found", f"tab not found: {target_raw}")
        target_page = resolved_page
    target_id = ctx["get_tab_index"](target_page)
    was_active = session.page is target_page
    await target_page.close()
    remaining = page.context.pages
    if not remaining:
        fallback_page = await page.context.new_page()
        remaining = [fallback_page]
    if was_active or session.page not in remaining:
        next_idx = min(target_id, len(remaining) - 1)
        session.page = remaining[next_idx]
        session.dialog_listener_armed = False
        session.file_chooser_listener_armed = False
    active_page = session.page or remaining[0]
    session.observability.attach_page(active_page)
    tabs = await ctx["tabs_payload_async"](session, list(active_page.context.pages))
    current_tab_id = ctx["get_tab_index"](active_page)
    current_target_id = await ctx["get_page_target_id"](active_page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "closed_tab_id": target_id,
        "current_tab_id": current_tab_id,
        "targetId": current_tab_id,
        "cdp_target_id": current_target_id,
        "current_url": active_page.url,
        "tab": await ctx["tab_payload_async"](session, active_page, current_tab_id),
        "tabs": tabs,
    }


async def browser_tabs_action(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    op = str(
        params.get("tab_action")
        or params.get("op")
        or params.get("action")
        or (payload.get("tab_action") if isinstance(payload, dict) else None)
        or (payload.get("op") if isinstance(payload, dict) else None)
        or (payload.get("action") if isinstance(payload, dict) else None)
        or "list"
    ).strip().lower()
    if op in {"list"}:
        return await browser_tabs(params, ctx)
    if op in {"new", "open"}:
        return await browser_tabs_open(params, ctx)
    if op in {"select", "focus"}:
        return await browser_tabs_focus(params, ctx)
    if op in {"close", "delete"}:
        return await browser_tabs_close(params, ctx)
    return ctx["build_error"]("invalid_input", "tabs.action must be one of: list|new|open|select|focus|close")
