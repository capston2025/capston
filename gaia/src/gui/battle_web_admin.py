"""Small Human vs GAIA battle-board admin client for the desktop GUI."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

BATTLE_DEFAULT_SITE_URL = "https://gaia-battle-web.vercel.app"
BATTLE_DEFAULT_SESSION_ID = "battle-live"


class BattleWebError(RuntimeError):
    """Raised when the battle-board admin API cannot complete an operation."""


def normalize_battle_site_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    return value or BATTLE_DEFAULT_SITE_URL


def _admin_headers(token: str = "") -> dict[str, str]:
    headers = {"Accept": "application/json"}
    clean_token = str(token or "").strip()
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"
        headers["x-battle-reset-token"] = clean_token
    return headers


def _request_json(url: str, *, method: str = "GET", token: str = "", timeout: float = 8.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers=_admin_headers(token),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            if not body.strip():
                return {}
            payload = json.loads(body)
            return payload if isinstance(payload, dict) else {"data": payload}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BattleWebError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BattleWebError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise BattleWebError(f"invalid json response: {exc}") from exc


def reset_battle_timer(
    *,
    site_url: str = BATTLE_DEFAULT_SITE_URL,
    session_id: str = BATTLE_DEFAULT_SESSION_ID,
    token: str = "",
) -> dict[str, Any]:
    clean_site = normalize_battle_site_url(site_url)
    clean_session = str(session_id or "").strip() or BATTLE_DEFAULT_SESSION_ID
    query = urllib.parse.urlencode({"sessionId": clean_session, "scope": "timer"})
    return _request_json(f"{clean_site}/api/session?{query}", method="DELETE", token=token)


def list_battle_records(
    *,
    site_url: str = BATTLE_DEFAULT_SITE_URL,
    session_id: str = BATTLE_DEFAULT_SESSION_ID,
) -> list[dict[str, Any]]:
    clean_site = normalize_battle_site_url(site_url)
    clean_session = str(session_id or "").strip() or BATTLE_DEFAULT_SESSION_ID
    query = urllib.parse.urlencode({"sessionId": clean_session})
    payload = _request_json(f"{clean_site}/api/records?{query}")
    records = payload.get("records")
    return [dict(item) for item in records if isinstance(item, Mapping)] if isinstance(records, list) else []


def delete_battle_record(
    *,
    site_url: str = BATTLE_DEFAULT_SITE_URL,
    session_id: str = BATTLE_DEFAULT_SESSION_ID,
    record_id: str,
    token: str = "",
) -> dict[str, Any]:
    clean_site = normalize_battle_site_url(site_url)
    clean_session = str(session_id or "").strip() or BATTLE_DEFAULT_SESSION_ID
    clean_record_id = str(record_id or "").strip()
    if not clean_record_id:
        raise BattleWebError("record_id is required")
    query = urllib.parse.urlencode({"sessionId": clean_session, "recordId": clean_record_id})
    return _request_json(f"{clean_site}/api/records?{query}", method="DELETE", token=token)
