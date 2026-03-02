"""Browser handler map factory for MCP execute dispatch."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict


BrowserHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


def build_browser_handlers(
    *,
    browser_start: BrowserHandler,
    browser_install: BrowserHandler,
    browser_profiles: BrowserHandler,
    browser_tabs: BrowserHandler,
    browser_tabs_open: BrowserHandler,
    browser_tabs_focus: BrowserHandler,
    browser_tabs_close: BrowserHandler,
    browser_tabs_action: BrowserHandler,
    browser_snapshot: BrowserHandler,
    browser_act: BrowserHandler,
    browser_wait: BrowserHandler,
    browser_screenshot: BrowserHandler,
    browser_pdf: BrowserHandler,
    browser_console_get: BrowserHandler,
    browser_errors_get: BrowserHandler,
    browser_requests_get: BrowserHandler,
    browser_response_body: BrowserHandler,
    browser_trace_start: BrowserHandler,
    browser_trace_stop: BrowserHandler,
    browser_highlight: BrowserHandler,
    browser_dialog_arm: BrowserHandler,
    browser_file_chooser_arm: BrowserHandler,
    browser_download_wait: BrowserHandler,
    browser_state: BrowserHandler,
    browser_env: BrowserHandler,
) -> Dict[str, BrowserHandler]:
    return {
        "browser_start": browser_start,
        "browser_install": browser_install,
        "browser_profiles": browser_profiles,
        "browser_tabs": browser_tabs,
        "browser_tabs_open": browser_tabs_open,
        "browser_tabs_focus": browser_tabs_focus,
        "browser_tabs_close": browser_tabs_close,
        "browser_tabs_action": browser_tabs_action,
        "browser_snapshot": browser_snapshot,
        "browser_act": browser_act,
        "browser_wait": browser_wait,
        "browser_screenshot": browser_screenshot,
        "browser_pdf": browser_pdf,
        "browser_console_get": browser_console_get,
        "browser_errors_get": browser_errors_get,
        "browser_requests_get": browser_requests_get,
        "browser_response_body": browser_response_body,
        "browser_trace_start": browser_trace_start,
        "browser_trace_stop": browser_trace_stop,
        "browser_highlight": browser_highlight,
        "browser_dialog_arm": browser_dialog_arm,
        "browser_file_chooser_arm": browser_file_chooser_arm,
        "browser_download_wait": browser_download_wait,
        "browser_state": browser_state,
        "browser_env": browser_env,
    }
