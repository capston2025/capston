from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple



from gaia.src.phase4.mcp_ref_snapshot_helpers import (
    _collect_close_ref_candidates,
    _collect_modal_regions_from_snapshot,
    _is_close_intent_ref,
    _is_modal_corner_close_candidate,
)
from gaia.src.phase4.mcp_ref_close_fallbacks import (
    attempt_backdrop_close,
    attempt_close_ref_fallbacks,
    attempt_modal_corner_close,
)
from gaia.src.phase4.mcp_ref_action_exception_recovery import (
    handle_action_exception_recovery,
)
from gaia.src.phase4.mcp_ref_diagnostic_helpers import (
    capture_close_diagnostic,
)
from gaia.src.phase4.mcp_ref_response_helpers import (
    build_fallback_success_response,
    build_full_failure_response,
    build_full_success_response,
)
from gaia.src.phase4.mcp_ref_session_helpers import (
    resolve_session_page_for_ref_action,
)
from gaia.src.phase4.mcp_ref_execution_context import (
    prepare_ref_action_execution_context,
)
from gaia.src.phase4.mcp_ref_attempt_helpers import (
    append_attempt_timeout_log,
    resolve_locator_for_attempt,
)
from gaia.src.phase4.mcp_ref_before_state import (
    capture_before_state,
    unpack_before_state,
)
from gaia.src.phase4.mcp_ref_state_probe import (
    collect_state_change_probe,
)
from gaia.src.phase4.mcp_ref_action_snapshot_recovery import (
    recover_snapshot_ref_state,
)
from gaia.src.phase4.mcp_ref_action_transport import (
    goto_with_retry,
    safe_capture_page_screenshot_base64,
    safe_page_url,
)
from gaia.src.phase4.mcp_ref_verify_fallbacks import (
    run_verify_fallback_chain,
)
from gaia.src.phase4.mcp_error_converter import to_ai_friendly_error


from gaia.src.phase4.mcp_ref_action_result_context import (
    collect_ref_action_result_context,
)

from gaia.src.phase4.mcp_ref_action_attempt import (
    execute_ref_action_attempt,
)



