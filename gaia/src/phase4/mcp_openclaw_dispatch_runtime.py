from __future__ import annotations

import base64
from datetime import date, timedelta
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse, urlunparse

import requests

from gaia.src.phase4.embedded_openclaw_runtime import ensure_embedded_openclaw_base_url
from gaia.src.phase4.browser_context_manager import build_auto_follow_state_update
from gaia.src.phase4.mcp_ref.snapshot_helpers import (
    _build_context_snapshot_from_elements,
    _build_role_snapshot_from_elements,
    _build_role_tree,
    _role_snapshot_stats,
)
from gaia.src.phase4.mcp_ref.actionability_errors import extract_pointer_interceptor

_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_BASE_URL_CACHE: Dict[str, str] = {}
_DEFAULT_OPENCLAW_REQUEST_TIMEOUT_S = 12.0
_TABS_CACHE_MAX_AGE_S = 2.0

_INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "treeitem",
}

_SURFACED_CONTENT_ROLES = {
    "heading",
    "paragraph",
    "generic",
    "cell",
    "columnheader",
    "link",
    "article",
    "region",
    "navigation",
    "main",
    "banner",
    "complementary",
}

_CONTAINER_CANDIDATE_ROLES = {
    "article",
    "region",
    "main",
    "navigation",
    "list",
    "listitem",
    "row",
    "rowgroup",
    "grid",
    "cell",
    "gridcell",
    "tabpanel",
    "tablist",
    "dialog",
    "form",
    "group",
    "menu",
    "menubar",
    "toolbar",
    "complementary",
    "banner",
}

_ROLE_TO_TAG = {
    "button": "button",
    "link": "a",
    "textbox": "input",
    "searchbox": "input",
    "checkbox": "input",
    "radio": "input",
    "combobox": "select",
    "listbox": "select",
    "option": "option",
    "heading": "h2",
    "listitem": "li",
    "article": "article",
    "region": "section",
    "main": "main",
    "navigation": "nav",
    "tab": "button",
    "switch": "button",
    "menuitem": "button",
    "menuitemcheckbox": "button",
    "menuitemradio": "button",
    "treeitem": "button",
}

_LOGIN_HINT_TOKENS = (
    "로그인",
    "login",
    "log in",
    "sign in",
    "signin",
    "auth",
    "인증",
    "회원가입",
    "signup",
    "sign up",
    "register",
)

_LOGOUT_HINT_TOKENS = (
    "로그아웃",
    "logout",
    "log out",
    "sign out",
    "signout",
)

_PASSWORD_HINT_TOKENS = (
    "password",
    "비밀번호",
    "passwd",
    "pwd",
)

_IDENTIFIER_HINT_TOKENS = (
    "email",
    "이메일",
    "아이디",
    "id",
    "username",
    "user name",
    "user id",
    "학번",
)

_MODAL_HINT_TOKENS = (
    "modal",
    "dialog",
    "popup",
    "sheet",
    "drawer",
    "overlay",
    "backdrop",
)

_CUSTOM_OPTION_DANGEROUS_TOKENS = (
    "로그인",
    "login",
    "log in",
    "sign in",
    "signin",
    "회원가입",
    "signup",
    "sign up",
    "register",
    "장바구니",
    "cart",
    "checkout",
    "결제",
    "구매",
    "주문",
    "buy",
    "purchase",
    "삭제",
    "delete",
    "제거",
    "remove",
    "저장",
    "save",
    "작성",
    "등록",
    "submit",
    "예약",
    "예매",
    "신청",
)


def _openclaw_snapshot_max_chars_param() -> int:
    """Return the OpenClaw snapshot maxChars query value.

    OpenClaw's route treats an explicit non-positive ``maxChars`` as no cap.
    Defaulting to 0 keeps long reading surfaces, such as reviews/comments,
    available to the LLM instead of silently trimming them at the host default.
    """
    raw_value = str(os.getenv("GAIA_OPENCLAW_SNAPSHOT_MAX_CHARS", "0") or "0").strip()
    try:
        value = int(raw_value)
    except Exception:
        return 0
    return max(0, value)


def _openclaw_dom_text_evidence_enabled() -> bool:
    try:
        raw_value = str(os.getenv("GAIA_OPENCLAW_DOM_TEXT_EVIDENCE", "1")).strip().lower()
    except Exception:
        raw_value = "1"
    return raw_value not in {"0", "false", "no", "off"}


def _openclaw_dom_text_block_limit() -> int:
    raw_value = str(os.getenv("GAIA_OPENCLAW_DOM_TEXT_BLOCK_LIMIT", "80") or "80").strip()
    try:
        value = int(raw_value)
    except Exception:
        return 80
    return max(0, min(value, 200))


def _resolve_base_url(raw_base_url: str | None) -> str:
    explicit_base_url = str(os.getenv("GAIA_OPENCLAW_BASE_URL", "") or "").strip()
    base_url = explicit_base_url or str(raw_base_url or "").strip()
    if not base_url:
        return ensure_embedded_openclaw_base_url()
    if "://" not in base_url:
        base_url = f"http://{base_url}"
    base_url = base_url.rstrip("/")
    cached = _BASE_URL_CACHE.get(base_url)
    if cached:
        return cached

    def _looks_like_browser_server(candidate: str) -> bool:
        try:
            response = requests.get(candidate, headers=_headers(), timeout=1.5)
        except Exception:
            return False
        if response.status_code >= 400:
            return False
        try:
            data = response.json()
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        return "enabled" in data and "profile" in data and ("cdpPort" in data or "cdpUrl" in data)

    candidates = [base_url]
    parsed = urlparse(base_url)
    try:
        port = int(parsed.port or 0)
    except Exception:
        port = 0
    if port > 0:
        browser_port = port + 2
        netloc = f"{parsed.hostname}:{browser_port}"
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth += f":{parsed.password}"
            netloc = f"{auth}@{netloc}"
        derived = urlunparse((parsed.scheme or "http", netloc, "", "", "", "")).rstrip("/")
        if derived not in candidates:
            candidates.append(derived)

    for candidate in candidates:
        if _looks_like_browser_server(candidate):
            _BASE_URL_CACHE[base_url] = candidate
            return candidate

    if not explicit_base_url:
        embedded = ensure_embedded_openclaw_base_url()
        _BASE_URL_CACHE[base_url] = embedded
        return embedded

    _BASE_URL_CACHE[base_url] = base_url
    return base_url


def _profile_name(raw: Any = None) -> str:
    return str(raw or os.getenv("GAIA_OPENCLAW_PROFILE", "openclaw") or "openclaw").strip() or "openclaw"


def _session_profile(session_id: str, explicit_profile: Any = None) -> str:
    state = _session_state(session_id)
    profile = _profile_name(explicit_profile or state.get("profile") or None)
    previous = str(state.get("profile") or "").strip()
    if previous and previous != profile:
        state["target_id"] = ""
        state["current_url"] = ""
        _clear_snapshot_cache(state)
        _clear_tabs_cache(state)
    state["profile"] = profile
    return profile


def _headers() -> Dict[str, str]:
    headers: Dict[str, str] = {}
    token = str(os.getenv("GAIA_OPENCLAW_TOKEN", "")).strip()
    password = str(os.getenv("GAIA_OPENCLAW_PASSWORD", "")).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif password:
        headers["x-openclaw-password"] = password
    return headers


def _coerce_request_timeout(timeout: Any) -> Any:
    if timeout is not None:
        return timeout
    raw = str(os.getenv("GAIA_OPENCLAW_REQUEST_TIMEOUT_S", "") or "").strip()
    try:
        total_timeout_s = float(raw) if raw else _DEFAULT_OPENCLAW_REQUEST_TIMEOUT_S
    except Exception:
        total_timeout_s = _DEFAULT_OPENCLAW_REQUEST_TIMEOUT_S
    total_timeout_s = max(2.0, float(total_timeout_s))
    connect_timeout_s = min(3.0, max(1.0, total_timeout_s / 3.0))
    return (connect_timeout_s, total_timeout_s)


def _request(
    method: str,
    *,
    base_url: str,
    path: str,
    timeout: Any = None,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, Any], str]:
    query = dict(params or {})
    payload_profile = ""
    if isinstance(payload, dict):
        payload_profile = str(payload.get("profile") or "").strip()
    query.setdefault("profile", _profile_name(query.get("profile") or payload_profile or None))
    url = f"{base_url}{path}"
    response = requests.request(
        method=method.upper(),
        url=url,
        params=query,
        json=payload,
        headers=_headers(),
        timeout=_coerce_request_timeout(timeout),
    )
    try:
        data = response.json()
    except Exception:
        data = {"error": response.text or "invalid_json_response"}
    return int(response.status_code), data, str(response.text or "")


def _normalize_url(url: str | None) -> str:
    return str(url or "").strip()


def _is_about_blank_url(url: str | None) -> bool:
    return _normalize_url(url).lower() == "about:blank"


def _session_state(session_id: str) -> Dict[str, Any]:
    with _SESSION_LOCK:
        return _SESSIONS.setdefault(
            session_id,
            {
                "target_id": "",
                "current_url": "",
                "profile": "",
                "snapshot_counter": 0,
                "last_snapshot_id": "",
                "last_snapshot_payload": {},
                "last_tabs_payload": {},
                "last_tabs_target_id": "",
                "last_tabs_profile": "",
                "last_tabs_observed_at": 0.0,
            },
        )


def _clear_snapshot_cache(state: Dict[str, Any]) -> None:
    state["last_snapshot_id"] = ""
    state["last_snapshot_payload"] = {}


def _clear_tabs_cache(state: Dict[str, Any]) -> None:
    state["last_tabs_payload"] = {}
    state["last_tabs_target_id"] = ""
    state["last_tabs_profile"] = ""
    state["last_tabs_observed_at"] = 0.0


def _clear_session_target(session_id: str) -> None:
    with _SESSION_LOCK:
        state = _SESSIONS.setdefault(session_id, {})
        state["target_id"] = ""
        _clear_snapshot_cache(state)
        _clear_tabs_cache(state)


def _remember_tabs_payload(
    *,
    state: Dict[str, Any],
    target_id: str,
    profile: str,
    payload: Optional[Dict[str, Any]],
) -> None:
    if not isinstance(payload, dict) or not payload:
        return
    state["last_tabs_payload"] = dict(payload)
    state["last_tabs_target_id"] = str(target_id or "").strip()
    state["last_tabs_profile"] = str(profile or "").strip()
    state["last_tabs_observed_at"] = time.monotonic()


def _cached_tabs_payload(
    *,
    state: Dict[str, Any],
    target_id: str,
    profile: str,
) -> Optional[Dict[str, Any]]:
    cached = state.get("last_tabs_payload")
    if not isinstance(cached, dict) or not cached:
        return None
    if str(state.get("last_tabs_target_id") or "").strip() != str(target_id or "").strip():
        return None
    if str(state.get("last_tabs_profile") or "").strip() != str(profile or "").strip():
        return None
    try:
        age_s = time.monotonic() - float(state.get("last_tabs_observed_at") or 0.0)
    except Exception:
        return None
    if age_s < 0 or age_s > _TABS_CACHE_MAX_AGE_S:
        return None
    return dict(cached)


def _cached_snapshot_payload(
    *,
    state: Dict[str, Any],
    snapshot_id: str,
    target_id: str,
) -> Optional[Dict[str, Any]]:
    requested_snapshot_id = str(snapshot_id or "").strip()
    if not requested_snapshot_id:
        return None
    if requested_snapshot_id != str(state.get("last_snapshot_id") or "").strip():
        return None
    cached = state.get("last_snapshot_payload")
    if not isinstance(cached, dict) or not cached:
        return None
    if requested_snapshot_id != str(cached.get("snapshot_id") or "").strip():
        return None
    cached_target_id = str(cached.get("targetId") or cached.get("tab_id") or "").strip()
    if cached_target_id and cached_target_id != str(target_id or "").strip():
        return None
    if bool(cached.get("scope_applied")):
        return None
    return dict(cached)


def _target_missing(status_code: int, data: Dict[str, Any], text: str) -> bool:
    message = " ".join(
        [
            str(status_code),
            str((data or {}).get("error") or ""),
            str(text or ""),
        ]
    ).lower()
    return (
        status_code in {404, 409}
        or "tab not found" in message
        or "browser not running" in message
        or "target" in message and "not found" in message
    )


def _choose_existing_tab(
    payload: Optional[Dict[str, Any]],
    *,
    allow_about_blank: bool = False,
) -> Optional[Dict[str, Any]]:
    descriptors = _extract_tab_descriptors(payload)
    if not descriptors:
        return None
    candidates = [
        item
        for item in descriptors
        if str(item.get("target_id") or "").strip()
        and (allow_about_blank or not _is_about_blank_url(str(item.get("url") or "")))
    ]
    if not candidates:
        return None
    active = [item for item in candidates if bool(item.get("active"))]
    return active[0] if active else candidates[0]


def _adopt_existing_target(
    *,
    base_url: str,
    state: Dict[str, Any],
    profile: str,
    timeout: Any,
) -> bool:
    tabs_payload = _tabs_payload_for_target(
        base_url=base_url,
        target_id="",
        profile=profile,
        timeout=timeout,
    )
    chosen = _choose_existing_tab(tabs_payload, allow_about_blank=False)
    if not chosen:
        return False
    target_id = str(chosen.get("target_id") or "").strip()
    current_url = _normalize_url(str(chosen.get("url") or ""))
    if not target_id:
        return False
    state["target_id"] = target_id
    state["current_url"] = current_url
    _clear_snapshot_cache(state)
    _clear_tabs_cache(state)
    return True


def _cleanup_about_blank_tabs(
    *,
    base_url: str,
    profile: str,
    timeout: Any,
    keep_target_id: str = "",
) -> None:
    try:
        tabs_payload = _tabs_payload_for_target(
            base_url=base_url,
            target_id=str(keep_target_id or "").strip(),
            profile=profile,
            timeout=timeout,
        )
    except Exception:
        return
    descriptors = _extract_tab_descriptors(tabs_payload)
    if not any(not _is_about_blank_url(str(item.get("url") or "")) for item in descriptors):
        return
    for item in descriptors:
        target_id = str(item.get("target_id") or "").strip()
        if not target_id or target_id == str(keep_target_id or "").strip():
            continue
        if not _is_about_blank_url(str(item.get("url") or "")):
            continue
        try:
            _request(
                "DELETE",
                base_url=base_url,
                path=f"/tabs/{quote(target_id, safe='')}",
                timeout=timeout,
                params={"profile": profile},
            )
        except Exception:
            pass


def _close_profile_tabs(
    *,
    base_url: str,
    profile: str,
    timeout: Any,
    keep_target_id: str = "",
) -> List[Dict[str, Any]]:
    """Close existing tabs in the dedicated OpenClaw profile.

    Benchmark cold-state isolation keeps the browser process warm but should not
    keep scenario tabs warm. Closing profile tabs before a reset prevents old
    pages from piling up while preserving the server/browser process itself.
    """

    try:
        tabs_payload = _tabs_payload_for_target(
            base_url=base_url,
            target_id=str(keep_target_id or "").strip(),
            profile=profile,
            timeout=timeout,
        )
    except Exception:
        return []
    closed: List[Dict[str, Any]] = []
    keep = str(keep_target_id or "").strip()
    for item in _extract_tab_descriptors(tabs_payload):
        target_id = str(item.get("target_id") or "").strip()
        if not target_id or target_id == keep:
            continue
        status_code = 0
        reason = ""
        try:
            status_code, data, text = _request(
                "DELETE",
                base_url=base_url,
                path=f"/tabs/{quote(target_id, safe='')}",
                timeout=timeout,
                params={"profile": profile},
            )
            if status_code >= 400:
                reason = str((data or {}).get("error") or text or status_code)
        except Exception as exc:
            reason = str(exc)
        closed.append(
            {
                "targetId": target_id,
                "url": str(item.get("url") or ""),
                "ok": status_code > 0 and status_code < 400 and not reason,
                "status_code": status_code,
                "reason": reason,
            }
        )
    return closed


def _ensure_target(
    *,
    base_url: str,
    session_id: str,
    requested_url: str,
    timeout: Any,
) -> Dict[str, Any]:
    state = _session_state(session_id)
    profile = _session_profile(session_id)
    target_id = str(state.get("target_id") or "").strip()
    current_url = _normalize_url(state.get("current_url"))
    normalized_requested = _normalize_url(requested_url)

    if not target_id:
        open_url = normalized_requested or ("" if _is_about_blank_url(current_url) else current_url)
        if not open_url:
            try:
                adopted_existing_target = _adopt_existing_target(
                    base_url=base_url,
                    state=state,
                    profile=profile,
                    timeout=timeout,
                )
            except Exception:
                adopted_existing_target = False
            if adopted_existing_target:
                _cleanup_about_blank_tabs(
                    base_url=base_url,
                    profile=profile,
                    timeout=timeout,
                    keep_target_id=str(state.get("target_id") or "").strip(),
                )
                return state
            _cleanup_about_blank_tabs(
                base_url=base_url,
                profile=profile,
                timeout=timeout,
            )
            return state
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/tabs/open",
            timeout=timeout,
            payload={"url": open_url, "profile": profile},
        )
        if status_code >= 400:
            raise RuntimeError(str(data.get("error") or text or "openclaw tabs/open failed"))
        target_id = str(data.get("targetId") or data.get("target_id") or "").strip()
        if not target_id:
            raise RuntimeError("openclaw tabs/open did not return targetId")
        state["target_id"] = target_id
        state["current_url"] = _normalize_url(data.get("url") or open_url)
        _clear_snapshot_cache(state)
        _clear_tabs_cache(state)
        current_url = str(state.get("current_url") or "")
        if not _is_about_blank_url(current_url):
            _cleanup_about_blank_tabs(
                base_url=base_url,
                profile=profile,
                timeout=timeout,
                keep_target_id=target_id,
            )

    if normalized_requested and normalized_requested != current_url:
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/navigate",
            timeout=timeout,
            payload={"targetId": target_id, "url": normalized_requested, "profile": profile},
        )
        if status_code >= 400 and _target_missing(status_code, data, text):
            _clear_session_target(session_id)
            return _ensure_target(
                base_url=base_url,
                session_id=session_id,
                requested_url=normalized_requested,
                timeout=timeout,
            )
        if status_code >= 400:
            raise RuntimeError(str(data.get("error") or text or "openclaw navigate failed"))
        target_id = str(data.get("targetId") or target_id).strip() or target_id
        state["target_id"] = target_id
        state["current_url"] = _normalize_url(data.get("url") or normalized_requested)
        _clear_snapshot_cache(state)
        _clear_tabs_cache(state)
        if not _is_about_blank_url(str(state.get("current_url") or "")):
            _cleanup_about_blank_tabs(
                base_url=base_url,
                profile=profile,
                timeout=timeout,
                keep_target_id=target_id,
            )

    return state


def get_openclaw_session_url(session_id: str) -> str:
    return str(_session_state(str(session_id or "default")).get("current_url") or "")


