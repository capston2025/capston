"""Interactive chat hub for GAIA."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from gaia.src.phase4.memory.models import MemorySummaryRecord
from gaia.src.phase4.memory.store import MemoryStore


@dataclass(slots=True)
class HubContext:
    provider: str
    model: str
    auth_strategy: str
    url: str
    runtime: str = "gui"
    control_channel: str = "local"
    stop_requested: bool = False
    memory_enabled: bool = True


@dataclass(slots=True)
class CommandResult:
    code: int = 0
    status: str = "ok"
    output: str = ""


class HubSink(Protocol):
    def info(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...


class TerminalSink:
    def info(self, text: str) -> None:
        if text:
            print(text)

    def error(self, text: str) -> None:
        if text:
            print(text)


def _help_text() -> str:
    return (
        "\n사용 가능한 명령\n"
        "/help                           도움말\n"
        "/test <자연어 목표>              목표 기반 테스트 1회 실행\n"
        "/ai [max_actions]                자율 탐색 실행\n"
        "/plan                            GUI plan 모드 열기\n"
        "/plan spec <pdf-path>            GUI plan + spec 주입\n"
        "/plan plan <json-path>           GUI plan + plan 주입\n"
        "/plan resume <run-id|path>       GUI plan + resume 주입\n"
        "/url <new-url>                   대상 URL 변경\n"
        "/runtime <gui|terminal>          기본 런타임 변경\n"
        "/status                          현재 세션 상태\n"
        "/stop                            실행 중단 요청 플래그 설정\n"
        "/memory stats                    도메인 KB 통계\n"
        "/memory clear                    현재 도메인 KB 삭제\n"
        "/exit                            종료\n"
    )


def _domain_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    return (parsed.netloc or "").lower()


def _record_summary(
    memory_store: MemoryStore | None,
    *,
    context: HubContext,
    command: str,
    status: str,
    summary: str,
    metadata: dict | None = None,
) -> None:
    if not memory_store or not memory_store.enabled:
        return
    try:
        memory_store.add_dialog_summary(
            MemorySummaryRecord(
                domain=_domain_from_url(context.url),
                command=command,
                summary=summary,
                status=status,
                metadata=metadata or {},
            )
        )
    except Exception:
        return


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
    return 2


def dispatch_command(
    context: HubContext,
    raw_line: str,
    sink: HubSink,
    memory_store: MemoryStore | None = None,
) -> CommandResult:
    line = (raw_line or "").strip()
    if not line:
        return CommandResult(code=0, status="empty")
    if not line.startswith("/"):
        line = f"/test {line}"

    if line == "/exit":
        return CommandResult(code=0, status="exit", output="종료합니다.")
    if line == "/help":
        return CommandResult(code=0, output=_help_text())
    if line == "/status":
        payload = {
            "provider": context.provider,
            "model": context.model,
            "auth": context.auth_strategy,
            "url": context.url,
            "runtime": context.runtime,
            "control": context.control_channel,
            "stop_requested": context.stop_requested,
        }
        return CommandResult(code=0, output=json.dumps(payload, ensure_ascii=False, indent=2))
    if line == "/stop":
        context.stop_requested = True
        return CommandResult(
            code=0,
            output=(
                "중단 요청 플래그를 설정했습니다. "
                "실행 중 작업 강제 중단은 지원하지 않으며 다음 루프부터 반영됩니다."
            ),
        )
    if line.startswith("/url "):
        new_url = line[5:].strip()
        if not new_url:
            return CommandResult(code=2, status="error", output="URL을 입력해주세요.")
        context.url = new_url
        return CommandResult(code=0, output=f"url 변경됨: {context.url}")
    if line.startswith("/runtime "):
        runtime = line[9:].strip().lower()
        if runtime not in {"gui", "terminal"}:
            return CommandResult(code=2, status="error", output="runtime은 gui 또는 terminal만 가능합니다.")
        context.runtime = runtime
        return CommandResult(code=0, output=f"runtime 변경됨: {context.runtime}")
    if line == "/memory stats":
        if not memory_store or not memory_store.enabled:
            return CommandResult(code=2, status="error", output="KB가 비활성화되어 있습니다.")
        domain = _domain_from_url(context.url)
        if not domain:
            return CommandResult(code=2, status="error", output="현재 URL에서 도메인을 찾을 수 없습니다.")
        stats = memory_store.get_stats(domain=domain)
        return CommandResult(code=0, output=json.dumps(stats, ensure_ascii=False, indent=2))
    if line == "/memory clear":
        if not memory_store or not memory_store.enabled:
            return CommandResult(code=2, status="error", output="KB가 비활성화되어 있습니다.")
        domain = _domain_from_url(context.url)
        if not domain:
            return CommandResult(code=2, status="error", output="현재 URL에서 도메인을 찾을 수 없습니다.")
        deleted = memory_store.clear_domain(domain)
        return CommandResult(code=0, output=f"현재 도메인 KB 삭제 완료: {deleted} rows")

    if line.startswith("/test "):
        query = line[6:].strip()
        if not query:
            return CommandResult(code=2, status="error", output="테스트 목표를 입력해주세요.")
        t0 = time.time()
        code = _run_test(context, query)
        _record_summary(
            memory_store,
            context=context,
            command="/test",
            status="success" if code == 0 else "failed",
            summary=f"query={query}, exit_code={code}, duration={time.time() - t0:.2f}s",
            metadata={"query": query, "exit_code": code},
        )
        return CommandResult(code=code, output=f"실행 종료 코드: {code}")

    if line.startswith("/ai"):
        max_actions = 50
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip().isdigit():
            max_actions = max(1, int(parts[1].strip()))
        t0 = time.time()
        code = _run_ai(context, max_actions=max_actions)
        _record_summary(
            memory_store,
            context=context,
            command="/ai",
            status="success" if code == 0 else "failed",
            summary=f"max_actions={max_actions}, exit_code={code}, duration={time.time() - t0:.2f}s",
            metadata={"max_actions": max_actions, "exit_code": code},
        )
        return CommandResult(code=code, output=f"실행 종료 코드: {code}")

    if line.startswith("/plan"):
        raw = line[5:].strip()
        code = _run_plan(context, raw)
        if code == 2:
            return CommandResult(code=2, status="error", output="잘못된 /plan 형식입니다. /help를 확인하세요.")
        _record_summary(
            memory_store,
            context=context,
            command="/plan",
            status="success" if code == 0 else "failed",
            summary=f"args={raw or '(none)'}, exit_code={code}",
            metadata={"args": raw, "exit_code": code},
        )
        return CommandResult(code=code, output=f"실행 종료 코드: {code}")

    return CommandResult(code=2, status="error", output="알 수 없는 명령입니다. /help를 확인하세요.")


def run_chat_hub(context: HubContext) -> int:
    sink = TerminalSink()
    memory_store = MemoryStore(enabled=context.memory_enabled)
    if memory_store.enabled:
        try:
            memory_store.garbage_collect(retention_days=30)
        except Exception:
            pass

    sink.info("GAIA Chat Hub")
    sink.info(f"- provider: {context.provider}")
    sink.info(f"- model: {context.model}")
    sink.info(f"- auth: {context.auth_strategy}")
    sink.info(f"- url: {context.url}")
    sink.info(f"- runtime: {context.runtime}")
    sink.info(f"- control: {context.control_channel}")
    sink.info("명령어 도움말: /help")

    while True:
        try:
            line = input("gaia> ").strip()
        except EOFError:
            sink.info("")
            return 0
        except KeyboardInterrupt:
            sink.error("\n중단되었습니다.")
            return 130

        result = dispatch_command(context, line, sink, memory_store)
        if result.output:
            sink.info(result.output)
        if result.status == "exit":
            return 0
