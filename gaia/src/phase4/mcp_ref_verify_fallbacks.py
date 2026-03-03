from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


async def run_verify_fallback_chain(
    *,
    verify_for_action: bool,
    effective: bool,
    action: str,
    close_like_click: bool,
    page: Any,
    locator: Any,
    requested_meta: Optional[Dict[str, Any]],
    requested_snapshot: Optional[Dict[str, Any]],
    modal_regions: Optional[List[Dict[str, float]]],
    ref_id: str,
    attempt_idx: int,
    mode: str,
    resolved_selector: str,
    frame_index: Optional[int],
    state_change: Dict[str, Any],
    attempt_logs: List[Dict[str, Any]],
    deadline_exceeded_fn: Callable[[], bool],
    collect_state_change_probe_fn: Callable[..., Awaitable[Dict[str, Any]]],
    capture_close_diagnostic_fn: Callable[..., Awaitable[None]],
    attempt_close_ref_fallbacks_fn: Callable[..., Awaitable[Dict[str, Any]]],
    attempt_backdrop_close_fn: Callable[..., Awaitable[Dict[str, Any]]],
    attempt_modal_corner_close_fn: Callable[..., Awaitable[Dict[str, Any]]],
    try_click_hit_target_from_point_fn: Callable[..., Awaitable[Dict[str, Any]]],
    try_click_container_ancestor_fn: Callable[[Any, Any], Awaitable[Dict[str, Any]]],
    collect_close_ref_candidates_fn: Callable[..., List[Tuple[str, Dict[str, Any]]]],
    build_ref_candidates_fn: Callable[[Dict[str, Any]], List[Tuple[str, str]]],
    resolve_locator_from_ref_fn: Callable[..., Awaitable[Tuple[Any, Any, Any, Any]]],
) -> Dict[str, Any]:
    current_effective = bool(effective)
    current_state_change = state_change
    current_ref_id = ref_id
    current_requested_meta = requested_meta
    timed_out = False

    if verify_for_action and not current_effective and action in {"click", "press"}:
        scroll_probes: List[Tuple[str, str]] = [
            ("top", "window.scrollTo(0, 0)"),
            (
                "mid",
                "window.scrollTo(0, Math.max(0, Math.floor(((document.documentElement && document.documentElement.scrollHeight) || 0) * 0.5)))",
            ),
            (
                "bottom",
                "window.scrollTo(0, Math.max(0, ((document.documentElement && document.documentElement.scrollHeight) || 0)))",
            ),
        ]
        for probe_name, probe_script in scroll_probes:
            if deadline_exceeded_fn():
                timed_out = True
                break
            try:
                await page.evaluate(probe_script)
            except Exception:
                pass
            await page.wait_for_timeout(250)
            current_state_change = await collect_state_change_probe_fn(
                probe_wait_ms=1500,
                probe_scroll=probe_name,
            )
            current_effective = bool(current_state_change.get("effective", True))
            if current_effective:
                break

    if (
        verify_for_action
        and not current_effective
        and close_like_click
        and not timed_out
        and not deadline_exceeded_fn()
    ):
        close_ref_result = await attempt_close_ref_fallbacks_fn(
            close_like_click=close_like_click,
            page=page,
            attempt_idx=attempt_idx,
            mode=mode,
            ref_id=current_ref_id,
            requested_meta=current_requested_meta if isinstance(current_requested_meta, dict) else None,
            requested_snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
            attempt_logs=attempt_logs,
            deadline_exceeded_fn=deadline_exceeded_fn,
            collect_close_ref_candidates_fn=collect_close_ref_candidates_fn,
            build_ref_candidates_fn=build_ref_candidates_fn,
            resolve_locator_from_ref_fn=resolve_locator_from_ref_fn,
            collect_state_change_probe_fn=collect_state_change_probe_fn,
        )
        current_effective = bool(close_ref_result.get("success"))
        if current_effective:
            current_state_change = close_ref_result.get("state_change") or current_state_change
            current_ref_id = str(close_ref_result.get("ref_id") or current_ref_id)
            updated_meta = close_ref_result.get("requested_meta")
            if isinstance(updated_meta, dict):
                current_requested_meta = updated_meta
        else:
            await capture_close_diagnostic_fn("verify_alternate_ref_failed")

    if (
        verify_for_action
        and not current_effective
        and close_like_click
        and not timed_out
        and not deadline_exceeded_fn()
    ):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
            current_state_change = await collect_state_change_probe_fn(
                probe_wait_ms=250,
                probe_scroll="escape_fallback",
            )
            current_effective = bool(current_state_change.get("effective", True))
        except Exception:
            pass
        if not current_effective:
            await capture_close_diagnostic_fn("verify_escape_failed")

    if (
        verify_for_action
        and not current_effective
        and close_like_click
        and not timed_out
        and not deadline_exceeded_fn()
    ):
        backdrop_result = await attempt_backdrop_close_fn(
            close_like_click=close_like_click,
            page=page,
            attempt_idx=attempt_idx,
            mode=mode,
            attempt_logs=attempt_logs,
            deadline_exceeded_fn=deadline_exceeded_fn,
            collect_state_change_probe_fn=collect_state_change_probe_fn,
        )
        current_effective = bool(backdrop_result.get("success"))
        if current_effective:
            current_state_change = backdrop_result.get("state_change") or current_state_change
        else:
            await capture_close_diagnostic_fn("verify_backdrop_failed")

    if (
        verify_for_action
        and not current_effective
        and close_like_click
        and not timed_out
        and not deadline_exceeded_fn()
    ):
        modal_corner_result = await attempt_modal_corner_close_fn(
            close_like_click=close_like_click,
            page=page,
            attempt_idx=attempt_idx,
            mode=mode,
            attempt_logs=attempt_logs,
            deadline_exceeded_fn=deadline_exceeded_fn,
            collect_state_change_probe_fn=collect_state_change_probe_fn,
            modal_regions=modal_regions if isinstance(modal_regions, list) else None,
        )
        current_effective = bool(modal_corner_result.get("success"))
        if current_effective:
            current_state_change = modal_corner_result.get("state_change") or current_state_change
        else:
            await capture_close_diagnostic_fn("verify_modal_corner_failed")

    if (
        verify_for_action
        and not current_effective
        and action == "click"
        and not timed_out
        and not deadline_exceeded_fn()
    ):
        hit_fallback = await try_click_hit_target_from_point_fn(
            page,
            locator,
            current_requested_meta if isinstance(current_requested_meta, dict) else None,
            close_like_click=close_like_click,
        )
        if bool(hit_fallback.get("clicked")):
            await page.wait_for_timeout(250)
            current_state_change = await collect_state_change_probe_fn(
                probe_wait_ms=250,
                probe_scroll="hit_target_fallback",
            )
            post_watch = (
                hit_fallback.get("post_watch")
                if isinstance(hit_fallback.get("post_watch"), dict)
                else {}
            )
            if isinstance(current_state_change, dict) and post_watch:
                current_state_change["post_watch"] = post_watch
                if bool(post_watch.get("nav_detected")) or bool(post_watch.get("popup_detected")) or bool(post_watch.get("dialog_detected")):
                    current_state_change["resnapshot_required"] = True
                    current_state_change["strong_signal"] = "post_watch"
            current_effective = bool(current_state_change.get("effective", True))
            strong_signal = bool(post_watch.get("nav_detected")) or bool(post_watch.get("popup_detected")) or bool(post_watch.get("dialog_detected"))
            popup_risk = close_like_click and bool(post_watch.get("popup_detected"))
            if strong_signal and not close_like_click:
                current_effective = True
            if popup_risk:
                current_effective = False
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": resolved_selector,
                    "frame_index": frame_index,
                    "reason_code": "ok" if current_effective else "no_state_change",
                    "fallback": "hit_target_click",
                    "fallback_selector": str(hit_fallback.get("selector") or ""),
                    "fallback_reason": str(hit_fallback.get("reason") or ""),
                    "fallback_confidence": hit_fallback.get("confidence"),
                    "fallback_risk_flags": hit_fallback.get("risk_flags"),
                    "fallback_click_x": hit_fallback.get("clickX"),
                    "fallback_click_y": hit_fallback.get("clickY"),
                    "post_watch": hit_fallback.get("post_watch"),
                    "fallback_meta": hit_fallback,
                    "state_change": current_state_change,
                }
            )
            if close_like_click and not current_effective:
                await capture_close_diagnostic_fn(
                    "verify_hit_target_no_state_change",
                    extra={"fallback_reason": str(hit_fallback.get("reason") or "")},
                )
        else:
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": resolved_selector,
                    "frame_index": frame_index,
                    "reason_code": "not_actionable",
                    "fallback": "hit_target_click",
                    "fallback_error": str(
                        hit_fallback.get("error")
                        or hit_fallback.get("reason")
                        or "hit_target_not_clicked"
                    ),
                    "fallback_confidence": hit_fallback.get("confidence"),
                    "fallback_risk_flags": hit_fallback.get("risk_flags"),
                    "fallback_click_x": hit_fallback.get("clickX"),
                    "fallback_click_y": hit_fallback.get("clickY"),
                    "post_watch": hit_fallback.get("post_watch"),
                    "fallback_meta": hit_fallback,
                }
            )
            if close_like_click:
                await capture_close_diagnostic_fn(
                    "verify_hit_target_not_clicked",
                    extra={
                        "fallback_error": str(
                            hit_fallback.get("error")
                            or hit_fallback.get("reason")
                            or ""
                        )
                    },
                )

    if (
        verify_for_action
        and not current_effective
        and action == "click"
        and not timed_out
        and not deadline_exceeded_fn()
    ):
        fallback_result = await try_click_container_ancestor_fn(page, locator)
        if bool(fallback_result.get("clicked")):
            await page.wait_for_timeout(350)
            current_state_change = await collect_state_change_probe_fn(
                probe_wait_ms=350,
                probe_scroll="container_fallback",
                ancestor_click_fallback=True,
                ancestor_click_selector=str(fallback_result.get("selector") or ""),
            )
            current_effective = bool(current_state_change.get("effective", True))
            if close_like_click and not current_effective:
                await capture_close_diagnostic_fn("verify_container_fallback_failed")

    return {
        "effective": current_effective,
        "state_change": current_state_change,
        "ref_id": current_ref_id,
        "requested_meta": current_requested_meta,
        "timed_out": timed_out,
    }
