from __future__ import annotations

import getpass
import os
import re
import socket
from typing import Mapping


_RUNNER_ID_MAX_LEN = 96


def sanitize_runner_id(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^0-9A-Za-z가-힣_.@:-]+", "-", text).strip("-")
    if len(text) > _RUNNER_ID_MAX_LEN:
        text = text[:_RUNNER_ID_MAX_LEN].rstrip("-")
    return text or "unknown"


def resolve_runner_id(explicit: object = None, env: Mapping[str, str] | None = None) -> str:
    if str(explicit or "").strip():
        return sanitize_runner_id(explicit)

    source = env if env is not None else os.environ
    for key in ("GAIA_RUNNER_ID", "CODEX_RUNNER_ID"):
        raw = str(source.get(key) or "").strip()
        if raw:
            return sanitize_runner_id(raw)

    try:
        user = getpass.getuser()
    except Exception:
        user = str(source.get("USER") or source.get("USERNAME") or "").strip()

    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = ""

    if user and host:
        return sanitize_runner_id(f"{user}@{host}")
    return sanitize_runner_id(user or host or "unknown")
