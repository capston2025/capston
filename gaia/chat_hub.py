"""Interactive chat hub for GAIA."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol
from urllib.parse import urlparse

import requests

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
    session_id: str = "chat_hub"
    last_snapshot_id: str = ""


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
        "/snapshot                        현재 페이지 snapshot 생성\n"
        "/act <action> <ref_id> [value]   마지막 snapshot 기준 ref 액션\n"
        "/wait [selector|js|load|url ...] 복합 wait\n"
        "/tabs                            현재 탭 목록\n"
        "/console [limit]                 콘솔 로그\n"
        "/errors [limit]                  JS/page 에러 로그\n"
        "/requests [limit]                네트워크 요청/응답 로그\n"
        "/trace start|stop [path]         Playwright trace 제어\n"
        "/state get|set|clear [json]      cookies/storage 상태\n"
        "/env get|set [json]              브라우저 환경 상태\n"
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


def _mcp_execute(action: str, params: dict) -> tuple[int, dict]:
    host = (os.getenv("GAIA_MCP_HOST_URL") or "http://127.0.0.1:8001").rstrip("/")
    try:
        resp = requests.post(
            f"{host}/execute",
            json={"action": action, "params": params},
            timeout=90,
        )
    except Exception as exc:
        return 500, {"detail": str(exc)}
    try:
        data = resp.json()
    except Exception:
        data = {"detail": resp.text or "invalid_json_response"}
    return resp.status_code, data


def _parse_value(text: str):
    raw = (text or "").strip()
    if not raw:
        return None
    if raw[:1] in {"{", "[", "\""}:
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def _run_gui(*args: str) -> int:
    from gaia.main import main as launch_gui

    return launch_gui(list(args))


def _build_telegram_intervention_callback(sink: HubSink):
    def _callback(payload: dict) -> dict:
        kind = str(payload.get("kind") or "").strip().lower()
        if kind == "auth":
            sink.info(
                "추가 입력 필요: 로그인/인증 정보가 필요합니다.\n"
                "다시 실행 예시:\n"
                "/test <목표문장> username=<id_or_email> password=<pw>\n"
                "또는 브라우저에서 수동 로그인 후 같은 목표로 다시 실행하세요."
            )
        elif kind == "clarification":
            sink.info(
                "추가 입력 필요: 목표가 모호하거나 중요한 정보가 부족합니다.\n"
                "다시 실행 예시:\n"
                "/test <구체 목표> username=<id> password=<pw> email=<email>"
            )
        else:
            sink.info("추가 입력 필요: 실행에 필요한 정보가 부족합니다.")
        return {"action": "cancel", "proceed": False}

    return _callback


def _run_test(
    context: HubContext,
    query: str,
    sink: HubSink,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> tuple[int, dict]:
    if context.runtime == "gui":
        code = _run_gui("--mode", "chat", "--url", context.url, "--feature-query", query)
        return code, {
            "goal": query,
            "status": "success" if code == 0 else "failed",
            "steps": None,
            "reason": "gui mode completed",
            "duration_seconds": None,
        }

    from gaia.terminal import run_chat_terminal_once

    cb = intervention_callback
    if cb is None and context.control_channel == "telegram":
        cb = _build_telegram_intervention_callback(sink)
    code, summary = run_chat_terminal_once(
        url=context.url,
        query=query,
        intervention_callback=cb,
    )
    return code, summary


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
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
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
            "session_id": context.session_id,
            "last_snapshot_id": context.last_snapshot_id,
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

    if line == "/snapshot":
        code, data = _mcp_execute(
            "browser_snapshot",
            {"session_id": context.session_id, "url": context.url},
        )
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"snapshot 실패: {data.get('detail') or data}")
        context.last_snapshot_id = str(data.get("snapshot_id") or "")
        summary = {
            "snapshot_id": context.last_snapshot_id,
            "epoch": data.get("epoch"),
            "dom_hash": data.get("dom_hash"),
            "element_count": len(data.get("elements") or []),
            "tab_id": data.get("tab_id"),
        }
        return CommandResult(code=0, output=json.dumps(summary, ensure_ascii=False, indent=2))

    if line.startswith("/act "):
        parts = line.split(maxsplit=4)
        if len(parts) < 3:
            return CommandResult(code=2, status="error", output="형식: /act <action> <ref_id> [value]")
        act = parts[1].strip()
        snapshot_id = context.last_snapshot_id
        ref_id = parts[2].strip()
        value = parts[3] if len(parts) >= 4 else None

        if ref_id and not ref_id.startswith("t") and len(parts) >= 4:
            snapshot_id = ref_id
            ref_id = parts[3].strip()
            value = parts[4] if len(parts) >= 5 else None

        if not snapshot_id:
            return CommandResult(code=2, status="error", output="snapshot_id가 없습니다. 먼저 /snapshot 실행하세요.")
        if not ref_id:
            return CommandResult(code=2, status="error", output="ref_id가 필요합니다.")

        code, data = _mcp_execute(
            "browser_act",
            {
                "session_id": context.session_id,
                "snapshot_id": snapshot_id,
                "ref_id": ref_id,
                "action": act,
                "value": _parse_value(value or ""),
                "url": context.url,
                "verify": True,
            },
        )
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"act 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/wait"):
        args = line.split(maxsplit=2)
        payload: dict = {"session_id": context.session_id}
        if len(args) >= 3:
            mode = args[1].strip().lower()
            value = args[2].strip()
            if mode == "selector":
                payload["selector"] = value
            elif mode == "js":
                payload["js"] = value
            elif mode == "load":
                payload["load_state"] = value
            elif mode == "url":
                payload["url"] = value
            elif mode == "timeout":
                try:
                    payload["timeout_ms"] = int(value)
                except Exception:
                    return CommandResult(code=2, status="error", output="timeout은 숫자(ms)여야 합니다.")
            else:
                return CommandResult(code=2, status="error", output="형식: /wait [selector|js|load|url|timeout] ...")
        code, data = _mcp_execute("browser_wait", payload)
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"wait 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line == "/tabs":
        code, data = _mcp_execute("browser_tabs", {"session_id": context.session_id})
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"tabs 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/console"):
        parts = line.split(maxsplit=1)
        limit = 50
        if len(parts) == 2 and parts[1].strip().isdigit():
            limit = max(1, int(parts[1].strip()))
        code, data = _mcp_execute("browser_console_get", {"session_id": context.session_id, "limit": limit})
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"console 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/errors"):
        parts = line.split(maxsplit=1)
        limit = 50
        if len(parts) == 2 and parts[1].strip().isdigit():
            limit = max(1, int(parts[1].strip()))
        code, data = _mcp_execute("browser_errors_get", {"session_id": context.session_id, "limit": limit})
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"errors 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/requests"):
        parts = line.split(maxsplit=1)
        limit = 50
        if len(parts) == 2 and parts[1].strip().isdigit():
            limit = max(1, int(parts[1].strip()))
        code, data = _mcp_execute("browser_requests_get", {"session_id": context.session_id, "limit": limit})
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"requests 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/trace "):
        parts = line.split(maxsplit=2)
        mode = parts[1].strip().lower() if len(parts) >= 2 else ""
        if mode == "start":
            code, data = _mcp_execute("browser_trace_start", {"session_id": context.session_id})
        elif mode == "stop":
            payload = {"session_id": context.session_id}
            if len(parts) >= 3 and parts[2].strip():
                payload["path"] = parts[2].strip()
            code, data = _mcp_execute("browser_trace_stop", payload)
        else:
            return CommandResult(code=2, status="error", output="형식: /trace start|stop [path]")
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"trace 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/state"):
        parts = line.split(maxsplit=2)
        op = parts[1].strip().lower() if len(parts) >= 2 else "get"
        payload: dict = {"session_id": context.session_id, "op": op}
        if op in {"set", "clear"} and len(parts) >= 3:
            parsed = _parse_value(parts[2])
            payload["state"] = parsed if isinstance(parsed, dict) else {}
        elif op not in {"get", "set", "clear"}:
            return CommandResult(code=2, status="error", output="형식: /state get|set|clear [json]")
        code, data = _mcp_execute("browser_state", payload)
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"state 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/env"):
        parts = line.split(maxsplit=2)
        op = parts[1].strip().lower() if len(parts) >= 2 else "get"
        payload: dict = {"session_id": context.session_id, "op": op}
        if op == "set":
            if len(parts) < 3:
                return CommandResult(code=2, status="error", output="형식: /env set <json>")
            parsed = _parse_value(parts[2])
            if not isinstance(parsed, dict):
                return CommandResult(code=2, status="error", output="/env set은 JSON object만 허용합니다.")
            payload["env"] = parsed
        elif op != "get":
            return CommandResult(code=2, status="error", output="형식: /env get|set [json]")
        code, data = _mcp_execute("browser_env", payload)
        if code >= 400:
            return CommandResult(code=code, status="error", output=f"env 실패: {data.get('detail') or data}")
        return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2))

    if line.startswith("/test "):
        query = line[6:].strip()
        if not query:
            return CommandResult(code=2, status="error", output="테스트 목표를 입력해주세요.")
        t0 = time.time()
        code, detail = _run_test(context, query, sink, intervention_callback=intervention_callback)
        _record_summary(
            memory_store,
            context=context,
            command="/test",
            status="success" if code == 0 else "failed",
            summary=f"query={query}, exit_code={code}, duration={time.time() - t0:.2f}s, result={detail}",
            metadata={"query": query, "exit_code": code, "result": detail},
        )
        lines = [
            "실행 결과",
            f"goal: {detail.get('goal') or query}",
            f"status: {detail.get('status') or ('success' if code == 0 else 'failed')}",
        ]
        if detail.get("steps") is not None:
            lines.append(f"steps: {detail.get('steps')}")
        if detail.get("reason"):
            lines.append(f"reason: {detail.get('reason')}")
        if detail.get("duration_seconds") is not None:
            lines.append(f"duration: {detail.get('duration_seconds')}s")
        auth = detail.get("auth")
        if isinstance(auth, dict) and auth:
            lines.append("auth:")
            if auth.get("auth_mode"):
                lines.append(f"  mode: {auth.get('auth_mode')}")
            if auth.get("username"):
                lines.append(f"  username: {auth.get('username')}")
            if auth.get("email"):
                lines.append(f"  email: {auth.get('email')}")
            if auth.get("password"):
                lines.append(f"  password: {auth.get('password')}")
            if auth.get("department"):
                lines.append(f"  department: {auth.get('department')}")
            if auth.get("grade_year"):
                lines.append(f"  grade_year: {auth.get('grade_year')}")
        lines.append(f"exit_code: {code}")
        return CommandResult(code=code, output="\n".join(lines))

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
