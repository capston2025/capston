from __future__ import annotations

import atexit
import json
import os
import signal
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STATE: Dict[str, Any] = {
    "process": None,
    "base_url": "",
    "gateway_port": 0,
    "control_port": 0,
    "cdp_port": 0,
}

_SERVER_READY_TIMEOUT_S = 45.0
_INSTALL_TIMEOUT_S = 900.0
_DEFAULT_BROWSER_COLOR = "#FF4500"

_CHROME_EXECUTABLE_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)

_WINDOWS_BROWSER_RELATIVE_CANDIDATES = (
    "Google/Chrome/Application/chrome.exe",
    "Chromium/Application/chrome.exe",
    "BraveSoftware/Brave-Browser/Application/brave.exe",
    "Microsoft/Edge/Application/msedge.exe",
)

_PLAYWRIGHT_CACHE_DIR_CANDIDATES = (
    Path.home() / "Library" / "Caches" / "ms-playwright",
    Path.home() / ".cache" / "ms-playwright",
    Path.home() / "AppData" / "Local" / "ms-playwright",
)

_PORT_CANDIDATES = (
    (18789, 18791, 18800),
    (19001, 19003, 19012),
    (19101, 19103, 19112),
    (19201, 19203, 19212),
    (19301, 19303, 19312),
)


def _is_windows() -> bool:
    return os.name == "nt"


def browser_headless_enabled() -> bool:
    raw = str(os.getenv("GAIA_OPENCLAW_HEADLESS", "") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    visible = str(os.getenv("GAIA_OPENCLAW_VISIBLE", "") or "").strip().lower()
    if visible in {"1", "true", "yes", "on"}:
        return False
    return False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def vendor_root() -> Path:
    return _repo_root() / "vendor" / "openclaw"


def runtime_vendor_root() -> Path:
    return _repo_root() / "vendor" / "openclaw-runtime"


def runtime_root() -> Path:
    return _repo_root() / "artifacts" / "embedded_openclaw"


def _logs_dir() -> Path:
    return runtime_root() / "logs"


def _state_dir() -> Path:
    return runtime_root() / "state"


def _config_path() -> Path:
    return runtime_root() / "openclaw.json"


def _browser_user_data_dir() -> Path:
    return _state_dir() / "browser" / "openclaw" / "user-data"


def _bundle_path() -> Path:
    return runtime_vendor_root() / "gaia-embedded-browser-server.bundle.mjs"


def _node_modules_present(root: Path) -> bool:
    required = (
        root / "node_modules" / "playwright-core",
        root / "node_modules" / "sharp",
        root / "node_modules" / "ajv",
        root / "node_modules" / "ajv-formats",
    )
    return all(path.exists() for path in required)


def _npm_command() -> list[str]:
    if shutil.which("npm"):
        return ["npm"]
    raise RuntimeError("npm is required to bootstrap embedded OpenClaw runtime")


def _node_command() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required to run vendored OpenClaw")
    return node


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex(("127.0.0.1", int(port))) != 0


def _select_ports() -> tuple[int, int, int]:
    for gateway_port, control_port, cdp_port in _PORT_CANDIDATES:
        if _is_port_free(gateway_port) and _is_port_free(control_port) and _is_port_free(cdp_port):
            return gateway_port, control_port, cdp_port
    raise RuntimeError("no free port set available for embedded OpenClaw runtime")


def detect_browser_executable() -> str | None:
    override = str(os.getenv("GAIA_OPENCLAW_BROWSER_EXECUTABLE", "") or "").strip()
    if override:
        return override if Path(override).exists() else None
    playwright_browser = _detect_playwright_chromium_executable()
    if playwright_browser:
        return playwright_browser
    for candidate in _browser_executable_candidates():
        if Path(candidate).exists():
            return candidate
    return None


def _browser_executable_candidates() -> tuple[str, ...]:
    if not _is_windows():
        return tuple(_CHROME_EXECUTABLE_CANDIDATES)
    candidates: list[str] = []
    seen: set[str] = set()
    for root in _windows_browser_root_candidates():
        for relative in _WINDOWS_BROWSER_RELATIVE_CANDIDATES:
            candidate = root / relative
            candidate_str = str(candidate)
            if candidate_str in seen:
                continue
            seen.add(candidate_str)
            candidates.append(candidate_str)
    return tuple(candidates)


def _windows_browser_root_candidates() -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[str] = set()
    for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "ProgramW6432"):
        raw = str(os.getenv(env_name, "") or "").strip()
        if not raw:
            continue
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(Path(raw))
    return tuple(roots)


