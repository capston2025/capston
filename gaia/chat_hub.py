"""Interactive chat hub for GAIA."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class HubContext:
    provider: str
    model: str
    auth_strategy: str
    url: str
    runtime: str = "gui"


def _print_help() -> None:
    print("\n사용 가능한 명령")
    print("/help                           도움말")
    print("/test <자연어 목표>              목표 기반 테스트 1회 실행")
    print("/ai [max_actions]                자율 탐색 실행")
    print("/plan                            GUI plan 모드 열기")
    print("/plan spec <pdf-path>            GUI plan + spec 주입")
    print("/plan plan <json-path>           GUI plan + plan 주입")
    print("/plan resume <run-id|path>       GUI plan + resume 주입")
    print("/url <new-url>                   대상 URL 변경")
    print("/runtime <gui|terminal>          기본 런타임 변경")
    print("/exit                            종료\n")


def _run_gui(*args: str) -> int:
    from gaia.main import main as launch_gui

    return launch_gui(list(args))


def _run_test(context: HubContext, query: str) -> int:
    if context.runtime == "gui":
        return _run_gui("--mode", "chat", "--url", context.url, "--feature-query", query)

    from gaia.terminal import run_chat_terminal

    return run_chat_terminal(url=context.url, initial_query=query, repl=False)


def _run_ai(context: HubContext, max_actions: int = 50) -> int:
    if context.runtime == "gui":
        return _run_gui("--mode", "ai", "--url", context.url)

    from gaia.terminal import run_ai_terminal

    return run_ai_terminal(url=context.url, max_actions=max_actions)


def _run_plan(context: HubContext, raw: str) -> int:
    # plan은 GUI 우선 실행
    parts = raw.strip().split(maxsplit=2)
    forwarded = ["--mode", "plan", "--url", context.url]
    if not parts:
        return _run_gui(*forwarded)

    if len(parts) == 2 and parts[0] in {"spec", "plan", "resume"}:
        key = parts[0]
        value = parts[1]
        if key == "spec":
            forwarded += ["--spec", value]
        elif key == "plan":
            forwarded += ["--plan", value]
        else:
            forwarded += ["--resume", value]
        return _run_gui(*forwarded)

    print("잘못된 /plan 형식입니다. /help를 확인하세요.")
    return 2


def run_chat_hub(context: HubContext) -> int:
    print("GAIA Chat Hub")
    print(f"- provider: {context.provider}")
    print(f"- model: {context.model}")
    print(f"- auth: {context.auth_strategy}")
    print(f"- url: {context.url}")
    print(f"- runtime: {context.runtime}")
    print("명령어 도움말: /help")

    while True:
        try:
            line = input("gaia> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\n중단되었습니다.")
            return 130

        if not line:
            continue
        if not line.startswith("/"):
            # 자연어 입력은 /test로 처리
            code = _run_test(context, line)
            if code not in {0, 130}:
                print(f"실행 종료 코드: {code}")
            continue

        if line == "/exit":
            return 0
        if line == "/help":
            _print_help()
            continue
        if line.startswith("/url "):
            new_url = line[5:].strip()
            if not new_url:
                print("URL을 입력해주세요.")
                continue
            context.url = new_url
            print(f"url 변경됨: {context.url}")
            continue
        if line.startswith("/runtime "):
            runtime = line[9:].strip().lower()
            if runtime not in {"gui", "terminal"}:
                print("runtime은 gui 또는 terminal만 가능합니다.")
                continue
            context.runtime = runtime
            print(f"runtime 변경됨: {context.runtime}")
            continue
        if line.startswith("/test "):
            query = line[6:].strip()
            if not query:
                print("테스트 목표를 입력해주세요.")
                continue
            code = _run_test(context, query)
            if code not in {0, 130}:
                print(f"실행 종료 코드: {code}")
            continue
        if line.startswith("/ai"):
            max_actions = 50
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip().isdigit():
                max_actions = max(1, int(parts[1].strip()))
            code = _run_ai(context, max_actions=max_actions)
            if code not in {0, 130}:
                print(f"실행 종료 코드: {code}")
            continue
        if line.startswith("/plan"):
            raw = line[5:].strip()
            code = _run_plan(context, raw)
            if code not in {0, 130}:
                print(f"실행 종료 코드: {code}")
            continue

        print("알 수 없는 명령입니다. /help를 확인하세요.")
