from __future__ import annotations

import base64
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from gaia.src.phase4.mcp_ref.snapshot_helpers import (
    _collect_close_ref_candidates,
    _collect_modal_regions_from_snapshot,
    _is_close_intent_ref,
    _is_modal_corner_close_candidate,
)
from gaia.src.phase4.mcp_ref.close_fallbacks import (
    attempt_backdrop_close,
    attempt_close_ref_fallbacks,
    attempt_modal_corner_close,
)
from gaia.src.phase4.mcp_ref.action_exception_recovery import (
    handle_action_exception_recovery,
)
from gaia.src.phase4.mcp_ref.diagnostic_helpers import (
    capture_close_diagnostic,
)
from gaia.src.phase4.mcp_ref.response_helpers import (
    build_fallback_success_response,
    build_full_failure_response,
    build_full_success_response,
)
from gaia.src.phase4.mcp_ref.session_helpers import (
    resolve_session_page_for_ref_action,
)
from gaia.src.phase4.mcp_ref.execution_context import (
    prepare_ref_action_execution_context,
)
from gaia.src.phase4.mcp_ref.attempt_helpers import (
    append_attempt_timeout_log,
    resolve_locator_for_attempt,
)
from gaia.src.phase4.mcp_ref.before_state import (
    capture_before_state,
    unpack_before_state,
)
from gaia.src.phase4.mcp_ref.state_probe import (
    collect_state_change_probe,
)
from gaia.src.phase4.mcp_ref.verify_fallbacks import (
    run_verify_fallback_chain,
)
from gaia.src.phase4.mcp_server.error_converter import to_ai_friendly_error


def _is_retryable_page_detach_error(exc: BaseException) -> bool:
    message = str(exc or "").strip().lower()
    if not message:
        return False
    return (
        "frame has been detached" in message
        or "target page, context or browser has been closed" in message
        or "target closed" in message
        or "connection closed while reading from the driver" in message
        or "transport closed" in message
        or "browser has been closed" in message
    )


def _is_driver_disconnect_error(exc_or_message: Any) -> bool:
    message = str(exc_or_message or "").strip().lower()
    if not message:
        return False
    return (
        "connection closed while reading from the driver" in message
        or "target page, context or browser has been closed" in message
        or "frame has been detached" in message
        or "browser has been closed" in message
        or "target closed" in message
        or "connection closed" in message
        or "transport closed" in message
    )


def _is_fatal_timeout_abort(exc_or_message: Any) -> bool:
    message = str(exc_or_message or "").strip().lower()
    if not message:
        return False
    return (
        "action budget exceeded" in message
        or "deadline exceeded" in message
        or "timed out after" in message
        or "action_timeout" in message
    )


def _is_visibility_timeout_abort(exc_or_message: Any) -> bool:
    message = str(exc_or_message or "").strip().lower()
    if not message:
        return False
    has_timeout = (
        "timeouterror" in message
        or "timeout" in message
        or "timed out" in message
        or "deadline exceeded" in message
        or "action_timeout" in message
        or "waiting for locator" in message
    )
    has_visibility = (
        "to be visible" in message
        or "not visible" in message
        or "to be attached" in message
        or "waiting for locator" in message
        or "찾을 수 없거나 표시되지" in message
        or "최신 snapshot" in message
    )
    return bool(has_timeout and has_visibility)


async def _best_effort_close_broken_session(
    session: Any,
    *,
    active_sessions: Optional[Dict[str, Any]] = None,
    session_id: str = "",
) -> None:
    try:
        await session.close()
    except Exception:
        pass
    try:
        session.current_url = ""
        session.current_snapshot_id = ""
        session.current_dom_hash = ""
        session.snapshots = {}
    except Exception:
        pass
    if isinstance(active_sessions, dict) and session_id:
        try:
            active_sessions.pop(session_id, None)
        except Exception:
            pass


