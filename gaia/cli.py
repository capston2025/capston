"""Console entry point for GAIA."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import select
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Sequence

from gaia import auth as gaia_auth
from gaia.src.phase4.session import (
    WORKSPACE_DEFAULT,
    SessionState,
    allocate_session_id,
    load_session_state,
    save_session_state,
)

if os.name == "nt":
    import msvcrt
else:
    import termios
    import tty


PROFILE_PATH = Path.home() / ".gaia" / "cli_profile.json"
AUTH_CHOICES = ("reuse", "fresh")
RUNTIME_CHOICES = ("gui", "terminal")
MODE_CHOICES = ("chat", "ai", "plan")
OPENAI_AUTH_METHOD_CHOICES = ("oauth", "manual")
CONTROL_CHOICES = ("local", "telegram")
TELEGRAM_MODE_CHOICES = ("polling", "webhook")
TELEGRAM_SETUP_CHOICES = ("reuse", "fresh")
DEFAULT_TELEGRAM_TOKEN_FILE = str(Path.home() / ".gaia" / "telegram_bot_token")

OPENAI_MODEL_CHOICES = (
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-5.1-codex",
    "gpt-5-codex",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1",
    "직접 입력",
)

GEMINI_MODEL_CHOICES = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "직접 입력",
)

OPENAI_MODEL_PRIORITY = (
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4.1",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
    "gpt-5.1-codex",
    "gpt-5-codex",
)

GEMINI_MODEL_PRIORITY = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)

_MCP_HOST_PROCESS: subprocess.Popen[str] | None = None
_MCP_HOST_LOG_FILE = None
_MCP_HOST_CLEANUP_REGISTERED = False


def _load_profile() -> dict[str, str]:
    if not PROFILE_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}


def _resolve_mcp_target() -> tuple[str, int, str]:
    raw_url = (os.getenv("MCP_HOST_URL", "http://127.0.0.1:8001") or "").strip()
    if "://" not in raw_url:
        raw_url = f"http://{raw_url}"
    parsed = urllib.parse.urlparse(raw_url)
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if scheme == "https" else 80
    base_url = f"{scheme}://{host}:{int(port)}"
    return host, int(port), base_url


def _is_tcp_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_mcp_ready(host: str, port: int, base_url: str, timeout: float = 0.8) -> bool:
    if not _is_tcp_open(host, port, timeout=min(timeout, 0.35)):
        return False

    probes = (
        (
            f"{base_url.rstrip('/')}/health",
            lambda payload: isinstance(payload, dict) and payload.get("status") == "ok",
        ),
        (
            f"{base_url.rstrip('/')}/openapi.json",
            lambda payload: isinstance(payload, dict)
            and isinstance(payload.get("info"), dict)
            and payload.get("info", {}).get("title") == "MCP Host",
        ),
    )

    for url, validator in probes:
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if int(getattr(resp, "status", 0) or 0) != 200:
                    continue
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            if validator(payload):
                return True
        except Exception:
            continue
    return False


def _stop_spawned_mcp_host() -> None:
    global _MCP_HOST_PROCESS
    global _MCP_HOST_LOG_FILE
    if _MCP_HOST_PROCESS and _MCP_HOST_PROCESS.poll() is None:
        _MCP_HOST_PROCESS.terminate()
        try:
            _MCP_HOST_PROCESS.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _MCP_HOST_PROCESS.kill()
    _MCP_HOST_PROCESS = None
    if _MCP_HOST_LOG_FILE is not None:
        try:
            _MCP_HOST_LOG_FILE.close()
        except Exception:
            pass
        _MCP_HOST_LOG_FILE = None


def _ensure_mcp_host_running() -> bool:
    global _MCP_HOST_PROCESS
    global _MCP_HOST_LOG_FILE
    global _MCP_HOST_CLEANUP_REGISTERED

    host, port, base_url = _resolve_mcp_target()
    if _is_mcp_ready(host, port, base_url):
        return True

    if _is_tcp_open(host, port) and not _is_mcp_ready(host, port, base_url):
        print(
            "MCP_HOST_URL가 GAIA MCP가 아닌 다른 서비스로 연결되어 있습니다. "
            f"현재: {base_url}. "
            "MCP_HOST_URL을 비우거나(기본값 사용) 다른 포트로 설정하세요.",
            file=sys.stderr,
        )
        return False

    if _MCP_HOST_PROCESS and _MCP_HOST_PROCESS.poll() is None:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if _is_mcp_ready(host, port, base_url):
                return True
            time.sleep(0.15)
        return False

    if not _MCP_HOST_CLEANUP_REGISTERED:
        atexit.register(_stop_spawned_mcp_host)
        _MCP_HOST_CLEANUP_REGISTERED = True

    log_dir = Path.home() / ".gaia" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mcp_host.log"
    _MCP_HOST_LOG_FILE = log_path.open("a", encoding="utf-8")
    _MCP_HOST_PROCESS = subprocess.Popen(
        [sys.executable, "-m", "gaia.src.phase4.mcp_host"],
        stdout=_MCP_HOST_LOG_FILE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _is_mcp_ready(host, port, base_url):
            print(f"MCP host 자동 시작됨: {host}:{port}")
            return True
        if _MCP_HOST_PROCESS.poll() is not None:
            break
        time.sleep(0.2)

    print(
        "MCP host 자동 시작에 실패했습니다. "
        f"로그를 확인하세요: {log_path}",
        file=sys.stderr,
    )
    _stop_spawned_mcp_host()
    return False


def _save_profile(profile: dict[str, str]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_session_binding(
    parsed: argparse.Namespace,
    profile: dict[str, str],
) -> tuple[str, str, bool]:
    workspace = profile.get("last_workspace", WORKSPACE_DEFAULT)
    explicit_session = getattr(parsed, "session", None)
    session_key = explicit_session or profile.get("last_session_key") or workspace
    session_key = (session_key or WORKSPACE_DEFAULT).strip() or WORKSPACE_DEFAULT
    is_new_session = bool(getattr(parsed, "new_session", False))

    if is_new_session:
        mcp_session_id = allocate_session_id(session_key)
        return session_key, mcp_session_id, True

    saved = load_session_state(session_key)
    if saved and saved.mcp_session_id:
        return session_key, saved.mcp_session_id, False

    if explicit_session:
        return session_key, session_key, False

    profile_session_id = (profile.get("last_mcp_session_id") or "").strip()
    if profile_session_id:
        return session_key, profile_session_id, False

    return session_key, session_key, False


def _persist_session_state(
    *,
    session_key: str,
    mcp_session_id: str,
    url: str | None,
    last_snapshot_id: str | None = None,
    pending_user_input: dict[str, str] | None = None,
) -> None:
    state = load_session_state(session_key) or SessionState(
        session_key=session_key,
        mcp_session_id=mcp_session_id,
    )
    state.session_key = session_key
    state.mcp_session_id = mcp_session_id
    if url:
        state.last_url = url
    if last_snapshot_id is not None:
        state.last_snapshot_id = str(last_snapshot_id or "")
    if pending_user_input is not None:
        state.pending_user_input = dict(pending_user_input)
    save_session_state(state)


def _default_model(provider: str) -> str:
    return "gpt-5.2" if provider == "openai" else "gemini-2.5-pro"


def _prompt(prompt: str, default: str | None = None) -> str:
    if not sys.stdin.isatty():
        return default or ""
    text = input(f"{prompt}" + (f" [default: {default}]" if default else "") + ": ").strip()
    return text or (default or "")


def _prompt_non_empty(prompt: str, default: str | None = None) -> str:
    while True:
        value = _prompt(prompt, default=default).strip()
        if value:
            return value
        print("값을 입력해주세요.")


def _read_key() -> str:
    if not sys.stdin.isatty():
        return ""
    if os.name == "nt":
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            nxt = msvcrt.getwch()
            if nxt == "H":
                return "UP"
            if nxt == "P":
                return "DOWN"
            return nxt
        if ch == "\r":
            return "\n"
        return ch

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # Read full escape sequence and support both CSI and SS3 arrows:
            # ESC [ A / ESC [ B / ESC O A / ESC O B.
            seq = ch
            # Wait long enough for slower terminals to deliver full sequence.
            if select.select([sys.stdin], [], [], 0.12)[0]:
                seq += sys.stdin.read(1)
            for _ in range(24):
                if not select.select([sys.stdin], [], [], 0.03)[0]:
                    break
                seq += sys.stdin.read(1)
                if seq in {"\x1b[A", "\x1b[B", "\x1bOA", "\x1bOB", "\x1b[C", "\x1b[D", "\x1bOC", "\x1bOD"}:
                    break
                if len(seq) >= 3 and seq[-1].isalpha():
                    break

            # Also accept modifier forms like ESC[1;5A
            if (seq.startswith("\x1b[") or seq.startswith("\x1bO")) and seq.endswith("A"):
                return "UP"
            if (seq.startswith("\x1b[") or seq.startswith("\x1bO")) and seq.endswith("B"):
                return "DOWN"
            return "ESC"
        # Handle split escape sequence tail that can arrive as separate reads.
        if ch == "[" and select.select([sys.stdin], [], [], 0.12)[0]:
            tail = sys.stdin.read(1)
            if tail == "A":
                return "UP"
            if tail == "B":
                return "DOWN"
            return ch + tail
        if ch == "O" and select.select([sys.stdin], [], [], 0.12)[0]:
            tail = sys.stdin.read(1)
            if tail == "A":
                return "UP"
            if tail == "B":
                return "DOWN"
            return ch + tail
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _print_select_prompt(prompt: str, options: Sequence[str], selected: int) -> None:
    print(prompt)
    for index, option in enumerate(options):
        if index == selected:
            print(f"  ▶ [{index + 1}] {option}")
        else:
            print(f"    [{index + 1}] {option}")


def _prompt_select_curses(prompt: str, options: Sequence[str], default: str | None = None) -> str:
    import curses

    choices = list(options)
    selected = choices.index(default) if default in choices else 0

    def _run(stdscr: "curses._CursesWindow") -> str:
        nonlocal selected
        curses.curs_set(0)
        stdscr.keypad(True)
        while True:
            stdscr.erase()
            stdscr.addstr(0, 0, prompt)
            for index, option in enumerate(choices):
                marker = "▶" if index == selected else " "
                line = f"  {marker} [{index + 1}] {option}"
                stdscr.addstr(index + 1, 0, line)
            stdscr.addstr(len(choices) + 2, 0, "방향키(↑/↓)로 이동, 번호 입력, Enter로 확정")
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k"), ord("K")):
                selected = max(0, selected - 1)
                continue
            if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                selected = min(len(choices) - 1, selected + 1)
                continue
            if key in (10, 13):
                return choices[selected]
            if ord("1") <= key <= ord("9"):
                idx = key - ord("1")
                if 0 <= idx < len(choices):
                    return choices[idx]
            if key in (3, 4):
                raise KeyboardInterrupt

    return curses.wrapper(_run)


def _prompt_select(prompt: str, options: Sequence[str], default: str | None = None) -> str:
    if not options:
        return default or ""
    if not sys.stdin.isatty():
        return default or options[0]

    # Prefer prompt_toolkit when available: it handles ESC vs arrow timing
    # robustly across terminals.
    try:
        from prompt_toolkit.shortcuts import choice as pt_choice

        values = [(opt, opt) for opt in options]
        if default in options:
            default_index = list(options).index(default)
            values = values[default_index:] + values[:default_index]
        selected = pt_choice(
            message=prompt,
            options=values,
        )
        if selected is None:
            raise KeyboardInterrupt
        print(f"선택: {selected}")
        return str(selected)
    except Exception:
        # Fall back to manual parser when prompt_toolkit is unavailable.
        pass

    try:
        selected = _prompt_select_curses(prompt, options, default=default)
        print(f"선택: {selected}")
        return selected
    except Exception:
        pass

    choices = list(options)
    selected = choices.index(default) if default in choices else 0

    def _render() -> None:
        sys.stdout.write("\033[2J\033[H")
        _print_select_prompt(prompt, choices, selected)
        print("방향키(↑/↓)로 이동, 번호 입력, Enter로 확정")
        sys.stdout.flush()

    _render()
    while True:
        key = _read_key().upper()
        if key in {"\x03", "\x04"}:
            raise KeyboardInterrupt
        if key in {"UP", "K"}:
            selected = max(0, selected - 1)
            _render()
            continue
        if key in {"DOWN", "J"}:
            selected = min(len(choices) - 1, selected + 1)
            _render()
            continue
        if key in {"\r", "\n"}:
            print(f"선택: {choices[selected]}")
            return choices[selected]
        if key == "ESC":
            # Ignore bare ESC to prevent accidental cancellation
            # when terminals emit split arrow-key sequences.
            continue
        if len(key) == 1 and key.isdigit():
            idx = ord(key) - ord("1")
            if 0 <= idx < len(choices):
                print(f"선택: {choices[idx]}")
                return choices[idx]
            print(f"1~{len(choices)} 중에서 입력해주세요.")


def _apply_llm_environment(
    provider: str | None,
    model: str | None,
    openai_token: str | None,
    gemini_token: str | None,
) -> None:
    if provider:
        os.environ["GAIA_LLM_PROVIDER"] = provider
        os.environ["VISION_PROVIDER"] = provider
    if model:
        os.environ["GAIA_LLM_MODEL"] = model
        os.environ["VISION_MODEL"] = model
    if openai_token:
        os.environ["OPENAI_API_KEY"] = openai_token
    if gemini_token:
        os.environ["GEMINI_API_KEY"] = gemini_token


def _is_openai_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    if lowered.startswith(("gpt-", "chatgpt-")):
        pass
    elif lowered.startswith(("o1", "o3", "o4")):
        pass
    else:
        return False

    blocked = (
        "audio",
        "realtime",
        "transcribe",
        "tts",
        "image",
        "embedding",
        "moderation",
        "whisper",
        "dall-e",
        "babbage",
        "davinci",
    )
    return not any(keyword in lowered for keyword in blocked)


def _sort_by_priority(models: list[str], priority: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for model in models:
        if model and model not in seen:
            seen.add(model)
            deduped.append(model)
    order = {name: idx for idx, name in enumerate(priority)}
    return sorted(deduped, key=lambda name: (order.get(name, 10_000), name))


def _fetch_openai_models(token: str) -> list[str]:
    request = urllib.request.Request(
        "https://api.openai.com/v1/models",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return []

    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    models: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        if isinstance(model_id, str) and _is_openai_chat_model(model_id):
            models.append(model_id)
    return _sort_by_priority(models, OPENAI_MODEL_PRIORITY)


def _fetch_gemini_models(token: str) -> list[str]:
    query = urllib.parse.urlencode({"key": token})
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return []

    rows = payload.get("models")
    if not isinstance(rows, list):
        return []
    models: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        methods = row.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        name = row.get("name")
        if not isinstance(name, str):
            continue
        model_id = name.split("/", 1)[-1]
        if model_id.startswith("gemini-"):
            models.append(model_id)
    return _sort_by_priority(models, GEMINI_MODEL_PRIORITY)


def _resolve_account_model_choices(provider: str, token: str | None) -> list[str]:
    if not token:
        return []
    if provider == "openai":
        return _fetch_openai_models(token)
    if provider == "gemini":
        return _fetch_gemini_models(token)
    return []


def _resolve_runtime(parsed: argparse.Namespace, profile: dict[str, str], default: str = "gui") -> str:
    runtime = parsed.runtime or profile.get("last_runtime", default)
    if getattr(parsed, "gui", False):
        runtime = "gui"
    if getattr(parsed, "terminal", False):
        runtime = "terminal"
    if runtime not in RUNTIME_CHOICES:
        runtime = default
    if sys.stdin.isatty() and not parsed.runtime and not getattr(parsed, "gui", False) and not getattr(parsed, "terminal", False):
        runtime = _prompt_select(
            "실행 런타임을 선택하세요",
            ("gui", "terminal"),
            default=runtime,
        )
    return runtime


def _resolve_control_channel(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    control = getattr(parsed, "control", None) or profile.get("control_channel", "local")
    if control not in CONTROL_CHOICES:
        control = "local"
    if sys.stdin.isatty() and not getattr(parsed, "control", None):
        selected = _prompt_select(
            "Telegram 원격 제어를 사용하시겠어요?",
            ("telegram", "no"),
            default="telegram" if control == "telegram" else "no",
        )
        control = "telegram" if selected == "telegram" else "local"
    return control


def _resolve_telegram_setup_strategy(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    strategy = getattr(parsed, "tg_setup", None) or profile.get("telegram_setup_strategy", "reuse")
    if strategy not in TELEGRAM_SETUP_CHOICES:
        strategy = "reuse"
    if sys.stdin.isatty() and not getattr(parsed, "tg_setup", None):
        strategy = _prompt_select(
            "Telegram 설정을 선택하세요",
            TELEGRAM_SETUP_CHOICES,
            default=strategy,
        )
    return strategy


def _resolve_telegram_mode(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    mode = getattr(parsed, "tg_mode", None) or profile.get("telegram_mode", "polling")
    if mode not in TELEGRAM_MODE_CHOICES:
        mode = "polling"
    if sys.stdin.isatty() and not getattr(parsed, "tg_mode", None):
        mode = _prompt_select(
            "Telegram 모드를 선택하세요",
            TELEGRAM_MODE_CHOICES,
            default=mode,
        )
    return mode


def _parse_telegram_allowlist(raw: str) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            continue
    return out


def _resolve_telegram_token_file(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    path = getattr(parsed, "tg_token_file", None) or profile.get("telegram_token_file", DEFAULT_TELEGRAM_TOKEN_FILE)
    return path


def _resolve_telegram_allowlist(parsed: argparse.Namespace, profile: dict[str, str]) -> tuple[list[int], str]:
    raw = getattr(parsed, "tg_allowlist", None) or profile.get("telegram_allowlist", "")
    if sys.stdin.isatty() and not getattr(parsed, "tg_allowlist", None):
        raw = _prompt(
            "Telegram 관리자 chat_id 목록(콤마 구분, 비우면 첫 /start 사용자가 관리자)",
            default=raw,
        )
    parsed_ids = _parse_telegram_allowlist(raw)
    return parsed_ids, raw


def _resolve_telegram_webhook_url(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    url = getattr(parsed, "tg_webhook_url", None) or profile.get("telegram_webhook_url", "")
    if sys.stdin.isatty() and not getattr(parsed, "tg_webhook_url", None):
        url = _prompt("Telegram webhook URL", default=url)
    return url


def _resolve_telegram_webhook_bind(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    bind = getattr(parsed, "tg_webhook_bind", None) or profile.get("telegram_webhook_bind", "127.0.0.1:8088")
    if sys.stdin.isatty() and not getattr(parsed, "tg_webhook_bind", None):
        bind = _prompt_non_empty("Telegram webhook bind(host:port)", default=bind)
    return bind


def _materialize_telegram_token(
    parsed: argparse.Namespace,
    profile: dict[str, str],
    *,
    tg_setup: str,
) -> str:
    token_file = _resolve_telegram_token_file(parsed, profile)
    token_value = (getattr(parsed, "tg_token", None) or "").strip()

    if tg_setup == "fresh" and sys.stdin.isatty() and not token_value:
        token_value = _prompt_non_empty("Telegram Bot Token")

    if token_value:
        path = Path(token_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{token_value}\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        print(f"Telegram 토큰 저장됨: {path}")
    return token_file


def _resolve_provider(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    provider = parsed.llm_provider or profile.get("provider") or os.getenv("GAIA_LLM_PROVIDER", "openai")
    if provider not in {"openai", "gemini"}:
        provider = "openai"
    if sys.stdin.isatty() and not parsed.llm_provider:
        provider = _prompt_select(
            "AI 제공자를 선택하세요",
            ("openai", "gemini"),
            default=provider,
        )
    return provider


def _resolve_model(
    parsed: argparse.Namespace,
    profile: dict[str, str],
    provider: str,
    token: str | None,
) -> str:
    model = parsed.llm_model or profile.get("model") or os.getenv("GAIA_LLM_MODEL") or _default_model(provider)
    if not sys.stdin.isatty() or parsed.llm_model:
        return model

    account_models = _resolve_account_model_choices(provider, token)
    fallback_models = OPENAI_MODEL_CHOICES if provider == "openai" else GEMINI_MODEL_CHOICES
    merged_models = _sort_by_priority(
        [*account_models, *fallback_models],
        OPENAI_MODEL_PRIORITY if provider == "openai" else GEMINI_MODEL_PRIORITY,
    )
    base_options = tuple(merged_models) if merged_models else fallback_models
    if "직접 입력" not in base_options:
        base_options = (*base_options, "직접 입력")
    if base_options and model not in base_options:
        options = (model, *base_options)
    else:
        options = base_options

    if account_models:
        print(f"{provider} 계정에서 사용 가능한 모델 {len(account_models)}개를 불러왔습니다.")

    if provider == "openai":
        selected = _prompt_select("OpenAI 모델을 선택하세요", options, default=model)
        if selected == "직접 입력":
            return _prompt_non_empty("OpenAI 모델명을 입력하세요", default=_default_model(provider))
        return selected

    selected = _prompt_select("Gemini 모델을 선택하세요", options, default=model)
    if selected == "직접 입력":
        return _prompt_non_empty("Gemini 모델명을 입력하세요", default=_default_model(provider))
    return selected


def _resolve_auth_strategy(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    strategy = parsed.auth or profile.get("default_auth_strategy", "reuse")
    if strategy not in AUTH_CHOICES:
        strategy = "reuse"
    if sys.stdin.isatty() and not parsed.auth:
        strategy = _prompt_select(
            "인증 방식을 선택하세요",
            ("reuse", "fresh"),
            default=strategy,
        )
    return strategy


def _resolve_openai_auth_method(parsed: argparse.Namespace, profile: dict[str, str], provider: str) -> str:
    if provider != "openai":
        return "auto"

    method = getattr(parsed, "auth_method", None) or profile.get("default_openai_auth_method", "oauth")
    if method not in OPENAI_AUTH_METHOD_CHOICES:
        method = "oauth"
    if sys.stdin.isatty() and not getattr(parsed, "auth_method", None):
        method = _prompt_select(
            "OpenAI 인증 방식을 선택하세요",
            ("oauth", "manual"),
            default=method,
        )
    return method


def _resolve_url(parsed: argparse.Namespace, profile: dict[str, str], required: bool) -> str | None:
    url = parsed.url or profile.get("last_url")
    if required and sys.stdin.isatty() and not parsed.url:
        url = _prompt_non_empty("테스트할 URL", default=url)
    if required and not url:
        print("URL is required. Use --url <target-url>.", file=sys.stderr)
        return None
    return url


def _resolve_auth(provider: str, strategy: str, method: str = "auto") -> str | None:
    token, source = gaia_auth.resolve_auth(provider=provider, strategy=strategy, method=method)
    if not token:
        print("인증이 완료되지 않아 실행을 중단합니다.", file=sys.stderr)
        return None
    if provider == "openai":
        os.environ["GAIA_OPENAI_AUTH_SOURCE"] = source or ""
    if source:
        print(f"{provider} 인증 사용: {source}")
    return token


def _build_common_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--llm-provider", choices=("openai", "gemini"))
    parser.add_argument("--llm-model")
    parser.add_argument("--auth", choices=AUTH_CHOICES)
    parser.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    parser.add_argument("--url")
    parser.add_argument("--runtime", choices=RUNTIME_CHOICES)
    parser.add_argument("--gui", action="store_true", help="Force GUI runtime")
    parser.add_argument("--terminal", action="store_true", help="Force terminal runtime")
    parser.add_argument("--session", help=f"Session key (default: {WORKSPACE_DEFAULT})")
    parser.add_argument("--new-session", action="store_true", help="Force new MCP session id")
    return parser


def _persist_profile(
    profile: dict[str, str],
    *,
    provider: str,
    model: str,
    auth_strategy: str,
    auth_method: str,
    url: str | None,
    runtime: str,
    control_channel: str | None = None,
    telegram_mode: str | None = None,
    telegram_token_file: str | None = None,
    telegram_allowlist: str | None = None,
    telegram_webhook_url: str | None = None,
    telegram_webhook_bind: str | None = None,
    workspace: str | None = None,
    session_key: str | None = None,
    mcp_session_id: str | None = None,
) -> None:
    profile["provider"] = provider
    profile["model"] = model
    profile["default_auth_strategy"] = auth_strategy
    if provider == "openai" and auth_method in OPENAI_AUTH_METHOD_CHOICES:
        profile["default_openai_auth_method"] = auth_method
    profile["last_runtime"] = runtime
    if url:
        profile["last_url"] = url
    if control_channel in CONTROL_CHOICES:
        profile["control_channel"] = control_channel
    if telegram_mode in TELEGRAM_MODE_CHOICES:
        profile["telegram_mode"] = telegram_mode
    if telegram_token_file:
        profile["telegram_token_file"] = telegram_token_file
    if telegram_allowlist is not None:
        profile["telegram_allowlist"] = telegram_allowlist
    if telegram_webhook_url is not None:
        profile["telegram_webhook_url"] = telegram_webhook_url
    if telegram_webhook_bind:
        profile["telegram_webhook_bind"] = telegram_webhook_bind
    if workspace:
        profile["last_workspace"] = workspace
    if session_key:
        profile["last_session_key"] = session_key
    if mcp_session_id:
        profile["last_mcp_session_id"] = mcp_session_id
    _save_profile(profile)


def _configure_session(
    parsed: argparse.Namespace,
    *,
    require_url: bool,
) -> tuple[str, str, str, str | None, str, str, str, bool] | None:
    profile = _load_profile()
    session_key, mcp_session_id, is_new_session = _resolve_session_binding(parsed, profile)
    os.environ["GAIA_SESSION_KEY"] = session_key
    os.environ["GAIA_MCP_SESSION_ID"] = mcp_session_id
    provider = _resolve_provider(parsed, profile)
    auth_strategy = _resolve_auth_strategy(parsed, profile)
    auth_method = _resolve_openai_auth_method(parsed, profile, provider)
    if provider == "openai":
        print(f"OpenAI 인증 시작: strategy={auth_strategy}, method={auth_method}")
    else:
        print(f"{provider} 인증 시작: strategy={auth_strategy}")
    token = _resolve_auth(provider, auth_strategy, auth_method)
    if not token:
        return None
    if not _ensure_mcp_host_running():
        return None
    model = _resolve_model(parsed, profile, provider, token)

    url = _resolve_url(parsed, profile, required=require_url)
    if require_url and not url:
        return None
    runtime = _resolve_runtime(parsed, profile, default="gui")

    if provider == "openai":
        _apply_llm_environment(provider, model, token, None)
    else:
        _apply_llm_environment(provider, model, None, token)

    _persist_profile(
        profile,
        provider=provider,
        model=model,
        auth_strategy=auth_strategy,
        auth_method=auth_method,
        url=url,
        runtime=runtime,
        workspace=session_key,
        session_key=session_key,
        mcp_session_id=mcp_session_id,
    )
    _persist_session_state(
        session_key=session_key,
        mcp_session_id=mcp_session_id,
        url=url,
    )
    return (
        provider,
        model,
        auth_strategy,
        url,
        runtime,
        session_key,
        mcp_session_id,
        is_new_session,
    )


def run_gui(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gaia gui")
    parser.add_argument("--resume")
    parser.add_argument("--url")
    parser.add_argument("--plan")
    parser.add_argument("--spec")
    parser.add_argument("--mode", choices=("plan", "ai", "chat"))
    parser.add_argument("--feature-query")
    args = parser.parse_args(list(argv or []))

    from gaia.main import main as launch_gui

    forwarded: list[str] = []
    if args.resume:
        forwarded += ["--resume", str(args.resume)]
    if args.url:
        forwarded += ["--url", str(args.url)]
    if args.plan:
        forwarded += ["--plan", str(args.plan)]
    if args.spec:
        forwarded += ["--spec", str(args.spec)]
    if args.mode:
        forwarded += ["--mode", str(args.mode)]
    if args.feature_query:
        forwarded += ["--feature-query", str(args.feature_query)]
    return launch_gui(forwarded)


def _dispatch_chat(
    runtime: str,
    url: str,
    feature_query: str | None,
    repl: bool,
    *,
    session_id: str,
) -> int:
    if runtime == "gui":
        forwarded = ["--mode", "chat", "--url", url]
        if feature_query:
            forwarded += ["--feature-query", feature_query]
        return run_gui(forwarded)

    from gaia.terminal import run_chat_terminal

    return run_chat_terminal(
        url=url,
        initial_query=feature_query,
        repl=repl,
        session_id=session_id,
    )


def _dispatch_ai(runtime: str, url: str, max_actions: int, *, session_id: str) -> int:
    if runtime == "gui":
        return run_gui(["--mode", "ai", "--url", url])

    from gaia.terminal import run_ai_terminal

    return run_ai_terminal(url=url, max_actions=max_actions, session_id=session_id)


def _dispatch_plan(url: str | None, plan: str | None, spec: str | None, resume: str | None) -> int:
    forwarded = ["--mode", "plan"]
    if url:
        forwarded += ["--url", url]
    if plan:
        forwarded += ["--plan", plan]
    if spec:
        forwarded += ["--spec", spec]
    if resume:
        forwarded += ["--resume", resume]
    return run_gui(forwarded)


def run_chat(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia chat", "Run chat mode.")
    parser.add_argument("--feature-query")
    parser.add_argument("--once", action="store_true", help="Run one test and exit in terminal mode.")
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    _, _, _, url, runtime, _, mcp_session_id, _ = configured
    assert url is not None
    return _dispatch_chat(
        runtime,
        url,
        args.feature_query,
        repl=not args.once,
        session_id=mcp_session_id,
    )


def run_ai(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia ai", "Run autonomous exploratory mode.")
    parser.add_argument("--max-actions", type=int, default=50)
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    _, _, _, url, runtime, _, mcp_session_id, _ = configured
    assert url is not None
    return _dispatch_ai(
        runtime,
        url,
        max(1, int(args.max_actions)),
        session_id=mcp_session_id,
    )


def run_plan(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia plan", "Run plan/spec/resume flow (GUI first).")
    parser.add_argument("--plan")
    parser.add_argument("--spec")
    parser.add_argument("--resume")
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=False)
    if not configured:
        return 1
    _, _, _, url, runtime, _, _, _ = configured
    if runtime == "terminal":
        print("plan/spec 실행은 GUI를 사용합니다. GUI로 전환합니다.")
    return _dispatch_plan(url, args.plan, args.spec, args.resume)


def run_launcher(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia", "GAIA quick launcher.")
    parser.add_argument("--mode", choices=MODE_CHOICES, help="Run selected mode directly.")
    parser.add_argument("--plan")
    parser.add_argument("--spec")
    parser.add_argument("--resume")
    parser.add_argument("--feature-query")
    parser.add_argument("--max-actions", type=int, default=50)
    parser.add_argument("--control", choices=CONTROL_CHOICES)
    parser.add_argument("--tg-setup", choices=TELEGRAM_SETUP_CHOICES)
    parser.add_argument("--tg-mode", choices=TELEGRAM_MODE_CHOICES)
    parser.add_argument("--tg-token-file")
    parser.add_argument("--tg-token", help="Telegram bot token (fresh setup).")
    parser.add_argument("--tg-allowlist", help="Comma-separated telegram admin chat_id allowlist.")
    parser.add_argument("--tg-webhook-url")
    parser.add_argument("--tg-webhook-bind")
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    (
        provider,
        model,
        auth_strategy,
        url,
        runtime,
        session_key,
        mcp_session_id,
        session_new,
    ) = configured
    assert url is not None
    saved_state = load_session_state(session_key)
    last_snapshot_id = str(saved_state.last_snapshot_id or "") if saved_state else ""
    pending_user_input = dict(saved_state.pending_user_input) if saved_state else {}
    profile = _load_profile()
    control = _resolve_control_channel(args, profile)

    if control == "telegram":
        if runtime != "terminal":
            print("Telegram 제어 채널에서는 terminal runtime으로 고정합니다.")
            runtime = "terminal"
        tg_setup = _resolve_telegram_setup_strategy(args, profile)
        tg_mode = ""
        tg_token_file = ""
        tg_allowlist: list[int] = []
        tg_allowlist_raw = ""
        tg_webhook_url = ""
        tg_webhook_bind = "127.0.0.1:8088"

        if tg_setup == "reuse":
            tg_mode = getattr(args, "tg_mode", None) or profile.get("telegram_mode", "")
            tg_token_file = getattr(args, "tg_token_file", None) or profile.get("telegram_token_file", "")
            tg_allowlist_raw = getattr(args, "tg_allowlist", None) or profile.get("telegram_allowlist", "")
            tg_allowlist = _parse_telegram_allowlist(tg_allowlist_raw)
            tg_webhook_url = getattr(args, "tg_webhook_url", None) or profile.get("telegram_webhook_url", "")
            tg_webhook_bind = getattr(args, "tg_webhook_bind", None) or profile.get("telegram_webhook_bind", "127.0.0.1:8088")

            if not tg_mode or not tg_token_file:
                print(
                    "저장된 Telegram 설정이 존재하지 않습니다. "
                    "다시 실행해서 Telegram 설정에서 fresh를 선택하세요.",
                    file=sys.stderr,
                )
                return 2
            if tg_mode not in TELEGRAM_MODE_CHOICES:
                print(
                    f"저장된 Telegram 모드가 유효하지 않습니다: {tg_mode}. "
                    "fresh 설정으로 다시 저장하세요.",
                    file=sys.stderr,
                )
                return 2
            if not Path(tg_token_file).exists():
                print(
                    f"저장된 Telegram token 파일이 존재하지 않습니다: {tg_token_file}. "
                    "fresh 설정으로 경로를 다시 지정하세요.",
                    file=sys.stderr,
                )
                return 2
        else:
            tg_mode = _resolve_telegram_mode(args, profile)
            tg_token_file = _materialize_telegram_token(args, profile, tg_setup=tg_setup)
            tg_allowlist, tg_allowlist_raw = _resolve_telegram_allowlist(args, profile)
            if tg_mode == "webhook":
                tg_webhook_url = _resolve_telegram_webhook_url(args, profile)
                tg_webhook_bind = _resolve_telegram_webhook_bind(args, profile)
            else:
                tg_webhook_url = ""
                tg_webhook_bind = profile.get("telegram_webhook_bind", "127.0.0.1:8088")
            if not Path(tg_token_file).exists():
                print(
                    "Telegram Bot Token이 설정되지 않았습니다. "
                    "fresh에서 토큰을 입력하거나 --tg-token으로 전달하세요.",
                    file=sys.stderr,
                )
                return 2

        if tg_mode == "webhook" and not tg_webhook_url:
            print("Webhook mode requires --tg-webhook-url.", file=sys.stderr)
            return 2

        profile["telegram_setup_strategy"] = tg_setup
        _persist_profile(
            profile,
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            auth_method=getattr(args, "auth_method", None) or profile.get("default_openai_auth_method", "oauth"),
            url=url,
            runtime=runtime,
            control_channel=control,
            telegram_mode=tg_mode,
            telegram_token_file=tg_token_file,
            telegram_allowlist=tg_allowlist_raw,
            telegram_webhook_url=tg_webhook_url,
            telegram_webhook_bind=tg_webhook_bind,
            workspace=session_key,
            session_key=session_key,
            mcp_session_id=mcp_session_id,
        )

        if args.mode:
            print("Telegram 제어 채널에서는 --mode direct 실행을 무시하고 Chat Hub 대기 상태로 시작합니다.")

        from gaia.chat_hub import HubContext
        from gaia.telegram_bridge import TelegramConfig, run_telegram_bridge

        def _on_session_update(ctx: HubContext) -> None:
            profile_local = _load_profile()
            _persist_profile(
                profile_local,
                provider=ctx.provider,
                model=ctx.model,
                auth_strategy=ctx.auth_strategy,
                auth_method=getattr(args, "auth_method", None)
                or profile_local.get("default_openai_auth_method", "oauth"),
                url=ctx.url,
                runtime=ctx.runtime,
                control_channel=ctx.control_channel,
                telegram_mode=tg_mode,
                telegram_token_file=tg_token_file,
                telegram_allowlist=tg_allowlist_raw,
                telegram_webhook_url=tg_webhook_url,
                telegram_webhook_bind=tg_webhook_bind,
                workspace=ctx.workspace,
                session_key=ctx.session_key,
                mcp_session_id=ctx.session_id,
            )
            _persist_session_state(
                session_key=ctx.session_key,
                mcp_session_id=ctx.session_id,
                url=ctx.url,
                last_snapshot_id=ctx.last_snapshot_id,
                pending_user_input={k: str(v) for k, v in ctx.pending_user_input.items()},
            )

        return run_telegram_bridge(
            HubContext(
                provider=provider,
                model=model,
                auth_strategy=auth_strategy,
                url=url,
                runtime=runtime,
                control_channel="telegram",
                memory_enabled=True,
                workspace=session_key,
                session_key=session_key,
                session_id=mcp_session_id,
                session_new=session_new,
                last_snapshot_id=last_snapshot_id,
                pending_user_input=pending_user_input,
                on_session_update=_on_session_update,
            ),
            TelegramConfig(
                mode=tg_mode,
                token_file=tg_token_file,
                allowlist=tuple(tg_allowlist),
                webhook_url=tg_webhook_url,
                webhook_bind=tg_webhook_bind,
            ),
        )

    if args.mode == "chat":
        _persist_profile(
            profile,
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            auth_method=getattr(args, "auth_method", None) or profile.get("default_openai_auth_method", "oauth"),
            url=url,
            runtime=runtime,
            control_channel="local",
            workspace=session_key,
            session_key=session_key,
            mcp_session_id=mcp_session_id,
        )
        return _dispatch_chat(
            runtime,
            url,
            args.feature_query,
            repl=True,
            session_id=mcp_session_id,
        )
    if args.mode == "ai":
        _persist_profile(
            profile,
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            auth_method=getattr(args, "auth_method", None) or profile.get("default_openai_auth_method", "oauth"),
            url=url,
            runtime=runtime,
            control_channel="local",
            workspace=session_key,
            session_key=session_key,
            mcp_session_id=mcp_session_id,
        )
        return _dispatch_ai(
            runtime,
            url,
            max(1, int(args.max_actions)),
            session_id=mcp_session_id,
        )
    if args.mode == "plan":
        _persist_profile(
            profile,
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            auth_method=getattr(args, "auth_method", None) or profile.get("default_openai_auth_method", "oauth"),
            url=url,
            runtime=runtime,
            control_channel="local",
            workspace=session_key,
            session_key=session_key,
            mcp_session_id=mcp_session_id,
        )
        return _dispatch_plan(url, args.plan, args.spec, args.resume)

    from gaia.chat_hub import HubContext, run_chat_hub

    _persist_profile(
        profile,
        provider=provider,
        model=model,
        auth_strategy=auth_strategy,
        auth_method=getattr(args, "auth_method", None) or profile.get("default_openai_auth_method", "oauth"),
        url=url,
        runtime=runtime,
        control_channel="local",
        workspace=session_key,
        session_key=session_key,
        mcp_session_id=mcp_session_id,
    )

    def _on_session_update(ctx: HubContext) -> None:
        profile_local = _load_profile()
        _persist_profile(
            profile_local,
            provider=ctx.provider,
            model=ctx.model,
            auth_strategy=ctx.auth_strategy,
            auth_method=getattr(args, "auth_method", None)
            or profile_local.get("default_openai_auth_method", "oauth"),
            url=ctx.url,
            runtime=ctx.runtime,
            control_channel=ctx.control_channel,
            workspace=ctx.workspace,
            session_key=ctx.session_key,
            mcp_session_id=ctx.session_id,
        )
        _persist_session_state(
            session_key=ctx.session_key,
            mcp_session_id=ctx.session_id,
            url=ctx.url,
            last_snapshot_id=ctx.last_snapshot_id,
            pending_user_input={k: str(v) for k, v in ctx.pending_user_input.items()},
        )

    return run_chat_hub(
        HubContext(
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            url=url,
            runtime=runtime,
            control_channel="local",
            memory_enabled=True,
            workspace=session_key,
            session_key=session_key,
            session_id=mcp_session_id,
            session_new=session_new,
            last_snapshot_id=last_snapshot_id,
            pending_user_input=pending_user_input,
            on_session_update=_on_session_update,
        )
    )


def _build_start_legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaia start", description="Legacy alias for gaia launcher.")
    subparsers = parser.add_subparsers(dest="subcommand", required=False)
    gui = subparsers.add_parser("gui")
    gui.add_argument("--mode", choices=("plan", "ai", "chat"), default="chat")
    gui.add_argument("--url")
    gui.add_argument("--plan")
    gui.add_argument("--spec")
    gui.add_argument("--resume")
    gui.add_argument("--feature-query")
    gui.add_argument("--llm-provider", choices=("openai", "gemini"))
    gui.add_argument("--llm-model")
    gui.add_argument("--auth", choices=AUTH_CHOICES)
    gui.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    gui.add_argument("--session")
    gui.add_argument("--new-session", action="store_true")
    terminal = subparsers.add_parser("terminal")
    terminal.add_argument("--mode", choices=("plan", "ai", "chat"), default="chat")
    terminal.add_argument("--url")
    terminal.add_argument("--plan")
    terminal.add_argument("--spec")
    terminal.add_argument("--resume")
    terminal.add_argument("--feature-query")
    terminal.add_argument("--max-actions", type=int, default=50)
    terminal.add_argument("--llm-provider", choices=("openai", "gemini"))
    terminal.add_argument("--llm-model")
    terminal.add_argument("--auth", choices=AUTH_CHOICES)
    terminal.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    terminal.add_argument("--runtime", choices=RUNTIME_CHOICES, default="terminal")
    terminal.add_argument("--session")
    terminal.add_argument("--new-session", action="store_true")
    parser.add_argument("--llm-provider", choices=("openai", "gemini"))
    parser.add_argument("--llm-model")
    parser.add_argument("--auth", choices=AUTH_CHOICES)
    parser.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    parser.add_argument("--url")
    parser.add_argument("--runtime", choices=RUNTIME_CHOICES)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--terminal", action="store_true")
    parser.add_argument("--session")
    parser.add_argument("--new-session", action="store_true")
    parser.add_argument("--mode", choices=MODE_CHOICES)
    parser.add_argument("--control", choices=CONTROL_CHOICES)
    parser.add_argument("--tg-setup", choices=TELEGRAM_SETUP_CHOICES)
    parser.add_argument("--tg-mode", choices=TELEGRAM_MODE_CHOICES)
    parser.add_argument("--tg-token-file")
    parser.add_argument("--tg-token")
    parser.add_argument("--tg-allowlist")
    parser.add_argument("--tg-webhook-url")
    parser.add_argument("--tg-webhook-bind")
    return parser


def run_start(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    if not args:
        return run_launcher([])

    parser = _build_start_legacy_parser()
    parsed = parser.parse_args(args)

    if parsed.subcommand == "gui":
        forwarded: list[str] = ["--gui"]
        if parsed.url:
            forwarded += ["--url", parsed.url]
        if parsed.llm_provider:
            forwarded += ["--llm-provider", parsed.llm_provider]
        if parsed.llm_model:
            forwarded += ["--llm-model", parsed.llm_model]
        if parsed.auth:
            forwarded += ["--auth", parsed.auth]
        if parsed.auth_method:
            forwarded += ["--auth-method", parsed.auth_method]
        if parsed.session:
            forwarded += ["--session", parsed.session]
        if parsed.new_session:
            forwarded += ["--new-session"]
        if parsed.mode == "ai":
            return run_ai(forwarded)
        if parsed.mode == "plan":
            if parsed.plan:
                forwarded += ["--plan", parsed.plan]
            if parsed.spec:
                forwarded += ["--spec", parsed.spec]
            if parsed.resume:
                forwarded += ["--resume", parsed.resume]
            return run_plan(forwarded)
        if parsed.feature_query:
            forwarded += ["--feature-query", parsed.feature_query]
        return run_chat(forwarded)

    if parsed.subcommand == "terminal":
        forwarded = ["--terminal"]
        if parsed.url:
            forwarded += ["--url", parsed.url]
        if parsed.llm_provider:
            forwarded += ["--llm-provider", parsed.llm_provider]
        if parsed.llm_model:
            forwarded += ["--llm-model", parsed.llm_model]
        if parsed.auth:
            forwarded += ["--auth", parsed.auth]
        if parsed.auth_method:
            forwarded += ["--auth-method", parsed.auth_method]
        if parsed.session:
            forwarded += ["--session", parsed.session]
        if parsed.new_session:
            forwarded += ["--new-session"]
        if parsed.mode == "ai":
            forwarded += ["--max-actions", str(parsed.max_actions)]
            return run_ai(forwarded)
        if parsed.mode == "plan" or parsed.plan or parsed.spec or parsed.resume:
            if parsed.plan:
                forwarded += ["--plan", parsed.plan]
            if parsed.spec:
                forwarded += ["--spec", parsed.spec]
            if parsed.resume:
                forwarded += ["--resume", parsed.resume]
            return run_plan(forwarded)
        if parsed.feature_query:
            forwarded += ["--feature-query", parsed.feature_query]
        return run_chat(forwarded)

    forwarded = []
    if parsed.llm_provider:
        forwarded += ["--llm-provider", parsed.llm_provider]
    if parsed.llm_model:
        forwarded += ["--llm-model", parsed.llm_model]
    if parsed.auth:
        forwarded += ["--auth", parsed.auth]
    if parsed.auth_method:
        forwarded += ["--auth-method", parsed.auth_method]
    if parsed.url:
        forwarded += ["--url", parsed.url]
    if parsed.runtime:
        forwarded += ["--runtime", parsed.runtime]
    if parsed.gui:
        forwarded += ["--gui"]
    if parsed.terminal:
        forwarded += ["--terminal"]
    if parsed.session:
        forwarded += ["--session", parsed.session]
    if parsed.new_session:
        forwarded += ["--new-session"]
    if parsed.mode:
        forwarded += ["--mode", parsed.mode]
    if parsed.control:
        forwarded += ["--control", parsed.control]
    if parsed.tg_setup:
        forwarded += ["--tg-setup", parsed.tg_setup]
    if parsed.tg_mode:
        forwarded += ["--tg-mode", parsed.tg_mode]
    if parsed.tg_token_file:
        forwarded += ["--tg-token-file", parsed.tg_token_file]
    if parsed.tg_token:
        forwarded += ["--tg-token", parsed.tg_token]
    if parsed.tg_allowlist:
        forwarded += ["--tg-allowlist", parsed.tg_allowlist]
    if parsed.tg_webhook_url:
        forwarded += ["--tg-webhook-url", parsed.tg_webhook_url]
    if parsed.tg_webhook_bind:
        forwarded += ["--tg-webhook-bind", parsed.tg_webhook_bind]
    return run_launcher(forwarded)


def run_terminal(argv: Sequence[str] | None = None) -> int:
    # Legacy alias: gaia terminal ...
    return run_start(["terminal", *(list(argv or []))])


def _build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia",
        description="GAIA command line interface",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("start", help="Legacy alias of gaia launcher")
    subparsers.add_parser("chat", help="Run chat mode")
    subparsers.add_parser("ai", help="Run autonomous exploratory mode")
    subparsers.add_parser("plan", help="Run plan/spec/resume flow")
    subparsers.add_parser("gui", help="Legacy GUI alias")
    subparsers.add_parser("terminal", help="Legacy terminal alias")
    subparsers.add_parser("auth", help="Manage GAIA auth tokens")
    subparsers.add_parser("help", help="Show help")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            return run_launcher([])

        if args[0] in {"-h", "--help", "help"}:
            _build_main_parser().print_help()
            return 0

        if args[0] == "start":
            return run_start(args[1:])
        if args[0] == "chat":
            return run_chat(args[1:])
        if args[0] == "ai":
            return run_ai(args[1:])
        if args[0] == "plan":
            return run_plan(args[1:])
        if args[0] == "gui":
            return run_start(["gui", *args[1:]])
        if args[0] == "terminal":
            return run_terminal(args[1:])
        if args[0] == "auth":
            return gaia_auth.run_auth(args[1:])

        _build_main_parser().print_help()
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n중단되었습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
