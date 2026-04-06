"""Parsing helpers for goal-driven action payloads."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def parse_multi_values(raw: Optional[str]) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None

    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, dict):
        values = parsed.get("values")
        if isinstance(values, list):
            normalized = [str(item).strip() for item in values if str(item).strip()]
            if normalized:
                return normalized
        single = parsed.get("value")
        if single is not None and str(single).strip():
            return [str(single).strip()]

    if "," in text:
        values = [part.strip() for part in text.split(",") if part.strip()]
        if values:
            return values
    return [text]


def parse_wait_payload(raw: Optional[Any]) -> Dict[str, Any]:
    if isinstance(raw, (int, float)):
        return {"time_ms": max(0, int(raw))}

    if isinstance(raw, dict):
        parsed = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return {"time_ms": 1000}
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None

        if isinstance(parsed, (int, float)):
            return {"time_ms": max(0, int(parsed))}

        if not isinstance(parsed, dict):
            if text.isdigit():
                return {"time_ms": max(0, int(text))}
            return {"text": text}

    text = str(raw or "").strip()
    if not text:
        return {"time_ms": 1000}

    if isinstance(parsed, dict):
        payload: Dict[str, Any] = {}
        if bool(parsed.get("for_network_idle")) or bool(parsed.get("forNetworkIdle")):
            payload["load_state"] = "networkidle"
        key_aliases = {
            "ms": "time_ms",
            "duration_ms": "time_ms",
            "durationMs": "time_ms",
            "sleep_ms": "time_ms",
            "sleepMs": "time_ms",
            "timeout": "timeout_ms",
            "timeMs": "time_ms",
            "timeoutMs": "timeout_ms",
            "textGone": "text_gone",
            "loadState": "load_state",
            "fn": "js",
        }
        for key in (
            "time_ms",
            "timeMs",
            "ms",
            "duration_ms",
            "durationMs",
            "sleep_ms",
            "sleepMs",
            "timeout",
            "timeout_ms",
            "timeoutMs",
            "selector",
            "selector_state",
            "text",
            "text_gone",
            "textGone",
            "url",
            "load_state",
            "loadState",
            "js",
            "fn",
        ):
            value = parsed.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            normalized_key = key_aliases.get(key, key)
            payload[normalized_key] = value
        if payload:
            return payload
        return {"time_ms": 1000}

    if text.isdigit():
        return {"time_ms": max(0, int(text))}
    return {"text": text}
