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
import termios
import time
import tty
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Sequence

from gaia import auth as gaia_auth


PROFILE_PATH = Path.home() / ".gaia" / "cli_profile.json"
AUTH_CHOICES = ("reuse", "fresh")
RUNTIME_CHOICES = ("gui", "terminal")
MODE_CHOICES = ("chat", "ai", "plan")
OPENAI_AUTH_METHOD_CHOICES = ("oauth", "manual")

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


def _resolve_mcp_target() -> tuple[str, int]:
    raw_url = (os.getenv("MCP_HOST_URL", "http://127.0.0.1:8001") or "").strip()
    if "://" not in raw_url:
        raw_url = f"http://{raw_url}"
    parsed = urllib.parse.urlparse(raw_url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if parsed.scheme == "https" else 80
    return host, int(port)


def _is_mcp_ready(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
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

    host, port = _resolve_mcp_target()
    if _is_mcp_ready(host, port):
        return True

    if _MCP_HOST_PROCESS and _MCP_HOST_PROCESS.poll() is None:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if _is_mcp_ready(host, port):
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
        if _is_mcp_ready(host, port):
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
) -> None:
    profile["provider"] = provider
    profile["model"] = model
    profile["default_auth_strategy"] = auth_strategy
    if provider == "openai" and auth_method in OPENAI_AUTH_METHOD_CHOICES:
        profile["default_openai_auth_method"] = auth_method
    profile["last_runtime"] = runtime
    if url:
        profile["last_url"] = url
    _save_profile(profile)


def _configure_session(parsed: argparse.Namespace, *, require_url: bool) -> tuple[str, str, str, str | None, str] | None:
    profile = _load_profile()
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
    )
    return provider, model, auth_strategy, url, runtime


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


def _dispatch_chat(runtime: str, url: str, feature_query: str | None, repl: bool) -> int:
    if runtime == "gui":
        forwarded = ["--mode", "chat", "--url", url]
        if feature_query:
            forwarded += ["--feature-query", feature_query]
        return run_gui(forwarded)

    from gaia.terminal import run_chat_terminal

    return run_chat_terminal(url=url, initial_query=feature_query, repl=repl)


def _dispatch_ai(runtime: str, url: str, max_actions: int) -> int:
    if runtime == "gui":
        return run_gui(["--mode", "ai", "--url", url])

    from gaia.terminal import run_ai_terminal

    return run_ai_terminal(url=url, max_actions=max_actions)


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
    _, _, _, url, runtime = configured
    assert url is not None
    return _dispatch_chat(runtime, url, args.feature_query, repl=not args.once)


def run_ai(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia ai", "Run autonomous exploratory mode.")
    parser.add_argument("--max-actions", type=int, default=50)
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    _, _, _, url, runtime = configured
    assert url is not None
    return _dispatch_ai(runtime, url, max(1, int(args.max_actions)))


def run_plan(argv: Sequence[str] | None = None) -> int:
    parser = _build_common_parser("gaia plan", "Run plan/spec/resume flow (GUI first).")
    parser.add_argument("--plan")
    parser.add_argument("--spec")
    parser.add_argument("--resume")
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=False)
    if not configured:
        return 1
    _, _, _, url, runtime = configured
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
    args = parser.parse_args(list(argv or []))

    configured = _configure_session(args, require_url=True)
    if not configured:
        return 1
    provider, model, auth_strategy, url, runtime = configured
    assert url is not None

    if args.mode == "chat":
        return _dispatch_chat(runtime, url, args.feature_query, repl=True)
    if args.mode == "ai":
        return _dispatch_ai(runtime, url, max(1, int(args.max_actions)))
    if args.mode == "plan":
        return _dispatch_plan(url, args.plan, args.spec, args.resume)

    from gaia.chat_hub import HubContext, run_chat_hub

    return run_chat_hub(
        HubContext(
            provider=provider,
            model=model,
            auth_strategy=auth_strategy,
            url=url,
            runtime=runtime,
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
    parser.add_argument("--llm-provider", choices=("openai", "gemini"))
    parser.add_argument("--llm-model")
    parser.add_argument("--auth", choices=AUTH_CHOICES)
    parser.add_argument("--auth-method", choices=("auto", "oauth", "manual"))
    parser.add_argument("--url")
    parser.add_argument("--runtime", choices=RUNTIME_CHOICES)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--terminal", action="store_true")
    parser.add_argument("--mode", choices=MODE_CHOICES)
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
    if parsed.mode:
        forwarded += ["--mode", parsed.mode]
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
