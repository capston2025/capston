"""OpenClaw-compatible protocol constants for GAIA MCP host."""
from __future__ import annotations

from typing import Any, Dict


ELEMENT_ACTIONS = {
    "click",
    "fill",
    "press",
    "hover",
    "scroll",
    "scrollIntoView",
    "select",
    "dragAndDrop",
    "dragSlider",
}


SELECTOR_FORBIDDEN_LEGACY_ACTIONS = ELEMENT_ACTIONS | {
    "scrollIntoView",
    "focus",
    "uploadFile",
    "storeCSSValue",
    "expectCSSChanged",
    "expectVisible",
    "expectHidden",
    "expectText",
    "expectAttribute",
    "expectCountAtLeast",
    "evaluate",
}


OPENCLAW_ACTIONS = {
    "browser_start",
    "browser_install",
    "browser_profiles",
    "browser_tabs",
    "browser_tabs_open",
    "browser_tabs_focus",
    "browser_tabs_close",
    "browser_tabs_action",
    "browser_snapshot",
    "browser_act",
    "browser_wait",
    "browser_screenshot",
    "browser_pdf",
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
    "legacy_selector_forbidden",
    "snapshot_not_found",
    "stale_snapshot",
    "stale_ref_recovered",
    "not_found",
    "not_actionable",
    "no_state_change",
    "ambiguous_target_id",
    "action_timeout",
    "ambiguous_ref_target",
    "invalid_snapshot_options",
    "invalid_input",
    "failed",
    "unknown_error",
    "auth_required",
    "request_exception",
    "tab_scope_mismatch",
    "frame_scope_mismatch",
    "ambiguous_selector",
    "ambiguous_ref_target",
    "http_4xx",
    "http_5xx",
}


def is_element_action(action: str) -> bool:
    return (action or "").strip() in ELEMENT_ACTIONS


def legacy_selector_forbidden(action: str, selector: str = "") -> bool:
    if not str(selector or "").strip():
        return False
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
