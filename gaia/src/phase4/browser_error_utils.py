from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple


OPENCLAW_NO_RETRY_HINT = (
    "Do NOT retry the same browser action immediately. "
    "Refresh snapshot and switch to an alternative action."
)

_TRANSPORT_TIMEOUT_PATTERNS = (
    "read timed out",
    "timeout",
    "timed out",
    "connection refused",
    "failed to establish a new connection",
    "max retries exceeded",
    "temporarily unavailable",
)


def add_no_retry_hint(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return OPENCLAW_NO_RETRY_HINT
    lower = text.lower()
    if any(pattern in lower for pattern in _TRANSPORT_TIMEOUT_PATTERNS):
        timeout_match = (
            re.search(r"read timeout[=:\s]*(\d+(?:\.\d+)?)", lower)
            or re.search(r"timed out after\s+(\d+(?:\.\d+)?)\s*ms", lower)
            or re.search(r"timeout[=:\s]*(\d+(?:\.\d+)?)", lower)
        )
        timeout_suffix = ""
        if timeout_match:
            raw_timeout = timeout_match.group(1)
            unit = "ms" if "timed out after" in lower else "s"
            timeout_suffix = f" (timeout={raw_timeout}{unit})"
        transport_text = (
            "Can't reach the GAIA browser control service"
            f"{timeout_suffix}. Restart the local MCP host if needed. "
            f"{OPENCLAW_NO_RETRY_HINT}"
        )
        return transport_text
    if OPENCLAW_NO_RETRY_HINT in text:
        return text
    return f"{text} | {OPENCLAW_NO_RETRY_HINT}"


def _status_reason_code(status_code: int | None) -> str | None:
    if not isinstance(status_code, int):
        return None
    if 400 <= status_code < 500:
        return "http_4xx"
    if status_code >= 500:
        return "http_5xx"
    return None


def extract_reason_fields(
    payload: Dict[str, Any] | None,
    status_code: int | None = None,
) -> Tuple[str, str]:
    data = payload or {}
    reason_code = data.get("reason_code") or data.get("error")
    reason = data.get("reason") or data.get("message") or data.get("detail")
    detail = data.get("detail")
    if isinstance(detail, dict):
        reason_code = reason_code or detail.get("reason_code")
        reason = (
            detail.get("reason")
            or detail.get("message")
            or detail.get("detail")
            or reason
        )

    if isinstance(reason, (dict, list)):
        reason = json.dumps(reason, ensure_ascii=False)

    if not reason_code:
        reason_code = _status_reason_code(status_code) or "unknown_error"
    if not reason:
        reason = "Unknown error"

    return str(reason_code), str(reason)
