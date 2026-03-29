from __future__ import annotations

import os
from typing import Any, Optional


def thin_wrapper_mode_enabled(agent: Any, *, browser_backend_name: Optional[str] = None) -> bool:
    backend_name = str(
        browser_backend_name
        or getattr(agent, "_browser_backend_name", "")
        or os.getenv("GAIA_BROWSER_BACKEND", "")
        or ""
    ).strip().lower()
    override = str(
        getattr(agent, "_goal_wrapper_mode", "")
        or os.getenv("GAIA_GOAL_WRAPPER_MODE", "")
        or ""
    ).strip().lower()
    if override in {"thin", "openclaw", "openclaw-thin", "openclaw_thin"}:
        return True
    if override in {"classic", "legacy", "full", "off", "false", "0"}:
        return False
    return backend_name == "openclaw"


def wrapper_mode_label(agent: Any, *, browser_backend_name: Optional[str] = None) -> str:
    return "thin" if thin_wrapper_mode_enabled(agent, browser_backend_name=browser_backend_name) else "classic"
