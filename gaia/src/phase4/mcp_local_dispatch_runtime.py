from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import requests

from gaia.src.phase4.mcp_openclaw_dispatch_runtime import (
    dispatch_openclaw_action,
    dispatch_openclaw_close,
    dispatch_openclaw_console_logs,
    get_openclaw_session_url,
)

_OPENCLAW_BROWSER_ACTIONS = {
    "browser_tabs_focus",
    "browser_snapshot",
    "browser_act",
    "browser_wait",
    "browser_screenshot",
    "capture_screenshot",
}


@dataclass
class DispatchResult:
    status_code: int
    payload: Dict[str, Any]
    text: str = ""


def current_browser_backend(raw_base_url: str | None = None) -> str:
    del raw_base_url
    return "openclaw"


def execute_mcp_action(
    raw_base_url: str | None,
    *,
    action: str,
    params: Dict[str, Any],
    timeout: Any = None,
) -> DispatchResult:
    request_params = dict(params or {})
    if action in _OPENCLAW_BROWSER_ACTIONS:
        status_code, payload, text = dispatch_openclaw_action(
            raw_base_url,
            action=action,
            params=request_params,
            timeout=timeout,
        )
        return DispatchResult(status_code=int(status_code), payload=payload, text=str(text or ""))

    if action == "get_console_logs":
        status_code, payload, text = dispatch_openclaw_console_logs(
            raw_base_url,
            session_id=str(request_params.get("session_id") or "default"),
            level=str(request_params.get("type") or request_params.get("level") or ""),
            limit=int(request_params.get("limit") or 100),
            timeout=timeout,
        )
        return DispatchResult(status_code=int(status_code), payload=payload, text=str(text or ""))

    if action == "get_current_url":
        session_id = str(request_params.get("session_id") or "default")
        return DispatchResult(
            status_code=200,
            payload={
                "success": True,
                "reason_code": "ok",
                "url": get_openclaw_session_url(session_id),
            },
            text="",
        )

    response = requests.post(
        f"{str(raw_base_url or '').rstrip('/')}/execute",
        json={"action": action, "params": request_params},
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text or "invalid_json_response"}
    return DispatchResult(status_code=int(response.status_code), payload=payload, text=str(response.text or ""))


def close_mcp_session(
    raw_base_url: str | None,
    *,
    session_id: str,
    timeout: Any = None,
) -> DispatchResult:
    status_code, payload, text = dispatch_openclaw_close(
        raw_base_url,
        session_id=session_id,
        timeout=timeout,
    )
    return DispatchResult(status_code=int(status_code), payload=payload, text=str(text or ""))
