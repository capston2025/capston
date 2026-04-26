from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from gaia.src.phase4.embedded_openclaw_runtime import ensure_embedded_openclaw_base_url
from gaia.src.phase4.browser_context_manager import build_auto_follow_state_update
from gaia.src.phase4.mcp_ref.snapshot_helpers import (
    _build_context_snapshot_from_elements,
    _build_role_snapshot_from_elements,
    _build_role_tree,
    _role_snapshot_stats,
)

_SESSION_LOCK = threading.Lock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}
_BASE_URL_CACHE: Dict[str, str] = {}
_DEFAULT_OPENCLAW_REQUEST_TIMEOUT_S = 12.0

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


def _profile_name() -> str:
    return str(os.getenv("GAIA_OPENCLAW_PROFILE", "openclaw") or "openclaw").strip() or "openclaw"


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
    query.setdefault("profile", _profile_name())
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


def _session_state(session_id: str) -> Dict[str, Any]:
    with _SESSION_LOCK:
        return _SESSIONS.setdefault(
            session_id,
            {
                "target_id": "",
                "current_url": "",
                "snapshot_counter": 0,
                "last_snapshot_id": "",
            },
        )


def _clear_session_target(session_id: str) -> None:
    with _SESSION_LOCK:
        state = _SESSIONS.setdefault(session_id, {})
        state["target_id"] = ""


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


