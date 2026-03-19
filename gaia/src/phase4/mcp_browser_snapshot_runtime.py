import asyncio
from typing import Any, Dict, Optional


async def browser_snapshot(params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
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
    url = str(pick("url") or "")
    scope_container_ref_id = str(pick("scope_container_ref_id") or "").strip()
    snapshot_format = str(pick("format") or "").strip().lower()
    mode = str(pick("mode") or "").strip().lower()
    refs_mode = str(pick("refs", "ref") or "ref").strip().lower()
    labels = bool(pick("labels", False))
    if refs_mode not in {"ref", "role", "aria"}:
        refs_mode = "ref"
    if snapshot_format and snapshot_format not in {"ai", "aria", "role", "ref"}:
        raise http_exception(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "format must be one of: ai, aria, role, ref",
            },
        )

    if mode == "efficient" and snapshot_format == "aria":
        raise http_exception(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "mode=efficient is not allowed with format=aria",
            },
        )
    if labels and snapshot_format == "aria":
        raise http_exception(
            status_code=400,
            detail={
                "reason_code": "invalid_snapshot_options",
                "message": "labels require format=ai|role|ref",
            },
        )

    normalized_tab_id = ctx["coerce_tab_id"](tab_id)

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

    if normalized_tab_id is not None:
        session, page = await ctx["resolve_session_page"](session_id, tab_id=normalized_tab_id)
        if url:
            current = ctx["normalize_url"](page.url)
            target = ctx["normalize_url"](url)
            if current != target:
                await _goto_with_retry(page, url, timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
        snap = await ctx["snapshot_page"](
            url="",
            session_id=session_id,
            scope_container_ref_id=scope_container_ref_id,
        )
    else:
        snap = await ctx["snapshot_page"](
            url=url,
            session_id=session_id,
            scope_container_ref_id=scope_container_ref_id,
        )
        session, page = await ctx["resolve_session_page"](session_id)
    elements = snap.get("dom_elements") or snap.get("elements") or []
    elements_by_ref = ctx["extract_elements_by_ref"](snap)
    result = {
        "success": True,
        "ok": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": ctx["get_tab_index"](page),
        "targetId": ctx["get_tab_index"](page),
        "snapshot_id": snap.get("snapshot_id", ""),
        "epoch": int(snap.get("epoch") or 0),
        "dom_hash": str(snap.get("dom_hash") or ""),
        "mode": "ref",
        "format": snapshot_format or "ref",
        "elements": elements,
        "dom_elements": elements,
        "elements_by_ref": elements_by_ref,
        "current_url": page.url,
        "requested_scope_container_ref_id": scope_container_ref_id,
        "scope_container_ref_id": str(snap.get("scope_container_ref_id") or ""),
        "scope_applied": bool(snap.get("scope_applied")),
        "context_snapshot": snap.get("context_snapshot") if isinstance(snap.get("context_snapshot"), dict) else {},
        "role_snapshot": snap.get("role_snapshot") if isinstance(snap.get("role_snapshot"), dict) else {},
    }

    wants_text_snapshot = bool(snapshot_format in {"ai", "aria", "role"} or mode == "efficient")
    if wants_text_snapshot:
        interactive = bool(pick("interactive", mode == "efficient"))
        compact = bool(pick("compact", mode == "efficient"))
        limit = int(pick("limit") or 700)
        max_chars = int(pick("max_chars") or pick("maxChars") or 64000)
        timeout_ms = int(pick("timeout_ms") or pick("timeoutMs") or 5000)
        max_depth_raw = pick("max_depth", pick("maxDepth"))
        max_depth: Optional[int] = None
        if max_depth_raw is not None and str(max_depth_raw).strip() != "":
            try:
                max_depth = max(0, int(max_depth_raw))
            except Exception:
                max_depth = None
        selector = str(pick("selector") or "").strip()
        frame_filter = pick("frame")
        requested_format = snapshot_format or ("ai" if mode == "efficient" else "ref")

        if refs_mode == "aria" and (selector or frame_filter is not None):
            raise http_exception(
                status_code=400,
                detail={
                    "reason_code": "invalid_snapshot_options",
                    "message": "refs=aria does not support selector/frame snapshots yet.",
                },
            )

        filtered_elements = elements
        if selector:
            needle = selector.lower()
            filtered_elements = [
                el for el in filtered_elements
                if needle in str(el.get("selector") or "").lower()
                or needle in str(el.get("full_selector") or "").lower()
            ]
        if frame_filter is not None:
            try:
                frame_idx = int(frame_filter)
                filtered_elements = [
                    el for el in filtered_elements
                    if int(((el.get("scope") or {}).get("frame_index", el.get("frame_index", 0)) or 0)) == frame_idx
                ]
            except Exception:
                pass

        refs_from_elements = ctx["build_role_refs_from_elements"](filtered_elements)
        meta_base = {
            "selector": selector,
            "frame": frame_filter,
            "interactive": interactive,
            "compact": compact,
            "limit": limit,
            "max_chars": max_chars,
            "max_depth": max_depth,
            "timeout_ms": timeout_ms,
            "refs_mode_requested": refs_mode,
        }

        used_special_snapshot = False

        if requested_format in {"role", "aria"}:
            aria_text = ""
            try:
                target_locator = None
                if frame_filter is not None:
                    frame_idx = int(frame_filter)
                    frames = page.frames
                    if frame_idx < 0 or frame_idx >= len(frames):
                        raise ValueError(f"frame index out of range: {frame_idx}")
                    frame_obj = frames[frame_idx]
                    if selector:
                        target_locator = frame_obj.locator(selector).first
                    else:
                        target_locator = frame_obj.locator(":root")
                else:
                    if selector:
                        target_locator = page.locator(selector).first
                    else:
                        target_locator = page.locator(":root")
                aria_text = await target_locator.aria_snapshot(timeout=max(500, min(timeout_ms, 60000)))
            except Exception:
                aria_text = ""

            if isinstance(aria_text, str) and aria_text.strip():
                role_payload = ctx["build_role_snapshot_from_aria_text"](
                    aria_text,
                    interactive=interactive,
                    compact=compact,
                    max_depth=max_depth,
                    line_limit=max(1, min(limit, 2000)),
                    max_chars=max_chars,
                )
                role_refs = role_payload.get("refs") if isinstance(role_payload.get("refs"), dict) else {}
                effective_refs_mode = refs_mode
                if refs_mode == "aria":
                    effective_refs_mode = "role"
                result.update(
                    {
                        "format": requested_format,
                        "mode": mode or "full",
                        "refs_mode": effective_refs_mode,
                        "snapshot": role_payload.get("snapshot", ""),
                        "snapshot_lines": str(role_payload.get("snapshot", "")).split("\n"),
                        "snapshot_stats": role_payload.get("stats", {}),
                        "refs": role_refs,
                        "labels": [] if labels else None,
                        "labelsCount": 0 if labels else None,
                        "labelsSkipped": 0 if labels else None,
                        "meta": {**meta_base, "snapshot_source": "aria_snapshot"},
                    }
                )
                used_special_snapshot = True

        if (not used_special_snapshot) and requested_format in {"ai"}:
            ai_text = await ctx["try_snapshot_for_ai"](page, timeout_ms=timeout_ms)
            if isinstance(ai_text, str) and ai_text.strip():
                ai_payload = ctx["build_role_snapshot_from_ai_text"](
                    ai_text,
                    interactive=interactive,
                    compact=compact,
                    max_depth=max_depth,
                    line_limit=max(1, min(limit, 5000)),
                    max_chars=max_chars,
                )
                parsed_refs = ai_payload.get("refs") if isinstance(ai_payload.get("refs"), dict) else {}
                effective_refs = parsed_refs or refs_from_elements
                effective_refs_mode = "aria" if parsed_refs else "role"
                if refs_mode == "ref":
                    effective_refs_mode = "ref"
                result.update(
                    {
                        "format": requested_format,
                        "mode": mode or "full",
                        "refs_mode": effective_refs_mode,
                        "snapshot": ai_payload.get("snapshot", ""),
                        "snapshot_lines": str(ai_payload.get("snapshot", "")).split("\n"),
                        "snapshot_stats": ai_payload.get("stats", {}),
                        "refs": effective_refs,
                        "labels": [] if labels else None,
                        "labelsCount": 0 if labels else None,
                        "labelsSkipped": 0 if labels else None,
                        "meta": {**meta_base, "snapshot_source": "ai_snapshot"},
                    }
                )
                used_special_snapshot = True

        if not used_special_snapshot:
            text_payload = ctx["build_snapshot_text"](
                filtered_elements,
                interactive_only=interactive,
                compact=compact,
                limit=limit,
                max_chars=max_chars,
            )
            result.update(
                {
                    "format": requested_format,
                    "mode": mode or "full",
                    "refs_mode": refs_mode,
                    "snapshot": text_payload.get("text", ""),
                    "snapshot_lines": text_payload.get("lines", []),
                    "snapshot_stats": text_payload.get("stats", {}),
                    "refs": refs_from_elements,
                    "labels": [] if labels else None,
                    "labelsCount": 0 if labels else None,
                    "labelsSkipped": 0 if labels else None,
                    "meta": {**meta_base, "snapshot_source": "dom_elements"},
                }
            )
    result["ok"] = bool(result.get("success", True))
    return result
