"""Runtime helpers for keeping the local MCP host available."""
from __future__ import annotations

import atexit
import json
import os
import signal
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
_SPAWNED_PID_FILE: Optional[Path] = None
_CLEANUP_REGISTERED = False
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_THIS_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _THIS_DIR.parents[2]


def _pid_file_for_port(port: int) -> Path:
    return Path.home() / ".gaia" / "logs" / f"mcp_host.runtime.{int(port)}.pid"


def _pid_running(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except Exception:
        return False
    return True


def _read_existing_pid(pid_file: Path) -> Optional[int]:
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        pid = int(raw or "0")
    except Exception:
        return None
    if pid > 0 and _pid_running(pid):
        return pid
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass
    return None


def _safe_unlink_pid_file(pid_file: Optional[Path], *, expected_pid: Optional[int] = None) -> None:
    if pid_file is None:
        return
    if expected_pid is not None:
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
            current_pid = int(raw or "0")
        except Exception:
            current_pid = 0
        if current_pid and current_pid != int(expected_pid):
            return
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass


def _terminate_pid_tree(pid: int, *, term_timeout: float = 2.5, kill_timeout: float = 2.0) -> None:
    if int(pid) <= 0:
        return
    term_sent = False
    try:
        os.killpg(int(pid), signal.SIGTERM)
        term_sent = True
    except Exception:
        try:
            os.kill(int(pid), signal.SIGTERM)
            term_sent = True
        except Exception:
            pass
    if not term_sent:
        return
    deadline = time.time() + max(0.2, float(term_timeout))
    while time.time() < deadline:
        if not _pid_running(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(int(pid), signal.SIGKILL)
    except Exception:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except Exception:
            pass
    deadline = time.time() + max(0.2, float(kill_timeout))
    while time.time() < deadline:
        if not _pid_running(pid):
            return
        time.sleep(0.05)


def _register_cleanup_hooks() -> None:
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return

    atexit.register(_stop_spawned_mcp_host)

    def _make_signal_handler(previous_handler: Any):
        def _handler(signum: int, frame: Any) -> None:
            try:
                _stop_spawned_mcp_host()
            finally:
                if callable(previous_handler):
                    previous_handler(signum, frame)
                elif previous_handler == signal.SIG_DFL:
                    raise SystemExit(128 + int(signum))
                elif previous_handler == signal.SIG_IGN:
                    return
        return _handler

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(signum)
            signal.signal(signum, _make_signal_handler(previous))
        except Exception:
            pass
    _CLEANUP_REGISTERED = True


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
    global _SPAWNED_PID_FILE
    proc = _SPAWNED_PROCESS
    pid_file = _SPAWNED_PID_FILE
    _SPAWNED_PROCESS = None
    _SPAWNED_PID_FILE = None
    if proc is not None:
        try:
            if proc.poll() is None:
                _terminate_pid_tree(int(proc.pid))
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    pass
        except Exception:
            pass
        _safe_unlink_pid_file(pid_file, expected_pid=int(getattr(proc, "pid", 0) or 0))
    else:
        _safe_unlink_pid_file(pid_file)
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
    global _SPAWNED_PID_FILE
    global _CLEANUP_REGISTERED

    host, port, base_url = resolve_mcp_target(raw_base_url)
    if host in _LOCAL_HOSTS and not _CLEANUP_REGISTERED:
        _register_cleanup_hooks()
    if is_mcp_ready(base_url):
        return True

    if host not in _LOCAL_HOSTS:
        return False

    if _is_tcp_open(host, port) and not is_mcp_ready(base_url):
        return False

    pid_file = _pid_file_for_port(port)
    existing_pid = _read_existing_pid(pid_file)
    if existing_pid and wait_for_mcp_ready(base_url, timeout_sec=min(max(2.0, startup_timeout), 10.0)):
        return True
    if existing_pid:
        _terminate_pid_tree(existing_pid)
        _safe_unlink_pid_file(pid_file, expected_pid=existing_pid)

    if _SPAWNED_PROCESS and _SPAWNED_PROCESS.poll() is None:
        return wait_for_mcp_ready(base_url, timeout_sec=min(max(2.0, startup_timeout), 10.0))

    log_dir = Path.home() / ".gaia" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"mcp_host.runtime.{port}.log"
    if _SPAWNED_LOG_FILE is not None:
        try:
            _SPAWNED_LOG_FILE.close()
        except Exception:
            pass
        _SPAWNED_LOG_FILE = None
    _SPAWNED_LOG_FILE = log_path.open("a", encoding="utf-8")
    child_env = os.environ.copy()
    workspace_root = str(_WORKSPACE_ROOT)
    existing_pythonpath = str(child_env.get("PYTHONPATH") or "").strip()
    if existing_pythonpath:
        child_env["PYTHONPATH"] = f"{workspace_root}:{existing_pythonpath}"
    else:
        child_env["PYTHONPATH"] = workspace_root
    child_env["MCP_HOST_URL"] = base_url
    _SPAWNED_PROCESS = subprocess.Popen(
        [sys.executable, "-m", "gaia.src.phase4.mcp_host"],
        stdout=_SPAWNED_LOG_FILE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=workspace_root,
        env=child_env,
        start_new_session=True,
        close_fds=True,
    )
    _SPAWNED_PID_FILE = pid_file
    try:
        pid_file.write_text(str(int(_SPAWNED_PROCESS.pid)), encoding="utf-8")
    except Exception:
        pass
    deadline = time.time() + max(3.0, float(startup_timeout))
    while time.time() < deadline:
        if is_mcp_ready(base_url):
            return True
        if _SPAWNED_PROCESS.poll() is not None:
            break
        time.sleep(0.2)

    _stop_spawned_mcp_host()
    return False
