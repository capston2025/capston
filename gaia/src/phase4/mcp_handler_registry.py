from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence, Tuple

from gaia.src.phase4.mcp_browser_handlers import build_browser_handlers


HandlerSpec = Tuple[str, str]


HANDLER_SPECS: Sequence[HandlerSpec] = (
    ("browser_start", "_browser_start"),
    ("browser_install", "_browser_install"),
    ("browser_profiles", "_browser_profiles"),
    ("browser_tabs", "_browser_tabs"),
    ("browser_tabs_open", "_browser_tabs_open"),
    ("browser_tabs_focus", "_browser_tabs_focus"),
    ("browser_tabs_close", "_browser_tabs_close"),
    ("browser_tabs_action", "_browser_tabs_action"),
    ("browser_snapshot", "_browser_snapshot"),
    ("browser_act", "_browser_act"),
    ("browser_wait", "_browser_wait"),
    ("browser_screenshot", "_browser_screenshot"),
    ("browser_pdf", "_browser_pdf"),
    ("browser_console_get", "_browser_console_get"),
    ("browser_errors_get", "_browser_errors_get"),
    ("browser_requests_get", "_browser_requests_get"),
    ("browser_response_body", "_browser_response_body"),
    ("browser_trace_start", "_browser_trace_start"),
    ("browser_trace_stop", "_browser_trace_stop"),
    ("browser_highlight", "_browser_highlight"),
    ("browser_dialog_arm", "_browser_dialog_arm"),
    ("browser_file_chooser_arm", "_browser_file_chooser_arm"),
    ("browser_download_wait", "_browser_download_wait"),
    ("browser_state", "_browser_state"),
    ("browser_env", "_browser_env"),
)


def build_registered_browser_handlers(namespace: Mapping[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    missing: list[str] = []
    for public_name, local_name in HANDLER_SPECS:
        fn = namespace.get(local_name)
        if fn is None:
            missing.append(local_name)
            continue
        kwargs[public_name] = fn
    if missing:
        raise KeyError(f"Missing browser handler(s): {', '.join(sorted(missing))}")
    return build_browser_handlers(**kwargs)