async def execute_ref_action_with_snapshot_impl(
    *,
    session_id: str,
    snapshot_id: str,
    ref_id: str,
    action: str,
    value: Any = None,
    options: Optional[Dict[str, Any]] = None,
    url: str = "",
    selector_hint: str = "",
    verify: bool = True,
    tab_id: Optional[Any] = None,
    ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trace_started_at = time.perf_counter()
    trace_auth_submit_enabled = str(os.getenv("GAIA_TRACE_AUTH_SUBMIT", "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    if isinstance(ctx, dict):
        globals().update(ctx)
    if not playwright_instance:
        raise HTTPException(status_code=503, detail="Playwright is not initialized.")

    session, page = await resolve_session_page_for_ref_action(
        session_id=session_id,
        tab_id=tab_id,
        active_sessions=active_sessions,
        ensure_session_fn=ensure_session,
        playwright_getter_fn=_get_playwright_instance,
        screencast_subscribers=screencast_subscribers,
        frame_setter=_set_current_screencast_frame,
        logger=logger,
    )

    if url:
        current_normalized = normalize_url(page.url)
        requested_normalized = normalize_url(url)
        if current_normalized != requested_normalized:
            await goto_with_retry(page, url, timeout=60000, wait_for_networkidle=True)
            await page.wait_for_timeout(1000)

    try:
        max_action_seconds = float(os.getenv("GAIA_REF_ACTION_MAX_SECONDS", "45"))
    except Exception:
        max_action_seconds = 45.0
    max_action_seconds = max(10.0, min(120.0, max_action_seconds))
    action_started_at = time.monotonic()

    def _deadline_exceeded() -> bool:
        return (time.monotonic() - action_started_at) >= max_action_seconds

    attempt_logs: List[Dict[str, Any]] = []
    retry_path: List[str] = []
    stale_recovered = False
    reason_code = "unknown_error"
    last_live_texts: List[str] = []
    _resnapshot_on_strong_signal = str(
        os.getenv("GAIA_RESNAPSHOT_ON_STRONG_SIGNAL", "1")
    ).strip().lower() in {"1", "true", "yes", "y", "on"}

    async def _maybe_resnapshot(reason: str) -> Optional[str]:
        if not _resnapshot_on_strong_signal:
            return None
        try:
            snap = await snapshot_page(session_id=session_id, url=page.url, tab_id=tab_id)
            snap_id = snap.get("snapshot_id") if isinstance(snap, dict) else None
            if isinstance(snap_id, str) and snap_id.strip():
                retry_path.append(f"resnapshot:{reason}")
                return snap_id
        except Exception:
            return None
        return None

    recovery = await recover_snapshot_ref_state(
        session=session,
        page=page,
        session_id=session_id,
        snapshot_id=snapshot_id,
        ref_id=ref_id,
        retry_path=retry_path,
        to_ai_friendly_error_fn=to_ai_friendly_error,
        snapshot_page_fn=snapshot_page,
        resolve_ref_meta_from_snapshot_fn=_resolve_ref_meta_from_snapshot,
        resolve_stale_ref_fn=_resolve_stale_ref,
        get_tab_index_fn=_get_tab_index,
    )
    requested_snapshot = recovery.get("requested_snapshot")
    requested_meta = recovery.get("requested_meta")
    snapshot_id = str(recovery.get("snapshot_id") or snapshot_id)
    ref_id = str(recovery.get("ref_id") or ref_id)
    stale_recovered = bool(recovery.get("stale_recovered"))
    reason_code = str(recovery.get("reason_code") or reason_code)

    recovery_response = recovery.get("response")
    if isinstance(recovery_response, dict):
        recovery_response.setdefault("attempt_logs", attempt_logs)
        return recovery_response


    if not isinstance(requested_meta, dict):
        return {
            "success": False,
            "effective": False,
            "reason_code": "not_found",
            "reason": "유효한 ref metadata가 없습니다.",
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }

    scope = requested_meta.get("scope", {}) if isinstance(requested_meta.get("scope"), dict) else {}
    current_tab_index = _get_tab_index(page)
    ref_tab_index = scope.get("tab_index")
    if ref_tab_index is not None:
        try:
            if int(ref_tab_index) != current_tab_index:
                return {
                    "success": False,
                    "effective": False,
                    "reason_code": "tab_scope_mismatch",
                    "reason": f"ref tab scope mismatch: ref={ref_tab_index}, current={current_tab_index}",
                    "stale_recovered": stale_recovered,
                    "retry_path": retry_path,
                    "attempt_logs": attempt_logs,
                }
        except Exception:
            pass

    ref_frame_index = int(scope.get("frame_index", requested_meta.get("frame_index", 0)) or 0)
    if ref_frame_index < 0 or ref_frame_index >= len(page.frames):
        return {
            "success": False,
            "effective": False,
            "reason_code": "frame_scope_mismatch",
            "reason": f"ref frame scope mismatch: ref={ref_frame_index}, frame_count={len(page.frames)}",
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }

    candidates = _build_ref_candidates(requested_meta)
    deduped: List[Tuple[str, str]] = []
    seen_selectors = set()
    for mode, cand in candidates:
        key = cand.strip()
        if not key or key in seen_selectors:
            continue
        seen_selectors.add(key)
        deduped.append((mode, cand))
    candidates = deduped[:3]
    if not candidates:
        return {
            "success": False,
            "effective": False,
            "reason_code": "not_found",
            "reason": "ref metadata에 dom_ref가 없어 요소를 찾을 수 없습니다. 최신 snapshot이 필요합니다.",
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }
    transport_success = True
    locator_found = False
    interaction_success = False
    context_prep = await prepare_ref_action_execution_context(
        action=action,
        value=value,
        selector_hint=selector_hint,
        verify=verify,
        requested_meta=requested_meta,
        requested_snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
        page=page,
        snapshot_id=snapshot_id,
        ref_id=ref_id,
        retry_path=retry_path,
        attempt_logs=attempt_logs,
        stale_recovered=stale_recovered,
        max_action_seconds=max_action_seconds,
        collect_page_evidence_fn=_collect_page_evidence,
        collect_modal_regions_from_snapshot_fn=_collect_modal_regions_from_snapshot,
        is_close_intent_ref_fn=_is_close_intent_ref,
        is_modal_corner_close_candidate_fn=_is_modal_corner_close_candidate,
    )
    state_change = context_prep.get("state_change") if isinstance(context_prep, dict) else {}
    if not isinstance(state_change, dict):
        state_change = {}
    auth_submit_like_click = bool(
        context_prep.get("auth_submit_like_click")
    ) if isinstance(context_prep, dict) else False
    submit_like_click = bool(context_prep.get("submit_like_click")) if isinstance(context_prep, dict) else False
    close_like_click = bool(context_prep.get("close_like_click")) if isinstance(context_prep, dict) else False
    modal_regions_for_requested = (
        context_prep.get("modal_regions_for_requested")
        if isinstance(context_prep, dict)
        else None
    )
    probe_wait_schedule = context_prep.get("probe_wait_schedule") if isinstance(context_prep, dict) else (350, 700, 1500)
    if not isinstance(probe_wait_schedule, tuple):
        probe_wait_schedule = (350, 700, 1500)
    verify_for_action = bool(context_prep.get("verify_for_action")) if isinstance(context_prep, dict) else verify
    max_action_seconds = float(context_prep.get("max_action_seconds") or max_action_seconds) if isinstance(context_prep, dict) else max_action_seconds
    precheck_response = context_prep.get("precheck_response") if isinstance(context_prep, dict) else None
    if isinstance(precheck_response, dict):
        return precheck_response

    for attempt_idx, (mode, candidate_selector) in enumerate(candidates, start=1):
        attempt_result = await execute_ref_action_attempt(
            page=page,
            session=session,
            action=action,
            value=value,
            options=options,
            selector_hint=selector_hint,
            snapshot_id=snapshot_id,
            ref_id=ref_id,
            requested_meta=requested_meta,
            requested_snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
            stale_recovered=stale_recovered,
            attempt_idx=attempt_idx,
            mode=mode,
            candidate_selector=candidate_selector,
            verify_for_action=verify_for_action,
            close_like_click=close_like_click,
            submit_like_click=submit_like_click,
            auth_submit_like_click=auth_submit_like_click,
            modal_regions_for_requested=(
                modal_regions_for_requested
                if isinstance(modal_regions_for_requested, list)
                else None
            ),
            probe_wait_schedule=probe_wait_schedule,
            retry_path=retry_path,
            attempt_logs=attempt_logs,
            state_change=state_change,
            max_action_seconds=max_action_seconds,
            trace_auth_submit_enabled=trace_auth_submit_enabled,
            deadline_exceeded_fn=_deadline_exceeded,
            maybe_resnapshot_fn=_maybe_resnapshot,
            to_ai_friendly_error_fn=to_ai_friendly_error,
            resolve_locator_for_attempt_fn=resolve_locator_for_attempt,
            capture_before_state_fn=capture_before_state,
            unpack_before_state_fn=unpack_before_state,
            collect_state_change_probe_impl_fn=collect_state_change_probe,
            capture_close_diagnostic_impl_fn=capture_close_diagnostic,
            execute_action_on_locator_fn=_execute_action_on_locator,
            handle_action_exception_recovery_fn=handle_action_exception_recovery,
            run_verify_fallback_chain_fn=run_verify_fallback_chain,
            collect_page_evidence_fn=_collect_page_evidence,
            collect_page_evidence_light_fn=_collect_page_evidence_light,
            compute_runtime_dom_hash_fn=_compute_runtime_dom_hash,
            read_focus_signature_fn=_read_focus_signature,
            safe_read_target_state_fn=_safe_read_target_state,
            state_change_flags_fn=_state_change_flags,
            extract_live_texts_fn=_extract_live_texts,
            attempt_close_ref_fallbacks_fn=attempt_close_ref_fallbacks,
            attempt_backdrop_close_fn=attempt_backdrop_close,
            attempt_modal_corner_close_fn=attempt_modal_corner_close,
            collect_close_ref_candidates_fn=_collect_close_ref_candidates,
            build_ref_candidates_fn=_build_ref_candidates,
            resolve_locator_from_ref_fn=_resolve_locator_from_ref,
            try_click_hit_target_from_point_fn=_try_click_hit_target_from_point,
            try_click_container_ancestor_fn=_try_click_container_ancestor,
            build_fallback_success_response_fn=build_fallback_success_response,
        )

        if isinstance(attempt_result.get("return_response"), dict):
            return attempt_result["return_response"]

        state_change = attempt_result.get("state_change") or state_change
        reason_code = str(attempt_result.get("reason_code") or reason_code)
        ref_id = str(attempt_result.get("ref_id") or ref_id)

        updated_meta = attempt_result.get("requested_meta")
        if isinstance(updated_meta, dict):
            requested_meta = updated_meta

        locator_found = bool(locator_found or attempt_result.get("locator_found"))
        interaction_success = bool(interaction_success or attempt_result.get("interaction_success"))

        live_texts = attempt_result.get("last_live_texts")
        if isinstance(live_texts, list) and live_texts:
            last_live_texts = live_texts

        if bool(attempt_result.get("timed_out")):
            if reason_code == "action_timeout":
                append_attempt_timeout_log(
                    attempt_logs=attempt_logs,
                    attempt_idx=attempt_idx,
                    mode=mode,
                    candidate_selector=candidate_selector,
                    max_action_seconds=max_action_seconds,
                )
            break

        if bool(attempt_result.get("continue_loop")):
            continue

        if bool(attempt_result.get("effective")):
            result_context = await collect_ref_action_result_context(
                page=page,
                session=session,
                get_tab_index_fn=_get_tab_index,
                safe_page_url_fn=safe_page_url,
                safe_capture_page_screenshot_base64_fn=safe_capture_page_screenshot_base64,
            )

            if auth_submit_like_click and trace_auth_submit_enabled:
                print(
                    f"[trace_ref_action] total_ms={int((time.perf_counter() - trace_started_at) * 1000)} "
                    f"result=success reason_code={reason_code}"
                )
            return build_full_success_response(
                reason="ref action executed and state changed",
                snapshot_id=snapshot_id,
                ref_id=ref_id,
                stale_recovered=stale_recovered,
                transport_success=transport_success,
                locator_found=locator_found,
                interaction_success=interaction_success,
                state_change=state_change,
                live_texts=last_live_texts,
                retry_path=retry_path,
                attempt_logs=attempt_logs,
                screenshot_base64=result_context.get("screenshot_base64"),
                current_url=result_context.get("current_url"),
                tab_id=result_context.get("tab_id"),        
            )

    result_context = await collect_ref_action_result_context(
        page=page,
        session=session,
        get_tab_index_fn=_get_tab_index,
        safe_page_url_fn=safe_page_url,
        safe_capture_page_screenshot_base64_fn=safe_capture_page_screenshot_base64,
    )

    if auth_submit_like_click and trace_auth_submit_enabled:
        print(
            f"[trace_ref_action] total_ms={int((time.perf_counter() - trace_started_at) * 1000)} "
            f"result=failure reason_code={reason_code}"
        )
    return build_full_failure_response(
        reason_code=reason_code,
        snapshot_id=snapshot_id,
        ref_id=ref_id,
        stale_recovered=stale_recovered,
        transport_success=transport_success,
        locator_found=locator_found,
        interaction_success=interaction_success,
        state_change=state_change,
        live_texts=last_live_texts,
        retry_path=retry_path,
        attempt_logs=attempt_logs,
        screenshot_base64=result_context.get("screenshot_base64"),
        current_url=result_context.get("current_url"),
        tab_id=result_context.get("tab_id"),    
    )
