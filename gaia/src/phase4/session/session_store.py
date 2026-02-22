"""Persistent session pointer storage for GAIA."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from .types import SessionState

SESSIONS_ROOT = Path.home() / ".gaia" / "sessions"
WORKSPACE_DEFAULT = "workspace_default"


def _safe_key(session_key: str) -> str:
    key = (session_key or WORKSPACE_DEFAULT).strip()
    if not key:
        key = WORKSPACE_DEFAULT
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", key)


def session_file_path(session_key: str) -> Path:
    return SESSIONS_ROOT / f"{_safe_key(session_key)}.json"


def allocate_session_id(session_key: str) -> str:
    safe = _safe_key(session_key)
    return f"{safe}_{int(time.time())}"


def load_session_state(session_key: str) -> Optional[SessionState]:
    path = session_file_path(session_key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        state = SessionState.from_dict(data)
    except Exception:
        return None
    if not state.session_key:
        state.session_key = _safe_key(session_key)
    if not state.mcp_session_id:
        state.mcp_session_id = state.session_key
    return state


def save_session_state(state: SessionState) -> Path:
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    path = session_file_path(state.session_key)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path

