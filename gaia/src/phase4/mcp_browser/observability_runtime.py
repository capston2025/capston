from __future__ import annotations

import base64
import time
from pathlib import Path
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


def _is_retryable_page_detach_error(exc: BaseException) -> bool:
    message = str(exc or "").strip().lower()
    if not message:
        return False
    return (
        "frame has been detached" in message
        or "target page, context or browser has been closed" in message
    )


async def _goto_with_retry(target_page: Any, target_url: str, *, timeout: int) -> None:
    try:
        await target_page.goto(target_url, timeout=timeout)
    except Exception as exc:
        if not _is_retryable_page_detach_error(exc):
            raise
        await target_page.wait_for_timeout(150)
        await target_page.goto(target_url, timeout=timeout)


async def _screenshot_with_retry(target_page: Any, **kwargs: Any) -> bytes:
    try:
        return await target_page.screenshot(**kwargs)
    except Exception as exc:
        if not _is_retryable_page_detach_error(exc):
            raise
        await target_page.wait_for_timeout(150)
        return await target_page.screenshot(**kwargs)


async def browser_screenshot(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    url = str(_pick(params, "url") or "")
    full_page = _as_bool(_pick(params, "full_page", _pick(params, "fullPage", False)), False)
    image_type = str(_pick(params, "type") or "png").strip().lower()
    if image_type not in {"png", "jpeg", "webp"}:
        image_type = "png"
    quality_raw = _pick(params, "quality")
    quality = None
    if quality_raw is not None and str(quality_raw).strip():
        try:
            quality = max(1, min(100, int(quality_raw)))
        except Exception:
            quality = None
    output_path = str(_pick(params, "path") or "")

    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    if url:
        current = ctx["normalize_url"](page.url)
        target = ctx["normalize_url"](url)
        if current != target:
            await _goto_with_retry(page, url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

    screenshot_kwargs: Dict[str, Any] = {"full_page": full_page, "type": image_type}
    if quality is not None and image_type in {"jpeg", "webp"}:
        screenshot_kwargs["quality"] = quality
    screenshot_bytes = await _screenshot_with_retry(page, **screenshot_kwargs)
    screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

    saved_path = ""
    if output_path:
        screenshot_root = (Path.home() / ".gaia" / "screenshots").resolve()
        screenshot_root.mkdir(parents=True, exist_ok=True)
        requested = Path(output_path).expanduser().resolve()
        if not requested.is_relative_to(screenshot_root):
            return ctx["build_error"]("not_actionable", f"screenshot path must be under {screenshot_root}")
        requested.parent.mkdir(parents=True, exist_ok=True)
        requested.write_bytes(screenshot_bytes)
        saved_path = str(requested)

    session.current_url = page.url
    tab_idx = ctx["get_tab_index"](page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "current_url": page.url,
        "screenshot": screenshot_base64,
        "mime_type": f"image/{image_type}",
        "saved_path": saved_path,
        "meta": {"full_page": full_page, "type": image_type, "quality": quality},
    }


async def browser_pdf(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    url = str(_pick(params, "url") or "")
    output_path = str(_pick(params, "path") or "")
    fmt = str(_pick(params, "format") or "A4")
    landscape = _as_bool(_pick(params, "landscape", False), False)
    print_background = _as_bool(_pick(params, "printBackground", _pick(params, "print_background", True)), True)
    scale_raw = _pick(params, "scale")
    scale = None
    if scale_raw is not None and str(scale_raw).strip():
        try:
            scale = max(0.1, min(2.0, float(scale_raw)))
        except Exception:
            scale = None
    margin = _pick(params, "margin")
    margin_dict = margin if isinstance(margin, dict) else None

    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    if url:
        current = ctx["normalize_url"](page.url)
        target = ctx["normalize_url"](url)
        if current != target:
            await _goto_with_retry(page, url, timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

    pdf_root = (Path.home() / ".gaia" / "pdf").resolve()
    pdf_root.mkdir(parents=True, exist_ok=True)
    if output_path:
        requested = Path(output_path).expanduser().resolve()
        if not requested.is_relative_to(pdf_root):
            return ctx["build_error"]("not_actionable", f"pdf path must be under {pdf_root}")
        final_path = requested
    else:
        final_path = (pdf_root / f"{session_id}_{int(time.time())}.pdf").resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_kwargs: Dict[str, Any] = {
        "path": str(final_path),
        "format": fmt,
        "landscape": landscape,
        "print_background": print_background,
    }
    if scale is not None:
        pdf_kwargs["scale"] = scale
    if margin_dict is not None:
        pdf_kwargs["margin"] = margin_dict
    await page.pdf(**pdf_kwargs)

    session.current_url = page.url
    tab_idx = ctx["get_tab_index"](page)
    return {
        "success": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "current_url": page.url,
        "path": str(final_path),
        "meta": {
            "format": fmt,
            "landscape": landscape,
            "print_background": print_background,
            "scale": scale,
        },
    }


async def browser_console_get(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    session, _ = await ctx["resolve_session_page"](session_id, tab_id=_pick(params, "tab_id", _pick(params, "targetId")))
    limit = int(_pick(params, "limit") or 100)
    level = str(_pick(params, "level") or "")
    tab_idx = ctx["get_tab_index"](session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "items": session.observability.get_console(limit=limit, level=level),
        "meta": {"limit": limit, "level": level},
    }


async def browser_errors_get(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    session, _ = await ctx["resolve_session_page"](session_id, tab_id=_pick(params, "tab_id", _pick(params, "targetId")))
    limit = int(_pick(params, "limit") or 100)
    tab_idx = ctx["get_tab_index"](session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "items": session.observability.get_errors(limit=limit),
        "meta": {"limit": limit},
    }


async def browser_requests_get(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    session, _ = await ctx["resolve_session_page"](session_id, tab_id=_pick(params, "tab_id", _pick(params, "targetId")))
    limit = int(_pick(params, "limit") or 100)
    url_contains = str(_pick(params, "url_contains") or "")
    pattern = str(_pick(params, "pattern") or _pick(params, "filter") or "")
    method = str(_pick(params, "method") or "")
    resource_type = str(_pick(params, "resource_type") or "")
    clear_raw = _pick(params, "clear", False)
    clear = clear_raw.strip().lower() in {"1", "true", "yes", "on"} if isinstance(clear_raw, str) else bool(clear_raw)
    status = _pick(params, "status")
    status_int = int(status) if isinstance(status, (int, str)) and str(status).strip() else None
    if clear:
        session.observability.clear_requests()
    items = session.observability.get_requests(
        limit=limit,
        url_contains=url_contains,
        pattern=pattern,
        method=method,
        resource_type=resource_type,
        status=status_int,
    )
    tab_idx = ctx["get_tab_index"](session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "items": items,
        "meta": {
            "limit": limit,
            "url_contains": url_contains,
            "pattern": pattern,
            "method": method,
            "resource_type": resource_type,
            "status": status_int,
            "clear": clear,
        },
    }


async def browser_response_body(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    session, _ = await ctx["resolve_session_page"](session_id, tab_id=_pick(params, "tab_id", _pick(params, "targetId")))
    request_id = str(_pick(params, "request_id") or "")
    url = str(_pick(params, "url") or "")
    url_contains = str(_pick(params, "url_contains") or "")
    pattern = str(_pick(params, "pattern") or _pick(params, "filter") or "")
    method = str(_pick(params, "method") or "")
    max_chars_raw = _pick(params, "max_chars", _pick(params, "maxChars"))
    max_chars = int(max_chars_raw) if isinstance(max_chars_raw, (int, str)) and str(max_chars_raw).strip() else 200_000
    result = await session.observability.get_response_body(
        request_id=request_id,
        url=url,
        url_contains=url_contains,
        pattern=pattern,
        method=method,
        max_chars=max_chars,
    )
    if not result.get("success"):
        return result
    tab_idx = ctx["get_tab_index"](session.page) if session.page else 0
    return {
        "success": True,
        "reason_code": "ok",
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "item": result.get("body", {}),
        "meta": {
            "request_id": request_id,
            "url": url,
            "url_contains": url_contains,
            "pattern": pattern,
            "method": method,
            "max_chars": max_chars,
        },
    }


async def browser_trace_start(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    tab_idx = ctx["get_tab_index"](page)
    if session.trace_active:
        return {
            "success": True,
            "reason_code": "ok",
            "active": True,
            "message": "trace already active",
            "tab_id": tab_idx,
            "targetId": tab_idx,
        }
    screenshots = _as_bool(_pick(params, "screenshots", True), True)
    snapshots = _as_bool(_pick(params, "snapshots", True), True)
    sources = _as_bool(_pick(params, "sources", True), True)
    await page.context.tracing.start(screenshots=screenshots, snapshots=snapshots, sources=sources)
    session.trace_active = True
    return {
        "success": True,
        "reason_code": "ok",
        "active": True,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "meta": {"screenshots": screenshots, "snapshots": snapshots, "sources": sources},
    }


async def browser_trace_stop(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    output_path = str(_pick(params, "path") or "")
    trace_root = (Path.home() / ".gaia" / "traces").resolve()
    trace_root.mkdir(parents=True, exist_ok=True)
    if output_path:
        requested = Path(output_path).expanduser().resolve()
        if not requested.is_relative_to(trace_root):
            return ctx["build_error"]("not_actionable", f"trace path must be under {trace_root}")
        final_path = requested
    else:
        final_path = (trace_root / f"{session_id}_{int(time.time())}.zip").resolve()
    final_path.parent.mkdir(parents=True, exist_ok=True)
    if session.trace_active:
        await page.context.tracing.stop(path=str(final_path))
        session.trace_active = False
        session.trace_path = str(final_path)
    tab_idx = ctx["get_tab_index"](page)
    return {
        "success": True,
        "reason_code": "ok",
        "active": False,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "path": str(final_path),
        "meta": {"trace_root": str(trace_root)},
    }