def _detect_playwright_chromium_executable() -> str | None:
    discovered: list[tuple[int, str]] = []
    for cache_dir in _PLAYWRIGHT_CACHE_DIR_CANDIDATES:
        if not cache_dir.exists():
            continue
        for entry in cache_dir.glob("chromium-*"):
            try:
                version = int(str(entry.name).split("chromium-", 1)[1] or "0")
            except Exception:
                version = 0
            candidates = (
                entry / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
                entry / "chrome-linux" / "chrome",
                entry / "chrome-win" / "chrome.exe",
            )
            for candidate in candidates:
                if candidate.exists():
                    discovered.append((version, str(candidate)))
                    break
    if not discovered:
        return None
    discovered.sort(key=lambda item: (int(item[0]), str(item[1])))
    return str(discovered[-1][1])


def build_embedded_openclaw_config(
    *,
    gateway_port: int,
    cdp_port: int,
    browser_executable: str | None,
) -> Dict[str, Any]:
    browser: Dict[str, Any] = {
        "enabled": True,
        "headless": browser_headless_enabled(),
        "defaultProfile": "openclaw",
        "cdpPortRangeStart": int(cdp_port),
        "profiles": {
            "openclaw": {
                "cdpPort": int(cdp_port),
                "color": _DEFAULT_BROWSER_COLOR,
            }
        },
    }
    if browser_executable:
        browser["executablePath"] = browser_executable
    return {
        "gateway": {
            "mode": "local",
            "port": int(gateway_port),
            "auth": {
                "mode": "none",
            },
        },
        "plugins": {
            "entries": {
                "browser": {
                    "enabled": True,
                }
            }
        },
        "browser": browser,
    }


