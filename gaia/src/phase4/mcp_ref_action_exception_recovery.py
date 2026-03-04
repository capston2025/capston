from __future__ import annotations

import base64
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple


async def handle_action_exception_recovery(
    *,
    action_exc: Exception,
    action: str,
    verify_for_action: bool,
    close_like_click: bool,
    deadline_exceeded_fn: Callable[[], bool],
    page: Any,
    locator: Any,
    attempt_idx: int,
    mode: str,
    resolved_selector: str,
    frame_index: Optional[int],
    ref_id: str,
    requested_meta: Optional[Dict[str, Any]],
    requested_snapshot: Optional[Dict[str, Any]],
    modal_regions: Optional[List[Dict[str, float]]],
    snapshot_id: str,
    retry_path: List[str],
    stale_recovered: bool,
    attempt_logs: List[Dict[str, Any]],
    state_change: Dict[str, Any],
    session: Any,
    collect_state_change_probe_fn: Callable[..., Awaitable[Dict[str, Any]]],
    capture_close_diagnostic_fn: Callable[..., Awaitable[None]],
    attempt_close_ref_fallbacks_fn: Callable[..., Awaitable[Dict[str, Any]]],
    attempt_backdrop_close_fn: Callable[..., Awaitable[Dict[str, Any]]],
    attempt_modal_corner_close_fn: Callable[..., Awaitable[Dict[str, Any]]],
    collect_close_ref_candidates_fn: Callable[..., List[Tuple[str, Dict[str, Any]]]],
    build_ref_candidates_fn: Callable[[Dict[str, Any]], List[Tuple[str, str]]],
    resolve_locator_from_ref_fn: Callable[..., Awaitable[Tuple[Any, Any, Any, Any]]],
    try_click_hit_target_from_point_fn: Callable[..., Awaitable[Dict[str, Any]]],
    build_fallback_success_response_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    current_state_change = state_change
    current_ref_id = ref_id
    current_requested_meta = requested_meta if isinstance(requested_meta, dict) else None

    if close_like_click and not deadline_exceeded_fn():
        close_ref_result = await attempt_close_ref_fallbacks_fn(
            close_like_click=close_like_click,
            page=page,
            attempt_idx=attempt_idx,
            mode=mode,
            ref_id=current_ref_id,
            requested_meta=current_requested_meta,
            requested_snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
            attempt_logs=attempt_logs,
            deadline_exceeded_fn=deadline_exceeded_fn,
            collect_close_ref_candidates_fn=collect_close_ref_candidates_fn,
            build_ref_candidates_fn=build_ref_candidates_fn,
            resolve_locator_from_ref_fn=resolve_locator_from_ref_fn,
            collect_state_change_probe_fn=collect_state_change_probe_fn,
        )
        if bool(close_ref_result.get("success")):
            current_state_change = close_ref_result.get("state_change") or current_state_change
            current_ref_id = str(close_ref_result.get("ref_id") or current_ref_id)
            updated_meta = close_ref_result.get("requested_meta")
            if isinstance(updated_meta, dict):
                current_requested_meta = updated_meta
            session.current_url = page.url
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return {
                "handled": True,
                "return_response": build_fallback_success_response_fn(
                    reason="close intent fallback via alternate close ref succeeded",
                    snapshot_id=snapshot_id,
                    ref_id=current_ref_id,
                    retry_path=retry_path,
                    attempt_count=attempt_idx,
                    state_change=current_state_change,
                    attempt_logs=attempt_logs,
                    current_url=page.url,
                    screenshot_base64=screenshot_base64,
                    stale_recovered=stale_recovered,
                ),
            }
        await capture_close_diagnostic_fn("alternate_ref_failed")
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
            current_state_change = await collect_state_change_probe_fn(
                probe_wait_ms=250,
                probe_scroll="escape_fallback",
            )
            escape_effective = bool(current_state_change.get("effective", True))
            if escape_effective:
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": mode,
                        "selector": resolved_selector,
                        "frame_index": frame_index,
                        "reason_code": "ok",
                        "fallback": "escape",
                        "state_change": current_state_change,
                    }
                )
                session.current_url = page.url
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                return {
                    "handled": True,
                    "return_response": build_fallback_success_response_fn(
                        reason="close intent fallback via Escape succeeded",
                        snapshot_id=snapshot_id,
                        ref_id=current_ref_id,
                        retry_path=retry_path,
                        attempt_count=attempt_idx,
                        state_change=current_state_change,
                        attempt_logs=attempt_logs,
                        current_url=page.url,
                        screenshot_base64=screenshot_base64,
                        stale_recovered=stale_recovered,
                    ),
                }
            await capture_close_diagnostic_fn("escape_no_state_change")
        except Exception:
            await capture_close_diagnostic_fn("escape_exception")

        backdrop_result = await attempt_backdrop_close_fn(
            close_like_click=close_like_click,
            page=page,
            attempt_idx=attempt_idx,
            mode=mode,
            attempt_logs=attempt_logs,
            deadline_exceeded_fn=deadline_exceeded_fn,
            collect_state_change_probe_fn=collect_state_change_probe_fn,
        )
        if bool(backdrop_result.get("success")):
            current_state_change = backdrop_result.get("state_change") or current_state_change
            session.current_url = page.url
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return {
                "handled": True,
                "return_response": build_fallback_success_response_fn(
                    reason="close intent fallback via backdrop click succeeded",
                    snapshot_id=snapshot_id,
                    ref_id=current_ref_id,
                    retry_path=retry_path,
                    attempt_count=attempt_idx,
                    state_change=current_state_change,
                    attempt_logs=attempt_logs,
                    current_url=page.url,
                    screenshot_base64=screenshot_base64,
                    stale_recovered=stale_recovered,
                ),
            }
        await capture_close_diagnostic_fn("backdrop_failed")

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
        if bool(modal_corner_result.get("success")):
            current_state_change = modal_corner_result.get("state_change") or current_state_change
            session.current_url = page.url
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return {
                "handled": True,
                "return_response": build_fallback_success_response_fn(
                    reason="close intent fallback via modal-corner click succeeded",
                    snapshot_id=snapshot_id,
                    ref_id=current_ref_id,
                    retry_path=retry_path,
                    attempt_count=attempt_idx,
                    state_change=current_state_change,
                    attempt_logs=attempt_logs,
                    current_url=page.url,
                    screenshot_base64=screenshot_base64,
                    stale_recovered=stale_recovered,
                ),
            }
        await capture_close_diagnostic_fn("modal_corner_failed")

    if action == "click" and not deadline_exceeded_fn():
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
            hit_effective = (
                bool(current_state_change.get("effective", True))
                if verify_for_action
                else True
            )
            strong_signal = bool(post_watch.get("nav_detected")) or bool(post_watch.get("popup_detected")) or bool(post_watch.get("dialog_detected"))
            popup_risk = close_like_click and bool(post_watch.get("popup_detected"))
            if strong_signal and not close_like_click:
                hit_effective = True
            if popup_risk:
                hit_effective = False
            if hit_effective:
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": mode,
                        "selector": resolved_selector,
                        "frame_index": frame_index,
                        "reason_code": "ok",
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
                session.current_url = page.url
                screenshot_bytes = await page.screenshot(full_page=False)
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                return {
                    "handled": True,
                    "return_response": build_fallback_success_response_fn(
                        reason="click fallback via hit-target mouse click succeeded",
                        snapshot_id=snapshot_id,
                        ref_id=current_ref_id,
                        retry_path=retry_path,
                        attempt_count=attempt_idx,
                        state_change=current_state_change,
                        attempt_logs=attempt_logs,
                        current_url=page.url,
                        screenshot_base64=screenshot_base64,
                        stale_recovered=stale_recovered,
                    ),
                }
            attempt_logs.append(
                {
                    "attempt": attempt_idx,
                    "mode": mode,
                    "selector": resolved_selector,
                    "frame_index": frame_index,
                    "reason_code": "no_state_change",
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
            await capture_close_diagnostic_fn(
                "hit_target_no_state_change",
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
            await capture_close_diagnostic_fn(
                "hit_target_not_clicked",
                extra={
                    "fallback_error": str(
                        hit_fallback.get("error")
                        or hit_fallback.get("reason")
                        or ""
                    )
                },
            )

    reason_code = "not_actionable"
    attempt_logs.append(
        {
            "attempt": attempt_idx,
            "mode": mode,
            "selector": resolved_selector,
            "frame_index": frame_index,
            "reason_code": reason_code,
            "error": str(action_exc),
        }
    )
    print(f"[execute_ref_action] step={attempt_idx} mode={mode} reason={reason_code}")
    return {
        "handled": True,
        "return_response": None,
        "reason_code": reason_code,
        "state_change": current_state_change,
        "ref_id": current_ref_id,
        "requested_meta": current_requested_meta,
        "continue_loop": True,
    }
