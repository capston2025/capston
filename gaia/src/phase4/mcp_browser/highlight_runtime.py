from __future__ import annotations

import base64
from typing import Any, Dict


def _pick(params: Dict[str, Any], key: str, default: Any = None) -> Any:
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    if key in params:
        return params.get(key)
    if isinstance(payload, dict) and key in payload:
        return payload.get(key)
    return default


async def browser_highlight(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    session_id = str(_pick(params, "session_id", "default"))
    session, page = await ctx["resolve_session_page"](session_id)
    selector = str(_pick(params, "selector") or "")
    snapshot_id = str(_pick(params, "snapshot_id") or "")
    ref_id = str(_pick(params, "ref_id") or _pick(params, "ref") or "")
    duration_ms = int(_pick(params, "duration_ms", 1200) or 1200)

    if selector:
        return ctx["build_error"](
            "legacy_selector_forbidden",
            "selector is not supported for highlight; use ref (and optional snapshot_id).",
        )
    if not ref_id:
        return ctx["build_error"]("ref_required", "ref is required for highlight.")
    if not snapshot_id:
        snapshot_id = str(session.current_snapshot_id or "")
    if not snapshot_id and session.snapshots:
        try:
            snapshot_id = max(
                session.snapshots.keys(),
                key=lambda sid: int((session.snapshots.get(sid) or {}).get("epoch") or 0),
            )
        except Exception:
            snapshot_id = next(iter(session.snapshots.keys()), "")
    if not snapshot_id:
        return ctx["build_error"]("snapshot_not_found", "snapshot_id is required for highlight.")

    locator = None
    snap = session.snapshots.get(snapshot_id)
    if not snap:
        return ctx["build_error"]("snapshot_not_found", f"snapshot not found: {snapshot_id}")
    meta = ctx["resolve_ref_meta_from_snapshot"](snap, ref_id)
    if not meta:
        return ctx["build_error"]("not_found", f"ref not found in snapshot: {ref_id}")
    candidates = ctx["build_ref_candidates"](meta)
    for _, cand in candidates:
        loc, _, _, _ = await ctx["resolve_locator_from_ref"](page, meta, cand)
        if loc is not None:
            locator = loc
            break
    if locator is None:
        return ctx["build_error"]("not_found", "target not found for highlight")

    await locator.evaluate(
        """
        (el, durationMs) => {
          const prevOutline = el.style.outline;
          const prevOffset = el.style.outlineOffset;
          el.style.outline = "3px solid #ff4d4f";
          el.style.outlineOffset = "2px";
          setTimeout(() => {
            el.style.outline = prevOutline;
            el.style.outlineOffset = prevOffset;
          }, durationMs);
          return true;
        }
        """,
        duration_ms,
    )
    screenshot_bytes = await page.screenshot(full_page=False)
    screenshot = base64.b64encode(screenshot_bytes).decode("utf-8")
    tab_idx = ctx["get_tab_index"](page)
    return {
        "success": True,
        "reason_code": "ok",
        "duration_ms": duration_ms,
        "tab_id": tab_idx,
        "targetId": tab_idx,
        "screenshot": screenshot,
    }
