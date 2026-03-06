"""Runtime helpers for keeping the local MCP host available."""
from __future__ import annotations

import atexit
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Optional, Tuple
from urllib import request as urllib_request
from urllib.parse import urlparse

_SPAWNED_PROCESS: Optional[subprocess.Popen[str]] = None
_SPAWNED_LOG_FILE: Optional[IO[str]] = None
_CLEANUP_REGISTERED = False
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def resolve_mcp_target(raw_base_url: str | None) -> Tuple[str, int, str]:
    raw = (raw_base_url or "http://127.0.0.1:8001").strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "http").strip().lower()
    host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    default_port = 443 if scheme == "https" else 8001
    try:
        port = int(parsed.port or default_port)
    except Exception:
        port = default_port
    base_url = f"{scheme}://{host}:{port}"
    return host, port, base_url


def _is_tcp_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def is_mcp_ready(raw_base_url: str | None, timeout: float = 0.8) -> bool:
    host, port, base_url = resolve_mcp_target(raw_base_url)
    if not _is_tcp_open(host, port, timeout=min(timeout, 0.35)):
        return False
    try:
        req = urllib_request.Request(
            f"{base_url.rstrip('/')}/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            if int(getattr(resp, "status", 0) or 0) != 200:
                return False
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
        return isinstance(payload, dict) and payload.get("status") == "ok"
    except Exception:
        return False


def wait_for_mcp_ready(
    raw_base_url: str | None,
    *,
    timeout_sec: float = 3.0,
    poll_interval: float = 0.2,
) -> bool:
    deadline = time.time() + max(0.2, float(timeout_sec))
    while time.time() < deadline:
        if is_mcp_ready(raw_base_url):
            return True
        time.sleep(max(0.05, float(poll_interval)))
    return is_mcp_ready(raw_base_url)


def _stop_spawned_mcp_host() -> None:
    global _SPAWNED_PROCESS
    global _SPAWNED_LOG_FILE
    if _SPAWNED_PROCESS and _SPAWNED_PROCESS.poll() is None:
        _SPAWNED_PROCESS.terminate()
        try:
            _SPAWNED_PROCESS.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _SPAWNED_PROCESS.kill()
    _SPAWNED_PROCESS = None
    if _SPAWNED_LOG_FILE is not None:
        try:
            _SPAWNED_LOG_FILE.close()
        except Exception:
            pass
        _SPAWNED_LOG_FILE = None


def ensure_mcp_host_running(
    raw_base_url: str | None,
    *,
    startup_timeout: float = 10.0,
) -> bool:
    global _SPAWNED_PROCESS
    global _SPAWNED_LOG_FILE
    global _CLEANUP_REGISTERED

    host, port, base_url = resolve_mcp_target(raw_base_url)
    if is_mcp_ready(base_url):
        return True

    if host not in _LOCAL_HOSTS:
        return False

    if _is_tcp_open(host, port) and not is_mcp_ready(base_url):
        return False

    if _SPAWNED_PROCESS and _SPAWNED_PROCESS.poll() is None:
        return wait_for_mcp_ready(base_url, timeout_sec=min(max(2.0, startup_timeout), 10.0))

    if not _CLEANUP_REGISTERED:
        atexit.register(_stop_spawned_mcp_host)
        _CLEANUP_REGISTERED = True

    log_dir = Path.home() / ".gaia" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"mcp_host.runtime.{port}.log"
    _SPAWNED_LOG_FILE = log_path.open("a", encoding="utf-8")
    _SPAWNED_PROCESS = subprocess.Popen(
        [sys.executable, "-m", "gaia.src.phase4.mcp_host"],
        stdout=_SPAWNED_LOG_FILE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + max(3.0, float(startup_timeout))
    while time.time() < deadline:
        if is_mcp_ready(base_url):
            return True
        if _SPAWNED_PROCESS.poll() is not None:
            break
        time.sleep(0.2)

    _stop_spawned_mcp_host()
    return False
