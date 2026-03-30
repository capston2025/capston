"""Runtime helpers for keeping the local MCP host available."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, Any, Dict, Iterator, Optional, Tuple
from urllib import request as urllib_request
from urllib.parse import urlparse

_SPAWNED_PROCESS: Optional[subprocess.Popen[str]] = None
_SPAWNED_LOG_FILE: Optional[IO[str]] = None
_SPAWNED_PID_FILE: Optional[Path] = None
_SPAWNED_STATE_FILE: Optional[Path] = None
_CLEANUP_REGISTERED = False
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_THIS_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _THIS_DIR.parents[2]


def _runtime_dir() -> Path:
    path = Path.home() / ".gaia" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pid_file_for_port(port: int) -> Path:
    return _runtime_dir() / f"mcp_host.runtime.{int(port)}.pid"


def _runtime_state_path(port: int) -> Path:
    return _runtime_dir() / f"mcp_host.runtime.{int(port)}.json"


def _runtime_lock_path(port: int) -> Path:
    return _runtime_dir() / f"mcp_host.runtime.{int(port)}.lock"


@contextmanager
def _runtime_lock(port: int) -> Iterator[None]:
    lock_path = _runtime_lock_path(port)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_runtime_state(state_file: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_runtime_state_atomic(state_file: Path, state: Dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(state_file.parent),
        prefix=f"{state_file.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        json.dump(state, tmp, ensure_ascii=False, sort_keys=True)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(state_file)


def _safe_unlink_runtime_state(
    state_file: Optional[Path],
    *,
    expected_pid: Optional[int] = None,
) -> None:
    if state_file is None:
        return
    if expected_pid is not None:
        state = _load_runtime_state(state_file) or {}
        try:
            current_pid = int(state.get("pid") or 0)
        except Exception:
            current_pid = 0
        if current_pid and current_pid != int(expected_pid):
            return
    try:
        state_file.unlink(missing_ok=True)
    except Exception:
        pass


def _runtime_state_matches_process(state: Optional[Dict[str, Any]], pid: int) -> bool:
    if not isinstance(state, dict):
        return False
    try:
        state_pid = int(state.get("pid") or 0)
    except Exception:
        return False
    if state_pid != int(pid):
        return False
    if str(state.get("workspace_root") or "") != str(_WORKSPACE_ROOT):
        return False
    return _pid_running(pid)


def _build_runtime_state(
    *,
    pid: int,
    base_url: str,
    port: int,
    boot_id: str = "",
    ppid: int = 0,
) -> Dict[str, Any]:
    return {
        "pid": int(pid),
        "ppid": int(ppid or 0),
        "port": int(port),
        "base_url": str(base_url),
        "workspace_root": str(_WORKSPACE_ROOT),
        "boot_id": str(boot_id or ""),
        "started_at": time.time(),
        "owner_pid": int(os.getpid()),
    }


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


def should_auto_start_mcp_host() -> bool:
    backend = str(os.getenv("GAIA_BROWSER_BACKEND", "") or "").strip().lower()
    if backend in {"gaia", "local", "legacy"}:
        return True
    if backend in {"openclaw", "open-claw", "oc"}:
        return False
    if str(os.getenv("GAIA_OPENCLAW_BASE_URL", "") or "").strip():
        return False
    return False


def _is_tcp_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_mcp_health(raw_base_url: str | None, timeout: float = 0.8) -> Optional[Dict[str, Any]]:
    host, port, base_url = resolve_mcp_target(raw_base_url)
    if not _is_tcp_open(host, port, timeout=min(timeout, 0.35)):
        return None
    try:
        req = urllib_request.Request(
            f"{base_url.rstrip('/')}/health",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            if int(getattr(resp, "status", 0) or 0) != 200:
                return None
            raw = resp.read().decode("utf-8")
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return None
    return payload


def is_mcp_ready(raw_base_url: str | None, timeout: float = 0.8) -> bool:
    return isinstance(_probe_mcp_health(raw_base_url, timeout=timeout), dict)


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
    global _SPAWNED_STATE_FILE
    proc = _SPAWNED_PROCESS
    pid_file = _SPAWNED_PID_FILE
    state_file = _SPAWNED_STATE_FILE
    _SPAWNED_PROCESS = None
    _SPAWNED_PID_FILE = None
    _SPAWNED_STATE_FILE = None
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
        _safe_unlink_runtime_state(state_file, expected_pid=int(getattr(proc, "pid", 0) or 0))
    else:
        _safe_unlink_pid_file(pid_file)
        _safe_unlink_runtime_state(state_file)
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
    global _SPAWNED_STATE_FILE
    global _CLEANUP_REGISTERED

    host, port, base_url = resolve_mcp_target(raw_base_url)
    if host in _LOCAL_HOSTS and not _CLEANUP_REGISTERED:
        _register_cleanup_hooks()
    if is_mcp_ready(base_url):
        return True

    if host not in _LOCAL_HOSTS:
        return False

    pid_file = _pid_file_for_port(port)
    state_file = _runtime_state_path(port)

    with _runtime_lock(port):
        health = _probe_mcp_health(base_url)
        if isinstance(health, dict):
            return True

        runtime_state = _load_runtime_state(state_file)
        runtime_pid = int((runtime_state or {}).get("pid") or 0)
        if runtime_pid and not _pid_running(runtime_pid):
            runtime_state = None
            _safe_unlink_runtime_state(state_file, expected_pid=runtime_pid)
        existing_pid = runtime_pid or _read_existing_pid(pid_file) or 0

        if _is_tcp_open(host, port) and not isinstance(health, dict):
            if runtime_pid and _runtime_state_matches_process(runtime_state, runtime_pid):
                _terminate_pid_tree(runtime_pid)
                _safe_unlink_runtime_state(state_file, expected_pid=runtime_pid)
                _safe_unlink_pid_file(pid_file, expected_pid=runtime_pid)
                runtime_state = None
                existing_pid = 0
            elif _SPAWNED_PROCESS and _SPAWNED_PROCESS.poll() is None:
                _stop_spawned_mcp_host()
            else:
                return False

        if _SPAWNED_PROCESS and _SPAWNED_PROCESS.poll() is None:
            if wait_for_mcp_ready(base_url, timeout_sec=min(max(2.0, startup_timeout), 10.0)):
                health = _probe_mcp_health(base_url)
                if isinstance(health, dict):
                    _write_runtime_state_atomic(
                        state_file,
                        _build_runtime_state(
                            pid=int(health.get("pid") or _SPAWNED_PROCESS.pid),
                            ppid=int(health.get("ppid") or 0),
                            boot_id=str(health.get("boot_id") or ""),
                            base_url=base_url,
                            port=port,
                        ),
                    )
                return True
            _stop_spawned_mcp_host()

        if existing_pid:
            if wait_for_mcp_ready(base_url, timeout_sec=min(max(2.0, startup_timeout), 10.0)):
                health = _probe_mcp_health(base_url)
                if isinstance(health, dict) and _runtime_state_matches_process(runtime_state, existing_pid):
                    _write_runtime_state_atomic(
                        state_file,
                        _build_runtime_state(
                            pid=int(health.get("pid") or existing_pid),
                            ppid=int(health.get("ppid") or 0),
                            boot_id=str(health.get("boot_id") or ""),
                            base_url=base_url,
                            port=port,
                        ),
                    )
                return True
            if runtime_pid and _runtime_state_matches_process(runtime_state, existing_pid):
                _terminate_pid_tree(existing_pid)
                _safe_unlink_runtime_state(state_file, expected_pid=existing_pid)
                _safe_unlink_pid_file(pid_file, expected_pid=existing_pid)

        log_path = _runtime_dir() / f"mcp_host.runtime.{port}.log"
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
        _SPAWNED_STATE_FILE = state_file
        try:
            pid_file.write_text(str(int(_SPAWNED_PROCESS.pid)), encoding="utf-8")
        except Exception:
            pass
        _write_runtime_state_atomic(
            state_file,
            _build_runtime_state(
                pid=int(_SPAWNED_PROCESS.pid),
                base_url=base_url,
                port=port,
            ),
        )
        deadline = time.time() + max(3.0, float(startup_timeout))
        while time.time() < deadline:
            health = _probe_mcp_health(base_url)
            if isinstance(health, dict):
                _write_runtime_state_atomic(
                    state_file,
                    _build_runtime_state(
                        pid=int(health.get("pid") or _SPAWNED_PROCESS.pid),
                        ppid=int(health.get("ppid") or 0),
                        boot_id=str(health.get("boot_id") or ""),
                        base_url=base_url,
                        port=port,
                    ),
                )
                return True
            if _SPAWNED_PROCESS.poll() is not None:
                break
            time.sleep(0.2)

        _stop_spawned_mcp_host()
        return False
