from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional


def _coerce_scalar_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "textContains", "url", "loadState", "selector"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested
        return ""
    return ""


def _pick(params: Dict[str, Any], key: str, default: Any = None) -> Any:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    if key in params:
        return params.get(key)
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return default


async def browser_wait(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    tab_id = _pick(params, "tab_id", _pick(params, "targetId"))
    session, page = await ctx["resolve_session_page"](session_id, tab_id=tab_id)
    timeout_ms = int(_pick(params, "timeout_ms") or _pick(params, "timeoutMs") or 20000)
    selector = _coerce_scalar_str(_pick(params, "selector"))
    selector_state = _coerce_scalar_str(_pick(params, "selector_state") or "visible") or "visible"
    js_expr = _coerce_scalar_str(_pick(params, "js") or _pick(params, "fn"))
    target_url = _coerce_scalar_str(_pick(params, "url"))
    load_state = _coerce_scalar_str(_pick(params, "load_state") or _pick(params, "loadState"))
    text_contains = _coerce_scalar_str(_pick(params, "text"))
    text_gone = _coerce_scalar_str(_pick(params, "text_gone") or _pick(params, "textGone"))
    allowed_load_states = {"load", "domcontentloaded", "networkidle"}
    if load_state and load_state not in allowed_load_states:
        raise ctx["HTTPException"](
            status_code=400,
            detail={
                "reason_code": "invalid_input",
                "message": "load_state must be one of: load, domcontentloaded, networkidle",
            },
        )
    evaluate_enabled_raw = str(os.getenv("GAIA_BROWSER_EVALUATE_ENABLED", "true")).strip().lower()
    evaluate_enabled = evaluate_enabled_raw not in {"0", "false", "no", "off"}
    if js_expr and not evaluate_enabled:
        raise ctx["HTTPException"](
            status_code=403,
            detail={
                "reason_code": "not_actionable",
                "message": (
                    "wait --fn is disabled by config (browser.evaluateEnabled=false).\n"
                    "Docs: /gateway/configuration#browser-openclaw-managed-browser"
                ),
            },
        )
    time_ms = _pick(params, "time_ms", _pick(params, "timeMs"))
    explicit_time_ms: Optional[int] = None
    if isinstance(time_ms, (int, str)) and str(time_ms).strip():
        try:
            explicit_time_ms = max(0, int(time_ms))
            timeout_ms = max(timeout_ms, explicit_time_ms)
        except Exception:
            pass

    if (
        explicit_time_ms is None
        and not selector
        and not text_contains
        and not text_gone
        and not target_url
        and not load_state
        and not js_expr
    ):
        raise ctx["HTTPException"](
            status_code=400,
            detail={
                "reason_code": "invalid_input",
                "message": "wait requires at least one of: timeMs, text, textGone, selector, url, loadState, fn",
            },
        )

    has_wait_conditions = any((target_url, load_state, selector, text_contains, text_gone, js_expr))
    if explicit_time_ms is not None and not has_wait_conditions:
        await page.wait_for_timeout(explicit_time_ms)

    if target_url:
        current = ctx["normalize_url"](page.url)
        target = ctx["normalize_url"](target_url)
        if current != target:
            try:
                await page.goto(target_url, timeout=max(timeout_ms, 1000))
            except Exception as exc:
                message = str(exc or "").strip().lower()
                if (
                    "frame has been detached" not in message
                    and "target page, context or browser has been closed" not in message
                ):
                    raise
                await page.wait_for_timeout(150)
                await page.goto(target_url, timeout=max(timeout_ms, 1000))
    if load_state:
        await page.wait_for_load_state(load_state, timeout=timeout_ms)
    if selector:
        await page.locator(selector).first.wait_for(state=selector_state, timeout=timeout_ms)
    if text_contains:
        await page.locator(f"text={text_contains}").first.wait_for(state="visible", timeout=timeout_ms)
    if text_gone:
        await page.locator(f"text={text_gone}").first.wait_for(state="hidden", timeout=timeout_ms)
    if js_expr:
        start = time.time()
        ok = False
        while (time.time() - start) * 1000 < timeout_ms:
            try:
                if await page.evaluate(js_expr):
                    ok = True
                    break
            except Exception:
                pass
            await page.wait_for_timeout(200)
        if not ok:
            return ctx["build_error"]("not_found", "js condition not satisfied", timeout_ms=timeout_ms)

    session.current_url = page.url
    tab_idx = ctx["get_tab_index"](page)
    return {
        "success": True,
        "reason_code": "ok",
        "current_url": session.current_url,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "meta": {
            "selector": selector,
            "selector_state": selector_state,
            "text": text_contains,
            "text_gone": text_gone,
            "load_state": load_state,
            "js": bool(js_expr),
            "timeout_ms": timeout_ms,
        },
    }