def dispatch_openclaw_console_logs(
    raw_base_url: str | None,
    *,
    session_id: str,
    profile: str = "",
    level: str = "",
    limit: int = 100,
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    base_url = _resolve_base_url(raw_base_url)
    normalized_session_id = str(session_id or "default")
    profile_name = _session_profile(normalized_session_id, profile)
    state = _ensure_target(
        base_url=base_url,
        session_id=normalized_session_id,
        requested_url="",
        timeout=timeout,
    )
    fallback_url = str(state.get("current_url") or "")
    target_id = str(state.get("target_id") or "").strip()
    query: Dict[str, Any] = {"targetId": target_id}
    query["profile"] = profile_name
    if str(level or "").strip():
        query["level"] = str(level).strip()
    status_code, data, text = _request(
        "GET",
        base_url=base_url,
        path="/console",
        timeout=timeout,
        params=query,
    )
    if status_code >= 400 and _target_missing(status_code, data, text):
        _clear_session_target(normalized_session_id)
        state = _ensure_target(
            base_url=base_url,
            session_id=normalized_session_id,
            requested_url=fallback_url,
            timeout=timeout,
        )
        target_id = str(state.get("target_id") or "").strip()
        query["targetId"] = target_id
        status_code, data, text = _request(
            "GET",
            base_url=base_url,
            path="/console",
            timeout=timeout,
            params=query,
        )
    if status_code >= 400:
        return _normalize_failure(status_code, data, text)

    raw_messages = list((data or {}).get("messages") or [])
    capped_limit = max(0, int(limit or 0))
    items = raw_messages[-capped_limit:] if capped_limit else raw_messages
    payload = {
        "success": True,
        "reason_code": "ok",
        "items": items,
        "meta": {
            "level": str(level or "").strip(),
            "limit": capped_limit,
            "backend": "openclaw",
        },
        "targetId": str((data or {}).get("targetId") or target_id),
        "current_url": str(state.get("current_url") or ""),
    }
    return 200, payload, ""


def ensure_openclaw_profile(
    raw_base_url: str | None,
    *,
    profile: str,
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Ensure an OpenClaw browser profile exists and is started."""
    base_url = _resolve_base_url(raw_base_url)
    profile_name = _profile_name(profile)
    status_code, data, text = _request(
        "GET",
        base_url=base_url,
        path="/profiles",
        timeout=timeout,
        params={"profile": profile_name},
    )
    if status_code >= 400:
        return _normalize_failure(status_code, data, text)

    profiles = data.get("profiles") if isinstance(data, dict) else []
    known_names: set[str] = set()
    if isinstance(profiles, list):
        for item in profiles:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("profile") or item.get("id") or "").strip()
            if name:
                known_names.add(name)

    created = False
    if profile_name not in known_names:
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/profiles/create",
            timeout=timeout,
            payload={"name": profile_name, "profile": profile_name},
        )
        message = str((data or {}).get("error") or text or "").lower()
        if status_code >= 400 and "already exists" not in message:
            return _normalize_failure(status_code, data, text)
        created = status_code < 400

    status_code, data, text = _request(
        "POST",
        base_url=base_url,
        path="/start",
        timeout=timeout,
        params={"profile": profile_name},
    )
    if status_code >= 400:
        return _normalize_failure(status_code, data, text)
    ok = bool((data or {}).get("ok", True))
    return (
        200,
        {
            "success": ok,
            "ok": ok,
            "reason_code": "ok" if ok else "openclaw_profile_start_failed",
            "profile": profile_name,
            "created": created,
        },
        "",
    )


def delete_openclaw_profile(
    raw_base_url: str | None,
    *,
    profile: str,
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Stop and delete an OpenClaw browser profile."""
    base_url = _resolve_base_url(raw_base_url)
    profile_name = _profile_name(profile)
    stop_status, _stop_data, _stop_text = _request(
        "POST",
        base_url=base_url,
        path="/stop",
        timeout=timeout,
        params={"profile": profile_name},
    )
    path = f"/profiles/{requests.utils.quote(profile_name, safe='')}"
    status_code, data, text = _request(
        "DELETE",
        base_url=base_url,
        path=path,
        timeout=timeout,
        params={"profile": profile_name},
    )
    if status_code >= 400:
        message = str((data or {}).get("error") or text or "").lower()
        if "not found" not in message:
            return _normalize_failure(status_code, data, text)
    return (
        200,
        {
            "success": True,
            "ok": True,
            "reason_code": "ok",
            "profile": profile_name,
            "stopped": stop_status < 400,
            "deleted": status_code < 400,
        },
        "",
    )


_SCENARIO_DEEP_STORAGE_CLEAR_FN = r"""async () => {
  const result = { indexedDB: "unsupported", caches: "unsupported", serviceWorkers: "unsupported" };
  if (globalThis.indexedDB && typeof indexedDB.databases === "function") {
    const databases = await indexedDB.databases();
    const names = databases.map((db) => db && db.name).filter(Boolean);
    await Promise.all(names.map((name) => new Promise((resolve) => {
      const request = indexedDB.deleteDatabase(name);
      request.onsuccess = () => resolve(true);
      request.onerror = () => resolve(false);
      request.onblocked = () => resolve(false);
    })));
    result.indexedDB = names.length;
  }
  if (globalThis.caches && typeof caches.keys === "function") {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
    result.caches = keys.length;
  }
  if (navigator.serviceWorker && typeof navigator.serviceWorker.getRegistrations === "function") {
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
    result.serviceWorkers = registrations.length;
  }
  return result;
}"""


def reset_openclaw_scenario_state(
    raw_base_url: str | None,
    *,
    session_id: str,
    url: str,
    profile: str = "",
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    """Clear one scenario's browser state while keeping the OpenClaw process warm.

    Cookies are browser-context wide. localStorage/sessionStorage are origin scoped,
    so the reset tab first navigates to the scenario start URL before clearing them.
    """

    base_url = _resolve_base_url(raw_base_url)
    reset_session_id = str(session_id or "benchmark-reset").strip() or "benchmark-reset"
    profile_name = _session_profile(reset_session_id, profile)
    target_id = ""
    closed_stale_tabs: List[Dict[str, Any]] = []
    clear_results: List[Dict[str, Any]] = []
    try:
        closed_stale_tabs = _close_profile_tabs(
            base_url=base_url,
            profile=profile_name,
            timeout=timeout,
        )
        state = _ensure_target(
            base_url=base_url,
            session_id=reset_session_id,
            requested_url=_normalize_url(url) or "about:blank",
            timeout=timeout,
        )
        target_id = str(state.get("target_id") or "").strip()
        if not target_id:
            return _normalize_failure(409, {"error": "openclaw_reset_target_missing"}, "")

        for label, path in (
            ("cookies", "/cookies/clear"),
            ("localStorage", "/storage/local/clear"),
            ("sessionStorage", "/storage/session/clear"),
        ):
            status_code, data, text = _request(
                "POST",
                base_url=base_url,
                path=path,
                timeout=timeout,
                payload={"targetId": target_id, "profile": profile_name},
            )
            ok = status_code < 400 and bool((data or {}).get("ok", True))
            clear_results.append(
                {
                    "kind": label,
                    "ok": ok,
                    "status_code": status_code,
                    "reason": "" if ok else str((data or {}).get("error") or text or "clear_failed"),
                }
            )
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/act",
            timeout=timeout,
            payload={
                "targetId": target_id,
                "profile": profile_name,
                "kind": "evaluate",
                "fn": _SCENARIO_DEEP_STORAGE_CLEAR_FN,
            },
        )
        ok = status_code < 400 and bool((data or {}).get("ok", True))
        clear_results.append(
            {
                "kind": "indexedDB/cache/serviceWorker",
                "ok": ok,
                "status_code": status_code,
                "reason": "" if ok else str((data or {}).get("error") or text or "clear_failed"),
            }
        )
    except Exception as exc:
        return _normalize_failure(500, {"error": f"openclaw_scenario_state_reset_failed: {exc}"}, "")
    finally:
        if target_id:
            try:
                _request(
                    "DELETE",
                    base_url=base_url,
                    path=f"/tabs/{quote(target_id, safe='')}",
                    timeout=timeout,
                    params={"profile": profile_name},
                )
            except Exception:
                pass
        _clear_session_target(reset_session_id)

    failed = [item for item in clear_results if not bool(item.get("ok"))]
    if failed:
        reason = "; ".join(
            f"{item.get('kind')}: {item.get('reason') or item.get('status_code')}" for item in failed
        )
        return _normalize_failure(500, {"error": f"openclaw_scenario_state_reset_incomplete: {reason}"}, "")

    return (
        200,
        {
            "success": True,
            "ok": True,
            "reason_code": "ok",
            "profile": profile_name,
            "targetId": target_id,
            "url": _normalize_url(url) or "about:blank",
            "cleared": clear_results,
            "closed_stale_tabs": closed_stale_tabs,
            "closed_stale_tab_count": len(closed_stale_tabs),
        },
        "",
    )


def _role_to_tag(role: str) -> str:
    return _ROLE_TO_TAG.get(str(role or "").strip().lower(), "div")


def _element_type_for_role(role: str) -> str:
    lowered = str(role or "").strip().lower()
    if lowered in {"button", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem", "switch"}:
        return "button"
    if lowered in {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "option"}:
        return "input"
    if lowered == "link":
        return "link"
    return "content"


def _should_surface_role_node(role: str, name: str, interactive: bool) -> bool:
    lowered = str(role or "").strip().lower()
    label = str(name or "").strip()
    if interactive:
        return True
    if lowered in _SURFACED_CONTENT_ROLES and label:
        return True
    return False


def _build_role_ref_context(
    snapshot: str,
    refs: Dict[str, Dict[str, Any]],
) -> Tuple[
    List[Dict[str, Any]],
    Dict[str, Dict[str, Any]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    set[str],
    Dict[str, int],
]:
    tree = _build_role_tree(snapshot, refs)
    tree_by_ref = {
        str(node.get("ref") or "").strip(): node
        for node in tree
        if str(node.get("ref") or "").strip()
    }
    text_by_raw_ref: Dict[str, List[str]] = {}
    ref_line_index: Dict[str, int] = {}
    line_meta: List[Dict[str, Any]] = []
    stack: List[Tuple[int, str]] = []
    pointer_like_refs: set[str] = set()
    for line_index, raw_line in enumerate(str(snapshot or "").splitlines()):
        line = raw_line.rstrip("\n")
        stripped = line.lstrip()
        if not stripped.startswith("-"):
            continue
        indent = max(0, (len(line) - len(stripped)) // 2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        ref_match = re.search(r"\[ref=([^\]]+)\]", stripped)
        current_ref = str(ref_match.group(1) or "").strip() if ref_match else ""
        if current_ref:
            ref_line_index[current_ref] = line_index
            if "[cursor=pointer]" in stripped:
                pointer_like_refs.add(current_ref)
        stack.append((indent, current_ref))
        text_match = re.match(r"-\s*text:\s*(.+)$", stripped)
        inline_label = ""
        if not text_match:
            quoted_match = re.match(r'-\s*[^"]+"([^"]+)"(?:\s*\[[^\]]+\])*', stripped)
            colon_match = re.match(r"-\s*[^\[]+\[ref=[^\]]+\](?:\s*\[[^\]]+\])*\s*:\s*(.+)$", stripped)
            if quoted_match:
                inline_label = str(quoted_match.group(1) or "").strip()
            elif colon_match:
                inline_label = str(colon_match.group(1) or "").strip()
        line_meta.append(
            {
                "index": line_index,
                "indent": indent,
                "ref": current_ref,
                "text": str(text_match.group(1) or "").strip() if text_match else inline_label,
            }
        )
        if current_ref and current_ref not in tree_by_ref:
            parent_ref = ""
            for _, candidate_ref in reversed(stack[:-1]):
                candidate_ref = str(candidate_ref or "").strip()
                if candidate_ref:
                    parent_ref = candidate_ref
                    break
            meta = (refs.get(current_ref) or {}) if isinstance(refs, dict) else {}
            fallback_node = {
                "depth": indent,
                "role": str(meta.get("role") or "").strip().lower() or "generic",
                "name": str(meta.get("name") or inline_label or "").strip(),
                "ref": current_ref,
                "nth": meta.get("nth"),
                "parent_ref": parent_ref or None,
                "line": stripped,
                "ancestor_names": [],
            }
            tree_by_ref[current_ref] = fallback_node
            tree.append(fallback_node)
        if text_match or inline_label:
            text_value = str(text_match.group(1) or "").strip() if text_match else inline_label
            if not text_value:
                continue
            carrier_ref = current_ref or ""
            if not carrier_ref:
                for _, candidate_ref in reversed(stack):
                    if candidate_ref:
                        carrier_ref = candidate_ref
                        break
            if carrier_ref:
                bucket = text_by_raw_ref.setdefault(carrier_ref, [])
                if text_value not in bucket:
                    bucket.append(text_value)
            for _, candidate_ref in reversed(stack):
                candidate_ref = str(candidate_ref or "").strip()
                if not candidate_ref or candidate_ref == carrier_ref:
                    continue
                if candidate_ref not in pointer_like_refs:
                    continue
                pointer_bucket = text_by_raw_ref.setdefault(candidate_ref, [])
                if text_value not in pointer_bucket:
                    pointer_bucket.append(text_value)
    nearby_text_by_raw_ref: Dict[str, List[str]] = {}
    text_lines = [item for item in line_meta if str(item.get("text") or "").strip()]
    all_raw_refs: List[str] = []
    for raw_ref in list(tree_by_ref.keys()) + list(refs.keys()):
        raw_ref_id = str(raw_ref or "").strip()
        if raw_ref_id and raw_ref_id not in all_raw_refs:
            all_raw_refs.append(raw_ref_id)
    for raw_ref_id in all_raw_refs:
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        ref_idx = ref_line_index.get(raw_ref_id)
        if ref_idx is None:
            continue
        own_name = str((meta or {}).get("name") or "").strip()
        own_role = str((meta or {}).get("role") or "").strip().lower()
        interactive_candidate = own_role in _INTERACTIVE_ROLES or raw_ref_id in pointer_like_refs
        nearby: List[Tuple[int, str]] = []
        for item in text_lines:
            text_value = str(item.get("text") or "").strip()
            if not text_value or text_value == own_name:
                continue
            distance = abs(int(item.get("index") or 0) - ref_idx)
            if distance > 8:
                continue
            nearby.append((distance, text_value))
        nearby.sort(key=lambda pair: pair[0])
        picked: List[str] = []
        if interactive_candidate:
            semantic_candidates: List[str] = []
            for _, text_value in nearby:
                normalized = re.sub(r"\s+", " ", text_value.lower()).strip()
                is_content_like = bool(
                    "(" in text_value
                    or ")" in text_value
                    or len(normalized) >= 8
                    or len(normalized.split()) >= 2
                )
                if not is_content_like or text_value in semantic_candidates:
                    continue
                semantic_candidates.append(text_value)
                if len(semantic_candidates) >= 2:
                    break
            for text_value in semantic_candidates:
                if text_value not in picked:
                    picked.append(text_value)
        for _, text_value in nearby:
            if text_value not in picked:
                picked.append(text_value)
            if len(picked) >= (4 if interactive_candidate else 3):
                break
        if picked:
            nearby_text_by_raw_ref[raw_ref_id] = picked
    return tree, tree_by_ref, text_by_raw_ref, nearby_text_by_raw_ref, pointer_like_refs, ref_line_index


def _css_attr_value(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _frame_descendant_selector_for_role_node(role: str, name: str, tag: str) -> str:
    cleaned_name = str(name or "").strip()
    lowered_role = str(role or "").strip().lower()
    lowered_tag = str(tag or "").strip().lower()
    if cleaned_name:
        quoted = _css_attr_value(cleaned_name)
        if lowered_role in {"textbox", "searchbox", "combobox"}:
            return (
                f'[aria-label="{quoted}"], '
                f'input[placeholder="{quoted}"], '
                f'textarea[placeholder="{quoted}"], '
                f'[role="{_css_attr_value(lowered_role)}"][aria-label="{quoted}"]'
            )
        return f'[aria-label="{quoted}"]'
    if lowered_tag in {"input", "textarea"}:
        return lowered_tag
    if lowered_role in {"textbox", "searchbox"}:
        return f'[role="{_css_attr_value(lowered_role)}"]'
    return "[contenteditable=\"true\"]"


def _frame_selectors_by_iframe_ref(
    tree: List[Dict[str, Any]],
    refs: Dict[str, Dict[str, Any]],
    frame_descriptors: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, str]:
    iframe_refs: List[str] = []
    for node in tree:
        if not isinstance(node, dict):
            continue
        raw_ref = str(node.get("ref") or "").strip()
        if not raw_ref:
            continue
        meta = (refs.get(raw_ref) or {}) if isinstance(refs, dict) else {}
        role = str(meta.get("role") or node.get("role") or "").strip().lower()
        if role == "iframe":
            iframe_refs.append(raw_ref)
    if not iframe_refs:
        return {}

    descriptors = [item for item in list(frame_descriptors or []) if isinstance(item, dict)]
    visible_descriptors = [
        item
        for item in descriptors
        if bool(item.get("visible")) and str(item.get("selector") or "").strip()
    ]
    usable_descriptors = visible_descriptors or [
        item for item in descriptors if str(item.get("selector") or "").strip()
    ]
    selector_by_ref: Dict[str, str] = {}
    for index, iframe_ref in enumerate(iframe_refs):
        if index < len(usable_descriptors):
            selector = str(usable_descriptors[index].get("selector") or "").strip()
        else:
            selector = f"iframe >> nth={index}"
        if selector:
            selector_by_ref[iframe_ref] = selector
    return selector_by_ref


def _frame_scope_for_raw_ref(
    *,
    raw_ref_id: str,
    tree_by_ref: Dict[str, Dict[str, Any]],
    iframe_selector_by_ref: Dict[str, str],
) -> Tuple[str, str]:
    current = str((tree_by_ref.get(raw_ref_id) or {}).get("parent_ref") or "").strip()
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        selector = str(iframe_selector_by_ref.get(current) or "").strip()
        if selector:
            return current, selector
        current = str((tree_by_ref.get(current) or {}).get("parent_ref") or "").strip()
    return "", ""


def _pseudo_elements_from_role_snapshot(
    snapshot: str,
    refs: Dict[str, Dict[str, Any]],
    frame_descriptors: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tree, tree_by_ref, text_by_raw_ref, nearby_text_by_raw_ref, pointer_like_refs, ref_line_index = _build_role_ref_context(snapshot, refs)
    iframe_selector_by_ref = _frame_selectors_by_iframe_ref(tree, refs, frame_descriptors)
    elements: List[Dict[str, Any]] = []
    child_refs_by_parent: Dict[str, List[str]] = {}
    tree_index_by_ref: Dict[str, int] = {}
    for index, node in enumerate(tree):
        if not isinstance(node, dict):
            continue
        node_ref = str(node.get("ref") or "").strip()
        if node_ref and node_ref not in tree_index_by_ref:
            tree_index_by_ref[node_ref] = index
        parent_ref = str(node.get("parent_ref") or "").strip()
        if not parent_ref or not node_ref:
            continue
        child_refs_by_parent.setdefault(parent_ref, []).append(node_ref)

    def _collect_select_state(raw_ref_id: str) -> Tuple[List[Dict[str, str]], str]:
        node = tree_by_ref.get(raw_ref_id) or {}
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        role = str(meta.get("role") or node.get("role") or "").strip().lower()
        if role not in {"combobox", "listbox"}:
            return [], ""
        options: List[Dict[str, str]] = []
        selected_value = ""
        seen: set[tuple[str, str]] = set()
        node_index = tree_index_by_ref.get(raw_ref_id)
        if node_index is None:
            return [], ""
        try:
            base_depth = int((tree[node_index] or {}).get("depth", 0) or 0)
        except Exception:
            base_depth = 0
        for child_node in tree[node_index + 1 :]:
            if not isinstance(child_node, dict):
                continue
            try:
                child_depth = int(child_node.get("depth", 0) or 0)
            except Exception:
                child_depth = 0
            if child_depth <= base_depth:
                break
            child_ref = str(child_node.get("ref") or "").strip()
            child_meta = (refs.get(child_ref) or {}) if child_ref and isinstance(refs, dict) else {}
            child_role = str(child_meta.get("role") or child_node.get("role") or "").strip().lower()
            child_name = str(child_meta.get("name") or child_node.get("name") or "").strip()
            if child_role != "option" or not child_name:
                continue
            child_line = str(child_node.get("line") or "").strip().lower()
            option_value = child_name
            option_text = child_name
            key = (_normalize_hint_text(option_value), _normalize_hint_text(option_text))
            if key in seen:
                continue
            seen.add(key)
            options.append({"value": option_value, "text": option_text})
            if not selected_value and "[selected]" in child_line:
                selected_value = option_value or option_text
        return options, selected_value

    def _candidate_label_for_raw_ref(raw_ref_id: str) -> str:
        node = tree_by_ref.get(raw_ref_id) or {}
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        for value in [
            meta.get("name"),
            node.get("name"),
            *(text_by_raw_ref.get(raw_ref_id) or [])[:2],
        ]:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip())
            if cleaned:
                return cleaned
        return ""

    def _is_safe_custom_option_label(label: str) -> bool:
        cleaned = re.sub(r"\s+", " ", str(label or "").strip())
        normalized = _normalize_hint_text(cleaned)
        if not cleaned or not normalized:
            return False
        if len(cleaned) > 36 or len(normalized.split()) > 5:
            return False
        if _looks_like_structural_context_label(cleaned) or _looks_like_action_label(cleaned):
            return False
        if _contains_hint(cleaned, _LOGIN_HINT_TOKENS) or _contains_hint(cleaned, _LOGOUT_HINT_TOKENS):
            return False
        if _contains_hint(cleaned, _CUSTOM_OPTION_DANGEROUS_TOKENS):
            return False
        if re.search(r"https?://|www\.", normalized):
            return False
        return True

    def _custom_dropdown_options_by_ref() -> Tuple[Dict[str, str], Dict[str, str]]:
        labels_by_ref: Dict[str, str] = {}
        parent_by_ref: Dict[str, str] = {}
        allowed_parent_roles = {"", "generic", "group", "list", "menu", "region"}
        allowed_child_roles = {"", "generic", "listitem", "menuitem", "option"}
        for parent_ref, raw_child_refs in child_refs_by_parent.items():
            parent_ref_id = str(parent_ref or "").strip()
            parent_node = tree_by_ref.get(parent_ref_id) or {}
            parent_meta = (refs.get(parent_ref_id) or {}) if isinstance(refs, dict) else {}
            parent_role = str(parent_meta.get("role") or parent_node.get("role") or "").strip().lower()
            if parent_role not in allowed_parent_roles:
                continue
            child_refs = [str(item or "").strip() for item in raw_child_refs if str(item or "").strip()]
            if len(child_refs) < 3 or len(child_refs) > 20:
                continue

            candidates: List[Tuple[str, str]] = []
            unsafe_seen = False
            seen_labels: set[str] = set()
            for child_ref in child_refs:
                child_node = tree_by_ref.get(child_ref) or {}
                child_meta = (refs.get(child_ref) or {}) if isinstance(refs, dict) else {}
                child_role = str(child_meta.get("role") or child_node.get("role") or "").strip().lower()
                if child_role not in allowed_child_roles:
                    continue
                label = _candidate_label_for_raw_ref(child_ref)
                if not label:
                    continue
                if not _is_safe_custom_option_label(label):
                    unsafe_seen = True
                    continue
                normalized_label = _normalize_hint_text(label)
                if normalized_label in seen_labels:
                    continue
                seen_labels.add(normalized_label)
                candidates.append((child_ref, label))

            if unsafe_seen or len(candidates) < 3:
                continue
            for child_ref, label in candidates:
                labels_by_ref[child_ref] = label
                parent_by_ref[child_ref] = parent_ref_id
        return labels_by_ref, parent_by_ref

    custom_option_label_by_ref, custom_option_parent_by_ref = _custom_dropdown_options_by_ref()

    surfaced_raw_refs: set[str] = set()
    surfaced_meta_by_raw_ref: Dict[str, Dict[str, Any]] = {}
    candidate_raw_refs: List[str] = []
    for raw_ref in list(tree_by_ref.keys()) + list(refs.keys()):
        raw_ref_id = str(raw_ref or "").strip()
        if raw_ref_id and raw_ref_id not in candidate_raw_refs:
            candidate_raw_refs.append(raw_ref_id)
    ordered_candidate_raw_refs = sorted(
        candidate_raw_refs,
        key=lambda raw_ref_id: (int(ref_line_index.get(str(raw_ref_id or "").strip()) or 10**9), str(raw_ref_id or "").strip()),
    )
    for raw_ref_id in ordered_candidate_raw_refs:
        if not raw_ref_id:
            continue
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        ref_id = raw_ref_id
        node = tree_by_ref.get(raw_ref_id) or {}
        role = str(meta.get("role") or node.get("role") or "generic").strip().lower() or "generic"
        source_role = role
        raw_text_hints = list(text_by_raw_ref.get(raw_ref_id) or [])
        nearby_text_hints = list(nearby_text_by_raw_ref.get(raw_ref_id) or [])
        custom_option_label = str(custom_option_label_by_ref.get(raw_ref_id) or "").strip()
        parent_ref = str(node.get("parent_ref") or "").strip()
        ancestor_texts: List[str] = []
        walk_ref = parent_ref
        visited: set[str] = set()
        while walk_ref and walk_ref not in visited:
            visited.add(walk_ref)
            for item in list(text_by_raw_ref.get(walk_ref) or []):
                cleaned = str(item or "").strip()
                if cleaned and cleaned not in ancestor_texts:
                    ancestor_texts.append(cleaned)
            walk_node = tree_by_ref.get(walk_ref) or {}
            walk_ref = str(walk_node.get("parent_ref") or "").strip()
        name = str(meta.get("name") or node.get("name") or "").strip()
        if not name and raw_text_hints:
            name = str(raw_text_hints[0] or "").strip()
        if custom_option_label:
            role = "option"
            name = custom_option_label
        nth = meta.get("nth")
        parent_node = tree_by_ref.get(parent_ref) if parent_ref else None
        ancestor_names = list(node.get("ancestor_names") or [])
        interactive = bool(custom_option_label) or role in _INTERACTIVE_ROLES or raw_ref_id in pointer_like_refs
        if not _should_surface_role_node(role, name, interactive):
            continue
        surfaced_raw_refs.add(raw_ref_id)
        surfaced_meta_by_raw_ref[raw_ref_id] = {
            "role": role,
            "name": name,
            "interactive": interactive,
            "parent_ref": parent_ref,
            "ancestor_names": ancestor_names,
            "openclaw_source_role": source_role,
            "custom_option": bool(custom_option_label),
        }
    container_raw_by_raw_ref: Dict[str, str] = {}
    descendant_count_by_raw_ref: Dict[str, int] = {}
    interactive_descendant_count_by_raw_ref: Dict[str, int] = {}
    content_descendant_count_by_raw_ref: Dict[str, int] = {}
    for raw_ref_id, meta in surfaced_meta_by_raw_ref.items():
        current = str(meta.get("parent_ref") or "").strip()
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            descendant_count_by_raw_ref[current] = int(descendant_count_by_raw_ref.get(current) or 0) + 1
            if bool(meta.get("interactive")):
                interactive_descendant_count_by_raw_ref[current] = int(interactive_descendant_count_by_raw_ref.get(current) or 0) + 1
            else:
                content_descendant_count_by_raw_ref[current] = int(content_descendant_count_by_raw_ref.get(current) or 0) + 1
            current = str((tree_by_ref.get(current) or {}).get("parent_ref") or "").strip()
    semantic_descendant_texts_by_raw_ref: Dict[str, List[str]] = {}
    for raw_ref_id in ordered_candidate_raw_refs:
        raw_ref_id = str(raw_ref_id or "").strip()
        if not raw_ref_id:
            continue
        node = tree_by_ref.get(raw_ref_id) or {}
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        own_values: List[Any] = [
            meta.get("name"),
            node.get("name"),
            *(text_by_raw_ref.get(raw_ref_id) or [])[:4],
        ]
        semantic_values: List[str] = []
        seen_values: set[str] = set()
        for value in own_values:
            cleaned = str(value or "").strip()
            normalized = _normalize_hint_text(cleaned)
            if not cleaned or not normalized or _looks_like_action_label(cleaned) or normalized in seen_values:
                continue
            seen_values.add(normalized)
            semantic_values.append(cleaned)
        if not semantic_values:
            continue
        current = raw_ref_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            bucket = semantic_descendant_texts_by_raw_ref.setdefault(current, [])
            for semantic_value in semantic_values:
                if semantic_value not in bucket:
                    bucket.append(semantic_value)
            current = str((tree_by_ref.get(current) or {}).get("parent_ref") or "").strip()

    def _select_container_raw_ref(raw_ref_id: str, meta: Dict[str, Any]) -> str:
        child_label = str(meta.get("name") or "").strip()
        excluded_texts = {re.sub(r"\s+", " ", child_label.lower()).strip()} if child_label else set()

        def _semantic_text_candidates(candidate_raw_ref: str) -> List[str]:
            candidate_meta = (refs.get(candidate_raw_ref) or {}) if isinstance(refs, dict) else {}
            candidate_node = tree_by_ref.get(candidate_raw_ref) or {}
            values: List[Any] = [
                candidate_meta.get("name"),
                candidate_node.get("name"),
                *(text_by_raw_ref.get(candidate_raw_ref) or [])[:4],
                *(semantic_descendant_texts_by_raw_ref.get(candidate_raw_ref) or [])[:4],
                *(nearby_text_by_raw_ref.get(candidate_raw_ref) or [])[:2],
            ]
            kept: List[str] = []
            seen: set[str] = set()
            for value in values:
                cleaned = str(value or "").strip()
                normalized = re.sub(r"\s+", " ", cleaned.lower()).strip()
                if (
                    not cleaned
                    or not normalized
                    or normalized in excluded_texts
                    or normalized in seen
                    or _looks_like_action_label(cleaned)
                ):
                    continue
                seen.add(normalized)
                kept.append(cleaned)
            return kept

        current = str(meta.get("parent_ref") or "").strip()
        fallback = ""
        preferred = ""
        preferred_score = float("-inf")
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            node = tree_by_ref.get(current) or {}
            role = str(node.get("role") or "").strip().lower()
            name = str(node.get("name") or "").strip()
            descendant_count = int(descendant_count_by_raw_ref.get(current) or 0)
            interactive_descendant_count = int(interactive_descendant_count_by_raw_ref.get(current) or 0)
            content_descendant_count = int(content_descendant_count_by_raw_ref.get(current) or 0)
            semantic_texts = _semantic_text_candidates(current)
            row_like = role in {"row", "listitem", "gridcell", "cell", "article"}
            card_like = role in {"article", "group", "region", "tabpanel"}
            toolbar_like = role in {"toolbar", "navigation", "menubar", "tablist"}
            compact_card_like = card_like and descendant_count <= 12
            anchor_like = (row_like or compact_card_like) and content_descendant_count >= 1 and bool(semantic_texts)
            if row_like and semantic_texts and interactive_descendant_count >= 1 and content_descendant_count >= 1:
                return current
            if compact_card_like and semantic_texts and interactive_descendant_count >= 1 and content_descendant_count >= 1:
                return current
            if not fallback and anchor_like and not toolbar_like:
                fallback = current
            score = 0.0
            if role in _CONTAINER_CANDIDATE_ROLES:
                score += 2.0
            if row_like:
                score += 2.5
            if card_like:
                score += 1.5
            if semantic_texts:
                score += 1.5
            if interactive_descendant_count >= 1 and content_descendant_count >= 1:
                score += 2.0
            if interactive_descendant_count >= 2 and content_descendant_count >= 1:
                score += 1.0
            if descendant_count >= 2:
                score += 1.0
            if name and role not in _INTERACTIVE_ROLES:
                score += 0.5
            if descendant_count > 16:
                score -= 1.5
            if descendant_count > 32:
                score -= 2.5
            if toolbar_like:
                score -= 3.0
            if score > preferred_score and (
                (
                    (row_like and content_descendant_count >= 1 and bool(semantic_texts))
                    or (card_like and content_descendant_count >= 1 and bool(semantic_texts))
                    or (role in _CONTAINER_CANDIDATE_ROLES and content_descendant_count >= 1 and bool(semantic_texts))
                )
                and not (toolbar_like or (interactive_descendant_count >= 1 and content_descendant_count == 0))
            ):
                preferred = current
                preferred_score = score
            current = str(node.get("parent_ref") or "").strip()
        return str(preferred or fallback or "")

    def _nearest_semantic_ancestor_hints(parent_ref: str) -> List[str]:
        hints: List[str] = []
        current = str(parent_ref or "").strip()
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            node = tree_by_ref.get(current) or {}
            role = str(node.get("role") or "").strip().lower()
            if role not in {"generic", "group", "row", "listitem", "gridcell", "cell", "article"}:
                current = str(node.get("parent_ref") or "").strip()
                continue
            values: List[str] = []
            for value in [
                *(nearby_text_by_raw_ref.get(current) or [])[:4],
                *(semantic_descendant_texts_by_raw_ref.get(current) or [])[:4],
                *(text_by_raw_ref.get(current) or [])[:2],
            ]:
                cleaned = str(value or "").strip()
                normalized = _normalize_hint_text(cleaned)
                if (
                    not cleaned
                    or not normalized
                    or _looks_like_action_label(cleaned)
                    or _looks_like_structural_context_label(cleaned)
                ):
                    continue
                if cleaned not in values:
                    values.append(cleaned)
            if values:
                hints.extend(values)
                break
            current = str(node.get("parent_ref") or "").strip()
        return hints

    for raw_ref_id, meta in surfaced_meta_by_raw_ref.items():
        container_raw_by_raw_ref[raw_ref_id] = _select_container_raw_ref(raw_ref_id, meta)

    container_texts_by_raw_ref: Dict[str, List[str]] = {}
    surfaced_candidate_raw_refs = [raw_ref_id for raw_ref_id in ordered_candidate_raw_refs if raw_ref_id in surfaced_meta_by_raw_ref]
    element_id_by_raw_ref = {
        raw_ref_id: index + 1
        for index, raw_ref_id in enumerate(surfaced_candidate_raw_refs)
    }

    for raw_ref_id in ordered_candidate_raw_refs:
        raw_ref_id = str(raw_ref_id or "").strip()
        if not raw_ref_id:
            continue
        container_raw_ref = str(container_raw_by_raw_ref.get(raw_ref_id) or "").strip()
        if not container_raw_ref:
            continue
        node = tree_by_ref.get(raw_ref_id) or {}
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        role = str(meta.get("role") or node.get("role") or "").strip().lower()
        name = str(meta.get("name") or node.get("name") or "").strip()
        custom_option_label = str(custom_option_label_by_ref.get(raw_ref_id) or "").strip()
        if custom_option_label:
            role = "option"
            name = custom_option_label
        bucket = container_texts_by_raw_ref.setdefault(container_raw_ref, [])
        for item in [name, *(text_by_raw_ref.get(raw_ref_id) or [])[:3]]:
            cleaned = str(item or "").strip()
            normalized = _normalize_hint_text(cleaned)
            if not cleaned or not normalized:
                continue
            if role in _INTERACTIVE_ROLES and _looks_like_action_label(cleaned):
                continue
            if cleaned not in bucket:
                bucket.append(cleaned)

    group_action_labels_by_container: Dict[str, List[str]] = {}
    for raw_ref_id, meta in surfaced_meta_by_raw_ref.items():
        if not bool(meta.get("interactive")):
            continue
        container_raw_ref = str(container_raw_by_raw_ref.get(raw_ref_id) or "").strip()
        if not container_raw_ref:
            continue
        container_node = tree_by_ref.get(container_raw_ref) or {}
        container_role = str(container_node.get("role") or "").strip().lower()
        if container_role not in {"row", "listitem", "gridcell", "cell", "article", "group"}:
            continue
        label = str(meta.get("name") or "").strip()
        if not label:
            continue
        bucket = group_action_labels_by_container.setdefault(container_raw_ref, [])
        if label not in bucket:
            bucket.append(label)

    for raw_ref_id in ordered_candidate_raw_refs:
        if not raw_ref_id:
            continue
        meta = (refs.get(raw_ref_id) or {}) if isinstance(refs, dict) else {}
        ref_id = raw_ref_id
        node = tree_by_ref.get(raw_ref_id) or {}
        role = str(meta.get("role") or node.get("role") or "generic").strip().lower() or "generic"
        source_role = role
        raw_text_hints = list(text_by_raw_ref.get(raw_ref_id) or [])
        nearby_text_hints = list(nearby_text_by_raw_ref.get(raw_ref_id) or [])
        custom_option_label = str(custom_option_label_by_ref.get(raw_ref_id) or "").strip()
        parent_ref = str(node.get("parent_ref") or "").strip()
        if custom_option_label and custom_option_parent_by_ref.get(raw_ref_id):
            parent_ref = str(custom_option_parent_by_ref.get(raw_ref_id) or parent_ref).strip()
        ancestor_texts: List[str] = []
        walk_ref = parent_ref
        visited: set[str] = set()
        while walk_ref and walk_ref not in visited:
            visited.add(walk_ref)
            for item in list(text_by_raw_ref.get(walk_ref) or []):
                cleaned = str(item or "").strip()
                if cleaned and cleaned not in ancestor_texts:
                    ancestor_texts.append(cleaned)
            walk_node = tree_by_ref.get(walk_ref) or {}
            walk_ref = str(walk_node.get("parent_ref") or "").strip()
        name = str(meta.get("name") or node.get("name") or "").strip()
        if not name and raw_text_hints:
            name = str(raw_text_hints[0] or "").strip()
        if custom_option_label:
            role = "option"
            name = custom_option_label
        nth = meta.get("nth")
        parent_node = tree_by_ref.get(parent_ref) if parent_ref else None
        ancestor_names = list(node.get("ancestor_names") or [])
        interactive = bool(custom_option_label) or role in _INTERACTIVE_ROLES or raw_ref_id in pointer_like_refs
        if not _should_surface_role_node(role, name, interactive):
            continue
        container_dom_ref = f"ocdom:{parent_ref}" if parent_ref else ""
        container_parent_dom_ref = ""
        if isinstance(parent_node, dict):
            grand_parent_ref = str(parent_node.get("parent_ref") or "").strip()
            if grand_parent_ref:
                container_parent_dom_ref = f"ocdom:{grand_parent_ref}"
        container_raw_ref = str(container_raw_by_raw_ref.get(raw_ref_id) or "").strip()
        container_node = tree_by_ref.get(container_raw_ref) if container_raw_ref else None
        container_ref_id = container_raw_ref
        container_text_hints = list(container_texts_by_raw_ref.get(container_raw_ref) or []) if container_raw_ref else []
        container_name = str((container_node or {}).get("name") or "").strip() or str((parent_node or {}).get("name") or "").strip()
        container_role = str((container_node or {}).get("role") or "").strip() or str((parent_node or {}).get("role") or "").strip()
        if (not container_name or _normalize_hint_text(container_name) in {"검색 결과", "search results", "results"}) and container_text_hints:
            container_name = str(container_text_hints[0] or "").strip()
        group_action_labels = list(group_action_labels_by_container.get(container_raw_ref) or [])
        normalized_group_action_labels = {
            _normalize_hint_text(label)
            for label in group_action_labels
            if str(label or "").strip()
        }
        row_context_hints = _nearest_semantic_ancestor_hints(parent_ref) if interactive else []
        if interactive:
            context_candidates = [
                *[str(item).strip() for item in row_context_hints if str(item).strip()][:3],
                *[str(item).strip() for item in container_text_hints if str(item).strip()][:3],
                *[str(item).strip() for item in ancestor_names if str(item).strip()][:2],
                *[str(item).strip() for item in ancestor_texts if str(item).strip()][:1],
            ]
        else:
            context_candidates = [
                *[str(item).strip() for item in ancestor_names if str(item).strip()][:2],
                *[str(item).strip() for item in nearby_text_hints if str(item).strip()][:2],
                *[str(item).strip() for item in ancestor_texts if str(item).strip()][:2],
            ]
        context_text = " | ".join([item for item in context_candidates if item])
        row_like = role in {"listitem", "row", "cell", "gridcell", "article"} or (role == "generic" and not interactive)
        display_parts: List[str] = []
        for item in [
            name,
            *raw_text_hints[:3],
            *nearby_text_hints[:3],
            *ancestor_texts[:3],
            container_name,
        ]:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in display_parts:
                display_parts.append(cleaned)
        display_name = name or ""
        if row_like:
            display_name = " | ".join(display_parts[:4]) or display_name or context_text
        elif interactive:
            interactive_self_name = display_name or ""
            if not interactive_self_name:
                for item in raw_text_hints:
                    cleaned = str(item or "").strip()
                    normalized = _normalize_hint_text(cleaned)
                    if not cleaned or not normalized:
                        continue
                    if normalized in normalized_group_action_labels:
                        continue
                    interactive_self_name = cleaned
                    break
            display_name = interactive_self_name
        elif not display_name and display_parts:
            display_name = display_parts[0]
        context_score_hint = 0.0
        if row_like and display_name:
            context_score_hint += 10.0
        if row_like and group_action_labels:
            context_score_hint += 3.0
        select_options, selected_value = _collect_select_state(raw_ref_id)
        if role in {"combobox", "listbox"}:
            if not selected_value:
                selected_value = str(display_name or name or "").strip()
            if select_options and selected_value:
                normalized_selected = _normalize_hint_text(selected_value)
                for option in select_options:
                    option_value = str(option.get("value") or "").strip()
                    option_text = str(option.get("text") or "").strip()
                    if normalized_selected in {
                        _normalize_hint_text(option_value),
                        _normalize_hint_text(option_text),
                    }:
                        selected_value = option_value or option_text
                        break
            if selected_value:
                display_name = selected_value
        role_ref_name = selected_value if role in {"combobox", "listbox"} and selected_value else (name or "")
        element_tag = "button" if custom_option_label or (role == "generic" and interactive) else _role_to_tag(role)
        frame_ref_id, frame_selector = _frame_scope_for_raw_ref(
            raw_ref_id=raw_ref_id,
            tree_by_ref=tree_by_ref,
            iframe_selector_by_ref=iframe_selector_by_ref,
        )
        frame_descendant_selector = ""
        frame_scoped_selector = ""
        if frame_selector:
            frame_descendant_selector = _frame_descendant_selector_for_role_node(role, role_ref_name or display_name, element_tag)
            frame_scoped_selector = (
                f"{frame_selector} >> internal:control=enter-frame >> {frame_descendant_selector}"
            )
        attrs: Dict[str, Any] = {
            "role": role,
            "aria-label": display_name or "",
            "title": display_name or "",
            "role_ref_role": role,
            "role_ref_name": role_ref_name,
            "role_ref_nth": nth,
            "openclaw_raw_ref": raw_ref_id,
            "container_dom_ref": container_dom_ref,
            "container_parent_dom_ref": container_parent_dom_ref,
            "container_ref_id": container_ref_id or None,
            "container_name": container_name or None,
            "container_role": container_role or None,
            "context_text": context_text,
            "group_action_labels": group_action_labels or None,
            "context_score_hint": context_score_hint,
            "container_source": "openclaw-role-tree",
            "gaia-visible-strict": "true",
            "gaia-actionable": "true" if interactive else "false",
            "gaia-disabled": "false",
        }
        if frame_selector:
            attrs["frame_ref_id"] = frame_ref_id
            attrs["frame_selector"] = frame_selector
            attrs["frame_descendant_selector"] = frame_descendant_selector
            attrs["frame_scoped_selector"] = frame_scoped_selector
            attrs["scope"] = {
                "frame_ref_id": frame_ref_id,
                "frame_selector": frame_selector,
                "frame_descendant_selector": frame_descendant_selector,
            }
        if custom_option_label:
            attrs["gaia-custom-option"] = "true"
            attrs["custom_option_kind"] = "dropdown"
            attrs["openclaw_source_role"] = source_role
        if select_options:
            attrs["options"] = select_options
        if selected_value:
            attrs["selected_value"] = selected_value
        if role == "textbox":
            attrs["type"] = "text"
            attrs["placeholder"] = name or ""
        elements.append(
            {
                "id": int(element_id_by_raw_ref.get(raw_ref_id) or (len(elements) + 1)),
                "tag": element_tag,
                "ref_id": ref_id,
                "selector": f"openclaw-ref:{ref_id}",
                "full_selector": f"openclaw-ref:{ref_id}",
                "text": display_name,
                "container_ref_id": container_ref_id or None,
                "container_name": container_name or None,
                "container_role": container_role or None,
                "context_text": context_text,
                "group_action_labels": group_action_labels or None,
                "context_score_hint": context_score_hint,
                "attributes": attrs,
                "scope": attrs.get("scope"),
                "bounding_box": None,
                "element_type": "button" if custom_option_label else _element_type_for_role(role),
                "is_visible": True,
            }
        )
    raw_role_snapshot = {
        "snapshot": snapshot,
        "refs_mode": "aria",
        "refs": refs,
        "tree": tree,
        "ref_line_index": ref_line_index,
        "element_id_by_ref": element_id_by_raw_ref,
        "stats": _role_snapshot_stats(snapshot, refs),
    }
    return elements, raw_role_snapshot


def _is_within_context_scope(
    container_ref_id: str,
    requested_scope_ref_id: str,
    node_by_ref: Dict[str, Dict[str, Any]],
) -> bool:
    current = str(container_ref_id or "").strip()
    requested = str(requested_scope_ref_id or "").strip()
    if not current or not requested:
        return False
    while current:
        if current == requested:
            return True
        current = str((node_by_ref.get(current) or {}).get("parent_ref_id") or "").strip()
    return False


def _normalize_hint_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _looks_like_action_label(value: Any) -> bool:
    normalized = _normalize_hint_text(value)
    if not normalized:
        return False
    actionish_tokens = (
        "바로 추가",
        "추가",
        "담기",
        "add",
        "apply",
        "remove",
        "delete",
        "삭제",
        "제거",
        "강의평",
        "강의평 보기",
        "review",
        "상세 정보 보기",
        "detail",
        "details",
        "위시리스트",
        "위시리스트에 담기",
        "wishlist",
        "favorite",
        "즐겨찾기",
    )
    return any(token in normalized for token in actionish_tokens)


def _contains_hint(blob: str, tokens: Tuple[str, ...]) -> bool:
    normalized = _normalize_hint_text(blob)
    return bool(normalized and any(token in normalized for token in tokens))


def _looks_like_structural_context_label(value: Any) -> bool:
    normalized = _normalize_hint_text(value)
    if not normalized:
        return True
    if normalized in {"검색 결과", "results", "search results", "전심", "필수 포함 과목"}:
        return True
    if normalized.startswith("(총 ") or normalized.startswith("총 "):
        return True
    if re.fullmatch(r"\(?\d+\s*학점\)?", normalized):
        return True
    return False


def _element_blob(item: Dict[str, Any]) -> str:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    parts = [
        item.get("tag"),
        item.get("text"),
        attrs.get("role"),
        attrs.get("aria-label"),
        attrs.get("title"),
        attrs.get("placeholder"),
        attrs.get("type"),
        attrs.get("container_name"),
        attrs.get("container_role"),
        attrs.get("context_text"),
    ]
    return " | ".join(str(part or "").strip() for part in parts if str(part or "").strip())


def _extract_context_segments(item: Dict[str, Any]) -> List[str]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    raw_context = str(attrs.get("context_text") or "").strip()
    if not raw_context:
        return []
    primary = {
        _normalize_hint_text(item.get("text")),
        _normalize_hint_text(attrs.get("aria-label")),
        _normalize_hint_text(attrs.get("title")),
        _normalize_hint_text(attrs.get("placeholder")),
    }
    segments: List[str] = []
    for part in raw_context.split("|"):
        cleaned = str(part or "").strip()
        normalized = _normalize_hint_text(cleaned)
        if not cleaned or normalized in primary:
            continue
        if cleaned not in segments:
            segments.append(cleaned)
    return segments


def _browser_find_text_parts(item: Dict[str, Any]) -> List[Tuple[str, str]]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    parts: List[Tuple[str, str]] = []

    def add(field: str, value: Any) -> None:
        cleaned = str(value or "").strip()
        if not cleaned:
            return
        key = (field, cleaned)
        if key not in parts:
            parts.append(key)

    add("text", item.get("text"))
    add("aria_label", attrs.get("aria-label"))
    add("title", attrs.get("title"))
    add("placeholder", attrs.get("placeholder"))
    add("role_ref_name", attrs.get("role_ref_name"))
    add("selected_value", attrs.get("selected_value"))
    add("container_name", item.get("container_name") or attrs.get("container_name"))
    add("container_role", item.get("container_role") or attrs.get("container_role"))
    add("context_text", item.get("context_text") or attrs.get("context_text"))
    for segment in _extract_context_segments(item):
        add("context_segment", segment)
    group_action_labels = attrs.get("group_action_labels") or item.get("group_action_labels")
    if isinstance(group_action_labels, list):
        for label in group_action_labels:
            add("group_action_label", label)
    options = attrs.get("options")
    if isinstance(options, list):
        for option in options:
            if isinstance(option, dict):
                add("option_text", option.get("text"))
                add("option_value", option.get("value"))
            else:
                add("option", option)
    return parts


def _browser_find_element_score(query: str, item: Dict[str, Any]) -> Tuple[int, List[str]]:
    normalized_query = _normalize_hint_text(query)
    if not normalized_query:
        return 0, []
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    role = _normalize_hint_text(attrs.get("role") or attrs.get("role_ref_role"))
    tag = _normalize_hint_text(item.get("tag"))
    actionable = str(attrs.get("gaia-actionable") or "").strip().lower() == "true"
    interactive = actionable or role in _INTERACTIVE_ROLES or tag in {"button", "a", "input", "select", "textarea", "option"}
    primary_fields = {"text", "aria_label", "title", "role_ref_name", "selected_value", "option_text", "option_value"}
    context_fields = {"context_text", "context_segment", "container_name", "container_role"}
    best_score = 0
    matched_fields: List[str] = []
    text_parts = _browser_find_text_parts(item)
    normalized_parts = [(field, value, _normalize_hint_text(value)) for field, value in text_parts]

    for field, _value, normalized_value in normalized_parts:
        if not normalized_value:
            continue
        score = 0
        if normalized_value == normalized_query:
            score = 100 if field in primary_fields else 78
        elif normalized_query in normalized_value:
            score = 76 if field in primary_fields else 58
        elif normalized_value in normalized_query and len(normalized_value) >= 2:
            score = 66 if field in primary_fields else 48
        if score <= 0:
            continue
        if field in context_fields:
            score = min(score, 64)
        if interactive:
            score += 12
        if role == "option":
            score += 8
        if bool(attrs.get("gaia-custom-option")):
            score += 8
        if not bool(item.get("is_visible", True)):
            score -= 80
        if str(attrs.get("gaia-disabled") or "").strip().lower() == "true":
            score -= 20
        if score > best_score:
            best_score = score
            matched_fields = [field]
        elif score == best_score and score > 0 and field not in matched_fields:
            matched_fields.append(field)

    if best_score <= 0:
        joined = _normalize_hint_text(" ".join(value for _field, value in text_parts))
        tokens = [token for token in normalized_query.split(" ") if token]
        if joined and tokens and all(token in joined for token in tokens):
            best_score = 52 + (10 if interactive else 0)
            matched_fields = ["token_match"]
    return max(0, int(best_score)), matched_fields


def _browser_find_matches(
    *,
    query: str,
    elements: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for index, item in enumerate(elements):
        ref_id = str(item.get("ref_id") or "").strip()
        if not ref_id:
            continue
        score, matched_fields = _browser_find_element_score(query, item)
        if score < 50:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        matches.append(
            {
                "ref_id": ref_id,
                "score": score,
                "matched_fields": matched_fields,
                "text": str(item.get("text") or "").strip(),
                "tag": str(item.get("tag") or "").strip(),
                "role": str(attrs.get("role") or attrs.get("role_ref_role") or "").strip(),
                "selector": str(item.get("selector") or "").strip(),
                "container_ref_id": str(item.get("container_ref_id") or attrs.get("container_ref_id") or "").strip(),
                "container_name": str(item.get("container_name") or attrs.get("container_name") or "").strip(),
                "context_text": str(item.get("context_text") or attrs.get("context_text") or "").strip(),
                "is_visible": bool(item.get("is_visible", True)),
                "_order": index,
            }
        )
    matches.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            0 if str(item.get("role") or "").lower() in _INTERACTIVE_ROLES else 1,
            int(item.get("_order") or 0),
        )
    )
    trimmed: List[Dict[str, Any]] = []
    for item in matches[: max(1, limit)]:
        cleaned = dict(item)
        cleaned.pop("_order", None)
        trimmed.append(cleaned)
    return trimmed


def _synthesize_snapshot_evidence(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    interactive_count = 0
    list_count = 0
    login_visible = False
    logout_visible = False
    auth_hint_hits = 0
    dialog_count = 0
    visible_texts: List[str] = []
    live_texts: List[str] = []
    container_auth: Dict[str, Dict[str, int]] = {}

    for item in elements:
        if not bool(item.get("is_visible", True)):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        tag = _normalize_hint_text(item.get("tag"))
        role = _normalize_hint_text(attrs.get("role"))
        input_type = _normalize_hint_text(attrs.get("type"))
        blob = _element_blob(item)
        blob_norm = _normalize_hint_text(blob)
        if not blob_norm:
            continue
        context_segments = _extract_context_segments(item)

        if len(visible_texts) < 24:
            primary_candidates = [
                str(item.get("text") or "").strip(),
                str(attrs.get("aria-label") or "").strip(),
                str(attrs.get("title") or "").strip(),
            ]
            for text_value in [*primary_candidates, *context_segments]:
                if text_value and text_value not in visible_texts:
                    visible_texts.append(text_value[:120])
                if len(visible_texts) >= 24:
                    break
        if len(live_texts) < 8:
            for text_value in context_segments:
                normalized_value = _normalize_hint_text(text_value)
                if len(normalized_value) < 12:
                    continue
                if text_value not in live_texts:
                    live_texts.append(text_value[:160])
                if len(live_texts) >= 8:
                    break

        interactive = role in _INTERACTIVE_ROLES or tag in {"button", "a", "input", "select", "textarea"}
        if interactive:
            interactive_count += 1
        if role in {"listitem", "row", "cell", "gridcell"} or tag in {"li", "tr", "td", "article"}:
            list_count += 1

        has_login_hint = _contains_hint(blob_norm, _LOGIN_HINT_TOKENS)
        if has_login_hint:
            login_visible = True
            auth_hint_hits += 1
        if _contains_hint(blob_norm, _LOGOUT_HINT_TOKENS):
            logout_visible = True
        if role in {"dialog", "alertdialog"} or tag == "dialog" or _contains_hint(blob_norm, _MODAL_HINT_TOKENS):
            dialog_count += 1

        if interactive or role in {"heading", "paragraph"} or tag in {"h1", "h2", "h3", "p", "div"}:
            container_key = str(
                attrs.get("container_dom_ref")
                or attrs.get("container_name")
                or attrs.get("container_role")
                or attrs.get("context_text")
                or "__root__"
            ).strip() or "__root__"
            bucket = container_auth.setdefault(
                container_key,
                {
                    "auth_hint_hits": 0,
                    "password_inputs": 0,
                    "identifier_inputs": 0,
                    "login_cta": 0,
                },
            )
            if has_login_hint:
                bucket["auth_hint_hits"] += 1
            input_like = role in {"textbox", "searchbox", "combobox"} or tag in {"input", "textarea", "select"}
            if input_like and (_contains_hint(blob_norm, _PASSWORD_HINT_TOKENS) or input_type == "password"):
                bucket["password_inputs"] += 1
            if input_like and _contains_hint(blob_norm, _IDENTIFIER_HINT_TOKENS):
                bucket["identifier_inputs"] += 1
            if interactive and has_login_hint:
                bucket["login_cta"] += 1

    auth_cluster_count = 0
    auth_prompt_visible = False
    for bucket in container_auth.values():
        has_password = int(bucket.get("password_inputs") or 0) > 0
        has_identifier = int(bucket.get("identifier_inputs") or 0) > 0
        has_login_cta = int(bucket.get("login_cta") or 0) > 0
        hint_hits = int(bucket.get("auth_hint_hits") or 0)
        if has_password and has_identifier and (has_login_cta or hint_hits >= 2):
            auth_cluster_count += 1
            auth_prompt_visible = True

    if not auth_prompt_visible and auth_hint_hits >= 3 and any(
        int(bucket.get("password_inputs") or 0) > 0 and int(bucket.get("identifier_inputs") or 0) > 0
        for bucket in container_auth.values()
    ):
        auth_prompt_visible = True

    modal_open = bool(dialog_count > 0 or auth_cluster_count > 0)
    modal_count = int(dialog_count or auth_cluster_count)
    text_digest = " ".join(visible_texts)[:2000]

    return {
        "text_digest": text_digest,
        "number_tokens": [],
        "live_texts": live_texts,
        "counters": [],
        "list_count": int(list_count),
        "interactive_count": int(interactive_count),
        "login_visible": bool(login_visible),
        "logout_visible": bool(logout_visible),
        "modal_count": int(modal_count),
        "backdrop_count": 0,
        "dialog_count": int(dialog_count),
        "modal_open": bool(modal_open),
        "auth_prompt_visible": bool(auth_prompt_visible),
        "modal_regions": [],
        "scroll_y": 0,
        "doc_height": 0,
    }


def _normalize_dom_text_blocks(raw_blocks: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_blocks, list):
        return []
    blocks: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        text = re.sub(r"\s+", " ", str(raw.get("text") or "").strip())
        if len(text) < 8:
            continue
        normalized = _normalize_hint_text(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        try:
            score = int(raw.get("score") or 0)
        except Exception:
            score = 0
        block = {
            "text": text[:1200],
            "tag": str(raw.get("tag") or "").strip().lower()[:32],
            "role": str(raw.get("role") or "").strip().lower()[:64],
            "selector": str(raw.get("selector") or "").strip()[:240],
            "section": re.sub(r"\s+", " ", str(raw.get("section") or "").strip())[:240],
            "score": score,
            "in_viewport": bool(raw.get("inViewport")),
        }
        blocks.append(block)
        if len(blocks) >= _openclaw_dom_text_block_limit():
            break
    return blocks


def _dom_text_evidence_lines(dom_text_blocks: List[Dict[str, Any]]) -> List[str]:
    if not dom_text_blocks:
        return []
    lines = ["", "[DOM text evidence]"]
    for index, block in enumerate(dom_text_blocks, start=1):
        text = re.sub(r"\s+", " ", str(block.get("text") or "").strip())
        if not text:
            continue
        tag = str(block.get("tag") or "").strip() or "node"
        section = str(block.get("section") or "").strip()
        selector = str(block.get("selector") or "").strip()
        meta_parts = [f"tag={tag}"]
        if section:
            meta_parts.append(f"section={section}")
        if selector:
            meta_parts.append(f"selector={selector}")
        lines.append(f"- dom_text {index}: {text[:900]} [{'; '.join(meta_parts)}]")
    return lines


def _merge_dom_text_evidence(
    *,
    role_snapshot: Dict[str, Any],
    evidence: Dict[str, Any],
    dom_text_blocks: Optional[List[Dict[str, Any]]],
) -> None:
    blocks = _normalize_dom_text_blocks(dom_text_blocks)
    if not blocks:
        return
    role_snapshot["dom_text_blocks"] = blocks
    role_snapshot["dom_text_block_count"] = len(blocks)
    snapshot = str(role_snapshot.get("snapshot") or "").strip()
    extra_lines = _dom_text_evidence_lines(blocks)
    if extra_lines:
        role_snapshot["snapshot"] = "\n".join([snapshot, *extra_lines]).strip()
        role_snapshot["stats"] = _role_snapshot_stats(
            str(role_snapshot.get("snapshot") or ""),
            role_snapshot.get("refs") if isinstance(role_snapshot.get("refs"), dict) else {},
        )

    existing_digest = str(evidence.get("text_digest") or "").strip()
    digest_parts = [existing_digest] if existing_digest else []
    live_texts = list(evidence.get("live_texts") or [])
    for block in blocks:
        text = re.sub(r"\s+", " ", str(block.get("text") or "").strip())
        if not text:
            continue
        digest_parts.append(text[:500])
        if len(text) >= 12 and text[:240] not in live_texts:
            live_texts.append(text[:240])
    evidence["text_digest"] = " ".join(digest_parts).strip()[:8000]
    evidence["live_texts"] = live_texts[:40]
    evidence["dom_text_blocks"] = blocks
    evidence["dom_text_block_count"] = len(blocks)


_DATE_PICKER_SELECTION_RE = re.compile(
    r"(?P<month>\d{1,2})\.(?P<day>\d{1,2})\s*\([^)]{1,8}\)\s*[•·]\s*(?P<nights>\d{1,2})\s*박"
)
_DATE_PICKER_YEAR_MONTH_RE = re.compile(r"(?P<year>20\d{2})\s*\.\s*(?P<month>\d{1,2})")
_DATE_RANGE_DISPLAY_RE = re.compile(
    r"(?P<start_month>\d{1,2})\s*[./]\s*(?P<start_day>\d{1,2})\s*(?:~|-|–|—|to|부터)\s*"
    r"(?P<end_month>\d{1,2})\s*[./]\s*(?P<end_day>\d{1,2})",
    re.IGNORECASE,
)


def _compact_snapshot_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _snapshot_search_text(payload: Optional[Dict[str, Any]]) -> str:
    if not isinstance(payload, dict):
        return ""
    chunks: List[str] = []
    for key in ("current_url", "url"):
        value = _compact_snapshot_text(payload.get(key))
        if value:
            chunks.append(value)
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    if evidence:
        for key in ("text_digest", "frame_texts"):
            value = evidence.get(key)
            if isinstance(value, list):
                chunks.extend(_compact_snapshot_text(item) for item in value if _compact_snapshot_text(item))
            else:
                text = _compact_snapshot_text(value)
                if text:
                    chunks.append(text)
        live_texts = evidence.get("live_texts") if isinstance(evidence.get("live_texts"), list) else []
        chunks.extend(_compact_snapshot_text(item) for item in live_texts[:60] if _compact_snapshot_text(item))
    role_snapshot = payload.get("role_snapshot") if isinstance(payload.get("role_snapshot"), dict) else {}
    role_text = _compact_snapshot_text(role_snapshot.get("snapshot"))
    if role_text:
        chunks.append(role_text)
    elements_by_ref = payload.get("elements_by_ref") if isinstance(payload.get("elements_by_ref"), dict) else {}
    for meta in list(elements_by_ref.values())[:160]:
        if not isinstance(meta, dict):
            continue
        attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
        for value in (
            meta.get("name"),
            meta.get("text"),
            meta.get("role_ref_name"),
            meta.get("context_text"),
            attrs.get("href"),
            attrs.get("aria-label"),
            attrs.get("title"),
        ):
            text = _compact_snapshot_text(value)
            if text:
                chunks.append(text)
    return " ".join(chunks)


def _ref_label_from_payload(payload: Optional[Dict[str, Any]], ref_id: str) -> str:
    if not isinstance(payload, dict) or not ref_id:
        return ""
    ref = str(ref_id or "").strip()
    elements_by_ref = payload.get("elements_by_ref") if isinstance(payload.get("elements_by_ref"), dict) else {}
    meta = elements_by_ref.get(ref) if isinstance(elements_by_ref.get(ref), dict) else {}
    attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
    chunks: List[str] = []
    for value in (
        meta.get("name"),
        meta.get("text"),
        meta.get("role_ref_name"),
        meta.get("context_text"),
        attrs.get("aria-label"),
        attrs.get("title"),
    ):
        text = _compact_snapshot_text(value)
        if text:
            chunks.append(text)
    elements = payload.get("elements") if isinstance(payload.get("elements"), list) else []
    for item in elements:
        if not isinstance(item, dict) or str(item.get("ref_id") or "").strip() != ref:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        for value in (
            item.get("text"),
            item.get("role_ref_name"),
            item.get("context_text"),
            attrs.get("aria-label"),
            attrs.get("title"),
        ):
            text = _compact_snapshot_text(value)
            if text:
                chunks.append(text)
        break
    return " ".join(chunks)


def _looks_like_commit_control_label(label: str) -> bool:
    normalized = _normalize_hint_text(label)
    if not normalized:
        return False
    return any(term in normalized for term in ("적용", "확인", "완료", "apply", "done", "confirm"))


def _year_for_date_picker_selection(text: str, month: int) -> int:
    fallback_year = date.today().year
    matches = list(_DATE_PICKER_YEAR_MONTH_RE.finditer(text or ""))
    for match in reversed(matches):
        try:
            if int(match.group("month")) == int(month):
                return int(match.group("year"))
        except Exception:
            continue
    for match in reversed(matches):
        try:
            return int(match.group("year"))
        except Exception:
            continue
    return fallback_year


def _date_picker_commit_expectation(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    if evidence and not bool(evidence.get("modal_open")):
        return None
    text = _snapshot_search_text(payload)
    matches = list(_DATE_PICKER_SELECTION_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    try:
        month = int(match.group("month"))
        day = int(match.group("day"))
        nights = int(match.group("nights"))
    except Exception:
        return None
    if nights < 1 or nights > 30:
        return None
    year = _year_for_date_picker_selection(text, month)
    try:
        start = date(year, month, day)
        end = start + timedelta(days=nights)
    except Exception:
        return None
    start_display = f"{start.month:02d}.{start.day:02d}"
    end_display = f"{end.month:02d}.{end.day:02d}"
    return {
        "kind": "date_range",
        "selected_summary": _compact_snapshot_text(match.group(0)),
        "nights": nights,
        "start_display": start_display,
        "end_display": end_display,
        "expected_range": f"{start_display}~{end_display}",
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
    }


def _display_date_pattern(display: str) -> str:
    try:
        month, day = [int(part) for part in str(display or "").split(".", 1)]
    except Exception:
        return re.escape(str(display or ""))
    return rf"0?{month}\s*[./]\s*0?{day}"


def _snapshot_contains_date_commit(payload: Optional[Dict[str, Any]], expectation: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or not expectation:
        return False
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    if bool(evidence.get("modal_open")):
        return False
    text = _snapshot_search_text(payload)
    compact = re.sub(r"\s+", "", text)
    start_iso = str(expectation.get("start_iso") or "")
    end_iso = str(expectation.get("end_iso") or "")
    if start_iso and end_iso and start_iso in compact and end_iso in compact:
        return True
    start_display = str(expectation.get("start_display") or "")
    end_display = str(expectation.get("end_display") or "")
    if not start_display or not end_display:
        return False
    range_re = re.compile(
        _display_date_pattern(start_display)
        + r"\s*(?:~|-|–|—|to|부터)\s*"
        + _display_date_pattern(end_display),
        re.IGNORECASE,
    )
    return bool(range_re.search(text))


def _observed_date_ranges(payload: Optional[Dict[str, Any]], *, limit: int = 4) -> List[str]:
    text = _snapshot_search_text(payload)
    ranges: List[str] = []
    for match in _DATE_RANGE_DISPLAY_RE.finditer(text):
        try:
            value = (
                f"{int(match.group('start_month')):02d}.{int(match.group('start_day')):02d}"
                f"~{int(match.group('end_month')):02d}.{int(match.group('end_day')):02d}"
            )
        except Exception:
            continue
        if value not in ranges:
            ranges.append(value)
        if len(ranges) >= limit:
            break
    return ranges


def _apply_commit_verification_to_state_change(
    *,
    state_change: Dict[str, Any],
    before_payload: Optional[Dict[str, Any]],
    after_payload: Optional[Dict[str, Any]],
    ref_id: str,
) -> Dict[str, Any]:
    updated = dict(state_change or {})
    label = _ref_label_from_payload(before_payload, ref_id)
    if not _looks_like_commit_control_label(label):
        return updated
    expectation = _date_picker_commit_expectation(before_payload)
    if not expectation:
        return updated
    reflected = _snapshot_contains_date_commit(after_payload, expectation)
    verification = {
        **expectation,
        "reflected": bool(reflected),
        "control_label": _compact_snapshot_text(label)[:160],
        "observed_ranges": _observed_date_ranges(after_payload),
    }
    updated["commit_verification"] = verification
    if reflected:
        updated["commit_verified"] = True
        updated["commit_verification_failed"] = False
        return updated
    updated["commit_verified"] = False
    updated["commit_verification_failed"] = True
    updated["commit_pending"] = True
    updated["backend_progress"] = False
    updated["backend_effective_only"] = True
    updated["commit_verification_reason"] = (
        f"expected {expectation.get('expected_range')} after commit control, "
        "but the post-action snapshot did not reflect that persistent range"
    )
    return updated


_OPENCLAW_REF_ACTIONABILITY_PROBE_FN = r"""(el) => {
  function rectOf(node) {
    try {
      const rect = node.getBoundingClientRect();
      return {
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        bottom: Math.round(rect.bottom),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    } catch (_) {
      return {};
    }
  }
  function describe(node) {
    if (!node) {
      return {};
    }
    let className = "";
    try {
      className = typeof node.className === "string" ? node.className : "";
    } catch (_) {}
    return {
      tag: String(node.tagName || "").toLowerCase(),
      id: node.id || "",
      className: className.slice(0, 120),
      role: node.getAttribute && node.getAttribute("role") || "",
      ariaLabel: node.getAttribute && node.getAttribute("aria-label") || "",
      text: String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80),
      rect: rectOf(node),
    };
  }
  function inViewport(rect) {
    return !!(
      rect &&
      rect.width >= 1 &&
      rect.height >= 1 &&
      rect.bottom >= 0 &&
      rect.right >= 0 &&
      rect.top <= window.innerHeight &&
      rect.left <= window.innerWidth
    );
  }
  function centerPoint(rect) {
    return {
      x: Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2)),
      y: Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2)),
    };
  }

  const style = window.getComputedStyle(el);
  const rect = rectOf(el);
  const target = describe(el);
  if (!style || style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) {
    return { status: "hidden", actionable: false, reason: "computed_hidden", target, rect };
  }
  if (rect.width < 1 || rect.height < 1) {
    return { status: "zero_rect", actionable: false, reason: "zero_sized_rect", target, rect };
  }
  if (!inViewport(rect)) {
    return { status: "offscreen", actionable: true, reason: "outside_viewport_but_scrollable", target, rect };
  }
  const point = centerPoint(rect);
  const hit = document.elementFromPoint(point.x, point.y);
  const hitDesc = describe(hit);
  if (!hit) {
    return { status: "no_hit", actionable: false, reason: "center_has_no_hit_target", target, rect, point };
  }
  if (hit === el || el.contains(hit)) {
    return { status: "ok", actionable: true, reason: "center_hits_target", target, rect, point, hit: hitDesc };
  }
  const tag = String(hit.tagName || "").toLowerCase();
  if (tag === "html" || tag === "body") {
    return { status: "no_hit", actionable: false, reason: "center_hits_page_shell", target, rect, point, hit: hitDesc };
  }
  return {
    status: "covered",
    actionable: false,
    reason: hit.contains && hit.contains(el) ? "center_hits_ancestor" : "center_hits_other_element",
    target,
    rect,
    point,
    hit: hitDesc,
  };
}"""


def _openclaw_actionability_probe_enabled() -> bool:
    raw = str(os.getenv("GAIA_OPENCLAW_ACTIONABILITY_PROBE", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _openclaw_actionability_probe_limit() -> int:
    try:
        return max(0, int(os.getenv("GAIA_OPENCLAW_ACTIONABILITY_PROBE_LIMIT", "24")))
    except Exception:
        return 24


def _actionability_probe_timeout_ms() -> int:
    try:
        return max(300, int(os.getenv("GAIA_OPENCLAW_ACTIONABILITY_PROBE_TIMEOUT_MS", "1200")))
    except Exception:
        return 1200


def _commit_verify_timeout_ms() -> int:
    try:
        return max(0, int(os.getenv("GAIA_OPENCLAW_COMMIT_VERIFY_TIMEOUT_MS", "3500")))
    except Exception:
        return 3500


def _commit_verify_interval_ms() -> int:
    try:
        return max(100, int(os.getenv("GAIA_OPENCLAW_COMMIT_VERIFY_INTERVAL_MS", "700")))
    except Exception:
        return 700


def _element_actionability_label(item: Dict[str, Any]) -> str:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    for value in (
        item.get("text"),
        attrs.get("role_ref_name"),
        attrs.get("aria-label"),
        attrs.get("title"),
    ):
        cleaned = re.sub(r"\s+", " ", str(value or "").strip())
        if cleaned:
            return cleaned
    return ""


def _select_ref_actionability_probe_candidates(payload: Dict[str, Any]) -> List[str]:
    limit = _openclaw_actionability_probe_limit()
    if limit <= 0:
        return []
    elements = payload.get("elements") if isinstance(payload.get("elements"), list) else []
    role_snapshot = payload.get("role_snapshot") if isinstance(payload.get("role_snapshot"), dict) else {}
    ref_line_index = role_snapshot.get("ref_line_index") if isinstance(role_snapshot.get("ref_line_index"), dict) else {}
    candidates: List[Tuple[int, int, str]] = []
    seen: set[str] = set()
    clickish_roles = {
        "button",
        "link",
        "option",
        "radio",
        "checkbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "gridcell",
    }
    clickish_tags = {"button", "a", "option", "summary"}
    priority_terms = (
        "적용",
        "정렬",
        "필터",
        "옵션",
        "예약",
        "선택",
        "다음",
        "이전",
        "검색",
        "apply",
        "sort",
        "filter",
        "option",
    )
    for item in elements:
        if not isinstance(item, dict):
            continue
        ref_id = str(item.get("ref_id") or "").strip()
        if not ref_id or ref_id in seen:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        role = str(attrs.get("role") or item.get("role") or "").strip().lower()
        tag = str(item.get("tag") or "").strip().lower()
        if role in {"textbox", "searchbox", "combobox", "listbox"}:
            continue
        if role not in clickish_roles and tag not in clickish_tags:
            continue
        label = _element_actionability_label(item)
        normalized = _normalize_hint_text(label)
        score = 8
        if re.fullmatch(r"\d{1,2}(?:[./-]\d{1,2})?", normalized):
            score = 0
        elif re.search(r"\d{1,2}\s*(?:월|일|개|명|박|일차)", normalized):
            score = 1
        elif normalized and len(normalized) <= 8:
            score = 2
        elif any(term in normalized for term in priority_terms):
            score = 3
        try:
            line_index = int(ref_line_index.get(ref_id) or 10**9)
        except Exception:
            line_index = 10**9
        seen.add(ref_id)
        candidates.append((score, line_index, ref_id))
    candidates.sort(key=lambda item: item[:2] + (item[2],))
    return [ref_id for _score, _line_index, ref_id in candidates[:limit]]


def _probe_ref_actionability(
    *,
    base_url: str,
    target_id: str,
    profile: str,
    timeout: Any,
    ref_id: str,
) -> Optional[Dict[str, Any]]:
    payload: Dict[str, Any] = {
        "targetId": target_id,
        "kind": "evaluate",
        "ref": ref_id,
        "fn": _OPENCLAW_REF_ACTIONABILITY_PROBE_FN,
        "timeoutMs": _actionability_probe_timeout_ms(),
    }
    if profile:
        payload["profile"] = profile
    status_code, data, text = _request(
        "POST",
        base_url=base_url,
        path="/act",
        timeout=timeout,
        payload=payload,
    )
    if status_code >= 400:
        message = str((data or {}).get("error") or text or "").strip()
        return {
            "ref": ref_id,
            "status": "probe_failed",
            "actionable": True,
            "reason": message[:200] or "probe_failed",
        }
    result = data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), dict) else {}
    if not result:
        return None
    report = dict(result)
    report["ref"] = ref_id
    report["status"] = str(report.get("status") or "unknown")
    report["actionable"] = bool(report.get("actionable"))
    report["reason"] = str(report.get("reason") or "")
    return report


def _actionability_node_description(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    tag = str(node.get("tag") or "").strip().lower()
    node_id = str(node.get("id") or "").strip()
    class_name = re.sub(r"\s+", ".", str(node.get("className") or "").strip())
    pieces = [tag or "node"]
    if node_id:
        pieces.append(f"#{node_id}")
    if class_name:
        pieces.append(f".{class_name[:80]}")
    return "".join(pieces)[:120]


def _actionability_warning_marker(report: Dict[str, Any]) -> str:
    status = str(report.get("status") or "").strip() or "unknown"
    hit_desc = _actionability_node_description(report.get("hit"))
    reason = str(report.get("reason") or "").strip()
    if status == "covered" and hit_desc:
        return f"not-actionable=covered by {hit_desc}"
    if status == "hidden":
        return "not-actionable=hidden"
    if status == "zero_rect":
        return "not-actionable=zero_rect"
    if status == "no_hit":
        return f"not-actionable=no_hit{':' + reason[:48] if reason else ''}"
    return f"actionability={status}"


def _annotate_role_snapshot_actionability(
    role_snapshot: Dict[str, Any],
    warnings: List[Dict[str, Any]],
    *,
    field: str = "snapshot",
) -> None:
    snapshot = str(role_snapshot.get(field) or "").strip()
    if not snapshot or not warnings:
        return
    warning_by_ref = {
        str(item.get("ref") or "").strip(): _actionability_warning_marker(item)
        for item in warnings
        if str(item.get("ref") or "").strip()
    }
    if not warning_by_ref:
        return
    annotated_lines: List[str] = []
    ref_pattern = re.compile(r"\[ref=([^\]]+)\]")
    for line in snapshot.splitlines():
        marker = ""
        for match in ref_pattern.finditer(line):
            ref_id = str(match.group(1) or "").strip()
            if ref_id in warning_by_ref:
                marker = warning_by_ref[ref_id]
                break
        if marker and marker not in line:
            annotated_lines.append(f"{line} [{marker}]")
        else:
            annotated_lines.append(line)
    role_snapshot[field] = "\n".join(annotated_lines).strip()


def _apply_ref_actionability_reports_to_payload(
    payload: Dict[str, Any],
    reports: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict) or not reports:
        return []
    actionable_bad_statuses = {"covered", "hidden", "zero_rect", "no_hit"}
    warnings = [
        report
        for report in reports
        if isinstance(report, dict)
        and str(report.get("ref") or "").strip()
        and str(report.get("status") or "").strip() in actionable_bad_statuses
        and not bool(report.get("actionable"))
    ]
    if not warnings:
        return []
    warnings_by_ref = {str(item.get("ref") or "").strip(): item for item in warnings}
    for item in list(payload.get("elements") or []) + list(payload.get("dom_elements") or []):
        if not isinstance(item, dict):
            continue
        ref_id = str(item.get("ref_id") or "").strip()
        report = warnings_by_ref.get(ref_id)
        if not report:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        item["attributes"] = attrs
        attrs["openclaw_actionability"] = str(report.get("status") or "")
        attrs["openclaw_actionability_reason"] = str(report.get("reason") or "")
        attrs["openclaw_actionability_hit"] = _actionability_node_description(report.get("hit"))
        attrs["gaia-disabled"] = "true"
        attrs["aria-disabled"] = "true"
        if str(report.get("status") or "") in {"hidden", "zero_rect", "no_hit"}:
            item["is_visible"] = False
    elements_by_ref = payload.get("elements_by_ref") if isinstance(payload.get("elements_by_ref"), dict) else {}
    for ref_id, report in warnings_by_ref.items():
        meta = elements_by_ref.get(ref_id)
        if not isinstance(meta, dict):
            continue
        attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
        meta["attributes"] = attrs
        attrs["openclaw_actionability"] = str(report.get("status") or "")
        attrs["openclaw_actionability_reason"] = str(report.get("reason") or "")
        attrs["openclaw_actionability_hit"] = _actionability_node_description(report.get("hit"))
        attrs["gaia-disabled"] = "true"
        attrs["aria-disabled"] = "true"
        if str(report.get("status") or "") in {"hidden", "zero_rect", "no_hit"}:
            meta["is_visible"] = False
    role_snapshot = payload.get("role_snapshot") if isinstance(payload.get("role_snapshot"), dict) else {}
    if role_snapshot:
        role_snapshot["actionability_warnings"] = warnings
        refs = role_snapshot.get("refs") if isinstance(role_snapshot.get("refs"), dict) else {}
        for ref_id, report in warnings_by_ref.items():
            if isinstance(refs.get(ref_id), dict):
                refs[ref_id]["actionability"] = str(report.get("status") or "")
                refs[ref_id]["actionability_reason"] = str(report.get("reason") or "")
        _annotate_role_snapshot_actionability(role_snapshot, warnings, field="snapshot")
        _annotate_role_snapshot_actionability(role_snapshot, warnings, field="scoped_snapshot")
        role_snapshot["stats"] = _role_snapshot_stats(
            str(role_snapshot.get("snapshot") or ""),
            role_snapshot.get("refs") if isinstance(role_snapshot.get("refs"), dict) else {},
        )
        if str(role_snapshot.get("scoped_snapshot") or "").strip():
            role_snapshot["scoped_stats"] = _role_snapshot_stats(
                str(role_snapshot.get("scoped_snapshot") or ""),
                role_snapshot.get("scoped_refs") if isinstance(role_snapshot.get("scoped_refs"), dict) else {},
            )
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    if evidence is not None:
        evidence["actionability_warning_count"] = len(warnings)
        evidence["actionability_warnings"] = [
            {
                "ref": str(item.get("ref") or ""),
                "status": str(item.get("status") or ""),
                "reason": str(item.get("reason") or ""),
                "hit": _actionability_node_description(item.get("hit")),
            }
            for item in warnings[:12]
        ]
    return warnings


def _augment_snapshot_with_ref_actionability(
    *,
    payload: Dict[str, Any],
    base_url: str,
    target_id: str,
    profile: str,
    timeout: Any,
) -> None:
    if not _openclaw_actionability_probe_enabled():
        return
    candidate_refs = _select_ref_actionability_probe_candidates(payload)
    if not candidate_refs:
        return
    reports: List[Dict[str, Any]] = []
    for ref_id in candidate_refs:
        try:
            report = _probe_ref_actionability(
                base_url=base_url,
                target_id=target_id,
                profile=profile,
                timeout=timeout,
                ref_id=ref_id,
            )
        except Exception:
            report = None
        if isinstance(report, dict):
            reports.append(report)
    warnings = _apply_ref_actionability_reports_to_payload(payload, reports)
    if warnings:
        payload["actionability_probe"] = {
            "enabled": True,
            "candidate_count": len(candidate_refs),
            "warning_count": len(warnings),
        }


def _apply_scope_to_elements(
    elements: List[Dict[str, Any]],
    requested_scope_ref_id: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], bool]:
    if not str(requested_scope_ref_id or "").strip():
        context_snapshot = _build_context_snapshot_from_elements(elements)
        return elements, context_snapshot, False
    full_context = _build_context_snapshot_from_elements(elements)
    node_by_ref = full_context.get("node_by_ref") if isinstance(full_context.get("node_by_ref"), dict) else {}
    filtered = []
    for item in elements:
        current_scope = str(item.get("container_ref_id") or "").strip()
        if _is_within_context_scope(current_scope, requested_scope_ref_id, node_by_ref):
            filtered.append(item)
    if not filtered:
        return elements, full_context, False
    scoped_context = _build_context_snapshot_from_elements(filtered)
    return filtered, scoped_context, True


def _build_snapshot_payload(
    *,
    session_id: str,
    target_id: str,
    current_url: str,
    requested_scope_ref_id: str,
    raw_snapshot: Dict[str, Any],
    state: Dict[str, Any],
    frame_descriptors: Optional[List[Dict[str, Any]]] = None,
    dom_text_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    snapshot = str(raw_snapshot.get("snapshot") or "")
    refs = raw_snapshot.get("refs") if isinstance(raw_snapshot.get("refs"), dict) else {}
    elements, role_snapshot = _pseudo_elements_from_role_snapshot(snapshot, refs, frame_descriptors)
    evidence = _synthesize_snapshot_evidence(elements)
    _merge_dom_text_evidence(role_snapshot=role_snapshot, evidence=evidence, dom_text_blocks=dom_text_blocks)
    frame_texts = _frame_evidence_texts(frame_descriptors)
    if frame_texts:
        evidence["frame_texts"] = frame_texts
        existing_digest = str(evidence.get("text_digest") or "").strip()
        evidence["text_digest"] = " ".join([existing_digest, *frame_texts]).strip()[:2000]
        live_texts = list(evidence.get("live_texts") or [])
        for frame_text in frame_texts:
            if frame_text not in live_texts:
                live_texts.append(frame_text[:160])
        evidence["live_texts"] = live_texts[:12]
    scoped_elements, context_snapshot, scope_applied = _apply_scope_to_elements(elements, requested_scope_ref_id)
    effective_role_snapshot = dict(role_snapshot or {})
    if scope_applied:
        scoped_role_snapshot = _build_role_snapshot_from_elements(scoped_elements)
        _merge_dom_text_evidence(
            role_snapshot=scoped_role_snapshot,
            evidence={"text_digest": "", "live_texts": []},
            dom_text_blocks=dom_text_blocks,
        )
        effective_role_snapshot["scoped_snapshot"] = str(scoped_role_snapshot.get("snapshot") or "")
        effective_role_snapshot["scoped_tree"] = list(scoped_role_snapshot.get("tree") or [])
        effective_role_snapshot["scoped_refs"] = dict(scoped_role_snapshot.get("refs") or {})
        effective_role_snapshot["scoped_stats"] = dict(scoped_role_snapshot.get("stats") or {})
        effective_role_snapshot["scope_applied"] = True
        effective_role_snapshot["scope_container_ref_id"] = requested_scope_ref_id
    else:
        effective_role_snapshot["scope_applied"] = False
        effective_role_snapshot["scope_container_ref_id"] = ""
    state["snapshot_counter"] = int(state.get("snapshot_counter") or 0) + 1
    snapshot_id = f"openclaw:{session_id}:{state['snapshot_counter']}"
    state["last_snapshot_id"] = snapshot_id
    state["current_url"] = current_url
    elements_by_ref = {
        str(item.get("ref_id") or "").strip(): item
        for item in scoped_elements
        if str(item.get("ref_id") or "").strip()
    }
    payload = {
        "success": True,
        "ok": True,
        "reason_code": "ok",
        "session_id": session_id,
        "tab_id": target_id,
        "targetId": target_id,
        "snapshot_id": snapshot_id,
        "epoch": int(state.get("snapshot_counter") or 0),
        "dom_hash": "",
        "mode": "ref",
        "format": "role",
        "elements": scoped_elements,
        "dom_elements": scoped_elements,
        "elements_by_ref": elements_by_ref,
        "current_url": current_url,
        "url": current_url,
        "requested_scope_container_ref_id": requested_scope_ref_id,
        "scope_container_ref_id": requested_scope_ref_id if scope_applied else "",
        "scope_applied": scope_applied,
        "context_snapshot": context_snapshot,
        "role_snapshot": effective_role_snapshot,
        "evidence": evidence,
    }
    state["last_snapshot_payload"] = payload
    return payload


def _snapshot_payload_for_target(
    *,
    base_url: str,
    session_id: str,
    state: Dict[str, Any],
    target_id: str,
    timeout: Any,
    requested_scope_ref_id: str = "",
) -> Optional[Dict[str, Any]]:
    profile = str(state.get("profile") or "").strip()
    params = {
        "targetId": target_id,
        "format": "role",
        "refs": "aria",
        "maxChars": _openclaw_snapshot_max_chars_param(),
    }
    if profile:
        params["profile"] = profile
    status_code, data, text = _request(
        "GET",
        base_url=base_url,
        path="/snapshot",
        timeout=timeout,
        params=params,
    )
    if status_code >= 400:
        return None
    frame_descriptors = (
        _fetch_frame_descriptors_for_target(
            base_url=base_url,
            target_id=target_id,
            profile=profile,
            timeout=timeout,
        )
        if _snapshot_may_contain_iframe(data)
        else []
    )
    dom_text_blocks = _fetch_dom_text_blocks_for_target(
        base_url=base_url,
        target_id=target_id,
        profile=profile,
        timeout=timeout,
    )
    payload = _build_snapshot_payload(
        session_id=session_id,
        target_id=target_id,
        current_url=str(data.get("url") or state.get("current_url") or ""),
        requested_scope_ref_id=requested_scope_ref_id,
        raw_snapshot=data,
        state=state,
        frame_descriptors=frame_descriptors,
        dom_text_blocks=dom_text_blocks,
    )
    _augment_snapshot_with_ref_actionability(
        payload=payload,
        base_url=base_url,
        target_id=target_id,
        profile=profile,
        timeout=timeout,
    )
    state["last_snapshot_payload"] = payload
    return payload


def _sorted_evidence_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    normalized = [str(item).strip() for item in value if str(item).strip()]
    normalized.sort()
    return normalized[:100]


def _same_origin(left: str, right: str) -> bool:
    try:
        left_parts = urlparse(str(left or "").strip())
        right_parts = urlparse(str(right or "").strip())
    except Exception:
        return False
    if not left_parts.scheme or not right_parts.scheme:
        return False
    return (
        str(left_parts.scheme).lower(),
        str(left_parts.netloc).lower(),
    ) == (
        str(right_parts.scheme).lower(),
        str(right_parts.netloc).lower(),
    )


_FRAME_DESCRIPTOR_SCRIPT = r"""() => {
  const frames = Array.from(document.querySelectorAll('iframe'));
  return frames.map((frame, index) => {
    const rect = frame.getBoundingClientRect();
    let sameOrigin = false;
    let bodyText = '';
    let editableCount = 0;
    try {
      const doc = frame.contentDocument;
      sameOrigin = !!doc;
      if (doc) {
        bodyText = String(doc.body?.innerText || '').slice(0, 500);
        editableCount = doc.querySelectorAll('input, textarea, [contenteditable], [role="textbox"], body[contenteditable]').length;
      }
    } catch (_) {}
    return {
      index,
      selector: `iframe >> nth=${index}`,
      id: frame.id || '',
      name: frame.name || '',
      title: frame.title || '',
      src: frame.src || '',
      visible: !!(rect.width && rect.height),
      width: Math.round(rect.width || 0),
      height: Math.round(rect.height || 0),
      sameOrigin,
      bodyText,
      editableCount,
    };
  });
}"""


_DOM_TEXT_EVIDENCE_SCRIPT = r"""() => {
  const READ_HINT_RE = /(comment|comments|reply|replies|review|reviews|opinion|opinions|post|posts|board|cmt|qna|댓글|답글|리뷰|후기|상품평|상품의견|의견)/i;
  const BLOCK_TAGS = new Set(['article', 'li', 'p', 'blockquote', 'dd', 'dt', 'td', 'th', 'figcaption']);
  const BLOCK_ROLES = new Set(['article', 'listitem', 'row', 'cell', 'gridcell', 'paragraph']);
  const SKIP_TAGS = new Set(['script', 'style', 'noscript', 'template', 'svg', 'path', 'canvas']);
  const MAX_NODES = 20000;
  const MAX_RESULTS = 120;

  function clean(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function visible(el) {
    try {
      const style = window.getComputedStyle(el);
      if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) {
        return false;
      }
      const rect = el.getBoundingClientRect();
      return Boolean(el.getClientRects().length && rect.width >= 1 && rect.height >= 1);
    } catch (_) {
      return false;
    }
  }

  function classIdRoleBlob(el) {
    const attrs = [
      el.id || '',
      el.className && typeof el.className === 'string' ? el.className : '',
      el.getAttribute('role') || '',
      el.getAttribute('aria-label') || '',
      el.getAttribute('data-testid') || '',
    ];
    return clean(attrs.join(' '));
  }

  function ancestorHint(el) {
    const parts = [];
    let cur = el;
    for (let depth = 0; cur && depth < 6; depth += 1, cur = cur.parentElement) {
      const blob = classIdRoleBlob(cur);
      if (blob && READ_HINT_RE.test(blob)) {
        parts.push(blob);
      }
    }
    return clean(parts.join(' > ')).slice(0, 240);
  }

  function nearestHeading(el) {
    let cur = el;
    for (let depth = 0; cur && depth < 5; depth += 1, cur = cur.parentElement) {
      const heading = cur.querySelector && cur.querySelector('h1,h2,h3,h4,[role="heading"]');
      const text = clean(heading && heading.innerText);
      if (text && text.length <= 120) {
        return text;
      }
    }
    return '';
  }

  function selectorFor(el) {
    const parts = [];
    let cur = el;
    for (let depth = 0; cur && cur.nodeType === 1 && depth < 5; depth += 1, cur = cur.parentElement) {
      const tag = String(cur.tagName || '').toLowerCase();
      if (!tag) {
        break;
      }
      if (cur.id) {
        parts.unshift(`${tag}#${String(cur.id).slice(0, 64)}`);
        break;
      }
      let part = tag;
      const cls = clean(cur.className && typeof cur.className === 'string' ? cur.className : '')
        .split(' ')
        .filter(Boolean)
        .slice(0, 2)
        .join('.');
      if (cls) {
        part += `.${cls.slice(0, 80)}`;
      }
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((item) => item.tagName === cur.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
        }
      }
      parts.unshift(part);
    }
    return parts.join(' > ');
  }

  const nodes = Array.from(document.body ? document.body.querySelectorAll('*') : []).slice(0, MAX_NODES);
  const candidates = [];
  for (let order = 0; order < nodes.length; order += 1) {
    const el = nodes[order];
    const tag = String(el.tagName || '').toLowerCase();
    if (!tag || SKIP_TAGS.has(tag) || !visible(el)) {
      continue;
    }
    const role = String(el.getAttribute('role') || '').toLowerCase();
    const hint = ancestorHint(el);
    const selfHint = classIdRoleBlob(el);
    const hasReadHint = READ_HINT_RE.test(`${hint} ${selfHint}`);
    const isBlock = BLOCK_TAGS.has(tag) || BLOCK_ROLES.has(role);
    if (!isBlock && !hasReadHint) {
      continue;
    }

    const text = clean(el.innerText || el.textContent || '');
    if (text.length < 12) {
      continue;
    }
    if (text.length > 1800 && !hasReadHint) {
      continue;
    }
    const childCount = el.children ? el.children.length : 0;
    if (childCount > 18 && !hasReadHint) {
      continue;
    }
    const rect = el.getBoundingClientRect();
    const inViewport = rect.bottom >= 0 && rect.top <= window.innerHeight;
    let score = 0;
    if (hasReadHint) score += 40;
    if (tag === 'li' || role === 'listitem') score += 16;
    if (tag === 'article' || role === 'article') score += 12;
    if (tag === 'p' || role === 'paragraph') score += 8;
    if (inViewport) score += 8;
    if (text.length >= 40 && text.length <= 700) score += 10;
    if (text.length > 1200) score -= 10;
    if (childCount > 12) score -= 12;
    if (READ_HINT_RE.test(text)) score += 4;
    candidates.push({
      order,
      score,
      text: text.slice(0, 1200),
      tag,
      role,
      selector: selectorFor(el),
      section: hint || nearestHeading(el),
      inViewport,
    });
  }

  candidates.sort((a, b) => (b.score - a.score) || (a.order - b.order));
  const out = [];
  const seen = new Set();
	  for (const item of candidates) {
	    const normalized = clean(item.text).toLowerCase();
	    if (!normalized || seen.has(normalized)) {
	      continue;
    }
    let duplicate = false;
    for (const existing of seen) {
      if (normalized.length > 80 && existing.includes(normalized)) {
        duplicate = true;
        break;
      }
      if (existing.length > 80 && normalized.includes(existing)) {
        duplicate = true;
        break;
      }
    }
    if (duplicate) {
      continue;
    }
    seen.add(normalized);
    out.push(item);
	    if (out.length >= MAX_RESULTS) {
	      break;
	    }
	  }
	  const pageText = clean(document.body && document.body.innerText);
	  const CHALLENGE_RE = /(cloudflare|checking your browser|verify you are human|captcha|access denied|service unavailable|temporarily unavailable|확인 중|접속이 원활하지|서비스 이용에 불편)/i;
	  if (pageText && CHALLENGE_RE.test(pageText)) {
	    const normalizedPageText = pageText.toLowerCase();
	    const alreadyIncluded = out.some((item) => normalizedPageText.includes(clean(item.text).toLowerCase()));
	    if (!alreadyIncluded) {
	      out.unshift({
	        order: -1,
	        score: 100,
	        text: pageText.slice(0, 1200),
	        tag: 'body',
	        role: 'document',
	        selector: 'body',
	        section: 'page_text',
	        inViewport: true,
	      });
	    }
	  }
	  return out;
	}"""


def _fetch_frame_descriptors_for_target(
    *,
    base_url: str,
    target_id: str,
    profile: str,
    timeout: Any,
) -> List[Dict[str, Any]]:
    if not str(target_id or "").strip():
        return []
    payload: Dict[str, Any] = {
        "kind": "evaluate",
        "targetId": target_id,
        "fn": _FRAME_DESCRIPTOR_SCRIPT,
    }
    if profile:
        payload["profile"] = profile
    try:
        status_code, data, _ = _request(
            "POST",
            base_url=base_url,
            path="/act",
            timeout=timeout,
            payload=payload,
        )
    except Exception:
        return []
    if status_code >= 400 or not isinstance(data, dict):
        return []
    result = data.get("result")
    if not isinstance(result, list):
        return []
    descriptors: List[Dict[str, Any]] = []
    for item in result:
        if isinstance(item, dict) and str(item.get("selector") or "").strip():
            descriptors.append(dict(item))
    return descriptors[:50]


def _fetch_dom_text_blocks_for_target(
    *,
    base_url: str,
    target_id: str,
    profile: str,
    timeout: Any,
) -> List[Dict[str, Any]]:
    if not _openclaw_dom_text_evidence_enabled() or _openclaw_dom_text_block_limit() <= 0:
        return []
    if not str(target_id or "").strip():
        return []
    payload: Dict[str, Any] = {
        "kind": "evaluate",
        "targetId": target_id,
        "fn": _DOM_TEXT_EVIDENCE_SCRIPT,
    }
    if profile:
        payload["profile"] = profile
    try:
        status_code, data, _ = _request(
            "POST",
            base_url=base_url,
            path="/act",
            timeout=timeout,
            payload=payload,
        )
    except Exception:
        return []
    if status_code >= 400 or not isinstance(data, dict):
        return []
    return _normalize_dom_text_blocks(data.get("result"))


def _snapshot_may_contain_iframe(raw_snapshot: Dict[str, Any]) -> bool:
    snapshot = str((raw_snapshot or {}).get("snapshot") or "").lower()
    if "- iframe" in snapshot or " iframe " in snapshot:
        return True
    refs = (raw_snapshot or {}).get("refs")
    if not isinstance(refs, dict):
        return False
    return any(
        str((meta or {}).get("role") or "").strip().lower() == "iframe"
        for meta in refs.values()
        if isinstance(meta, dict)
    )


def _frame_evidence_texts(frame_descriptors: Optional[List[Dict[str, Any]]]) -> List[str]:
    texts: List[str] = []
    for item in list(frame_descriptors or []):
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("bodyText") or "").strip())
        if not text:
            continue
        if text not in texts:
            texts.append(text[:300])
        if len(texts) >= 6:
            break
    return texts


def _tabs_payload_for_target(
    *,
    base_url: str,
    target_id: str,
    profile: str = "",
    timeout: Any,
) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {}
    if str(target_id or "").strip():
        params["targetId"] = str(target_id or "").strip()
    if str(profile or "").strip():
        params["profile"] = str(profile or "").strip()
    status_code, data, text = _request(
        "GET",
        base_url=base_url,
        path="/tabs",
        timeout=timeout,
        params=params,
    )
    if status_code >= 400 or not isinstance(data, dict):
        return None
    return data


def _normalize_tab_descriptor(
    raw_tab: Any,
    *,
    current_tab_id: str,
    current_target_id: str,
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_tab, dict):
        return None
    target_id = str(
        raw_tab.get("cdp_target_id")
        or raw_tab.get("targetId")
        or raw_tab.get("target_id")
        or ""
    ).strip()
    tab_id = str(raw_tab.get("tab_id") or raw_tab.get("id") or raw_tab.get("index") or "").strip()
    url = str(raw_tab.get("url") or raw_tab.get("current_url") or "").strip()
    title = str(raw_tab.get("title") or raw_tab.get("label") or raw_tab.get("name") or "").strip()
    descriptor_key = target_id or f"{tab_id}|{url}|{title}"
    if not descriptor_key.strip():
        return None
    return {
        "descriptor_key": descriptor_key,
        "target_id": target_id,
        "tab_id": tab_id,
        "url": url,
        "title": title,
        "active": bool(
            str(current_tab_id or "").strip() and tab_id and tab_id == str(current_tab_id or "").strip()
        )
        or bool(
            str(current_target_id or "").strip() and target_id and target_id == str(current_target_id or "").strip()
        )
        or bool(raw_tab.get("active")),
    }


def _extract_tab_descriptors(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    current_tab_id = str(payload.get("current_tab_id") or payload.get("targetId") or "").strip()
    current_target_id = str(payload.get("cdp_target_id") or "").strip()
    raw_tabs = payload.get("tabs") if isinstance(payload.get("tabs"), list) else []
    if not raw_tabs and isinstance(payload.get("tab"), dict):
        raw_tabs = [payload.get("tab")]

    descriptors: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw_tab in raw_tabs:
        descriptor = _normalize_tab_descriptor(
            raw_tab,
            current_tab_id=current_tab_id,
            current_target_id=current_target_id,
        )
        if not descriptor:
            continue
        descriptor_key = str(descriptor.get("descriptor_key") or "").strip()
        if not descriptor_key or descriptor_key in seen_keys:
            continue
        seen_keys.add(descriptor_key)
        descriptors.append(descriptor)
    return descriptors


def _resolve_openclaw_tab_descriptor(
    descriptors: List[Dict[str, Any]],
    identifier: str,
) -> Optional[Dict[str, Any]]:
    normalized = str(identifier or "").strip()
    if not normalized:
        return None

    exact_matches: List[Dict[str, Any]] = []
    prefix_matches: List[Dict[str, Any]] = []
    for item in descriptors:
        if not isinstance(item, dict):
            continue
        candidates = [
            str(item.get("target_id") or "").strip(),
            str(item.get("tab_id") or "").strip(),
            str(item.get("descriptor_key") or "").strip(),
        ]
        if normalized in candidates:
            exact_matches.append(item)
            continue
        if any(candidate.startswith(normalized) for candidate in candidates if candidate):
            prefix_matches.append(item)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if not exact_matches and len(prefix_matches) == 1:
        return prefix_matches[0]
    return None


def _guess_new_page_kind(*, url: str, title: str) -> str:
    blob = f"{str(url or '').lower()} {str(title or '').lower()}"
    if any(token in blob for token in ("viewer", "vod", "video", "player", "lecture", "stream", "watch")):
        return "viewer_like"
    if any(token in blob for token in ("doubleclick", "adservice", "promo", "promotion", "advert", "ads")):
        return "ad_like"
    if any(token in blob for token in ("help", "guide", "faq", "support")):
        return "help_like"
    return "unknown"


def _derive_new_page_evidence_from_tabs(
    *,
    before_tabs_payload: Optional[Dict[str, Any]],
    after_tabs_payload: Optional[Dict[str, Any]],
    reference_url: str,
) -> Dict[str, Any]:
    before_tabs = _extract_tab_descriptors(before_tabs_payload)
    after_tabs = _extract_tab_descriptors(after_tabs_payload)
    if not before_tabs or not after_tabs:
        return {}

    before_keys = {
        str(item.get("descriptor_key") or "").strip()
        for item in before_tabs
        if str(item.get("descriptor_key") or "").strip()
    }
    new_pages_raw = [
        item
        for item in after_tabs
        if str(item.get("descriptor_key") or "").strip()
        and str(item.get("descriptor_key") or "").strip() not in before_keys
    ]
    if not new_pages_raw:
        return {}

    new_pages: List[Dict[str, Any]] = []
    same_origin_count = 0
    urls: List[str] = []
    titles: List[str] = []
    kinds: List[str] = []
    for item in new_pages_raw[:5]:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        same_origin = bool(reference_url and url and _same_origin(reference_url, url))
        kind_guess = _guess_new_page_kind(url=url, title=title)
        if same_origin:
            same_origin_count += 1
        if url:
            urls.append(url)
        if title:
            titles.append(title)
        kinds.append(kind_guess)
        new_pages.append(
            {
                "target_id": str(item.get("target_id") or "").strip(),
                "tab_id": str(item.get("tab_id") or "").strip(),
                "url": url,
                "title": title,
                "same_origin": same_origin,
                "kind_guess": kind_guess,
                "active": bool(item.get("active")),
            }
        )

    return {
        "new_page_detected": True,
        "new_page_count": len(new_pages_raw),
        "new_page_same_origin_detected": bool(same_origin_count),
        "new_page_same_origin_count": int(same_origin_count),
        "new_page_urls": urls[:5],
        "new_page_titles": titles[:5],
        "new_page_kinds": kinds[:5],
        "new_pages": new_pages,
    }


def _merge_state_change_evidence(
    *,
    state_change: Optional[Dict[str, Any]],
    evidence: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged = dict(state_change or {})
    if not isinstance(evidence, dict) or not evidence:
        return merged
    for key, value in evidence.items():
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    if bool(evidence.get("new_page_detected")):
        merged["backend_progress"] = True
        merged["backend_effective_only"] = False
    return merged


def _derive_state_change_from_snapshot_payloads(
    *,
    before_payload: Optional[Dict[str, Any]],
    after_payload: Optional[Dict[str, Any]],
    new_page_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    before = before_payload if isinstance(before_payload, dict) else {}
    after = after_payload if isinstance(after_payload, dict) else {}
    before_evidence = before.get("evidence") if isinstance(before.get("evidence"), dict) else {}
    after_evidence = after.get("evidence") if isinstance(after.get("evidence"), dict) else {}

    before_url = str(before.get("current_url") or before.get("url") or "").strip()
    after_url = str(after.get("current_url") or after.get("url") or "").strip()
    before_texts = _sorted_evidence_list(before_evidence.get("live_texts"))
    after_texts = _sorted_evidence_list(after_evidence.get("live_texts"))

    url_changed = bool(before_url and after_url and before_url != after_url)
    text_digest_changed = str(before_evidence.get("text_digest") or "") != str(after_evidence.get("text_digest") or "")
    live_text_changed = before_texts != after_texts
    list_count_changed = int(before_evidence.get("list_count", 0) or 0) != int(after_evidence.get("list_count", 0) or 0)
    interactive_count_changed = int(before_evidence.get("interactive_count", 0) or 0) != int(after_evidence.get("interactive_count", 0) or 0)
    modal_count_changed = int(before_evidence.get("modal_count", 0) or 0) != int(after_evidence.get("modal_count", 0) or 0)
    backdrop_count_changed = int(before_evidence.get("backdrop_count", 0) or 0) != int(after_evidence.get("backdrop_count", 0) or 0)
    dialog_count_changed = int(before_evidence.get("dialog_count", 0) or 0) != int(after_evidence.get("dialog_count", 0) or 0)
    modal_state_changed = bool(before_evidence.get("modal_open")) != bool(after_evidence.get("modal_open"))
    auth_state_changed = any(
        (
            bool(before_evidence.get("auth_prompt_visible")) != bool(after_evidence.get("auth_prompt_visible")),
            bool(before_evidence.get("login_visible")) != bool(after_evidence.get("login_visible")),
            bool(before_evidence.get("logout_visible")) != bool(after_evidence.get("logout_visible")),
        )
    )
    auth_emerged = (not bool(before_evidence.get("auth_prompt_visible"))) and bool(after_evidence.get("auth_prompt_visible"))
    modal_emerged = (not bool(before_evidence.get("modal_open"))) and bool(after_evidence.get("modal_open"))
    login_emerged = (not bool(before_evidence.get("login_visible"))) and bool(after_evidence.get("login_visible"))
    logout_emerged = (not bool(before_evidence.get("logout_visible"))) and bool(after_evidence.get("logout_visible"))
    popup_detected = (not bool(before_evidence.get("modal_open"))) and bool(after_evidence.get("modal_open"))
    dialog_detected = int(after_evidence.get("dialog_count", 0) or 0) > int(before_evidence.get("dialog_count", 0) or 0)
    backend_progress = any(
        (
            url_changed,
            text_digest_changed,
            live_text_changed,
            list_count_changed,
            modal_count_changed,
            backdrop_count_changed,
            dialog_count_changed,
            modal_state_changed,
            auth_state_changed,
            auth_emerged,
            modal_emerged,
            login_emerged,
            logout_emerged,
            popup_detected,
            dialog_detected,
        )
    )

    state_change = {
        "backend": "openclaw",
        "backend_postact_probe": True,
        "backend_progress": bool(backend_progress),
        "backend_effective_only": not bool(backend_progress),
        "effective": True,
        "url_changed": bool(url_changed),
        "text_digest_changed": bool(text_digest_changed or live_text_changed),
        "status_text_changed": bool(text_digest_changed or live_text_changed),
        "list_count_changed": bool(list_count_changed),
        "interactive_count_changed": bool(interactive_count_changed),
        "modal_count_changed": bool(modal_count_changed),
        "backdrop_count_changed": bool(backdrop_count_changed),
        "dialog_count_changed": bool(dialog_count_changed),
        "modal_state_changed": bool(modal_state_changed),
        "auth_state_changed": bool(auth_state_changed),
        "auth_emerged": bool(auth_emerged),
        "modal_emerged": bool(modal_emerged),
        "login_emerged": bool(login_emerged),
        "logout_emerged": bool(logout_emerged),
        "popup_detected": bool(popup_detected),
        "dialog_detected": bool(dialog_detected),
        "snapshot_id_before": str(before.get("snapshot_id") or ""),
        "snapshot_id_after": str(after.get("snapshot_id") or ""),
    }
    return _merge_state_change_evidence(
        state_change=state_change,
        evidence=new_page_evidence,
    )


def _reason_code_from_error(message: str, status_code: int) -> str:
    text = str(message or "").strip().lower()
    if extract_pointer_interceptor(message):
        return "pointer_intercepted"
    if "ref is required" in text:
        return "ref_required"
    if "selector" in text and "unsupported" in text:
        return "legacy_selector_forbidden"
    if "timed out" in text or "timeout" in text:
        return "action_timeout"
    if "not found" in text:
        return "not_found"
    if "unknown ref" in text:
        return "ref_stale"
    if "browser not running" in text:
        return "request_exception"
    if status_code >= 500:
        return "http_5xx"
    if status_code >= 400:
        return "http_4xx"
    return "failed"


def _build_openclaw_action_payload(
    *,
    target_id: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    browser_action = str((params or {}).get("action") or "").strip()
    input_ref_id = str((params or {}).get("ref_id") or (params or {}).get("refId") or (params or {}).get("ref") or "").strip()
    ref_id = input_ref_id
    value = (params or {}).get("value")
    payload: Dict[str, Any] = {"targetId": target_id}

    if browser_action == "wait":
        payload["kind"] = "wait"
        for src, dest in (
            ("time_ms", "timeMs"),
            ("timeMs", "timeMs"),
            ("timeout_ms", "timeoutMs"),
            ("timeoutMs", "timeoutMs"),
            ("text", "text"),
            ("text_gone", "textGone"),
            ("textGone", "textGone"),
            ("selector", "selector"),
            ("url", "url"),
            ("load_state", "loadState"),
            ("loadState", "loadState"),
            ("fn", "fn"),
            ("js", "fn"),
        ):
            picked = (params or {}).get(src)
            if picked not in (None, ""):
                payload[dest] = picked
    elif browser_action == "click":
        payload.update({"kind": "click", "ref": ref_id})
        if (params or {}).get("doubleClick") is not None:
            payload["doubleClick"] = (params or {}).get("doubleClick")
        if (params or {}).get("button") is not None:
            payload["button"] = (params or {}).get("button")
        if (params or {}).get("modifiers") is not None:
            payload["modifiers"] = (params or {}).get("modifiers")
    elif browser_action == "fill":
        fields = (params or {}).get("fields")
        if isinstance(fields, list) and fields:
            mapped_fields: List[Dict[str, Any]] = []
            for field in fields:
                if not isinstance(field, dict):
                    continue
                mapped = dict(field)
                field_ref = str(mapped.get("ref") or mapped.get("ref_id") or "").strip()
                if field_ref:
                    mapped["ref"] = field_ref
                mapped_fields.append(mapped)
            payload.update({"kind": "fill", "fields": mapped_fields})
        else:
            payload.update(
                {
                    "kind": "fill",
                    "fields": [
                        {
                            "ref": ref_id,
                            "type": "text",
                            "value": "" if value is None else str(value),
                        }
                    ],
                }
            )
    elif browser_action == "type":
        selector = str((params or {}).get("selector") or (params or {}).get("full_selector") or "").strip()
        payload.update({"kind": "type", "text": "" if value is None else str(value)})
        if ref_id:
            payload["ref"] = ref_id
        if selector:
            payload["selector"] = selector
        if (params or {}).get("submit") is not None:
            payload["submit"] = (params or {}).get("submit")
        if (params or {}).get("slowly") is not None:
            payload["slowly"] = (params or {}).get("slowly")
    elif browser_action == "press":
        payload.update({"kind": "press", "key": str(value or (params or {}).get("key") or "").strip()})
    elif browser_action == "hover":
        payload.update({"kind": "hover", "ref": ref_id})
    elif browser_action == "select":
        values = (params or {}).get("values")
        if not isinstance(values, list):
            values = [str(value)] if value not in (None, "") else []
        payload.update({"kind": "select", "ref": ref_id, "values": values})
    elif browser_action in {"scrollIntoView", "scroll_into_view"}:
        payload.update({"kind": "scrollIntoView", "ref": ref_id})
    elif browser_action == "scroll":
        direction = str((value or {}).get("direction") if isinstance(value, dict) else value or "").strip().lower()
        if direction in {"up", "top", "home"}:
            fn = "() => { const target = document.scrollingElement || document.documentElement; const before = target.scrollTop; target.scrollTop = 0; return { before, after: target.scrollTop }; }"
        elif direction in {"bottom", "end"}:
            fn = "() => { const target = document.scrollingElement || document.documentElement; const before = target.scrollTop; target.scrollTop = target.scrollHeight || target.scrollTop; return { before, after: target.scrollTop }; }"
        else:
            fn = "() => { const target = document.scrollingElement || document.documentElement; const before = target.scrollTop; const delta = Math.max(600, Math.floor(window.innerHeight * 0.8)); target.scrollTop += delta; return { before, after: target.scrollTop }; }"
        payload.update({"kind": "evaluate", "fn": fn})
    elif browser_action in {"dragAndDrop", "drag"}:
        payload.update(
            {
                "kind": "drag",
                "startRef": str((params or {}).get("start_ref") or (params or {}).get("startRef") or input_ref_id).strip(),
                "endRef": str((params or {}).get("end_ref") or (params or {}).get("endRef") or "").strip(),
            }
        )
    elif browser_action == "resize":
        payload.update(
            {
                "kind": "resize",
                "width": (params or {}).get("width"),
                "height": (params or {}).get("height"),
            }
        )
    elif browser_action == "evaluate":
        payload.update({"kind": "evaluate", "fn": str((params or {}).get("fn") or value or "").strip()})
        if ref_id:
            payload["ref"] = ref_id
    elif browser_action == "close":
        payload.update({"kind": "close"})
    else:
        raise ValueError(f"unsupported openclaw action: {browser_action}")

    if (params or {}).get("timeoutMs") is not None:
        payload["timeoutMs"] = (params or {}).get("timeoutMs")
    elif (params or {}).get("timeout_ms") is not None:
        payload["timeoutMs"] = (params or {}).get("timeout_ms")
    return payload


def _normalize_failure(status_code: int, data: Dict[str, Any], text: str) -> Tuple[int, Dict[str, Any], str]:
    message = str((data or {}).get("error") or text or "openclaw_request_failed")
    pointer_interceptor = extract_pointer_interceptor(message)
    reason_code = _reason_code_from_error(message, status_code)
    state_change: Dict[str, Any] = {"effective": False, "backend": "openclaw"}
    attempt_logs: List[Dict[str, Any]] = []
    if pointer_interceptor:
        state_change["pointer_interceptor"] = pointer_interceptor
        attempt_logs.append(
            {
                "reason_code": reason_code,
                "error": message,
                "pointer_interceptor": pointer_interceptor,
            }
        )
    return (
        200,
        {
            "success": False,
            "effective": False,
            "reason_code": reason_code,
            "reason": message,
            "state_change": state_change,
            "attempt_logs": attempt_logs,
            "retry_path": [],
            "attempt_count": len(attempt_logs),
        },
        message,
    )


def _collect_act_failure_diagnostics(
    *,
    base_url: str,
    session_id: str,
    state: Dict[str, Any],
    target_id: str,
    ref_used: str,
    timeout: Any,
) -> Dict[str, Any]:
    """Capture a fresh snapshot right after a failed /act so the artifact can
    tell a genuinely stale ref apart from a ref that is still present but could
    not be acted on (commonly an overlay covering it). Records whether the
    failed ref survives in a fresh snapshot, whether the target drifted, and the
    fresh role/name so the real cause is visible instead of a bare not_found."""
    diagnostics: Dict[str, Any] = {"ref_used": ref_used}
    try:
        fresh = _snapshot_payload_for_target(
            base_url=base_url,
            session_id=session_id,
            state=state,
            target_id=target_id,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - best-effort diagnostic
        diagnostics["fresh_snapshot"] = "error"
        diagnostics["fresh_snapshot_error"] = str(exc)[:200]
        return diagnostics
    if not isinstance(fresh, dict):
        diagnostics["fresh_snapshot"] = "unavailable"
        return diagnostics
    elements_by_ref = fresh.get("elements_by_ref") if isinstance(fresh.get("elements_by_ref"), dict) else {}
    element = elements_by_ref.get(ref_used) if ref_used else None
    fresh_target_id = str(fresh.get("targetId") or fresh.get("tab_id") or "").strip()
    diagnostics["fresh_snapshot"] = "captured"
    diagnostics["fresh_snapshot_id"] = str(fresh.get("snapshot_id") or "")
    diagnostics["ref_present_in_fresh_snapshot"] = bool(element)
    diagnostics["target_changed"] = bool(fresh_target_id and target_id and fresh_target_id != target_id)
    if isinstance(element, dict):
        diagnostics["fresh_ref_role"] = str(element.get("role") or "")
        diagnostics["fresh_ref_name"] = str(element.get("name") or element.get("label") or "")[:80]
        diagnostics["fresh_ref_interactive"] = bool(element.get("interactive"))
    if ref_used:
        try:
            actionability = _probe_ref_actionability(
                base_url=base_url,
                target_id=target_id,
                profile=str(state.get("profile") or "").strip(),
                timeout=timeout,
                ref_id=ref_used,
            )
        except Exception:
            actionability = None
        if isinstance(actionability, dict):
            diagnostics["ref_actionability"] = {
                "status": str(actionability.get("status") or ""),
                "actionable": bool(actionability.get("actionable")),
                "reason": str(actionability.get("reason") or ""),
                "hit": _actionability_node_description(actionability.get("hit")),
                "target": _actionability_node_description(actionability.get("target")),
            }
    return diagnostics


_REF_VISIBILITY_REVEAL_FN = r"""(el) => {
  function rectOf(node) {
    try {
      const rect = node.getBoundingClientRect();
      return {
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        bottom: Math.round(rect.bottom),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    } catch (_) {
      return {};
    }
  }
  function visibleInViewport(rect) {
    return !!(
      rect &&
      rect.width >= 1 &&
      rect.height >= 1 &&
      rect.bottom >= 0 &&
      rect.right >= 0 &&
      rect.top <= window.innerHeight &&
      rect.left <= window.innerWidth
    );
  }
  function isScrollable(node) {
    try {
      const style = window.getComputedStyle(node);
      const overflow = `${style.overflow} ${style.overflowY} ${style.overflowX}`.toLowerCase();
      return (
        /(auto|scroll|overlay|hidden)/.test(overflow) &&
        ((node.scrollHeight - node.clientHeight) > 1 || (node.scrollWidth - node.clientWidth) > 1)
      );
    } catch (_) {
      return false;
    }
  }

  const beforeRect = rectOf(el);
  const scrollChanges = [];
  try {
    el.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
  } catch (_) {}

  const ancestors = [];
  for (let node = el.parentElement; node && ancestors.length < 8; node = node.parentElement) {
    if (isScrollable(node)) {
      ancestors.push(node);
    }
  }
  for (const node of ancestors) {
    const beforeTop = node.scrollTop;
    const beforeLeft = node.scrollLeft;
    try {
      const nodeRect = node.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      if (elRect.top < nodeRect.top || elRect.bottom > nodeRect.bottom) {
        node.scrollTop += (elRect.top + elRect.bottom) / 2 - (nodeRect.top + nodeRect.bottom) / 2;
      }
      if (elRect.left < nodeRect.left || elRect.right > nodeRect.right) {
        node.scrollLeft += (elRect.left + elRect.right) / 2 - (nodeRect.left + nodeRect.right) / 2;
      }
    } catch (_) {}
    const afterTop = node.scrollTop;
    const afterLeft = node.scrollLeft;
    if (Math.abs(afterTop - beforeTop) >= 1 || Math.abs(afterLeft - beforeLeft) >= 1) {
      scrollChanges.push({
        tag: String(node.tagName || "").toLowerCase(),
        id: node.id || "",
        className: typeof node.className === "string" ? node.className.slice(0, 120) : "",
        beforeTop,
        afterTop,
        beforeLeft,
        afterLeft,
      });
    }
  }

  const afterRect = rectOf(el);
  return {
    beforeRect,
    afterRect,
    viewportVisibleBefore: visibleInViewport(beforeRect),
    viewportVisibleAfter: visibleInViewport(afterRect),
    scrollChanged: scrollChanges.length > 0,
    scrollChanges,
  };
}"""


def _attempt_ref_visibility_reveal(
    *,
    base_url: str,
    target_id: str,
    profile_name: str,
    ref_used: str,
    timeout: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "targetId": target_id,
        "kind": "evaluate",
        "ref": ref_used,
        "fn": _REF_VISIBILITY_REVEAL_FN,
        "timeoutMs": 4500,
    }
    if profile_name:
        payload["profile"] = profile_name
    status_code, data, text = _request(
        "POST",
        base_url=base_url,
        path="/act",
        timeout=timeout,
        payload=payload,
    )
    result = data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), dict) else {}
    message = str((data or {}).get("error") or text or "").strip() if status_code >= 400 else ""
    return {
        "kind": "ref_visibility_reveal",
        "ref": ref_used,
        "success": status_code < 400,
        "reason_code": "ok" if status_code < 400 else _reason_code_from_error(message, status_code),
        "error": message,
        "result": {
            "viewport_visible_before": bool(result.get("viewportVisibleBefore")),
            "viewport_visible_after": bool(result.get("viewportVisibleAfter")),
            "scroll_changed": bool(result.get("scrollChanged")),
            "before_rect": result.get("beforeRect") if isinstance(result.get("beforeRect"), dict) else {},
            "after_rect": result.get("afterRect") if isinstance(result.get("afterRect"), dict) else {},
            "scroll_changes": list(result.get("scrollChanges") or [])[:4] if isinstance(result.get("scrollChanges"), list) else [],
        },
    }


_POINTER_INTERCEPTOR_RECOVERY_FN = r"""async (el) => {
  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
  function rectOf(node) {
    try {
      const rect = node.getBoundingClientRect();
      return {
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        bottom: Math.round(rect.bottom),
        right: Math.round(rect.right),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    } catch (_) {
      return {};
    }
  }
  function describe(node) {
    if (!node || node === document.documentElement || node === document.body) {
      return {};
    }
    let className = "";
    try {
      className = typeof node.className === "string" ? node.className : "";
    } catch (_) {}
    return {
      tag: String(node.tagName || "").toLowerCase(),
      id: node.id || "",
      className: className.slice(0, 160),
      role: node.getAttribute("role") || "",
      ariaLabel: node.getAttribute("aria-label") || "",
      rect: rectOf(node),
      textLength: String(node.innerText || node.textContent || "").trim().length,
    };
  }
  function centerPoint(node) {
    const rect = node.getBoundingClientRect();
    const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
    const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
    return { x, y };
  }
  function blockerAtTarget() {
    const point = centerPoint(el);
    const hit = document.elementFromPoint(point.x, point.y);
    const blocked = !!(hit && hit !== el && !el.contains(hit));
    return { point, hit, blocked };
  }
  function looksLikeInertOverlay(node) {
    if (!node || node === document.documentElement || node === document.body) {
      return false;
    }
    const info = describe(node);
    const style = window.getComputedStyle(node);
    const hint = `${info.tag} ${info.id} ${info.className} ${info.role} ${info.ariaLabel}`.toLowerCase();
    const hasOverlayHint = /(overlay|backdrop|scrim|dim|dimmed|mask|modal|popup|layer)/.test(hint);
    const rect = node.getBoundingClientRect();
    const area = Math.max(0, rect.width) * Math.max(0, rect.height);
    const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
    const coversViewport = (
      area >= viewportArea * 0.15 ||
      (rect.left <= 4 && rect.top <= 4 && rect.right >= window.innerWidth * 0.65 && rect.bottom >= window.innerHeight * 0.65)
    );
    const positioned = /^(fixed|absolute|sticky)$/.test(String(style.position || "").toLowerCase());
    const lowContent = info.textLength <= 120;
    return lowContent && (hasOverlayHint || (positioned && coversViewport));
  }
  function fireEscape() {
    const eventInit = {
      key: "Escape",
      code: "Escape",
      keyCode: 27,
      which: 27,
      bubbles: true,
      cancelable: true,
    };
    for (const target of [document.activeElement, document.body, document, window]) {
      try {
        target && target.dispatchEvent(new KeyboardEvent("keydown", eventInit));
        target && target.dispatchEvent(new KeyboardEvent("keyup", eventInit));
      } catch (_) {}
    }
  }

  const before = blockerAtTarget();
  const beforeBlocker = describe(before.hit);
  if (!before.blocked) {
    return {
      recovered: true,
      action: "already_clickable",
      targetClickableBefore: true,
      targetClickableAfter: true,
      beforeBlocker,
      afterBlocker: {},
    };
  }

  fireEscape();
  await sleep(80);
  const afterEscape = blockerAtTarget();
  if (!afterEscape.blocked) {
    return {
      recovered: true,
      action: "escape",
      targetClickableBefore: false,
      targetClickableAfter: true,
      beforeBlocker,
      afterBlocker: {},
    };
  }

  const blocker = afterEscape.hit;
  let action = "none";
  let bypassed = false;
  if (looksLikeInertOverlay(blocker)) {
    try {
      blocker.setAttribute("data-gaia-pointer-recovery", "pointer-events-none");
      blocker.style.pointerEvents = "none";
      action = "pointer_events_none";
      bypassed = true;
    } catch (_) {}
  }

  await sleep(20);
  const after = blockerAtTarget();
  return {
    recovered: !after.blocked,
    action,
    bypassed,
    targetClickableBefore: false,
    targetClickableAfter: !after.blocked,
    beforeBlocker,
    afterEscapeBlocker: describe(afterEscape.hit),
    afterBlocker: describe(after.hit),
  };
}"""


def _attempt_pointer_interceptor_recovery(
    *,
    base_url: str,
    target_id: str,
    profile_name: str,
    ref_used: str,
    timeout: Any,
    pointer_interceptor: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "targetId": target_id,
        "kind": "evaluate",
        "ref": ref_used,
        "fn": _POINTER_INTERCEPTOR_RECOVERY_FN,
        "timeoutMs": 4500,
    }
    if profile_name:
        payload["profile"] = profile_name
    status_code, data, text = _request(
        "POST",
        base_url=base_url,
        path="/act",
        timeout=timeout,
        payload=payload,
    )
    result = data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), dict) else {}
    message = str((data or {}).get("error") or text or "").strip() if status_code >= 400 else ""
    return {
        "kind": "pointer_interceptor_overlay_recovery",
        "ref": ref_used,
        "success": status_code < 400,
        "reason_code": "ok" if status_code < 400 else _reason_code_from_error(message, status_code),
        "error": message,
        "pointer_interceptor": pointer_interceptor or {},
        "result": {
            "recovered": bool(result.get("recovered")),
            "action": str(result.get("action") or ""),
            "bypassed": bool(result.get("bypassed")),
            "target_clickable_before": bool(result.get("targetClickableBefore")),
            "target_clickable_after": bool(result.get("targetClickableAfter")),
            "before_blocker": result.get("beforeBlocker") if isinstance(result.get("beforeBlocker"), dict) else {},
            "after_escape_blocker": result.get("afterEscapeBlocker") if isinstance(result.get("afterEscapeBlocker"), dict) else {},
            "after_blocker": result.get("afterBlocker") if isinstance(result.get("afterBlocker"), dict) else {},
        },
    }


def _retry_timeout_ms_for_revealed_ref(payload: Dict[str, Any]) -> int:
    raw_value = payload.get("timeoutMs")
    try:
        value = int(raw_value)
    except Exception:
        value = 6000
    return max(1000, min(value, 6000))


def dispatch_openclaw_action(
    raw_base_url: str | None,
    *,
    action: str,
    params: Dict[str, Any],
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    effective_params = dict(params or {})
    if action == "browser_wait" and not str(effective_params.get("action") or "").strip():
        effective_params["action"] = "wait"
    base_url = _resolve_base_url(raw_base_url)
    session_id = str((effective_params or {}).get("session_id") or "default")
    profile_name = _session_profile(
        session_id,
        (effective_params or {}).get("profile")
        or (effective_params or {}).get("browser_profile")
        or (effective_params or {}).get("profile_name"),
    )
    effective_params["profile"] = profile_name
    requested_url = str((effective_params or {}).get("url") or "").strip()
    if action == "browser_tabs_focus":
        target_identifier = str(
            (effective_params or {}).get("targetId")
            or (effective_params or {}).get("tab_id")
            or (effective_params or {}).get("index")
            or ""
        ).strip()
        if not target_identifier:
            return _normalize_failure(400, {"error": "targetId/tab_id/index is required for tabs.focus"}, "")
        state = _ensure_target(
            base_url=base_url,
            session_id=session_id,
            requested_url="",
            timeout=timeout,
        )
        current_target_id = str(state.get("target_id") or "").strip()
        tabs_payload = _tabs_payload_for_target(
            base_url=base_url,
            target_id=current_target_id,
            profile=profile_name,
            timeout=timeout,
        )
        descriptors = _extract_tab_descriptors(tabs_payload)
        matched = _resolve_openclaw_tab_descriptor(descriptors, target_identifier)
        if matched is None:
            return _normalize_failure(404, {"error": f"tab not found: {target_identifier}"}, "")
        matched_target_id = str(matched.get("target_id") or "").strip()
        matched_tab_id = str(matched.get("tab_id") or "").strip()
        matched_url = _normalize_url(str(matched.get("url") or "").strip())
        if matched_target_id:
            state["target_id"] = matched_target_id
        if matched_url:
            state["current_url"] = matched_url
        return (
            200,
            {
                "success": True,
                "reason_code": "ok",
                "session_id": session_id,
                "profile": profile_name,
                "targetId": matched_target_id or matched_tab_id or target_identifier,
                "current_tab_id": matched_tab_id,
                "current_url": str(state.get("current_url") or matched_url or ""),
                "tab": matched,
                "tabs": descriptors,
            },
            "",
        )
    if action == "browser_snapshot":
        fallback_url = requested_url
        state = _ensure_target(
            base_url=base_url,
            session_id=session_id,
            requested_url=requested_url,
            timeout=timeout,
        )
        if bool((effective_params or {}).get("force_refresh")):
            _clear_snapshot_cache(state)
        target_id = str(state.get("target_id") or "").strip()
        status_code, data, text = _request(
            "GET",
            base_url=base_url,
            path="/snapshot",
            timeout=timeout,
            params={
                "targetId": target_id,
                "format": "role",
                "refs": "aria",
                "profile": profile_name,
                "maxChars": _openclaw_snapshot_max_chars_param(),
            },
        )
        if status_code >= 400 and _target_missing(status_code, data, text):
            fallback_url = str(state.get("current_url") or fallback_url or "")
            _clear_session_target(session_id)
            state = _ensure_target(
                base_url=base_url,
                session_id=session_id,
                requested_url=fallback_url,
                timeout=timeout,
            )
            target_id = str(state.get("target_id") or "").strip()
            status_code, data, text = _request(
                "GET",
                base_url=base_url,
                path="/snapshot",
                timeout=timeout,
                params={
                    "targetId": target_id,
                    "format": "role",
                    "refs": "aria",
                    "profile": profile_name,
                    "maxChars": _openclaw_snapshot_max_chars_param(),
                },
            )
        if status_code >= 400:
            return _normalize_failure(status_code, data, text)
        frame_descriptors = (
            _fetch_frame_descriptors_for_target(
                base_url=base_url,
                target_id=target_id,
                profile=profile_name,
                timeout=timeout,
            )
            if _snapshot_may_contain_iframe(data)
            else []
        )
        dom_text_blocks = _fetch_dom_text_blocks_for_target(
            base_url=base_url,
            target_id=target_id,
            profile=profile_name,
            timeout=timeout,
        )
        payload = _build_snapshot_payload(
            session_id=session_id,
            target_id=target_id,
            current_url=str(data.get("url") or state.get("current_url") or requested_url),
            requested_scope_ref_id=str((effective_params or {}).get("scope_container_ref_id") or "").strip(),
            raw_snapshot=data,
            state=state,
            frame_descriptors=frame_descriptors,
            dom_text_blocks=dom_text_blocks,
        )
        return 200, payload, ""

    if action == "browser_find":
        query = str(
            (effective_params or {}).get("query")
            or (effective_params or {}).get("text")
            or (effective_params or {}).get("description")
            or (effective_params or {}).get("value")
            or ""
        ).strip()
        if not query:
            return _normalize_failure(400, {"error": "query/text/description is required for browser_find"}, "")
        try:
            limit = int((effective_params or {}).get("limit") or 5)
        except Exception:
            limit = 5
        limit = max(1, min(20, limit))
        fallback_url = requested_url
        state = _ensure_target(
            base_url=base_url,
            session_id=session_id,
            requested_url=requested_url,
            timeout=timeout,
        )
        if bool((effective_params or {}).get("force_refresh")):
            _clear_snapshot_cache(state)
        target_id = str(state.get("target_id") or "").strip()
        snapshot_payload = _snapshot_payload_for_target(
            base_url=base_url,
            session_id=session_id,
            state=state,
            target_id=target_id,
            timeout=timeout,
            requested_scope_ref_id=str((effective_params or {}).get("scope_container_ref_id") or "").strip(),
        )
        if snapshot_payload is None:
            fallback_url = str(state.get("current_url") or fallback_url or "")
            _clear_session_target(session_id)
            state = _ensure_target(
                base_url=base_url,
                session_id=session_id,
                requested_url=fallback_url,
                timeout=timeout,
            )
            target_id = str(state.get("target_id") or "").strip()
            snapshot_payload = _snapshot_payload_for_target(
                base_url=base_url,
                session_id=session_id,
                state=state,
                target_id=target_id,
                timeout=timeout,
                requested_scope_ref_id=str((effective_params or {}).get("scope_container_ref_id") or "").strip(),
            )
        if snapshot_payload is None:
            return _normalize_failure(502, {"error": "snapshot unavailable for browser_find"}, "")
        elements = list(snapshot_payload.get("elements") or [])
        matches = _browser_find_matches(query=query, elements=elements, limit=limit)
        best_match = matches[0] if matches else {}
        found = bool(best_match)
        return (
            200,
            {
                "success": True,
                "ok": True,
                "found": found,
                "reason_code": "ok" if found else "not_found",
                "query": query,
                "ref_id": str(best_match.get("ref_id") or "") if found else "",
                "match": best_match,
                "matches": matches,
                "snapshot_id": str(snapshot_payload.get("snapshot_id") or ""),
                "current_url": str(snapshot_payload.get("current_url") or snapshot_payload.get("url") or ""),
                "url": str(snapshot_payload.get("current_url") or snapshot_payload.get("url") or ""),
                "session_id": session_id,
                "profile": profile_name,
                "targetId": target_id,
            },
            "",
        )

    if action in {"capture_screenshot", "browser_screenshot"}:
        fallback_url = requested_url
        state = _ensure_target(
            base_url=base_url,
            session_id=session_id,
            requested_url=requested_url,
            timeout=timeout,
        )
        target_id = str(state.get("target_id") or "").strip()
        image_type = str((params or {}).get("type") or "png").strip().lower()
        if image_type not in {"png", "jpeg"}:
            image_type = "png"
        payload = {
            "targetId": target_id,
            "profile": profile_name,
            "fullPage": bool((effective_params or {}).get("fullPage") or (effective_params or {}).get("full_page")),
            "ref": str((effective_params or {}).get("ref") or "").strip() or None,
            "element": str((effective_params or {}).get("element") or "").strip() or None,
            "type": image_type,
        }
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/screenshot",
            timeout=timeout,
            payload=payload,
        )
        if status_code >= 400 and _target_missing(status_code, data, text):
            fallback_url = str(state.get("current_url") or fallback_url or "")
            _clear_session_target(session_id)
            state = _ensure_target(
                base_url=base_url,
                session_id=session_id,
                requested_url=fallback_url,
                timeout=timeout,
            )
            target_id = str(state.get("target_id") or "").strip()
            payload["targetId"] = target_id
            status_code, data, text = _request(
                "POST",
                base_url=base_url,
                path="/screenshot",
                timeout=timeout,
                payload=payload,
            )
        if status_code >= 400:
            return _normalize_failure(status_code, data, text)
        image_path = Path(str((data or {}).get("path") or "").strip())
        if not image_path.exists():
            return _normalize_failure(
                500,
                {"error": f"openclaw_screenshot_path_missing: {image_path}"},
                "",
            )
        screenshot_base64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        current_url = str((data or {}).get("url") or state.get("current_url") or requested_url)
        payload = {
            "success": True,
            "reason_code": "ok",
            "session_id": session_id,
            "profile": profile_name,
            "targetId": str((data or {}).get("targetId") or target_id),
            "current_url": current_url,
            "screenshot": screenshot_base64,
            "mime_type": f"image/{image_type}",
            "saved_path": str(image_path),
            "meta": {
                "full_page": bool((effective_params or {}).get("fullPage") or (effective_params or {}).get("full_page")),
                "type": image_type,
                "backend": "openclaw",
            },
        }
        return 200, payload, ""

    state = _ensure_target(
        base_url=base_url,
        session_id=session_id,
        requested_url=requested_url,
        timeout=timeout,
    )
    fallback_url = str(state.get("current_url") or requested_url or "")
    target_id = str(state.get("target_id") or "").strip()
    browser_action = str((effective_params or {}).get("action") or "").strip()
    if browser_action in {"goto", "navigate"}:
        current_url = str(state.get("current_url") or requested_url or "")
        return (
            200,
            {
                "success": True,
                "effective": True,
                "reason_code": "ok",
                "reason": "ok",
                "changed": True,
                "state_change": {
                    "backend": "openclaw",
                    "backend_progress": True,
                    "backend_effective_only": False,
                    "effective": True,
                    "navigated": True,
                },
                "attempt_logs": [],
                "retry_path": [],
                "attempt_count": 0,
                "current_url": current_url,
                "session_id": session_id,
                "profile": profile_name,
                "targetId": target_id,
                "tab_id": target_id,
            },
            "",
        )
    try:
        payload = _build_openclaw_action_payload(
            target_id=target_id,
            params=effective_params,
        )
    except ValueError as exc:
        return _normalize_failure(400, {"error": str(exc)}, "")

    if not payload:
        return _normalize_failure(400, {"error": f"unsupported openclaw action: {browser_action}"}, "")
    payload["profile"] = profile_name

    probe_kind = str(payload.get("kind") or "").strip()
    probe_post_action = probe_kind in {"click", "fill", "type", "press", "select", "drag", "hover", "evaluate"}
    before_payload: Optional[Dict[str, Any]] = None
    before_tabs_payload: Optional[Dict[str, Any]] = None
    snapshot_before_ms = 0
    snapshot_before_cache_hit = False
    tabs_before_cache_hit = False
    post_act_probe_ms = 0
    post_act_probe_rounds = 0
    second_probe_ms = 0
    commit_verify_probe_ms = 0
    commit_verify_probe_rounds = 0
    act_ms = 0
    ref_refresh_count = 0
    target_reopen_count = 0
    visibility_recovery_count = 0
    pointer_interceptor_recovery_count = 0
    recovery_attempt_logs: List[Dict[str, Any]] = []
    if probe_post_action:
        before_payload = _cached_snapshot_payload(
            state=state,
            snapshot_id=str((effective_params or {}).get("snapshot_id") or (effective_params or {}).get("snapshotId") or ""),
            target_id=target_id,
        )
        snapshot_before_cache_hit = before_payload is not None
        if before_payload is None:
            try:
                snapshot_before_started = time.perf_counter()
                before_payload = _snapshot_payload_for_target(
                    base_url=base_url,
                    session_id=session_id,
                    state=state,
                    target_id=target_id,
                    timeout=timeout,
                )
                snapshot_before_ms = int((time.perf_counter() - snapshot_before_started) * 1000)
            except Exception:
                before_payload = None
        if probe_kind in {"click", "press"}:
            before_tabs_payload = _cached_tabs_payload(
                state=state,
                target_id=target_id,
                profile=profile_name,
            )
            tabs_before_cache_hit = before_tabs_payload is not None
            if before_tabs_payload is None:
                try:
                    before_tabs_payload = _tabs_payload_for_target(
                        base_url=base_url,
                        target_id=target_id,
                        profile=profile_name,
                        timeout=timeout,
                    )
                    _remember_tabs_payload(
                        state=state,
                        target_id=target_id,
                        profile=profile_name,
                        payload=before_tabs_payload,
                    )
                except Exception:
                    before_tabs_payload = None

    act_started = time.perf_counter()
    status_code, data, text = _request(
        "POST",
        base_url=base_url,
        path="/act",
        timeout=timeout,
        payload=payload,
    )
    act_ms = int((time.perf_counter() - act_started) * 1000)
    if status_code >= 400 and _target_missing(status_code, data, text):
        _clear_session_target(session_id)
        before_payload = None
        snapshot_before_cache_hit = False
        tabs_before_cache_hit = False
        before_tabs_payload = None
        target_reopen_count += 1
        state = _ensure_target(
            base_url=base_url,
            session_id=session_id,
            requested_url=fallback_url,
            timeout=timeout,
        )
        target_id = str(state.get("target_id") or "").strip()
        retry_payload = dict(payload)
        retry_payload["targetId"] = target_id
        act_started = time.perf_counter()
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/act",
            timeout=timeout,
            payload=retry_payload,
        )
        act_ms += int((time.perf_counter() - act_started) * 1000)
    if status_code >= 400:
        status, failure_payload, failure_message = _normalize_failure(status_code, data, text)
        ref_used = str(payload.get("ref") or "").strip()
        if not ref_used:
            fields = payload.get("fields")
            if isinstance(fields, list) and fields and isinstance(fields[0], dict):
                ref_used = str(fields[0].get("ref") or "").strip()
        recovered_by_action_recovery = False
        if ref_used and probe_post_action:
            diagnostics = _collect_act_failure_diagnostics(
                base_url=base_url,
                session_id=session_id,
                state=state,
                target_id=target_id,
                ref_used=ref_used,
                timeout=timeout,
            )
            state_change = failure_payload.get("state_change")
            if not isinstance(state_change, dict):
                state_change = {"effective": False, "backend": "openclaw"}
            state_change["act_failure_diagnostics"] = diagnostics
            if (
                diagnostics.get("ref_present_in_fresh_snapshot")
                and failure_payload.get("reason_code") in {"not_found", "ref_stale", "missing_element_id"}
            ):
                # The ref still resolves in a fresh snapshot, so "not found" is
                # misleading: the element is present but the action could not
                # complete. Surface it as not_actionable so the agent stops
                # treating it as a stale ref and re-snapshotting in a loop.
                state_change["ref_present_but_act_failed"] = True
                failure_payload["reason_code"] = "not_actionable"
            failure_payload["state_change"] = state_change
            attempt_logs = list(failure_payload.get("attempt_logs") or [])
            attempt_logs.append(
                {
                    "reason_code": failure_payload.get("reason_code"),
                    "error": failure_message,
                    "act_failure_diagnostics": diagnostics,
                }
            )
            failure_payload["attempt_logs"] = attempt_logs
            failure_payload["attempt_count"] = len(attempt_logs)
            if (
                probe_kind == "click"
                and diagnostics.get("ref_present_in_fresh_snapshot")
                and not diagnostics.get("target_changed")
            ):
                reveal_log: Optional[Dict[str, Any]] = None
                pointer_interceptor = None
                state_change = failure_payload.get("state_change")
                if isinstance(state_change, dict) and isinstance(state_change.get("pointer_interceptor"), dict):
                    pointer_interceptor = state_change.get("pointer_interceptor")
                if pointer_interceptor is None:
                    pointer_interceptor = extract_pointer_interceptor(failure_message)
                if pointer_interceptor:
                    pointer_log = _attempt_pointer_interceptor_recovery(
                        base_url=base_url,
                        target_id=target_id,
                        profile_name=profile_name,
                        ref_used=ref_used,
                        timeout=timeout,
                        pointer_interceptor=pointer_interceptor,
                    )
                    recovery_attempt_logs.append(pointer_log)
                    pointer_result = pointer_log.get("result") if isinstance(pointer_log.get("result"), dict) else {}
                    if bool(pointer_log.get("success")) and bool(pointer_result.get("target_clickable_after")):
                        retry_payload = dict(payload)
                        retry_payload["timeoutMs"] = _retry_timeout_ms_for_revealed_ref(retry_payload)
                        act_started = time.perf_counter()
                        retry_status_code, retry_data, retry_text = _request(
                            "POST",
                            base_url=base_url,
                            path="/act",
                            timeout=timeout,
                            payload=retry_payload,
                        )
                        retry_ms = int((time.perf_counter() - act_started) * 1000)
                        act_ms += retry_ms
                        retry_log = {
                            "kind": "pointer_interceptor_overlay_retry",
                            "ref": ref_used,
                            "success": retry_status_code < 400,
                            "duration_ms": retry_ms,
                        }
                        if retry_status_code >= 400:
                            retry_message = str((retry_data or {}).get("error") or retry_text or "")
                            retry_log["reason_code"] = _reason_code_from_error(retry_message, retry_status_code)
                            retry_log["error"] = retry_message
                        else:
                            retry_log["reason_code"] = "ok"
                        recovery_attempt_logs.append(retry_log)
                        if retry_status_code < 400:
                            status_code, data, text = retry_status_code, retry_data, retry_text
                            pointer_interceptor_recovery_count += 1
                            recovered_by_action_recovery = True
                if not recovered_by_action_recovery:
                    reveal_log = _attempt_ref_visibility_reveal(
                        base_url=base_url,
                        target_id=target_id,
                        profile_name=profile_name,
                        ref_used=ref_used,
                        timeout=timeout,
                    )
                    recovery_attempt_logs.append(reveal_log)
                if (not recovered_by_action_recovery) and reveal_log and bool(reveal_log.get("success")):
                    retry_payload = dict(payload)
                    retry_payload["timeoutMs"] = _retry_timeout_ms_for_revealed_ref(retry_payload)
                    act_started = time.perf_counter()
                    retry_status_code, retry_data, retry_text = _request(
                        "POST",
                        base_url=base_url,
                        path="/act",
                        timeout=timeout,
                        payload=retry_payload,
                    )
                    retry_ms = int((time.perf_counter() - act_started) * 1000)
                    act_ms += retry_ms
                    retry_log = {
                        "kind": "ref_visibility_reveal_retry",
                        "ref": ref_used,
                        "success": retry_status_code < 400,
                        "duration_ms": retry_ms,
                    }
                    if retry_status_code >= 400:
                        retry_message = str((retry_data or {}).get("error") or retry_text or "")
                        retry_log["reason_code"] = _reason_code_from_error(retry_message, retry_status_code)
                        retry_log["error"] = retry_message
                    else:
                        retry_log["reason_code"] = "ok"
                    recovery_attempt_logs.append(retry_log)
                    if retry_status_code < 400:
                        status_code, data, text = retry_status_code, retry_data, retry_text
                        visibility_recovery_count += 1
                        recovered_by_action_recovery = True
                    else:
                        final_status, final_payload, final_message = _normalize_failure(
                            retry_status_code,
                            retry_data,
                            retry_text,
                        )
                        final_attempt_logs = list(failure_payload.get("attempt_logs") or [])
                        final_attempt_logs.extend(recovery_attempt_logs)
                        final_attempt_logs.extend(list(final_payload.get("attempt_logs") or []))
                        final_state_change = final_payload.get("state_change")
                        if not isinstance(final_state_change, dict):
                            final_state_change = {"effective": False, "backend": "openclaw"}
                        final_state_change["act_failure_diagnostics"] = diagnostics
                        final_state_change["ref_present_but_act_failed"] = True
                        final_state_change["action_recovery"] = {
                            "kind": "actionability_recovery",
                            "recovered": False,
                            "attempts": recovery_attempt_logs,
                        }
                        final_payload["reason_code"] = "not_actionable"
                        final_payload["state_change"] = final_state_change
                        final_payload["attempt_logs"] = final_attempt_logs
                        final_payload["attempt_count"] = len(final_attempt_logs)
                        return final_status, final_payload, final_message
        if not recovered_by_action_recovery:
            if recovery_attempt_logs:
                state_change = failure_payload.get("state_change")
                if not isinstance(state_change, dict):
                    state_change = {"effective": False, "backend": "openclaw"}
                state_change["action_recovery"] = {
                    "kind": "actionability_recovery",
                    "recovered": False,
                    "attempts": recovery_attempt_logs,
                }
                failure_payload["state_change"] = state_change
                attempt_logs = list(failure_payload.get("attempt_logs") or [])
                attempt_logs.extend(recovery_attempt_logs)
                failure_payload["attempt_logs"] = attempt_logs
                failure_payload["attempt_count"] = len(attempt_logs)
            return status, failure_payload, failure_message

    state_change: Dict[str, Any] = {
        "backend": "openclaw",
        "backend_postact_probe": False,
        "backend_progress": False,
        "backend_effective_only": True,
        "effective": True,
    }
    eval_result = data.get("result") if isinstance(data.get("result"), dict) else {}
    if probe_kind == "evaluate":
        if eval_result:
            state_change["evaluate_result"] = eval_result
        before_pos = eval_result.get("before")
        after_pos = eval_result.get("after")
        try:
            scroll_position_changed = abs(float(after_pos) - float(before_pos)) >= 1.0
        except Exception:
            scroll_position_changed = False
        if scroll_position_changed:
            state_change["scroll_position_changed"] = True
            state_change["backend_progress"] = True
            state_change["backend_effective_only"] = False
    current_url = str(data.get("url") or state.get("current_url") or "")
    post_action_snapshot: Optional[Dict[str, Any]] = None
    new_page_evidence: Dict[str, Any] = {}
    auto_follow_evidence: Dict[str, Any] = {}
    if probe_post_action:
        settle_ms = 350 if probe_kind in {"click", "press", "select", "drag"} else 180
        if settle_ms > 0:
            time.sleep(float(settle_ms) / 1000.0)
        try:
            probe_started = time.perf_counter()
            after_payload = _snapshot_payload_for_target(
                base_url=base_url,
                session_id=session_id,
                state=state,
                target_id=target_id,
                timeout=timeout,
            )
            post_act_probe_ms = int((time.perf_counter() - probe_started) * 1000)
            post_act_probe_rounds = 1
        except Exception:
            after_payload = None
        after_tabs_payload: Optional[Dict[str, Any]] = None
        if before_tabs_payload:
            try:
                after_tabs_payload = _tabs_payload_for_target(
                    base_url=base_url,
                    target_id=target_id,
                    profile=profile_name,
                    timeout=timeout,
                )
                _remember_tabs_payload(
                    state=state,
                    target_id=target_id,
                    profile=profile_name,
                    payload=after_tabs_payload,
                )
            except Exception:
                after_tabs_payload = None
            new_page_evidence = _derive_new_page_evidence_from_tabs(
                before_tabs_payload=before_tabs_payload,
                after_tabs_payload=after_tabs_payload,
                reference_url=str(
                    (before_payload or {}).get("current_url")
                    or (before_payload or {}).get("url")
                    or state.get("current_url")
                    or ""
                ),
            )
        if before_payload and after_payload:
            post_action_snapshot = after_payload
            state_change = _derive_state_change_from_snapshot_payloads(
                before_payload=before_payload,
                after_payload=after_payload,
                new_page_evidence=new_page_evidence,
            )
            current_url = str(after_payload.get("current_url") or after_payload.get("url") or current_url)
        elif after_payload:
            post_action_snapshot = after_payload
            current_url = str(after_payload.get("current_url") or after_payload.get("url") or current_url)
        if new_page_evidence:
            state_change = _merge_state_change_evidence(
                state_change=state_change,
                evidence=new_page_evidence,
            )
        if (
            probe_kind in {"click", "press", "select", "drag"}
            and not bool(state_change.get("backend_progress"))
        ):
            try:
                time.sleep(0.7)
                second_probe_started = time.perf_counter()
                second_after_payload = _snapshot_payload_for_target(
                    base_url=base_url,
                    session_id=session_id,
                    state=state,
                    target_id=target_id,
                    timeout=timeout,
                )
                second_probe_ms = int((time.perf_counter() - second_probe_started) * 1000)
                post_act_probe_rounds = 2
            except Exception:
                second_after_payload = None
            if second_after_payload:
                post_action_snapshot = second_after_payload
                current_url = str(second_after_payload.get("current_url") or second_after_payload.get("url") or current_url)
                second_after_tabs_payload: Optional[Dict[str, Any]] = None
                if before_tabs_payload:
                    try:
                        second_after_tabs_payload = _tabs_payload_for_target(
                            base_url=base_url,
                            target_id=target_id,
                            profile=profile_name,
                            timeout=timeout,
                        )
                        _remember_tabs_payload(
                            state=state,
                            target_id=target_id,
                            profile=profile_name,
                            payload=second_after_tabs_payload,
                        )
                    except Exception:
                        second_after_tabs_payload = None
                    new_page_evidence = _derive_new_page_evidence_from_tabs(
                        before_tabs_payload=before_tabs_payload,
                        after_tabs_payload=second_after_tabs_payload,
                        reference_url=str(
                            (before_payload or {}).get("current_url")
                            or (before_payload or {}).get("url")
                            or state.get("current_url")
                            or ""
                        ),
                    )
                if before_payload:
                    state_change = _derive_state_change_from_snapshot_payloads(
                        before_payload=before_payload,
                        after_payload=second_after_payload,
                        new_page_evidence=new_page_evidence,
                    )
                elif new_page_evidence:
                    state_change = _merge_state_change_evidence(
                        state_change=state_change,
                        evidence=new_page_evidence,
                    )

        if new_page_evidence:
            auto_follow_evidence = build_auto_follow_state_update(new_page_evidence)
            if auto_follow_evidence:
                follow_target_id = str(auto_follow_evidence.get("auto_follow_target_id") or "").strip()
                follow_url = str(auto_follow_evidence.get("auto_follow_url") or "").strip()
                if follow_target_id:
                    state["target_id"] = follow_target_id
                    _clear_tabs_cache(state)
                if follow_url:
                    state["current_url"] = follow_url
                    current_url = follow_url
                state_change = _merge_state_change_evidence(
                    state_change=state_change,
                    evidence=auto_follow_evidence,
                )

    commit_ref_id = str(payload.get("ref") or "").strip()
    if probe_post_action and commit_ref_id and before_payload and post_action_snapshot:
        state_change = _apply_commit_verification_to_state_change(
            state_change=state_change,
            before_payload=before_payload,
            after_payload=post_action_snapshot,
            ref_id=commit_ref_id,
        )
        if bool(state_change.get("commit_verification_failed")):
            deadline = time.perf_counter() + (float(_commit_verify_timeout_ms()) / 1000.0)
            interval_s = float(_commit_verify_interval_ms()) / 1000.0
            while bool(state_change.get("commit_verification_failed")) and time.perf_counter() < deadline:
                remaining_s = max(0.0, deadline - time.perf_counter())
                time.sleep(min(interval_s, remaining_s))
                try:
                    commit_probe_started = time.perf_counter()
                    commit_after_payload = _snapshot_payload_for_target(
                        base_url=base_url,
                        session_id=session_id,
                        state=state,
                        target_id=target_id,
                        timeout=timeout,
                    )
                    commit_verify_probe_ms += int((time.perf_counter() - commit_probe_started) * 1000)
                    commit_verify_probe_rounds += 1
                except Exception:
                    commit_after_payload = None
                if not commit_after_payload:
                    continue
                post_action_snapshot = commit_after_payload
                current_url = str(commit_after_payload.get("current_url") or commit_after_payload.get("url") or current_url)
                state_change = _derive_state_change_from_snapshot_payloads(
                    before_payload=before_payload,
                    after_payload=commit_after_payload,
                    new_page_evidence=new_page_evidence,
                )
                state_change = _apply_commit_verification_to_state_change(
                    state_change=state_change,
                    before_payload=before_payload,
                    after_payload=commit_after_payload,
                    ref_id=commit_ref_id,
                )

    if probe_kind == "evaluate" and eval_result:
        state_change["evaluate_result"] = eval_result
    if recovery_attempt_logs:
        state_change["action_recovery"] = {
            "kind": "actionability_recovery",
            "recovered": bool(visibility_recovery_count or pointer_interceptor_recovery_count),
            "attempts": recovery_attempt_logs,
        }
        if visibility_recovery_count:
            state_change["recovered_after_ref_visibility_reveal"] = True
        if pointer_interceptor_recovery_count:
            state_change["recovered_after_pointer_interceptor_recovery"] = True

    backend_trace = {
        "name": "openclaw",
        "kind": probe_kind,
        "snapshot_before_ms": int(snapshot_before_ms),
        "snapshot_before_cache_hit": bool(snapshot_before_cache_hit),
        "tabs_before_cache_hit": bool(tabs_before_cache_hit),
        "act_ms": int(act_ms),
        "post_act_probe_ms": int(post_act_probe_ms),
        "post_act_probe_rounds": int(post_act_probe_rounds),
        "second_probe_ms": int(second_probe_ms),
        "commit_verify_probe_ms": int(commit_verify_probe_ms),
        "commit_verify_probe_rounds": int(commit_verify_probe_rounds),
        "ref_refresh_count": int(ref_refresh_count),
        "target_reopen_count": int(target_reopen_count),
        "visibility_recovery_count": int(visibility_recovery_count),
        "pointer_interceptor_recovery_count": int(pointer_interceptor_recovery_count),
        "backend_verdict": "progress" if bool(state_change.get("backend_progress")) else "effective_only",
        "reason_code": "ok",
        "total_ms": int(snapshot_before_ms + act_ms + post_act_probe_ms + second_probe_ms),
        "owner": "openclaw_probe" if int(post_act_probe_ms) >= max(1, int(act_ms)) else "openclaw_act",
    }
    if auto_follow_evidence:
        backend_trace["auto_followed_new_page"] = True
        backend_trace["auto_follow_reason"] = str(auto_follow_evidence.get("auto_follow_reason") or "")
        backend_trace["auto_follow_target_id"] = str(auto_follow_evidence.get("auto_follow_target_id") or "")
    state_change["backend_trace"] = backend_trace
    response_target_id = str(state.get("target_id") or data.get("targetId") or target_id)

    return (
        200,
        {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "reason": "ok",
            "changed": bool(state_change.get("backend_progress")),
            "state_change": state_change,
            "backend_trace": backend_trace,
            "post_action_snapshot": post_action_snapshot if isinstance(post_action_snapshot, dict) else {},
            "attempt_logs": recovery_attempt_logs,
            "retry_path": [],
            "attempt_count": len(recovery_attempt_logs),
            "current_url": current_url,
            "profile": profile_name,
            "targetId": response_target_id,
            "tab_id": response_target_id,
            "snapshot_id_used": str((params or {}).get("snapshot_id") or ""),
            "ref_id_used": str((params or {}).get("ref_id") or (params or {}).get("refId") or (params or {}).get("ref") or "").strip(),
        },
        "",
    )


def dispatch_openclaw_close(
    raw_base_url: str | None,
    *,
    session_id: str,
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    base_url = _resolve_base_url(raw_base_url)
    state = _session_state(session_id)
    profile_name = _session_profile(session_id)
    target_id = str(state.get("target_id") or "").strip()
    if not target_id:
        return 200, {"success": True, "ok": True, "reason_code": "ok", "reason": "already_closed"}, ""
    path = f"/tabs/{requests.utils.quote(target_id, safe='')}"
    status_code, data, text = _request(
        "DELETE",
        base_url=base_url,
        path=path,
        timeout=timeout,
        params={"profile": profile_name},
    )
    _clear_session_target(session_id)
    if status_code >= 400:
        return _normalize_failure(status_code, data, text)
    return 200, {"success": True, "ok": True, "reason_code": "ok", "reason": "closed"}, ""
