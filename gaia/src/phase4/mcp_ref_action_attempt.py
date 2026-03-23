from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple


def _build_attempt_result(
    *,
    effective: bool,
    continue_loop: bool,
    timed_out: bool,
    reason_code: str,
    state_change: Dict[str, Any],
    ref_id: str,
    requested_meta: Dict[str, Any],
    locator_found: bool,
    interaction_success: bool,
    last_live_texts: List[str],
    return_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "effective": effective,
        "continue_loop": continue_loop,
        "timed_out": timed_out,
        "reason_code": reason_code,
        "state_change": state_change,
        "ref_id": ref_id,
        "requested_meta": requested_meta,
        "locator_found": locator_found,
        "interaction_success": interaction_success,
        "last_live_texts": last_live_texts,
        "return_response": return_response,
    }


async def execute_ref_action_attempt(
    *,
    page: Any,
    session: Any,
    action: str,
    value: Any,
    options: Optional[Dict[str, Any]],
    selector_hint: str,
    snapshot_id: str,
    ref_id: str,
    requested_meta: Dict[str, Any],
    requested_snapshot: Optional[Dict[str, Any]],
    stale_recovered: bool,
    attempt_idx: int,
    mode: str,
    candidate_selector: str,
    verify_for_action: bool,
    close_like_click: bool,
    submit_like_click: bool,
    auth_submit_like_click: bool,
    modal_regions_for_requested: Optional[List[Dict[str, Any]]],
    probe_wait_schedule: Tuple[int, ...],
    retry_path: List[str],
    attempt_logs: List[Dict[str, Any]],
    state_change: Dict[str, Any],
    max_action_seconds: float,
    trace_auth_submit_enabled: bool,
    deadline_exceeded_fn,
    maybe_resnapshot_fn,
    to_ai_friendly_error_fn,
    resolve_locator_for_attempt_fn,
    capture_before_state_fn,
    unpack_before_state_fn,
    collect_state_change_probe_impl_fn,
    capture_close_diagnostic_impl_fn,
    execute_action_on_locator_fn,
    handle_action_exception_recovery_fn,
    run_verify_fallback_chain_fn,
    collect_page_evidence_fn,
    collect_page_evidence_light_fn,
    compute_runtime_dom_hash_fn,
    read_focus_signature_fn,
    safe_read_target_state_fn,
    state_change_flags_fn,
    extract_live_texts_fn,
    attempt_close_ref_fallbacks_fn,
    attempt_backdrop_close_fn,
    attempt_modal_corner_close_fn,
    collect_close_ref_candidates_fn,
    build_ref_candidates_fn,
    resolve_locator_from_ref_fn,
    try_click_hit_target_from_point_fn,
    try_click_container_ancestor_fn,
    build_fallback_success_response_fn,
) -> Dict[str, Any]:
    reason_code = "unknown_error"
    locator_found = False
    interaction_success = False
    last_live_texts: List[str] = []

    if deadline_exceeded_fn():
        reason_code = "action_timeout"
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": mode,
                "selector": candidate_selector,
                "reason_code": reason_code,
                "error": f"action budget exceeded ({max_action_seconds:.1f}s)",
            }
        )
        return _build_attempt_result(
            effective=False,
            continue_loop=False,
            timed_out=True,
            reason_code=reason_code,
            state_change=state_change,
            ref_id=ref_id,
            requested_meta=requested_meta,
            locator_found=False,
            interaction_success=False,
            last_live_texts=last_live_texts,
        )

    retry_path.append(f"{attempt_idx}:{mode}")
    locator_resolution = await resolve_locator_for_attempt_fn(
        page=page,
        requested_meta=requested_meta,
        candidate_selector=candidate_selector,
        attempt_idx=attempt_idx,
        mode=mode,
        attempt_logs=attempt_logs,
        resolve_locator_from_ref_fn=resolve_locator_from_ref_fn,
    )
    if not bool(locator_resolution.get("ok")):
        reason_code = str(locator_resolution.get("reason_code") or "not_found")
        return _build_attempt_result(
            effective=False,
            continue_loop=True,
            timed_out=False,
            reason_code=reason_code,
            state_change=state_change,
            ref_id=ref_id,
            requested_meta=requested_meta,
            locator_found=False,
            interaction_success=False,
            last_live_texts=last_live_texts,
        )

    locator = locator_resolution.get("locator")
    frame_index = locator_resolution.get("frame_index")
    resolved_selector = str(locator_resolution.get("resolved_selector") or candidate_selector)

    locator_found = True
    before_state = await capture_before_state_fn(
        page=page,
        locator=locator,
        submit_like_click=submit_like_click,
        collect_page_evidence_fn=collect_page_evidence_fn,
        collect_page_evidence_light_fn=collect_page_evidence_light_fn,
        compute_runtime_dom_hash_fn=compute_runtime_dom_hash_fn,
        read_focus_signature_fn=read_focus_signature_fn,
        safe_read_target_state_fn=safe_read_target_state_fn,
    )
    before_state_unpack = unpack_before_state_fn(
        before_state=before_state if isinstance(before_state, dict) else {},
        fallback_url=page.url,
        fallback_evidence_collector=collect_page_evidence_fn,
    )
    before_url = str(before_state_unpack.get("before_url") or page.url)
    before_dom_hash = str(before_state_unpack.get("before_dom_hash") or "")
    before_evidence = (
        before_state_unpack.get("before_evidence")
        if isinstance(before_state_unpack.get("before_evidence"), dict)
        else {}
    )
    before_focus = (
        before_state_unpack.get("before_focus")
        if isinstance(before_state_unpack.get("before_focus"), dict)
        else {}
    )
    before_target = (
        before_state_unpack.get("before_target")
        if isinstance(before_state_unpack.get("before_target"), dict)
        else {}
    )
    evidence_collector = before_state_unpack.get("evidence_collector")

    async def _collect_state_change_probe(
        *,
        probe_wait_ms: int,
        probe_scroll: str,
        ancestor_click_fallback: bool = False,
        ancestor_click_selector: str = "",
    ) -> Dict[str, Any]:
        nonlocal last_live_texts
        probe_result = await collect_state_change_probe_impl_fn(
            page=page,
            locator=locator,
            action=action,
            value=value,
            before_url=before_url,
            before_dom_hash=before_dom_hash,
            before_evidence=before_evidence,
            before_focus=before_focus,
            before_target=before_target,
            probe_wait_ms=probe_wait_ms,
            probe_scroll=probe_scroll,
            ancestor_click_fallback=ancestor_click_fallback,
            ancestor_click_selector=ancestor_click_selector,
            compute_runtime_dom_hash_fn=compute_runtime_dom_hash_fn,
            evidence_collector_fn=evidence_collector,
            read_focus_signature_fn=read_focus_signature_fn,
            safe_read_target_state_fn=safe_read_target_state_fn,
            state_change_flags_fn=state_change_flags_fn,
            extract_live_texts_fn=extract_live_texts_fn,
        )
        change = probe_result.get("change") if isinstance(probe_result, dict) else {}
        if not isinstance(change, dict):
            change = {}
        live_texts_after = probe_result.get("live_texts_after") if isinstance(probe_result, dict) else []
        if not isinstance(live_texts_after, list):
            live_texts_after = []
        if live_texts_after:
            last_live_texts = live_texts_after
        return change

    async def _capture_close_diagnostic(label: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
        if not close_like_click:
            return
        await capture_close_diagnostic_impl_fn(
            page=page,
            locator=locator,
            requested_meta=requested_meta if isinstance(requested_meta, dict) else None,
            attempt_idx=attempt_idx,
            mode=mode,
            attempt_logs=attempt_logs,
            label=label,
            extra=extra if isinstance(extra, dict) else None,
        )

    try:
        locator_action_started_at = time.perf_counter()
        await execute_action_on_locator_fn(action, page, locator, value, options=options)
        if auth_submit_like_click and trace_auth_submit_enabled:
            print(
                f"[trace_ref_action] locator_action_ms={int((time.perf_counter() - locator_action_started_at) * 1000)} "
                f"action={action} selector_hint={selector_hint!r}"
            )
        interaction_success = True
    except Exception as action_exc:
        friendly_msg = to_ai_friendly_error_fn(action_exc, ref_id=ref_id)
        retry_path.append(f"action_error:{friendly_msg}")
        recovery_result = await handle_action_exception_recovery_fn(
            action_exc=action_exc,
            action=action,
            verify_for_action=verify_for_action,
            close_like_click=close_like_click,
            deadline_exceeded_fn=deadline_exceeded_fn,
            page=page,
            locator=locator,
            attempt_idx=attempt_idx,
            mode=mode,
            resolved_selector=resolved_selector,
            frame_index=frame_index,
            ref_id=ref_id,
            requested_meta=requested_meta if isinstance(requested_meta, dict) else None,
            requested_snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
            modal_regions=modal_regions_for_requested if isinstance(modal_regions_for_requested, list) else None,
            snapshot_id=snapshot_id,
            retry_path=retry_path,
            stale_recovered=stale_recovered,
            attempt_logs=attempt_logs,
            state_change=state_change,
            session=session,
            collect_state_change_probe_fn=_collect_state_change_probe,
            capture_close_diagnostic_fn=_capture_close_diagnostic,
            attempt_close_ref_fallbacks_fn=attempt_close_ref_fallbacks_fn,
            attempt_backdrop_close_fn=attempt_backdrop_close_fn,
            attempt_modal_corner_close_fn=attempt_modal_corner_close_fn,
            collect_close_ref_candidates_fn=collect_close_ref_candidates_fn,
            build_ref_candidates_fn=build_ref_candidates_fn,
            resolve_locator_from_ref_fn=resolve_locator_from_ref_fn,
            try_click_hit_target_from_point_fn=try_click_hit_target_from_point_fn,
            build_fallback_success_response_fn=build_fallback_success_response_fn,
        )
        if isinstance(recovery_result.get("return_response"), dict):
            return _build_attempt_result(
                effective=False,
                continue_loop=False,
                timed_out=False,
                reason_code=str(recovery_result.get("reason_code") or "not_actionable"),
                state_change=recovery_result.get("state_change") or state_change,
                ref_id=str(recovery_result.get("ref_id") or ref_id),
                requested_meta=recovery_result.get("requested_meta") or requested_meta,
                locator_found=locator_found,
                interaction_success=interaction_success,
                last_live_texts=last_live_texts,
                return_response=recovery_result["return_response"],
            )

        state_change = recovery_result.get("state_change") or state_change
        if isinstance(state_change, dict) and bool(state_change.get("resnapshot_required")):
            post_click_snapshot_id = await maybe_resnapshot_fn("exception_recovery")
            if post_click_snapshot_id:
                state_change["post_click_snapshot_id"] = post_click_snapshot_id

        ref_id = str(recovery_result.get("ref_id") or ref_id)
        updated_meta = recovery_result.get("requested_meta")
        if isinstance(updated_meta, dict):
            requested_meta = updated_meta

        reason_code = str(recovery_result.get("reason_code") or "not_actionable")
        if bool(recovery_result.get("continue_loop")):
            return _build_attempt_result(
                effective=False,
                continue_loop=True,
                timed_out=False,
                reason_code=reason_code,
                state_change=state_change,
                ref_id=ref_id,
                requested_meta=requested_meta,
                locator_found=locator_found,
                interaction_success=interaction_success,
                last_live_texts=last_live_texts,
            )

    if submit_like_click:
        await page.wait_for_timeout(250)

    effective = False
    if auth_submit_like_click and not verify_for_action:
        effective = True
        if isinstance(state_change, dict):
            state_change["effective"] = True
            state_change["auth_submit_fast_path"] = True
        if auth_submit_like_click and trace_auth_submit_enabled:
            print("[trace_ref_action] verify_loop_ms=0 effective=True auth_state_changed=False skipped=auth_submit_verify_false")
    else:
        verify_started_at = time.perf_counter()
        for probe_wait_ms in probe_wait_schedule:
            if deadline_exceeded_fn():
                reason_code = "action_timeout"
                break
            await page.wait_for_timeout(probe_wait_ms)
            state_change = await _collect_state_change_probe(
                probe_wait_ms=probe_wait_ms,
                probe_scroll="none",
            )
            effective = bool(state_change.get("effective", True)) if verify_for_action else True
            if auth_submit_like_click and bool(state_change.get("auth_state_changed")):
                effective = True
                state_change["effective"] = True
                state_change["auth_submit_fast_path"] = True
            if effective:
                break
        if auth_submit_like_click and trace_auth_submit_enabled:
            print(
                f"[trace_ref_action] verify_loop_ms={int((time.perf_counter() - verify_started_at) * 1000)} "
                f"effective={effective} auth_state_changed={bool(state_change.get('auth_state_changed')) if isinstance(state_change, dict) else False}"
            )

    auth_fast_path = bool(
        auth_submit_like_click
        and effective
        and isinstance(state_change, dict)
        and bool(state_change.get("auth_state_changed"))
    )
    if not auth_fast_path:
        verify_fallback_result = await run_verify_fallback_chain_fn(
            verify_for_action=verify_for_action,
            effective=effective,
            action=action,
            close_like_click=close_like_click,
            page=page,
            locator=locator,
            requested_meta=requested_meta if isinstance(requested_meta, dict) else None,
            requested_snapshot=requested_snapshot if isinstance(requested_snapshot, dict) else None,
            modal_regions=modal_regions_for_requested if isinstance(modal_regions_for_requested, list) else None,
            ref_id=ref_id,
            attempt_idx=attempt_idx,
            mode=mode,
            resolved_selector=resolved_selector,
            frame_index=frame_index,
            state_change=state_change,
            attempt_logs=attempt_logs,
            deadline_exceeded_fn=deadline_exceeded_fn,
            collect_state_change_probe_fn=_collect_state_change_probe,
            capture_close_diagnostic_fn=_capture_close_diagnostic,
            attempt_close_ref_fallbacks_fn=attempt_close_ref_fallbacks_fn,
            attempt_backdrop_close_fn=attempt_backdrop_close_fn,
            attempt_modal_corner_close_fn=attempt_modal_corner_close_fn,
            try_click_hit_target_from_point_fn=try_click_hit_target_from_point_fn,
            try_click_container_ancestor_fn=try_click_container_ancestor_fn,
            collect_close_ref_candidates_fn=collect_close_ref_candidates_fn,
            build_ref_candidates_fn=build_ref_candidates_fn,
            resolve_locator_from_ref_fn=resolve_locator_from_ref_fn,
        )
        effective = bool(verify_fallback_result.get("effective"))
        state_change = verify_fallback_result.get("state_change") or state_change
        if isinstance(state_change, dict) and bool(state_change.get("resnapshot_required")):
            post_click_snapshot_id = await maybe_resnapshot_fn("verify_fallback")
            if post_click_snapshot_id:
                state_change["post_click_snapshot_id"] = post_click_snapshot_id
        ref_id = str(verify_fallback_result.get("ref_id") or ref_id)
        updated_meta = verify_fallback_result.get("requested_meta")
        if isinstance(updated_meta, dict):
            requested_meta = updated_meta
        if bool(verify_fallback_result.get("timed_out")):
            reason_code = "action_timeout"

    if reason_code == "action_timeout":
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": mode,
                "selector": resolved_selector,
                "frame_index": frame_index,
                "reason_code": reason_code,
                "error": f"action budget exceeded ({max_action_seconds:.1f}s)",
            }
        )
        return _build_attempt_result(
            effective=False,
            continue_loop=False,
            timed_out=True,
            reason_code=reason_code,
            state_change=state_change,
            ref_id=ref_id,
            requested_meta=requested_meta,
            locator_found=locator_found,
            interaction_success=interaction_success,
            last_live_texts=last_live_texts,
        )

    reason_code = "ok" if effective else "no_state_change"
    attempt_logs.append(
        {
            "attempt": attempt_idx,
            "mode": mode,
            "selector": resolved_selector,
            "frame_index": frame_index,
            "reason_code": reason_code,
            "state_change": state_change,
        }
    )
    print(f"[execute_ref_action] step={attempt_idx} mode={mode} reason={reason_code}")

    return _build_attempt_result(
        effective=effective,
        continue_loop=False,
        timed_out=False,
        reason_code=reason_code,
        state_change=state_change,
        ref_id=ref_id,
        requested_meta=requested_meta,
        locator_found=locator_found,
        interaction_success=interaction_success,
        last_live_texts=last_live_texts,
    )
