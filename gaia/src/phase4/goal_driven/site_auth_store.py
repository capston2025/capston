from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse


def _normalize_domain(url_or_domain: str | None) -> str:
    raw = str(url_or_domain or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        try:
            parsed = urlparse(raw)
            return (parsed.netloc or "").strip().lower()
        except Exception:
            return ""
    return raw


def _store_path() -> Path:
    return Path.home() / ".gaia" / "auth" / "site_credentials.json"


def _read_store() -> Dict[str, Any]:
    path = _store_path()
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _write_store(payload: Dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp_path.write_text(data, encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except Exception:
        pass
    tmp_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def load_site_credentials(url_or_domain: str | None) -> Dict[str, str]:
    domain = _normalize_domain(url_or_domain)
    if not domain:
        return {}
    payload = _read_store()
    item = payload.get(domain)
    if not isinstance(item, dict):
        return {}
    username = str(item.get("username") or "").strip()
    password = str(item.get("password") or "").strip()
    email = str(item.get("email") or "").strip()
    if not username or not password:
        return {}
    result: Dict[str, str] = {
        "username": username,
        "password": password,
        "auth_mode": "provided_credentials",
        "return_credentials": "true",
    }
    if email:
        result["email"] = email
    return result


def save_site_credentials(
    url_or_domain: str | None,
    *,
    username: str,
    password: str,
    email: str = "",
) -> None:
    domain = _normalize_domain(url_or_domain)
    login_id = str(username or "").strip()
    secret = str(password or "").strip()
    contact = str(email or "").strip()
    if not domain or not login_id or not secret:
        return
    payload = _read_store()
    payload[domain] = {
        "username": login_id,
        "password": secret,
        "email": contact,
    }
    _write_store(payload)
