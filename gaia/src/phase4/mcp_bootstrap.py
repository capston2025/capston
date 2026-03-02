from __future__ import annotations

import os
from typing import Mapping, Tuple
from urllib.parse import urlparse


def resolve_bind_host_port(env: Mapping[str, str] | None = None) -> Tuple[str, int]:
    env_map = env or os.environ
    bind_host = str(env_map.get("MCP_HOST_BIND_HOST", "0.0.0.0") or "0.0.0.0")
    bind_port_raw = env_map.get("MCP_HOST_BIND_PORT")
    if bind_port_raw:
        try:
            return bind_host, int(bind_port_raw)
        except ValueError:
            return bind_host, 8001

    raw_url = str(env_map.get("MCP_HOST_URL", "http://127.0.0.1:8001") or "").strip()
    if "://" not in raw_url:
        raw_url = f"http://{raw_url}"
    parsed = urlparse(raw_url)
    return bind_host, int(parsed.port or 8001)
