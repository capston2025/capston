"""Console entry point for GAIA."""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
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
TERMINAL_PURPOSE_CHOICES = ("실제 사용 모드 실행", "벤치마크 용도 실행")
PROVIDER_CHOICES = ("openai", "gemini", "ollama")
DEFAULT_TELEGRAM_TOKEN_FILE = str(Path.home() / ".gaia" / "telegram_bot_token")
TELEGRAM_BRIDGE_PID_FILE = Path.home() / ".gaia" / "telegram_bridge.pid"
TELEGRAM_BRIDGE_STATUS_FILE = Path.home() / ".gaia" / "telegram_bridge.status.json"
DEFAULT_OPENAI_MODEL = "gpt-5.5"

OPENAI_MODEL_CHOICES = (
    DEFAULT_OPENAI_MODEL,
    "gpt-5.4",
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

OLLAMA_MODEL_CHOICES = (
    "gemma4:26b",
    "직접 입력",
)

OPENAI_MODEL_PRIORITY = (
    DEFAULT_OPENAI_MODEL,
    "gpt-5.4",
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

OLLAMA_MODEL_PRIORITY = (
    "gemma4:26b",
)


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


def _save_profile(profile: dict[str, str]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _pid_running(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except Exception:
        return False
    return True


def _run_telegram_bridge_bg_entry() -> int:
    from gaia.chat_hub import HubContext
    from gaia.telegram_bridge import TelegramConfig, run_telegram_bridge

    provider = str(os.getenv("GAIA_BG_PROVIDER") or "openai").strip() or "openai"
    model = str(os.getenv("GAIA_BG_MODEL") or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    auth_strategy = str(os.getenv("GAIA_BG_AUTH_STRATEGY") or "reuse").strip() or "reuse"
    url = str(os.getenv("GAIA_BG_URL") or "").strip()
    runtime = str(os.getenv("GAIA_BG_RUNTIME") or "gui").strip() or "gui"
    session_key = str(os.getenv("GAIA_BG_SESSION_KEY") or WORKSPACE_DEFAULT).strip() or WORKSPACE_DEFAULT
    session_id = str(os.getenv("GAIA_BG_MCP_SESSION_ID") or session_key).strip() or session_key
    session_new = str(os.getenv("GAIA_BG_SESSION_NEW") or "").strip().lower() in {"1", "true", "yes", "on"}
    tg_mode = str(os.getenv("GAIA_BG_TG_MODE") or "polling").strip() or "polling"
    tg_token_file = str(os.getenv("GAIA_BG_TG_TOKEN_FILE") or DEFAULT_TELEGRAM_TOKEN_FILE).strip()
    tg_allowlist_raw = str(os.getenv("GAIA_BG_TG_ALLOWLIST") or "").strip()
    tg_webhook_url = str(os.getenv("GAIA_BG_TG_WEBHOOK_URL") or "").strip()
    tg_webhook_bind = str(os.getenv("GAIA_BG_TG_WEBHOOK_BIND") or "127.0.0.1:8088").strip() or "127.0.0.1:8088"

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
            session_id=session_id,
            session_new=session_new,
            last_snapshot_id="",
            pending_user_input={},
            on_session_update=None,
        ),
        TelegramConfig(
            mode=tg_mode,
            token_file=tg_token_file,
            allowlist=tuple(_parse_telegram_allowlist(tg_allowlist_raw)),
            webhook_url=tg_webhook_url,
            webhook_bind=tg_webhook_bind,
        ),
    )


def _launch_telegram_bridge_background(
    *,
    provider: str,
    model: str,
    auth_strategy: str,
    url: str,
    runtime: str,
    session_key: str,
    mcp_session_id: str,
    session_new: bool,
    tg_mode: str,
    tg_token_file: str,
    tg_allowlist_raw: str,
    tg_webhook_url: str,
    tg_webhook_bind: str,
) -> bool:
    try:
        if TELEGRAM_BRIDGE_PID_FILE.exists():
            try:
                existing_pid = int(TELEGRAM_BRIDGE_PID_FILE.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                existing_pid = 0
            if existing_pid > 0 and _pid_running(existing_pid):
                return True
            try:
                TELEGRAM_BRIDGE_PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                TELEGRAM_BRIDGE_STATUS_FILE.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass

    log_dir = Path.home() / ".gaia" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "telegram_bridge.log"
    env = os.environ.copy()
    env.update(
        {
            "GAIA_BG_PROVIDER": provider,
            "GAIA_BG_MODEL": model,
            "GAIA_BG_AUTH_STRATEGY": auth_strategy,
            "GAIA_BG_URL": url,
            "GAIA_BG_RUNTIME": runtime,
            "GAIA_BG_SESSION_KEY": session_key,
            "GAIA_BG_MCP_SESSION_ID": mcp_session_id,
            "GAIA_BG_SESSION_NEW": "1" if session_new else "0",
            "GAIA_BG_TG_MODE": tg_mode,
            "GAIA_BG_TG_TOKEN_FILE": tg_token_file,
            "GAIA_BG_TG_ALLOWLIST": tg_allowlist_raw,
            "GAIA_BG_TG_WEBHOOK_URL": tg_webhook_url,
            "GAIA_BG_TG_WEBHOOK_BIND": tg_webhook_bind,
        }
    )
    with log_file.open("a", encoding="utf-8") as log_fp:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from gaia.cli import _run_telegram_bridge_bg_entry as e; raise SystemExit(e())",
            ],
            cwd=str(Path(__file__).resolve().parent.parent),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    TELEGRAM_BRIDGE_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    TELEGRAM_BRIDGE_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    return True


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
    if provider == "openai":
        return DEFAULT_OPENAI_MODEL
    if provider == "gemini":
        return "gemini-2.5-pro"
    return "gemma4:26b"


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


def _apply_llm_environment(provider: str | None, model: str | None, token: str | None) -> None:
    if provider:
        os.environ["GAIA_LLM_PROVIDER"] = provider
        os.environ["VISION_PROVIDER"] = provider
    if model:
        os.environ["GAIA_LLM_MODEL"] = model
        os.environ["VISION_MODEL"] = model
    if provider:
        gaia_auth.write_env_if_set(provider, token)


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


def _provider_model_choices(provider: str) -> Sequence[str]:
    if provider == "openai":
        return OPENAI_MODEL_CHOICES
    if provider == "gemini":
        return GEMINI_MODEL_CHOICES
    return OLLAMA_MODEL_CHOICES


def _provider_model_priority(provider: str) -> Sequence[str]:
    if provider == "openai":
        return OPENAI_MODEL_PRIORITY
    if provider == "gemini":
        return GEMINI_MODEL_PRIORITY
    return OLLAMA_MODEL_PRIORITY


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


def _resolve_terminal_launch_purpose(
    parsed: argparse.Namespace,
    profile: dict[str, str],
    *,
    runtime: str,
) -> str:
    if runtime != "terminal":
        return "actual"
    if not sys.stdin.isatty():
        return "actual"
    default = TERMINAL_PURPOSE_CHOICES[0]
    if str(profile.get("last_terminal_purpose") or "").strip().lower() == "benchmark":
        default = TERMINAL_PURPOSE_CHOICES[1]
    selected = _prompt_select(
        "테스트 용도 인가요?",
        TERMINAL_PURPOSE_CHOICES,
        default=default,
    )
    profile["last_terminal_purpose"] = "benchmark" if selected == TERMINAL_PURPOSE_CHOICES[1] else "actual"
    _save_profile(profile)
    return "benchmark" if selected == TERMINAL_PURPOSE_CHOICES[1] else "actual"


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
    if provider not in PROVIDER_CHOICES:
        provider = "openai"
    if sys.stdin.isatty() and not parsed.llm_provider and _should_prompt_interactive(parsed):
        provider = _prompt_select(
            "AI 제공자를 선택하세요",
            PROVIDER_CHOICES,
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
    if not sys.stdin.isatty() or parsed.llm_model or not _should_prompt_interactive(parsed):
        return model

    account_models = _resolve_account_model_choices(provider, token)
    fallback_models = _provider_model_choices(provider)
    merged_models = _sort_by_priority(
        [*account_models, *fallback_models],
        _provider_model_priority(provider),
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

    if provider == "gemini":
        selected = _prompt_select("Gemini 모델을 선택하세요", options, default=model)
        if selected == "직접 입력":
            return _prompt_non_empty("Gemini 모델명을 입력하세요", default=_default_model(provider))
        return selected

    if provider == "ollama":
        selected = _prompt_select("Ollama 모델을 선택하세요", options, default=model)
        if selected == "직접 입력":
            return _prompt_non_empty("Ollama 모델명을 입력하세요", default=_default_model(provider))
        return selected

    selected = _prompt_select("Ollama 모델을 선택하세요", options, default=model)
    if selected == "직접 입력":
        return _prompt_non_empty("Ollama 모델명을 입력하세요", default=_default_model(provider))
    return selected


def _resolve_auth_strategy(parsed: argparse.Namespace, profile: dict[str, str]) -> str:
    strategy = parsed.auth or profile.get("default_auth_strategy", "reuse")
    if strategy not in AUTH_CHOICES:
        strategy = "reuse"
    if sys.stdin.isatty() and not parsed.auth and _should_prompt_interactive(parsed):
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
    if sys.stdin.isatty() and not getattr(parsed, "auth_method", None) and _should_prompt_interactive(parsed):
        method = _prompt_select(
            "OpenAI 인증 방식을 선택하세요",
            ("oauth", "manual"),
            default=method,
        )
    return method


def _resolve_url(parsed: argparse.Namespace, profile: dict[str, str], required: bool) -> str | None:
    url = parsed.url or profile.get("last_url")
    if required and sys.stdin.isatty() and not parsed.url and _should_prompt_interactive(parsed):
        url = _prompt_non_empty("테스트할 URL", default=url)
    if required and not url:
        print("URL is required. Use --url <target-url>.", file=sys.stderr)
        return None
    return url


def _should_prompt_interactive(parsed: argparse.Namespace) -> bool:
    if not sys.stdin.isatty():
        return False
    if bool(getattr(parsed, "once", False)):
        return False
    if str(getattr(parsed, "feature_query", "") or "").strip():
        return False
    if str(getattr(parsed, "subcommand", "") or "").strip() == "terminal":
        return False
    if bool(getattr(parsed, "terminal", False)):
        return False
    return True


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
    parser.add_argument("--llm-provider", choices=PROVIDER_CHOICES)
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
    model = _resolve_model(parsed, profile, provider, token)

    url = _resolve_url(parsed, profile, required=require_url)
    if require_url and not url:
        return None
    runtime = _resolve_runtime(parsed, profile, default="gui")

    _apply_llm_environment(provider, model, token)

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
    parser.add_argument("--bundle")
    parser.add_argument("--spec")
    parser.add_argument("--mode", choices=("plan", "ai", "chat"))
    parser.add_argument("--control", choices=CONTROL_CHOICES)
    parser.add_argument("--feature-query")
    parser.add_argument("--max-actions", type=int)
    parser.add_argument("--session-key")
    parser.add_argument("--mcp-session-id")
    parser.add_argument("--mcp-host-url")
    args = parser.parse_args(list(argv or []))

    from gaia.main import main as launch_gui

    session_key = args.session_key or os.getenv("GAIA_SESSION_KEY")
    mcp_session_id = args.mcp_session_id or os.getenv("GAIA_MCP_SESSION_ID") or session_key
    mcp_host_url = args.mcp_host_url or os.getenv("MCP_HOST_URL") or os.getenv("GAIA_MCP_HOST_URL")

    forwarded: list[str] = []
    if args.resume:
        forwarded += ["--resume", str(args.resume)]
    if args.url:
        forwarded += ["--url", str(args.url)]
    if args.plan:
        forwarded += ["--plan", str(args.plan)]
    if args.bundle:
        forwarded += ["--bundle", str(args.bundle)]
    if args.spec:
        forwarded += ["--spec", str(args.spec)]
    if args.mode:
        forwarded += ["--mode", str(args.mode)]
    if args.control:
        forwarded += ["--control", str(args.control)]
    if args.feature_query:
        forwarded += ["--feature-query", str(args.feature_query)]
    if args.max_actions is not None:
        forwarded += ["--max-actions", str(max(1, int(args.max_actions)))]
    if session_key:
        forwarded += ["--session-key", str(session_key)]
    if mcp_session_id:
        forwarded += ["--mcp-session-id", str(mcp_session_id)]
    if mcp_host_url:
        forwarded += ["--mcp-host-url", str(mcp_host_url)]
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


def _dispatch_ai(
    runtime: str,
    url: str,
    max_actions: int,
    *,
    session_id: str,
    time_budget_seconds: int | None = None,
) -> int:
    if runtime == "gui" and time_budget_seconds and int(time_budget_seconds) > 0:
        runtime = "terminal"
    if runtime == "gui":
        return run_gui(["--mode", "ai", "--url", url, "--max-actions", str(max_actions)])

    from gaia.terminal import run_ai_terminal

    return run_ai_terminal(
        url=url,
        max_actions=max_actions,
        session_id=session_id,
        time_budget_seconds=time_budget_seconds,
    )


def _dispatch_plan(
    url: str | None,
    plan: str | None,
    bundle: str | None,
    spec: str | None,
    resume: str | None,
    feature_query: str | None = None,
) -> int:
    forwarded = ["--mode", "plan"]
    if url:
        forwarded += ["--url", url]
    if plan:
        forwarded += ["--plan", plan]
    if bundle:
        forwarded += ["--bundle", bundle]
    if spec:
        forwarded += ["--spec", spec]
    if resume:
        forwarded += ["--resume", resume]
    if feature_query:
        forwarded += ["--feature-query", feature_query]
    return run_gui(forwarded)


def _run_terminal_benchmark_mode(*, workspace_root: Path) -> int:
    from gaia.src.terminal_benchmark_mode import run_terminal_benchmark_mode

    return run_terminal_benchmark_mode(
        workspace_root=workspace_root,
        prompt_select=_prompt_select,
        prompt=_prompt,
        prompt_non_empty=_prompt_non_empty,
        emit=print,
    )


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
    parser.add_argument("--time-budget-seconds", type=int)
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    _, _, _, url, runtime, _, mcp_session_id, _ = configured
    assert url is not None
    effective_runtime = runtime
    if args.time_budget_seconds and int(args.time_budget_seconds) > 0 and runtime == "gui":
        print("time-budget 자율 모드는 terminal runtime으로 실행합니다.")
        effective_runtime = "terminal"
    return _dispatch_ai(
        effective_runtime,
        url,
        max(1, int(args.max_actions)),
        session_id=mcp_session_id,
        time_budget_seconds=(
            max(1, int(args.time_budget_seconds))
            if args.time_budget_seconds and int(args.time_budget_seconds) > 0
            else None
        ),
    )


def run_autonomous(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia autonomous", "Run time-budget autonomous site validation.")
    parser.add_argument("--time-budget-seconds", type=int, default=1800)
    parser.add_argument("--max-actions", type=int, default=10_000_000)
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    _, _, _, url, runtime, _, mcp_session_id, _ = configured
    assert url is not None

    effective_runtime = runtime
    if runtime == "gui":
        print("autonomous(time-budget) 모드는 terminal runtime으로 실행합니다.")
        effective_runtime = "terminal"

    return _dispatch_ai(
        effective_runtime,
        url,
        max(1, int(args.max_actions)),
        session_id=mcp_session_id,
        time_budget_seconds=max(1, int(args.time_budget_seconds)),
    )


def run_plan(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia plan", "Run plan/spec/resume flow (GUI first).")
    parser.add_argument("--plan")
    parser.add_argument("--bundle")
    parser.add_argument("--spec")
    parser.add_argument("--resume")
    parser.add_argument("--feature-query")
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=False)
    if not configured:
        return 1
    _, _, _, url, runtime, _, _, _ = configured
    if runtime == "terminal":
        print("plan/spec 실행은 GUI를 사용합니다. GUI로 전환합니다.")
    return _dispatch_plan(url, args.plan, args.bundle, args.spec, args.resume, args.feature_query)



def _slugify_filename(text: str) -> str:
    base = re.sub(r"[^0-9A-Za-z가-힣]+", "-", str(text or "").strip().lower()).strip("-")
    return base or "prd-bundle"


def run_prd(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gaia prd", description="PRD bundle utilities.")
    subparsers = parser.add_subparsers(dest="prd_command")

    ingest_parser = subparsers.add_parser("ingest", help="Normalize a PRD into a reusable bundle JSON.")
    ingest_parser.add_argument("--input")
    ingest_parser.add_argument("--text")
    ingest_parser.add_argument("--url")
    ingest_parser.add_argument("--output")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a PRD bundle JSON.")
    inspect_parser.add_argument("--bundle", required=True)
    inspect_parser.add_argument("--format", choices=("text", "json"), default="text")

    run_parser = subparsers.add_parser("run", help="Run generated goals from a PRD bundle.")
    run_parser.add_argument("--llm-provider", choices=PROVIDER_CHOICES)
    run_parser.add_argument("--llm-model")
    run_parser.add_argument("--auth", choices=AUTH_CHOICES)
    run_parser.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    run_parser.add_argument("--url")
    run_parser.add_argument("--runtime", choices=RUNTIME_CHOICES)
    run_parser.add_argument("--gui", action="store_true", help="Force GUI runtime")
    run_parser.add_argument("--terminal", action="store_true", help="Force terminal runtime")
    run_parser.add_argument("--session", help=f"Session key (default: {WORKSPACE_DEFAULT})")
    run_parser.add_argument("--new-session", action="store_true", help="Force new MCP session id")
    run_parser.add_argument("--bundle", required=True)
    run_parser.add_argument("--goal-id", action="append")
    run_parser.add_argument("--format", choices=("text", "json"), default="text")
    run_parser.add_argument("--output")

    args = parser.parse_args(list(argv or []))

    if args.prd_command == "ingest":
        from gaia.src.phase1.prd_bundle_repository import PRDBundleRepository
        from gaia.src.phase1.prd_ingest import ingest_prd_bundle

        if not args.input and not args.text:
            print("--input 또는 --text 중 하나는 필요합니다.", file=sys.stderr)
            return 2
        bundle = ingest_prd_bundle(
            input_path=args.input,
            raw_text=args.text,
            base_url=args.url,
        )
        repository = PRDBundleRepository()
        output_path = repository.save_bundle(bundle, args.output)
        print(f"bundle_path: {output_path}")
        print(f"project_name: {bundle.project_name}")
        print(f"goals: {bundle.goal_count()}")
        return 0

    if args.prd_command == "inspect":
        from gaia.src.phase1.prd_bundle_repository import PRDBundleRepository

        bundle = PRDBundleRepository().load_bundle(args.bundle)
        payload = {
            "schema_version": bundle.schema_version,
            "project_name": bundle.project_name,
            "source_type": bundle.source.type,
            "base_url": bundle.execution_profile.base_url,
            "requirements": len(bundle.normalized_prd.requirements),
            "flows": len(bundle.normalized_prd.user_flows),
            "goals": [
                {
                    "id": goal.id,
                    "title": goal.title,
                    "priority": goal.priority,
                    "contract": goal.success_contract,
                    "enabled": goal.enabled,
                }
                for goal in bundle.generated_goals
            ],
        }
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"프로젝트: {bundle.project_name}")
            print(f"소스: {bundle.source.type}")
            print(f"기본 URL: {bundle.execution_profile.base_url or '-'}")
            print(f"요구사항: {len(bundle.normalized_prd.requirements)}")
            print(f"사용자 플로우: {len(bundle.normalized_prd.user_flows)}")
            print(f"생성 목표: {len(bundle.generated_goals)}")
            for goal in bundle.generated_goals:
                marker = "ON" if goal.enabled else "OFF"
                print(f"- [{marker}] {goal.id} | {goal.priority} | {goal.title} | {goal.success_contract}")
        return 0

    if args.prd_command == "run":
        from gaia.src.phase1.prd_bundle_repository import PRDBundleRepository
        from gaia.terminal import run_chat_terminal_once

        configured = _configure_session(args, require_url=False)
        if not configured:
            return 1
        _, _, _, override_url, runtime, _, mcp_session_id, _ = configured
        bundle = PRDBundleRepository().load_bundle(args.bundle)
        resolved_url = bundle.base_url(override_url)
        if not resolved_url:
            print("번들에 base_url이 없고 --url도 지정되지 않았습니다.", file=sys.stderr)
            return 2
        selected = [goal for goal in bundle.generated_goals if goal.enabled]
        if args.goal_id:
            wanted = {str(goal_id).strip() for goal_id in args.goal_id if str(goal_id).strip()}
            selected = [goal for goal in selected if goal.id in wanted]
        if not selected:
            print("실행할 goal이 없습니다.", file=sys.stderr)
            return 2
        if runtime == "gui":
            forwarded = ["--bundle", str(Path(args.bundle).expanduser()), "--url", resolved_url, "--mode", "plan"]
            return run_gui(forwarded)

        results: list[dict[str, object]] = []
        failures = 0
        for goal in selected:
            prepared_goal = goal.to_test_goal(resolved_url)
            code, summary = run_chat_terminal_once(
                url=resolved_url,
                query=prepared_goal.description,
                session_id=mcp_session_id,
                prepared_goal=prepared_goal,
            )
            if code != 0:
                failures += 1
            results.append(
                {
                    "goal_id": goal.id,
                    "title": goal.title,
                    "status": summary.get("status"),
                    "final_status": summary.get("final_status"),
                    "reason": summary.get("reason"),
                    "duration_seconds": summary.get("duration_seconds"),
                }
            )
        payload = {
            "bundle": str(Path(args.bundle).expanduser()),
            "project_name": bundle.project_name,
            "url": resolved_url,
            "results": results,
            "status": "success" if failures == 0 else "failed",
        }
        if args.output:
            Path(args.output).expanduser().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"프로젝트: {bundle.project_name}")
            print(f"실행 URL: {resolved_url}")
            print(f"목표 수: {len(results)}")
            for row in results:
                print(f"- {row['goal_id']} | {row['status']} | {row['reason']}")
        return 0 if failures == 0 else 1

    parser.print_help()
    return 2


def _build_cli_harness_parser() -> argparse.ArgumentParser:
    from gaia.harness.cli_runtime import build_harness_parser

    return build_harness_parser(prog="gaia cli harness")


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    parser = argparse.ArgumentParser(
        prog="gaia cli",
        description="Harness command family.",
    )
    subparsers = parser.add_subparsers(dest="cli_command")
    subparsers.add_parser("harness", help="Harness command family")

    if not args or args[0] in {"-h", "--help", "help"}:
        parser.print_help()
        return 0

    if args[0] != "harness":
        parser.print_help()
        print(f"Unknown command: {args[0]}", file=sys.stderr)
        return 2

    harness_parser = _build_cli_harness_parser()
    if len(args) == 1 or args[1] in {"-h", "--help", "help"}:
        harness_parser.print_help()
        return 0

    command = args[1]
    if command not in {"ls", "run", "report"}:
        harness_parser.print_help()
        print(f"Unknown harness command: {command}", file=sys.stderr)
        return 2

    from gaia.harness.cli_runtime import run_harness_cli

    return run_harness_cli(args[1:])


def run_launcher(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia", "GAIA quick launcher.")
    parser.add_argument("--mode", choices=MODE_CHOICES, help="Run selected mode directly.")
    parser.add_argument("--plan")
    parser.add_argument("--bundle")
    parser.add_argument("--spec")
    parser.add_argument("--resume")
    parser.add_argument("--feature-query")
    parser.add_argument("--max-actions", type=int, default=50)
    parser.add_argument("--time-budget-seconds", type=int)
    parser.add_argument("--control", choices=CONTROL_CHOICES)
    parser.add_argument("--tg-setup", choices=TELEGRAM_SETUP_CHOICES)
    parser.add_argument("--tg-mode", choices=TELEGRAM_MODE_CHOICES)
    parser.add_argument("--tg-token-file")
    parser.add_argument("--tg-token", help="Telegram bot token (fresh setup).")
    parser.add_argument("--tg-allowlist", help="Comma-separated telegram admin chat_id allowlist.")
    parser.add_argument("--tg-webhook-url")
    parser.add_argument("--tg-webhook-bind")
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=False)
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
    saved_state = load_session_state(session_key)
    last_snapshot_id = str(saved_state.last_snapshot_id or "") if saved_state else ""
    pending_user_input = dict(saved_state.pending_user_input) if saved_state else {}
    profile = _load_profile()
    terminal_purpose = _resolve_terminal_launch_purpose(args, profile, runtime=runtime)
    if terminal_purpose == "benchmark":
        return _run_terminal_benchmark_mode(workspace_root=Path(__file__).resolve().parent.parent)

    url = _resolve_url(args, profile, required=True)
    if not url:
        return 1
    _persist_session_state(
        session_key=session_key,
        mcp_session_id=mcp_session_id,
        url=url,
    )
    control = _resolve_control_channel(args, profile)

    if runtime == "gui" and control != "telegram":
        _persist_profile(
            profile,
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            auth_method=getattr(args, "auth_method", None) or profile.get("default_openai_auth_method", "oauth"),
            url=url,
            runtime=runtime,
            control_channel=control,
            workspace=session_key,
            session_key=session_key,
            mcp_session_id=mcp_session_id,
        )
        forwarded = ["--url", url, "--control", control]
        if args.plan:
            forwarded += ["--plan", str(args.plan)]
        if args.bundle:
            forwarded += ["--bundle", str(args.bundle)]
        if args.spec:
            forwarded += ["--spec", str(args.spec)]
        if args.resume:
            forwarded += ["--resume", str(args.resume)]
        if args.mode:
            forwarded += ["--mode", str(args.mode)]
        if args.feature_query:
            forwarded += ["--feature-query", str(args.feature_query)]
        if args.max_actions is not None:
            forwarded += ["--max-actions", str(max(1, int(args.max_actions)))]
        if session_key:
            forwarded += ["--session-key", str(session_key)]
        if mcp_session_id:
            forwarded += ["--mcp-session-id", str(mcp_session_id)]
        return run_gui(forwarded)

    if control == "telegram":
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

        if runtime == "gui":
            launched = _launch_telegram_bridge_background(
                provider=provider,
                model=model,
                auth_strategy=auth_strategy,
                url=url,
                runtime=runtime,
                session_key=session_key,
                mcp_session_id=mcp_session_id,
                session_new=session_new,
                tg_mode=tg_mode,
                tg_token_file=tg_token_file,
                tg_allowlist_raw=tg_allowlist_raw,
                tg_webhook_url=tg_webhook_url,
                tg_webhook_bind=tg_webhook_bind,
            )
            if not launched:
                print("Telegram bridge 백그라운드 실행에 실패했습니다.", file=sys.stderr)
                return 1
            forwarded = ["--url", url, "--control", "telegram"]
            if args.plan:
                forwarded += ["--plan", str(args.plan)]
            if args.bundle:
                forwarded += ["--bundle", str(args.bundle)]
            if args.spec:
                forwarded += ["--spec", str(args.spec)]
            if args.resume:
                forwarded += ["--resume", str(args.resume)]
            if args.mode:
                forwarded += ["--mode", str(args.mode)]
            if args.feature_query:
                forwarded += ["--feature-query", str(args.feature_query)]
            if args.max_actions is not None:
                forwarded += ["--max-actions", str(max(1, int(args.max_actions)))]
            if session_key:
                forwarded += ["--session-key", str(session_key)]
            if mcp_session_id:
                forwarded += ["--mcp-session-id", str(mcp_session_id)]
            print("Telegram bridge를 백그라운드로 시작하고 GUI를 엽니다.")
            return run_gui(forwarded)

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
        effective_runtime = runtime
        if args.time_budget_seconds and int(args.time_budget_seconds) > 0 and runtime == "gui":
            print("time-budget 자율 모드는 terminal runtime으로 실행합니다.")
            effective_runtime = "terminal"
        return _dispatch_ai(
            effective_runtime,
            url,
            max(1, int(args.max_actions)),
            session_id=mcp_session_id,
            time_budget_seconds=(
                max(1, int(args.time_budget_seconds))
                if args.time_budget_seconds and int(args.time_budget_seconds) > 0
                else None
            ),
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
        return _dispatch_plan(url, args.plan, args.bundle, args.spec, args.resume, args.feature_query)

    if control == "local" and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
        quick_mode_map = {
            "specific": "특정 기능 테스트",
            "autonomous": "완전 자율",
        }
        default_quick = quick_mode_map.get(
            (profile.get("last_quick_mode") or "").strip().lower(),
            "특정 기능 테스트",
        )
        quick_selected = _prompt_select(
            "실행 방식을 선택하세요",
            ("특정 기능 테스트", "완전 자율"),
            default=default_quick,
        )

        if quick_selected == "특정 기능 테스트":
            feature_query = (
                str(args.feature_query).strip()
                if args.feature_query
                else _prompt_non_empty("테스트할 기능/목표")
            )
            profile["last_quick_mode"] = "specific"
            _persist_profile(
                profile,
                provider=provider,
                model=model,
                auth_strategy=auth_strategy,
                auth_method=getattr(args, "auth_method", None)
                or profile.get("default_openai_auth_method", "oauth"),
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
                feature_query,
                repl=False,
                session_id=mcp_session_id,
            )

        profile["last_quick_mode"] = "autonomous"
        if args.time_budget_seconds and int(args.time_budget_seconds) > 0:
            time_budget_seconds = max(1, int(args.time_budget_seconds))
        else:
            default_minutes = (profile.get("last_autonomous_minutes") or "30").strip() or "30"
            minutes_raw = _prompt("자율 검증 시간(분)", default=default_minutes).strip()
            try:
                minutes = max(1, int(minutes_raw))
            except Exception:
                minutes = max(1, int(default_minutes)) if default_minutes.isdigit() else 30
            profile["last_autonomous_minutes"] = str(minutes)
            time_budget_seconds = minutes * 60
        _persist_profile(
            profile,
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            auth_method=getattr(args, "auth_method", None)
            or profile.get("default_openai_auth_method", "oauth"),
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
            time_budget_seconds=time_budget_seconds,
        )

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
    gui.add_argument("--max-actions", type=int, default=50)
    gui.add_argument("--time-budget-seconds", type=int)
    gui.add_argument("--llm-provider", choices=PROVIDER_CHOICES)
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
    terminal.add_argument("--time-budget-seconds", type=int)
    terminal.add_argument("--llm-provider", choices=PROVIDER_CHOICES)
    terminal.add_argument("--llm-model")
    terminal.add_argument("--auth", choices=AUTH_CHOICES)
    terminal.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    terminal.add_argument("--runtime", choices=RUNTIME_CHOICES, default="terminal")
    terminal.add_argument("--session")
    terminal.add_argument("--new-session", action="store_true")
    parser.add_argument("--llm-provider", choices=PROVIDER_CHOICES)
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
    parser.add_argument("--time-budget-seconds", type=int)
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
            forwarded += ["--max-actions", str(parsed.max_actions)]
            if parsed.time_budget_seconds is not None:
                forwarded += ["--time-budget-seconds", str(parsed.time_budget_seconds)]
            return run_ai(forwarded)
        if parsed.mode == "plan":
            if parsed.plan:
                forwarded += ["--plan", parsed.plan]
            if parsed.spec:
                forwarded += ["--spec", parsed.spec]
            if parsed.resume:
                forwarded += ["--resume", parsed.resume]
            if parsed.feature_query:
                forwarded += ["--feature-query", parsed.feature_query]
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
            if parsed.feature_query:
                forwarded += ["--feature-query", parsed.feature_query]
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
    if parsed.time_budget_seconds is not None:
        forwarded += ["--time-budget-seconds", str(parsed.time_budget_seconds)]
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
    subparsers.add_parser("autonomous", help="Run time-budget autonomous validation mode")
    subparsers.add_parser("plan", help="Run plan/spec/resume flow")
    subparsers.add_parser("gui", help="Legacy GUI alias")
    subparsers.add_parser("terminal", help="Legacy terminal alias")
    subparsers.add_parser("prd", help="PRD bundle ingest/inspect/run")
    subparsers.add_parser("cli", help="Harness CLI family")
    subparsers.add_parser("harness", help="Run GAIA evaluation harness")
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
        if args[0] == "autonomous":
            return run_autonomous(args[1:])
        if args[0] == "plan":
            return run_plan(args[1:])
        if args[0] == "gui":
            return run_start(["gui", *args[1:]])
        if args[0] == "terminal":
            return run_terminal(args[1:])
        if args[0] == "cli":
            return run_cli(args[1:])
        if args[0] == "harness":
            from gaia.harness.cli_runtime import run_harness_cli
            return run_harness_cli(args[1:])
        if args[0] == "prd":
            return run_prd(args[1:])
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