def _write_config(*, gateway_port: int, cdp_port: int) -> Path:
    runtime_root().mkdir(parents=True, exist_ok=True)
    _logs_dir().mkdir(parents=True, exist_ok=True)
    _state_dir().mkdir(parents=True, exist_ok=True)
    config = build_embedded_openclaw_config(
        gateway_port=gateway_port,
        cdp_port=cdp_port,
        browser_executable=detect_browser_executable(),
    )
    config_path = _config_path()
    config_path.write_text(json.dumps(config, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return config_path


def _bootstrap_env(*, gateway_port: int, config_path: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env["OPENCLAW_CONFIG_DIR"] = str(_state_dir())
    env["OPENCLAW_STATE_DIR"] = str(_state_dir())
    env["OPENCLAW_CONFIG_PATH"] = str(config_path)
    env["OPENCLAW_BUNDLED_PLUGINS_DIR"] = str(vendor_root() / "extensions")
    env["OPENCLAW_GATEWAY_PORT"] = str(int(gateway_port))
    env.setdefault("OPENCLAW_TEST_FAST", "1")
    env.setdefault("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    env.setdefault("PUPPETEER_SKIP_DOWNLOAD", "1")
    if browser_headless_enabled():
        env.setdefault("CI", "1")
    else:
        env.pop("CI", None)
    return env


def _ensure_browser_profile_started(base_url: str) -> None:
    response = requests.post(
        f"{base_url.rstrip('/')}/start",
        params={"profile": "openclaw"},
        timeout=(2.0, 12.0),
    )
    try:
        data = response.json()
    except Exception:
        data = {"error": response.text or "invalid_json_response"}
    if response.status_code >= 400:
        raise RuntimeError(str(data.get("error") or response.text or "openclaw profile start failed"))
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError(str(data.get("error") or "openclaw profile start failed"))


def _browser_server_ready(base_url: str) -> bool:
    try:
        response = requests.get(base_url, timeout=1.5)
    except Exception:
        return False
    if response.status_code >= 400:
        return False
    try:
        data = response.json()
    except Exception:
        return False
    return isinstance(data, dict) and "enabled" in data and "profile" in data


def _probe_existing_browser_server() -> tuple[str, int, int, int] | None:
    for gateway_port, control_port, cdp_port in _PORT_CANDIDATES:
        base_url = f"http://127.0.0.1:{int(control_port)}"
        if _browser_server_ready(base_url):
            return base_url, gateway_port, control_port, cdp_port
    return None


def _powershell_command() -> str | None:
    for name in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def _stale_profile_process_ids(user_data_dir: Path) -> list[int]:
    pattern = str(user_data_dir)
    if _is_windows():
        powershell = _powershell_command()
        if not powershell:
            return []
        escaped = pattern.replace("'", "''")
        script = (
            f"$pattern = '{escaped}'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -and $_.CommandLine -like ('*' + $pattern + '*') } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        try:
            output = subprocess.check_output(
                [powershell, "-NoProfile", "-Command", script],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            output = ""
    else:
        try:
            output = subprocess.check_output(["pgrep", "-f", pattern], text=True)
        except Exception:
            output = ""

    pids: list[int] = []
    for line in output.splitlines():
        try:
            pid = int(line.strip())
        except Exception:
            continue
        if pid > 0 and pid != os.getpid():
            pids.append(pid)
    return pids


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _force_kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _cleanup_stale_browser_profile() -> None:
    user_data_dir = _browser_user_data_dir()
    pids = _stale_profile_process_ids(user_data_dir)
    for pid in pids:
        _terminate_pid(pid)
    if pids:
        time.sleep(1.0)
    for pid in pids:
        if _pid_is_alive(pid):
            _force_kill_pid(pid)
    for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock_path = user_data_dir / lock_name
        try:
            if lock_path.exists() or lock_path.is_symlink():
                lock_path.unlink()
        except Exception:
            pass


def _install_runtime_dependencies(root: Path, env: Dict[str, str]) -> None:
    if _node_modules_present(root):
        return
    log_path = _logs_dir() / "openclaw-runtime-install.log"
    with log_path.open("ab") as handle:
        package_lock = root / "package-lock.json"
        command = (
            [*_npm_command(), "ci", "--omit=dev", "--no-audit", "--no-fund"]
            if package_lock.exists()
            else [*_npm_command(), "install", "--omit=dev", "--no-audit", "--no-fund"]
        )
        process = subprocess.run(
            command,
            cwd=root,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=_INSTALL_TIMEOUT_S,
            check=False,
        )
    if process.returncode != 0:
        raise RuntimeError(f"embedded OpenClaw runtime dependency install failed; see {log_path}")


def _embedded_server_popen_kwargs() -> Dict[str, Any]:
    if not _is_windows():
        return {"start_new_session": True}
    creationflags = 0
    creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
    creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    payload: Dict[str, Any] = {}
    if creationflags:
        payload["creationflags"] = creationflags
    return payload


def _terminate_process(proc: Optional[subprocess.Popen[bytes]]) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def stop_embedded_openclaw_server() -> None:
    with _RUNTIME_LOCK:
        proc = _RUNTIME_STATE.get("process")
        _terminate_process(proc)
        _RUNTIME_STATE.update(
            {
                "process": None,
                "base_url": "",
                "gateway_port": 0,
                "control_port": 0,
                "cdp_port": 0,
            }
        )


atexit.register(stop_embedded_openclaw_server)


def ensure_embedded_openclaw_base_url() -> str:
    with _RUNTIME_LOCK:
        base_url = str(_RUNTIME_STATE.get("base_url") or "").strip()
        if base_url and _browser_server_ready(base_url):
            _ensure_browser_profile_started(base_url)
            return base_url
        existing_server = _probe_existing_browser_server()
        if existing_server is not None:
            reused_base_url, gateway_port, control_port, cdp_port = existing_server
            _ensure_browser_profile_started(reused_base_url)
            _RUNTIME_STATE.update(
                {
                    "process": None,
                    "base_url": reused_base_url,
                    "gateway_port": gateway_port,
                    "control_port": control_port,
                    "cdp_port": cdp_port,
                }
            )
            return reused_base_url
        current_proc = _RUNTIME_STATE.get("process")
        if current_proc is not None:
            _terminate_process(current_proc)
            _RUNTIME_STATE["process"] = None
        _cleanup_stale_browser_profile()

        source_root = vendor_root()
        root = runtime_vendor_root()
        bundle_path = _bundle_path()
        if not root.exists():
            raise RuntimeError(
                f"embedded OpenClaw runtime package is missing: expected {root}."
            )
        if not bundle_path.exists():
            raise RuntimeError(
                f"embedded OpenClaw bundle is missing: expected {bundle_path}. "
                "Rebuild it before running the embedded browser runtime."
            )

        gateway_port, control_port, cdp_port = _select_ports()
        config_path = _write_config(gateway_port=gateway_port, cdp_port=cdp_port)
        env = _bootstrap_env(gateway_port=gateway_port, config_path=config_path)
        if (source_root / "extensions").exists():
            env["OPENCLAW_BUNDLED_PLUGINS_DIR"] = str(source_root / "extensions")
        _install_runtime_dependencies(root, env)

        base_url = f"http://127.0.0.1:{int(control_port)}"
        if _browser_server_ready(base_url):
            _RUNTIME_STATE.update(
                {
                    "process": None,
                    "base_url": base_url,
                    "gateway_port": gateway_port,
                    "control_port": control_port,
                    "cdp_port": cdp_port,
                }
            )
            return base_url

        log_path = _logs_dir() / "browser-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as handle:
            proc = subprocess.Popen(
                [
                    _node_command(),
                    str(bundle_path),
                ],
                cwd=root,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                **_embedded_server_popen_kwargs(),
            )

        deadline = time.time() + _SERVER_READY_TIMEOUT_S
        while time.time() < deadline:
            if _browser_server_ready(base_url):
                _ensure_browser_profile_started(base_url)
                _RUNTIME_STATE.update(
                    {
                        "process": proc,
                        "base_url": base_url,
                        "gateway_port": gateway_port,
                        "control_port": control_port,
                        "cdp_port": cdp_port,
                    }
                )
                return base_url
            if proc.poll() is not None:
                break
            time.sleep(0.5)

        _terminate_process(proc)
        raise RuntimeError(f"embedded OpenClaw browser server failed to start; see {log_path}")