def _ensure_target(
    *,
    base_url: str,
    session_id: str,
    requested_url: str,
    timeout: Any,
) -> Dict[str, Any]:
    state = _session_state(session_id)
    target_id = str(state.get("target_id") or "").strip()
    current_url = _normalize_url(state.get("current_url"))
    normalized_requested = _normalize_url(requested_url)

    if not target_id:
        open_url = normalized_requested or current_url or "about:blank"
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/tabs/open",
            timeout=timeout,
            payload={"url": open_url},
        )
        if status_code >= 400:
            raise RuntimeError(str(data.get("error") or text or "openclaw tabs/open failed"))
        target_id = str(data.get("targetId") or data.get("target_id") or "").strip()
        if not target_id:
            raise RuntimeError("openclaw tabs/open did not return targetId")
        state["target_id"] = target_id
        state["current_url"] = _normalize_url(data.get("url") or open_url)
        current_url = str(state.get("current_url") or "")

    if normalized_requested and normalized_requested != current_url:
        status_code, data, text = _request(
            "POST",
            base_url=base_url,
            path="/navigate",
            timeout=timeout,
            payload={"targetId": target_id, "url": normalized_requested},
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

    return state


def get_openclaw_session_url(session_id: str) -> str:
    return str(_session_state(str(session_id or "default")).get("current_url") or "")


def dispatch_openclaw_console_logs(
    raw_base_url: str | None,
    *,
    session_id: str,
    level: str = "",
    limit: int = 100,
    timeout: Any = None,
) -> Tuple[int, Dict[str, Any], str]:
    base_url = _resolve_base_url(raw_base_url)
    normalized_session_id = str(session_id or "default")
    state = _ensure_target(
        base_url=base_url,
        session_id=normalized_session_id,
        requested_url="",
        timeout=timeout,
    )
    fallback_url = str(state.get("current_url") or "")
    target_id = str(state.get("target_id") or "").strip()
    query: Dict[str, Any] = {"targetId": target_id}
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


def _pseudo_elements_from_role_snapshot(snapshot: str, refs: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tree, tree_by_ref, text_by_raw_ref, nearby_text_by_raw_ref, pointer_like_refs, ref_line_index = _build_role_ref_context(snapshot, refs)
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
        raw_text_hints = list(text_by_raw_ref.get(raw_ref_id) or [])
        nearby_text_hints = list(nearby_text_by_raw_ref.get(raw_ref_id) or [])
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
        nth = meta.get("nth")
        parent_node = tree_by_ref.get(parent_ref) if parent_ref else None
        ancestor_names = list(node.get("ancestor_names") or [])
        interactive = role in _INTERACTIVE_ROLES or raw_ref_id in pointer_like_refs
        if not _should_surface_role_node(role, name, interactive):
            continue
        surfaced_raw_refs.add(raw_ref_id)
        surfaced_meta_by_raw_ref[raw_ref_id] = {
            "role": role,
            "name": name,
            "interactive": interactive,
            "parent_ref": parent_ref,
            "ancestor_names": ancestor_names,
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
        raw_text_hints = list(text_by_raw_ref.get(raw_ref_id) or [])
        nearby_text_hints = list(nearby_text_by_raw_ref.get(raw_ref_id) or [])
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
        nth = meta.get("nth")
        parent_node = tree_by_ref.get(parent_ref) if parent_ref else None
        ancestor_names = list(node.get("ancestor_names") or [])
        interactive = role in _INTERACTIVE_ROLES or raw_ref_id in pointer_like_refs
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
                "tag": ("button" if role == "generic" and interactive else _role_to_tag(role)),
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
                "bounding_box": None,
                "element_type": _element_type_for_role(role),
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
) -> Dict[str, Any]:
    snapshot = str(raw_snapshot.get("snapshot") or "")
    refs = raw_snapshot.get("refs") if isinstance(raw_snapshot.get("refs"), dict) else {}
    elements, role_snapshot = _pseudo_elements_from_role_snapshot(snapshot, refs)
    evidence = _synthesize_snapshot_evidence(elements)
    scoped_elements, context_snapshot, scope_applied = _apply_scope_to_elements(elements, requested_scope_ref_id)
    effective_role_snapshot = dict(role_snapshot or {})
    if scope_applied:
        scoped_role_snapshot = _build_role_snapshot_from_elements(scoped_elements)
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
    return {
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


def _snapshot_payload_for_target(
    *,
    base_url: str,
    session_id: str,
    state: Dict[str, Any],
    target_id: str,
    timeout: Any,
    requested_scope_ref_id: str = "",
) -> Optional[Dict[str, Any]]:
    status_code, data, text = _request(
        "GET",
        base_url=base_url,
        path="/snapshot",
        timeout=timeout,
        params={
            "targetId": target_id,
            "format": "role",
            "refs": "aria",
        },
    )
    if status_code >= 400:
        return None
    return _build_snapshot_payload(
        session_id=session_id,
        target_id=target_id,
        current_url=str(data.get("url") or state.get("current_url") or ""),
        requested_scope_ref_id=requested_scope_ref_id,
        raw_snapshot=data,
        state=state,
    )


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


def _tabs_payload_for_target(
    *,
    base_url: str,
    target_id: str,
    timeout: Any,
) -> Optional[Dict[str, Any]]:
    params: Dict[str, Any] = {}
    if str(target_id or "").strip():
        params["targetId"] = str(target_id or "").strip()
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
    return (
        200,
        {
            "success": False,
            "effective": False,
            "reason_code": _reason_code_from_error(message, status_code),
            "reason": message,
            "state_change": {"effective": False, "backend": "openclaw"},
            "attempt_logs": [],
            "retry_path": [],
            "attempt_count": 0,
        },
        message,
    )


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
                },
            )
        if status_code >= 400:
            return _normalize_failure(status_code, data, text)
        payload = _build_snapshot_payload(
            session_id=session_id,
            target_id=target_id,
            current_url=str(data.get("url") or state.get("current_url") or requested_url),
            requested_scope_ref_id=str((effective_params or {}).get("scope_container_ref_id") or "").strip(),
            raw_snapshot=data,
            state=state,
        )
        return 200, payload, ""

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
    try:
        payload = _build_openclaw_action_payload(
            target_id=target_id,
            params=effective_params,
        )
    except ValueError as exc:
        return _normalize_failure(400, {"error": str(exc)}, "")

    if not payload:
        return _normalize_failure(400, {"error": f"unsupported openclaw action: {browser_action}"}, "")

    probe_kind = str(payload.get("kind") or "").strip()
    probe_post_action = probe_kind in {"click", "fill", "press", "select", "drag", "hover", "evaluate"}
    before_payload: Optional[Dict[str, Any]] = None
    before_tabs_payload: Optional[Dict[str, Any]] = None
    snapshot_before_ms = 0
    post_act_probe_ms = 0
    post_act_probe_rounds = 0
    second_probe_ms = 0
    act_ms = 0
    ref_refresh_count = 0
    target_reopen_count = 0
    if probe_post_action:
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
            try:
                before_tabs_payload = _tabs_payload_for_target(
                    base_url=base_url,
                    target_id=target_id,
                    timeout=timeout,
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
        return _normalize_failure(status_code, data, text)

    state_change: Dict[str, Any] = {
        "backend": "openclaw",
        "backend_postact_probe": False,
        "backend_progress": False,
        "backend_effective_only": True,
        "effective": True,
    }
    eval_result = data.get("result") if isinstance(data.get("result"), dict) else {}
    if probe_kind == "evaluate":
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
                    timeout=timeout,
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
                            timeout=timeout,
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
                if follow_url:
                    state["current_url"] = follow_url
                    current_url = follow_url
                state_change = _merge_state_change_evidence(
                    state_change=state_change,
                    evidence=auto_follow_evidence,
                )

    backend_trace = {
        "name": "openclaw",
        "kind": probe_kind,
        "snapshot_before_ms": int(snapshot_before_ms),
        "act_ms": int(act_ms),
        "post_act_probe_ms": int(post_act_probe_ms),
        "post_act_probe_rounds": int(post_act_probe_rounds),
        "second_probe_ms": int(second_probe_ms),
        "ref_refresh_count": int(ref_refresh_count),
        "target_reopen_count": int(target_reopen_count),
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
            "attempt_logs": [],
            "retry_path": [],
            "attempt_count": 0,
            "current_url": current_url,
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
    target_id = str(state.get("target_id") or "").strip()
    if not target_id:
        return 200, {"success": True, "ok": True, "reason_code": "ok", "reason": "already_closed"}, ""
    path = f"/tabs/{requests.utils.quote(target_id, safe='')}"
    status_code, data, text = _request(
        "DELETE",
        base_url=base_url,
        path=path,
        timeout=timeout,
    )
    _clear_session_target(session_id)
    if status_code >= 400:
        return _normalize_failure(status_code, data, text)
    return 200, {"success": True, "ok": True, "reason_code": "ok", "reason": "closed"}, ""
