"""OpenClaw-compatible protocol constants for GAIA MCP host."""
from __future__ import annotations

from typing import Any, Dict


ELEMENT_ACTIONS = {
    "click",
    "fill",
    "press",
    "hover",
    "scroll",
    "select",
    "dragAndDrop",
    "dragSlider",
}


SELECTOR_FORBIDDEN_LEGACY_ACTIONS = ELEMENT_ACTIONS | {
    "scrollIntoView",
    "focus",
}


OPENCLAW_ACTIONS = {
    "browser_start",
    "browser_install",
    "browser_profiles",
    "browser_tabs",
    "browser_snapshot",
    "browser_act",
    "browser_wait",
    "browser_console_get",
    "browser_errors_get",
    "browser_requests_get",
    "browser_response_body",
    "browser_trace_start",
    "browser_trace_stop",
    "browser_highlight",
    "browser_dialog_arm",
    "browser_file_chooser_arm",
    "browser_download_wait",
    "browser_state",
    "browser_env",
    "browser_close",
}


REASON_CODES = {
    "ok",
    "ref_required",
    "snapshot_not_found",
    "stale_snapshot",
    "stale_ref_recovered",
    "not_found",
    "not_actionable",
    "no_state_change",
    "tab_scope_mismatch",
    "frame_scope_mismatch",
    "http_4xx",
    "http_5xx",
}


def is_element_action(action: str) -> bool:
    return (action or "").strip() in ELEMENT_ACTIONS


def legacy_selector_forbidden(action: str) -> bool:
    return (action or "").strip() in SELECTOR_FORBIDDEN_LEGACY_ACTIONS


def build_error(
    reason_code: str,
    reason: str,
    *,
    success: bool = False,
    effective: bool = False,
    **extra: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": success,
        "effective": effective,
        "reason_code": reason_code,
        "reason": reason,
    }
    payload.update(extra)
    return payload