async def _best_effort_force_disconnect_target_session(
    session: Any,
    *,
    active_sessions: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    reason: str = "",
) -> None:
    try:
        if hasattr(session, "force_disconnect_target"):
            await session.force_disconnect_target(reason=reason, hard=False)
            return
    except Exception:
        pass
    await _best_effort_close_broken_session(
        session,
        active_sessions=active_sessions,
        session_id=session_id,
    )


async def _safe_capture_page_screenshot_base64(page: Any) -> Optional[str]:
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
    except Exception as exc:
        if _is_retryable_page_detach_error(exc):
            return None
        return None
    try:
        return base64.b64encode(screenshot_bytes).decode("utf-8")
    except Exception:
        return None


def _safe_page_url(page: Any, fallback: str = "") -> str:
    try:
        return str(getattr(page, "url", "") or fallback or "")
    except Exception:
        return str(fallback or "")


def _session_accepts_followup_io(session: Any) -> bool:
    try:
        return not bool(getattr(session, "_teardown_in_progress", False) or getattr(session, "_closed", False))
    except Exception:
        return False


async def _goto_with_retry(page: Any, url: str, *, timeout: int, wait_for_networkidle: bool = True) -> None:
    try:
        await page.goto(url, timeout=timeout)
    except Exception as exc:
        if not _is_retryable_page_detach_error(exc):
            raise
        await page.wait_for_timeout(150)
        await page.goto(url, timeout=timeout)
    if wait_for_networkidle:
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass


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
            try:
                await _goto_with_retry(page, url, timeout=60000, wait_for_networkidle=True)
            except Exception as exc:
                if _is_driver_disconnect_error(exc):
                    await _best_effort_force_disconnect_target_session(
                        session,
                        active_sessions=active_sessions,
                        session_id=session_id,
                        reason="goto_disconnect",
                    )
                raise
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
        if not _session_accepts_followup_io(session):
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

    requested_snapshot = session.snapshots.get(snapshot_id)
    requested_meta = (
        _resolve_ref_meta_from_snapshot(requested_snapshot, ref_id)
        if requested_snapshot
        else None
    )
    initial_ref_state: Optional[str] = None
    if isinstance(requested_snapshot, dict):
        snap_epoch = int(requested_snapshot.get("epoch") or 0)
        snap_dom_hash = str(requested_snapshot.get("dom_hash") or "")
        snap_tab_index = int(requested_snapshot.get("tab_index") or 0)
        parsed_epoch = 0
        parsed_hash_short = ""
        try:
            parts = str(snapshot_id).split(":")
            if len(parts) >= 3:
                parsed_epoch = int(parts[-2] or 0)
                parsed_hash_short = str(parts[-1] or "")
        except Exception:
            parsed_epoch = 0
            parsed_hash_short = ""
        if parsed_epoch and parsed_epoch != snap_epoch:
            initial_ref_state = "stale_snapshot"
        if parsed_hash_short and snap_dom_hash and not snap_dom_hash.startswith(parsed_hash_short):
            initial_ref_state = "stale_snapshot"
        if snap_tab_index != _get_tab_index(page):
            initial_ref_state = "stale_snapshot"
    if not requested_snapshot:
        initial_ref_state = "snapshot_not_found"
    elif requested_meta is None:
        initial_ref_state = "not_found"
    elif not str(requested_meta.get("dom_ref") or "").strip():
        initial_ref_state = "stale_snapshot"

    if initial_ref_state:
        retry_path.append(f"recover:{initial_ref_state}")
        try:
            fresh_snapshot_result = await snapshot_page(
                url=(page.url or None), session_id=session_id
            )
            fresh_snapshot_id = str(fresh_snapshot_result.get("snapshot_id") or "")
            fresh_snapshot = (
                session.snapshots.get(fresh_snapshot_id)
                if fresh_snapshot_id
                else None
            )
            recovered_meta: Optional[Dict[str, Any]] = None
            recovered_ref_id = ref_id

            if isinstance(fresh_snapshot, dict):
                recovered_meta = _resolve_ref_meta_from_snapshot(fresh_snapshot, ref_id)
                if recovered_meta is None:
                    recovered_meta = _resolve_stale_ref(requested_meta, fresh_snapshot)
                    if isinstance(recovered_meta, dict):
                        recovered_ref_id = str(
                            recovered_meta.get("ref_id") or recovered_ref_id
                        )
                if isinstance(recovered_meta, dict):
                    requested_snapshot = fresh_snapshot
                    requested_meta = recovered_meta
                    snapshot_id = fresh_snapshot_id or snapshot_id
                    ref_id = recovered_ref_id
                    stale_recovered = True
                    reason_code = "stale_ref_recovered"
                    retry_path.append("recover:ok")
        except Exception as recover_exc:
            friendly = to_ai_friendly_error(recover_exc, ref_id=ref_id)
            retry_path.append(f"recover:error:{friendly}")

    if (
        not isinstance(requested_snapshot, dict)
        or not isinstance(requested_meta, dict)
        or not str(requested_meta.get("dom_ref") or "").strip()
    ):
        if initial_ref_state == "snapshot_not_found":
            fail_message = "snapshot을 찾을 수 없습니다. 최신 snapshot 기준으로 다시 의사결정하세요."
            fail_code = "snapshot_not_found"
        elif initial_ref_state == "not_found":
            fail_message = "snapshot 내 ref를 찾을 수 없습니다. 최신 snapshot 기준으로 다시 의사결정하세요."
            fail_code = "not_found"
        else:
            fail_message = "snapshot/ref가 stale 상태입니다. 최신 snapshot 기준으로 다시 의사결정하세요."
            fail_code = "stale_snapshot"
        return {
            "success": False,
            "effective": False,
            "reason_code": fail_code,
            "reason": fail_message,
            "stale_recovered": stale_recovered,
            "retry_path": retry_path,
            "attempt_logs": attempt_logs,
        }

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
    try:
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
    except Exception as exc:
        if not _is_driver_disconnect_error(exc):
            raise
        transport_success = False
        reason_code = "request_exception"
        await _best_effort_force_disconnect_target_session(
            session,
            active_sessions=active_sessions,
            session_id=session_id,
            reason="prepare_context_disconnect",
        )
        return build_full_failure_response(
            reason_code=reason_code,
            snapshot_id=snapshot_id,
            ref_id=ref_id,
            stale_recovered=stale_recovered,
            transport_success=transport_success,
            locator_found=locator_found,
            interaction_success=interaction_success,
            state_change={},
            live_texts=last_live_texts,
            retry_path=retry_path,
            attempt_logs=attempt_logs,
            screenshot_base64=None,
            current_url="",
            tab_id=tab_id,
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
        if _deadline_exceeded():
            reason_code = "action_timeout"
            append_attempt_timeout_log(
                attempt_logs=attempt_logs,
                attempt_idx=attempt_idx,
                mode=mode,
                candidate_selector=candidate_selector,
                max_action_seconds=max_action_seconds,
            )
            break
        retry_path.append(f"{attempt_idx}:{mode}")
        try:
            locator_resolution = await resolve_locator_for_attempt(
                page=page,
                requested_meta=requested_meta,
                candidate_selector=candidate_selector,
                attempt_idx=attempt_idx,
                mode=mode,
                attempt_logs=attempt_logs,
                resolve_locator_from_ref_fn=_resolve_locator_from_ref,
            )
        except Exception as exc:
            if not _is_driver_disconnect_error(exc):
                raise
            transport_success = False
            reason_code = "request_exception"
            await _best_effort_force_disconnect_target_session(
                session,
                active_sessions=active_sessions,
                session_id=session_id,
                reason="resolve_locator_disconnect",
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
                screenshot_base64=None,
                current_url="",
                tab_id=tab_id,
            )
        if not bool(locator_resolution.get("ok")):
            reason_code = str(locator_resolution.get("reason_code") or "not_found")
            continue
        locator = locator_resolution.get("locator")
        frame_index = locator_resolution.get("frame_index")
        resolved_selector = str(locator_resolution.get("resolved_selector") or candidate_selector)

        locator_found = True
        try:
            before_state = await capture_before_state(
                page=page,
                locator=locator,
                submit_like_click=submit_like_click,
                collect_page_evidence_fn=_collect_page_evidence,
                collect_page_evidence_light_fn=_collect_page_evidence_light,
                compute_runtime_dom_hash_fn=_compute_runtime_dom_hash,
                read_focus_signature_fn=_read_focus_signature,
                safe_read_target_state_fn=_safe_read_target_state,
            )
        except Exception as exc:
            if not _is_driver_disconnect_error(exc):
                raise
            transport_success = False
            reason_code = "request_exception"
            await _best_effort_force_disconnect_target_session(
                session,
                active_sessions=active_sessions,
                session_id=session_id,
                reason="capture_before_state_disconnect",
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
                screenshot_base64=None,
                current_url="",
                tab_id=tab_id,
            )
        before_state_unpack = unpack_before_state(
            before_state=before_state if isinstance(before_state, dict) else {},
            fallback_url=page.url,
            fallback_evidence_collector=_collect_page_evidence,
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
            probe_result = await collect_state_change_probe(
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
                compute_runtime_dom_hash_fn=_compute_runtime_dom_hash,
                evidence_collector_fn=evidence_collector,
                read_focus_signature_fn=_read_focus_signature,
                safe_read_target_state_fn=_safe_read_target_state,
                state_change_flags_fn=_state_change_flags,
                extract_live_texts_fn=_extract_live_texts,
            )
            change = probe_result.get("change") if isinstance(probe_result, dict) else {}
            if not isinstance(change, dict):
                change = {}
            live_texts_after = (
                probe_result.get("live_texts_after")
                if isinstance(probe_result, dict)
                else []
            )
            if not isinstance(live_texts_after, list):
                live_texts_after = []
            if live_texts_after:
                last_live_texts = live_texts_after
            return change

        async def _capture_close_diagnostic(label: str, *, extra: Optional[Dict[str, Any]] = None) -> None:
            if not close_like_click:
                return
            await capture_close_diagnostic(
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
            await _execute_action_on_locator(action, page, locator, value, options=options)
            if auth_submit_like_click and trace_auth_submit_enabled:
                print(
                    f"[trace_ref_action] locator_action_ms={int((time.perf_counter() - locator_action_started_at) * 1000)} "
                    f"action={action} selector_hint={selector_hint!r}"
                )
            interaction_success = True
        except Exception as action_exc:
            friendly_msg = to_ai_friendly_error(action_exc, ref_id=ref_id)
            retry_path.append(f"action_error:{friendly_msg}")
            fatal_driver_disconnect = _is_driver_disconnect_error(action_exc) or _is_driver_disconnect_error(friendly_msg)
            fatal_timeout_abort = _is_fatal_timeout_abort(action_exc) or _is_fatal_timeout_abort(friendly_msg)
            visibility_timeout_abort = _is_visibility_timeout_abort(action_exc) or _is_visibility_timeout_abort(friendly_msg)
            if visibility_timeout_abort:
                reason_code = "not_found"
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": mode,
                        "selector": resolved_selector,
                        "frame_index": frame_index,
                        "reason_code": reason_code,
                        "error": friendly_msg,
                    }
                )
                continue
            if fatal_driver_disconnect:
                transport_success = False
                reason_code = "request_exception"
                await _best_effort_force_disconnect_target_session(
                    session,
                    active_sessions=active_sessions,
                    session_id=session_id,
                    reason="action_disconnect",
                )
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": mode,
                        "selector": resolved_selector,
                        "frame_index": frame_index,
                        "reason_code": reason_code,
                        "error": friendly_msg,
                    }
                )
                break
            if fatal_timeout_abort:
                reason_code = "action_timeout"
                attempt_logs.append(
                    {
                        "attempt": attempt_idx,
                        "mode": mode,
                        "selector": resolved_selector,
                        "frame_index": frame_index,
                        "reason_code": reason_code,
                        "error": friendly_msg,
                    }
                )
                continue
            recovery_result = await handle_action_exception_recovery(
                action_exc=action_exc,
                action=action,
                verify_for_action=verify_for_action,
                close_like_click=close_like_click,
                deadline_exceeded_fn=_deadline_exceeded,
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
                attempt_close_ref_fallbacks_fn=attempt_close_ref_fallbacks,
                attempt_backdrop_close_fn=attempt_backdrop_close,
                attempt_modal_corner_close_fn=attempt_modal_corner_close,
                collect_close_ref_candidates_fn=_collect_close_ref_candidates,
                build_ref_candidates_fn=_build_ref_candidates,
                resolve_locator_from_ref_fn=_resolve_locator_from_ref,
                try_click_hit_target_from_point_fn=_try_click_hit_target_from_point,
                build_fallback_success_response_fn=build_fallback_success_response,
            )
            if isinstance(recovery_result.get("return_response"), dict):
                return recovery_result["return_response"]
            state_change = recovery_result.get("state_change") or state_change
            if isinstance(state_change, dict) and bool(state_change.get("resnapshot_required")):
                post_click_snapshot_id = await _maybe_resnapshot("exception_recovery")
                if post_click_snapshot_id:
                    state_change["post_click_snapshot_id"] = post_click_snapshot_id
            ref_id = str(recovery_result.get("ref_id") or ref_id)
            updated_meta = recovery_result.get("requested_meta")
            if isinstance(updated_meta, dict):
                requested_meta = updated_meta
            reason_code = str(recovery_result.get("reason_code") or "not_actionable")
            if bool(recovery_result.get("continue_loop")):
                continue

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
                if _deadline_exceeded():
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
            verify_fallback_result = await run_verify_fallback_chain(
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
                deadline_exceeded_fn=_deadline_exceeded,
                collect_state_change_probe_fn=_collect_state_change_probe,
                capture_close_diagnostic_fn=_capture_close_diagnostic,
                attempt_close_ref_fallbacks_fn=attempt_close_ref_fallbacks,
                attempt_backdrop_close_fn=attempt_backdrop_close,
                attempt_modal_corner_close_fn=attempt_modal_corner_close,
                try_click_hit_target_from_point_fn=_try_click_hit_target_from_point,
                try_click_container_ancestor_fn=_try_click_container_ancestor,
                collect_close_ref_candidates_fn=_collect_close_ref_candidates,
                build_ref_candidates_fn=_build_ref_candidates,
                resolve_locator_from_ref_fn=_resolve_locator_from_ref,
            )
            effective = bool(verify_fallback_result.get("effective"))
            state_change = verify_fallback_result.get("state_change") or state_change
            if isinstance(state_change, dict) and bool(state_change.get("resnapshot_required")):
                post_click_snapshot_id = await _maybe_resnapshot("verify_fallback")
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
            break

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
        if effective:
            session.current_url = _safe_page_url(page, session.current_url)
            screenshot_base64 = (
                await _safe_capture_page_screenshot_base64(page)
                if _session_accepts_followup_io(session)
                else None
            )
            tab_id_value = _get_tab_index(page)
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
                screenshot_base64=screenshot_base64,
                current_url=session.current_url,
                tab_id=tab_id_value,
            )

    screenshot = (
        await _safe_capture_page_screenshot_base64(page)
        if _session_accepts_followup_io(session)
        else None
    )

    session.current_url = _safe_page_url(page, session.current_url)
    tab_id_value = _get_tab_index(page)
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
        screenshot_base64=screenshot,
        current_url=session.current_url,
        tab_id=tab_id_value,
    )
