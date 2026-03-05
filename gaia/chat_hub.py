"""Interactive chat hub for GAIA."""
from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, TextIO
from urllib import request as urllib_request
from urllib.parse import urlparse

import requests

from gaia.src.phase4.memory.models import MemorySummaryRecord
from gaia.src.phase4.memory.store import MemoryStore
from gaia.src.phase4.session import WORKSPACE_DEFAULT, allocate_session_id, load_session_state


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
    workspace: str = WORKSPACE_DEFAULT
    session_key: str = WORKSPACE_DEFAULT
    session_id: str = WORKSPACE_DEFAULT
    session_new: bool = False
    last_snapshot_id: str = ""
    pending_user_input: Dict[str, Any] = field(default_factory=dict)
    pending_user_response: Dict[str, Any] = field(default_factory=dict)
    on_session_update: Optional[Callable[["HubContext"], None]] = None


@dataclass(slots=True)
class CommandResult:
    code: int = 0
    status: str = "ok"
    output: str = ""
    attachments: list[dict] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


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


_MCP_HOST_PROCESS: Optional[subprocess.Popen[str]] = None
_MCP_HOST_LOG_FILE: Optional[TextIO] = None
_MCP_HOST_CLEANUP_REGISTERED = False


def _help_text() -> str:
    return (
        "\n사용 가능한 명령\n"
        "/help                           도움말\n"
        "/test <자연어 목표>              목표 기반 테스트 1회 실행\n"
        "/ai [max_actions]                자율 탐색 실행\n"
        "/autonomous [minutes]            시간 예산 기반 자율 사이트 검증\n"
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
        "/session                         세션 상태 조회\n"
        "/session new                     새 세션 발급\n"
        "/session reuse <key>             세션 키 재사용/전환\n"
        "/handoff                         pending 사용자 요청 조회\n"
        "/handoff key=value ...           pending 요청 응답 등록\n"
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


def _capture_session_screenshot_attachment(session_id: str) -> dict | None:
    code, data = _mcp_execute(
        "browser_screenshot",
        {
            "session_id": session_id,
            "full_page": False,
            "type": "png",
        },
    )
    if code >= 400:
        return None
    screenshot = data.get("screenshot")
    if not isinstance(screenshot, str) or not screenshot.strip():
        return None
    payload: dict[str, Any] = {
        "kind": "image_base64",
        "mime": "image/png",
        "data": screenshot,
    }
    saved_path = data.get("path")
    if isinstance(saved_path, str) and saved_path.strip():
        payload["path"] = saved_path
    return payload


def _resolve_mcp_target() -> tuple[str, int, str]:
    raw = str(
        os.getenv("GAIA_MCP_HOST_URL")
        or os.getenv("MCP_HOST_URL")
        or "http://127.0.0.1:8001"
    ).strip()
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


def _is_mcp_ready(host: str, port: int, base_url: str, timeout: float = 0.8) -> bool:
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

    # 포트가 열려 있는데 health가 다른 형태면 타 서비스가 쓰고 있는 것으로 보고 중단한다.
    if _is_tcp_open(host, port) and not _is_mcp_ready(host, port, base_url):
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
    log_path = log_dir / "mcp_host.chat_hub.log"
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
            return True
        if _MCP_HOST_PROCESS.poll() is not None:
            break
        time.sleep(0.2)

    _stop_spawned_mcp_host()
    return False


def _mcp_execute(action: str, params: dict) -> tuple[int, dict]:
    host = (
        os.getenv("GAIA_MCP_HOST_URL")
        or os.getenv("MCP_HOST_URL")
        or "http://127.0.0.1:8001"
    ).rstrip("/")
    if not _ensure_mcp_host_running():
        return 500, {"detail": "mcp_host_unavailable"}
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


def _normalize_result_status(result: CommandResult) -> str:
    status = str(result.status or "").strip().lower()
    if status in {"exit", "empty"}:
        return status
    if status in {"error", "failed", "success"}:
        return status
    return "success" if int(result.code or 0) == 0 else "failed"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _build_reason_code_summary(detail: Dict[str, Any]) -> Dict[str, int]:
    summary = detail.get("reason_code_summary")
    if isinstance(summary, dict):
        out: Dict[str, int] = {}
        for k, v in summary.items():
            key = str(k or "").strip()
            if not key:
                continue
            out[key] = _as_int(v)
        if out:
            return out
    reason_code = str(detail.get("reason_code") or "").strip()
    if reason_code:
        return {reason_code: 1}
    return {}


def _short_cell(value: Any, limit: int = 52) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _check_status_label(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token == "passed":
        return "PASS"
    if token == "failed":
        return "FAIL"
    if token == "skipped":
        return "SKIP"
    if token:
        return token.upper()
    return "-"


def _build_validation_table(validation_checks: list[Any]) -> Dict[str, Any]:
    columns = ["step", "status", "name", "action", "input", "error"]
    rows: list[Dict[str, Any]] = []
    for row in validation_checks:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "step": row.get("step"),
                "status": _check_status_label(row.get("status")),
                "name": _short_cell(row.get("name") or "unnamed_check"),
                "action": _short_cell(row.get("action") or "-"),
                "input": _short_cell(row.get("input_value") or "-"),
                "error": _short_cell(row.get("error") or "-"),
            }
        )
    return {"columns": columns, "rows": rows, "total_rows": len(rows)}


def _build_validation_table_markdown(table: Dict[str, Any], max_rows: int = 10) -> str:
    columns = table.get("columns") if isinstance(table, dict) else None
    rows = table.get("rows") if isinstance(table, dict) else None
    if not isinstance(columns, list) or not isinstance(rows, list) or not columns:
        return ""

    safe_columns = [str(col or "").strip() for col in columns]

    def _esc(text: Any) -> str:
        return _short_cell(text).replace("|", "\\|")

    header = "| " + " | ".join(safe_columns) + " |"
    sep = "| " + " | ".join(["---"] * len(safe_columns)) + " |"
    lines = [header, sep]
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        cells = [_esc(row.get(col, "")) for col in safe_columns]
        lines.append("| " + " | ".join(cells) + " |")
    hidden = len(rows) - min(len(rows), max_rows)
    if hidden > 0:
        lines.append(f"_... +{hidden} more rows_")
    return "\n".join(lines)


def build_command_payload(
    context: HubContext,
    raw_command: str,
    result: CommandResult,
) -> Dict[str, Any]:
    data = result.data if isinstance(result.data, dict) else {}
    command = str(raw_command or "").strip()
    status = _normalize_result_status(result)

    payload: Dict[str, Any] = {
        "command": command,
        "status": str(data.get("status") or status),
        "goal": str(data.get("goal") or ""),
        "steps": _as_int(data.get("steps")),
        "reason": str(data.get("reason") or ""),
        "duration": _as_float(data.get("duration")),
        "reason_code_summary": (
            data.get("reason_code_summary")
            if isinstance(data.get("reason_code_summary"), dict)
            else {}
        ),
        "attachments": list(result.attachments or []),
        "exit_code": _as_int(result.code),
        "runtime": context.runtime,
        "url": context.url,
        "session_id": context.session_id,
    }
    if result.output:
        payload["output"] = result.output
    validation_summary = data.get("validation_summary")
    if isinstance(validation_summary, dict):
        payload["validation_summary"] = validation_summary
    validation_checks = data.get("validation_checks")
    if isinstance(validation_checks, list):
        payload["validation_checks"] = validation_checks
        table = _build_validation_table(validation_checks)
        payload["validation_table"] = table
        payload["validation_table_markdown"] = _build_validation_table_markdown(table)
    verification_report = data.get("verification_report")
    if isinstance(verification_report, dict):
        payload["verification_report"] = verification_report
    if not payload["goal"] and command.startswith("/test "):
        payload["goal"] = command[6:].strip()
    return payload


def _notify_session_update(context: HubContext) -> None:
    callback = context.on_session_update
    if not callback:
        return
    try:
        callback(context)
    except Exception:
        return


def _parse_kv_tokens(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for token in str(raw or "").split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        k = key.strip()
        v = value.strip().strip('"').strip("'")
        if k and v:
            out[k] = v
    return out


def _build_hub_intervention_callback(context: HubContext, sink: HubSink):
    def _callback(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        context.pending_user_input = dict(payload or {})
        _notify_session_update(context)
        if context.pending_user_response:
            response = dict(context.pending_user_response)
            context.pending_user_response = {}
            context.pending_user_input = {}
            _notify_session_update(context)
            return response

        question = str((payload or {}).get("question") or "").strip()
        if not question:
            question = "추가 입력이 필요합니다."
        sink.info(
            "추가 입력 요청이 대기 중입니다.\n"
            f"- {question}\n"
            "응답: /handoff key=value ...\n"
            "예시: /handoff username=user123 password=pass123 proceed=true"
        )
        return {"action": "cancel", "proceed": False}

    return _callback


def _handle_session_command(context: HubContext, raw: str) -> CommandResult:
    parts = [p for p in raw.split() if p]
    if len(parts) == 1:
        payload = {
            "workspace": context.workspace,
            "session_key": context.session_key,
            "session_id": context.session_id,
            "session_new": context.session_new,
            "last_snapshot_id": context.last_snapshot_id,
        }
        return CommandResult(code=0, output=json.dumps(payload, ensure_ascii=False, indent=2))

    if len(parts) >= 2 and parts[1] == "new":
        context.session_id = allocate_session_id(context.session_key or context.workspace or WORKSPACE_DEFAULT)
        context.session_new = True
        context.last_snapshot_id = ""
        context.pending_user_input = {}
        context.pending_user_response = {}
        _notify_session_update(context)
        return CommandResult(code=0, output=f"새 세션 발급: {context.session_id}")

    if len(parts) >= 3 and parts[1] == "reuse":
        key = str(parts[2]).strip()
        if not key:
            return CommandResult(code=2, status="error", output="형식: /session reuse <key>")
        loaded = load_session_state(key)
        context.session_key = key
        context.workspace = key
        context.session_id = loaded.mcp_session_id if loaded and loaded.mcp_session_id else key
        context.session_new = False
        context.last_snapshot_id = str(loaded.last_snapshot_id or "") if loaded else ""
        context.pending_user_input = dict(loaded.pending_user_input) if loaded else {}
        context.pending_user_response = {}
        _notify_session_update(context)
        return CommandResult(code=0, output=f"세션 재사용: key={context.session_key}, id={context.session_id}")

    return CommandResult(code=2, status="error", output="형식: /session | /session new | /session reuse <key>")


def _handle_handoff_command(context: HubContext, raw: str) -> CommandResult:
    parts = raw.split(maxsplit=1)
    if len(parts) == 1:
        if not context.pending_user_input:
            return CommandResult(code=0, output="대기 중인 사용자 입력 요청이 없습니다.")
        return CommandResult(
            code=0,
            output=(
                "대기 중인 요청:\n"
                + json.dumps(context.pending_user_input, ensure_ascii=False, indent=2)
            ),
        )

    response = _parse_kv_tokens(parts[1])
    if not response:
        return CommandResult(code=2, status="error", output="형식: /handoff key=value ...")
    if "proceed" not in response and "action" not in response:
        response["proceed"] = "true"
    context.pending_user_response = response
    _notify_session_update(context)
    return CommandResult(code=0, output="handoff 응답이 저장되었습니다. 다음 실행에서 자동 반영됩니다.")


def _run_gui(*args: str) -> int:
    from gaia.main import main as launch_gui

    return launch_gui(list(args))


def _build_telegram_intervention_callback(context: HubContext, sink: HubSink):
    def _callback(payload: dict) -> dict:
        context.pending_user_input = dict(payload or {})
        _notify_session_update(context)
        if context.pending_user_response:
            response = dict(context.pending_user_response)
            context.pending_user_response = {}
            context.pending_user_input = {}
            _notify_session_update(context)
            return response
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


def _build_ai_intervention_callback(context: HubContext, sink: HubSink):
    def _callback(reason: str, current_url: str) -> dict:
        payload: dict[str, Any] = {
            "kind": "auth",
            "reason": str(reason or "").strip(),
            "url": str(current_url or "").strip(),
            "question": "로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요.",
            "fields": [
                "proceed",
                "auth_mode",
                "manual_done",
                "username",
                "email",
                "password",
            ],
        }
        context.pending_user_input = dict(payload)
        _notify_session_update(context)
        if context.pending_user_response:
            response = dict(context.pending_user_response)
            context.pending_user_response = {}
            context.pending_user_input = {}
            _notify_session_update(context)
            return response
        sink.info(
            "로그인 요청왔는데 어떻게 할까요? 아이디 비밀번호를 알려주세요.\n"
            "응답 예시:\n"
            "/handoff proceed=true username=<id_or_email> password=<pw>\n"
            "또는 /handoff proceed=true auth_mode=signup\n"
            "또는 수동 로그인 후 /handoff proceed=true manual_done=true"
        )
        return {"action": "cancel", "proceed": False}

    return _callback


def _run_test(
    context: HubContext,
    query: str,
    sink: HubSink,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> tuple[int, dict]:
    if not _ensure_mcp_host_running():
        sink.error("MCP host를 시작할 수 없습니다. /health 확인 후 다시 시도하세요.")
        return 1, {
            "goal": query,
            "status": "failed",
            "steps": 0,
            "reason": "mcp_host_unavailable",
            "duration_seconds": 0.0,
        }

    runtime = context.runtime
    if context.control_channel == "telegram" and runtime == "gui":
        runtime = "terminal"
        sink.info("telegram 채널에서는 GUI 대신 terminal runtime으로 실행합니다.")

    if runtime == "gui":
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
    if cb is None:
        if context.control_channel == "telegram":
            cb = _build_telegram_intervention_callback(context, sink)
        else:
            cb = _build_hub_intervention_callback(context, sink)
    code, summary = run_chat_terminal_once(
        url=context.url,
        query=query,
        session_id=context.session_id,
        intervention_callback=cb,
    )
    return code, summary


def _run_ai(
    context: HubContext,
    sink: HubSink,
    max_actions: int = 50,
    *,
    time_budget_seconds: int | None = None,
) -> tuple[int, dict]:
    if not _ensure_mcp_host_running():
        sink.error("MCP host를 시작할 수 없습니다. /health 확인 후 다시 시도하세요.")
        return 1, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": "mcp_host_unavailable",
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }

    runtime = "terminal" if context.control_channel == "telegram" else context.runtime
    if runtime == "gui":
        if time_budget_seconds and int(time_budget_seconds) > 0:
            runtime = "terminal"
        else:
            code = _run_gui("--mode", "ai", "--url", context.url, "--max-actions", str(max_actions))
            return code, {
                "goal": "autonomous_exploration",
                "status": "success" if code == 0 else "failed",
                "steps": 0,
                "reason": "gui mode completed",
                "duration_seconds": 0.0,
                "reason_code_summary": {},
                "validation_summary": {},
                "validation_checks": [],
                "verification_report": {},
            }

    from gaia.terminal import run_ai_terminal_with_summary

    intervention_cb = _build_ai_intervention_callback(context, sink=sink)
    return run_ai_terminal_with_summary(
        url=context.url,
        max_actions=max_actions,
        session_id=context.session_id,
        time_budget_seconds=time_budget_seconds,
        intervention_callback=intervention_cb,
    )


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
            "workspace": context.workspace,
            "session_key": context.session_key,
            "stop_requested": context.stop_requested,
            "session_id": context.session_id,
            "last_snapshot_id": context.last_snapshot_id,
            "pending_user_input": bool(context.pending_user_input),
        }
        return CommandResult(code=0, output=json.dumps(payload, ensure_ascii=False, indent=2))
    if line.startswith("/session"):
        return _handle_session_command(context, line)
    if line.startswith("/handoff"):
        return _handle_handoff_command(context, line)
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
        _notify_session_update(context)
        return CommandResult(code=0, output=f"url 변경됨: {context.url}")
    if line.startswith("/runtime "):
        runtime = line[9:].strip().lower()
        if runtime not in {"gui", "terminal"}:
            return CommandResult(code=2, status="error", output="runtime은 gui 또는 terminal만 가능합니다.")
        context.runtime = runtime
        _notify_session_update(context)
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
        _notify_session_update(context)
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
        snapshot_used = str(data.get("snapshot_id_used") or "")
        if snapshot_used and snapshot_used != context.last_snapshot_id:
            context.last_snapshot_id = snapshot_used
            _notify_session_update(context)
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
        status_text = str(detail.get("status") or ("success" if code == 0 else "failed"))
        duration_value = _as_float(detail.get("duration_seconds"))
        reason_summary = _build_reason_code_summary(detail)
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
        validation_summary = (
            detail.get("validation_summary")
            if isinstance(detail.get("validation_summary"), dict)
            else {}
        )
        validation_checks = (
            detail.get("validation_checks")
            if isinstance(detail.get("validation_checks"), list)
            else []
        )
        if validation_summary:
            lines.append("validation:")
            lines.append(f"  total: {validation_summary.get('total_checks', 0)}")
            lines.append(f"  passed: {validation_summary.get('passed_checks', 0)}")
            lines.append(f"  failed: {validation_summary.get('failed_checks', 0)}")
            lines.append(f"  success_rate: {validation_summary.get('success_rate', 0)}%")
        if validation_checks:
            lines.append("checks:")
            for check in validation_checks[:8]:
                if not isinstance(check, dict):
                    continue
                status_token = str(check.get("status") or "").strip().lower()
                status_label = "PASS" if status_token == "passed" else ("FAIL" if status_token == "failed" else "SKIP")
                name = str(check.get("name") or "unnamed_check").strip()
                step_no = check.get("step")
                lines.append(f"  - [{status_label}] step={step_no} {name}")
            if len(validation_checks) > 8:
                lines.append(f"  - ... +{len(validation_checks) - 8} more")
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
        attachments: list[dict] = []
        if context.control_channel == "telegram":
            shot = _capture_session_screenshot_attachment(context.session_id)
            if shot is not None:
                attachments.append(shot)
        return CommandResult(
            code=code,
            output="\n".join(lines),
            attachments=attachments,
            data={
                "goal": detail.get("goal") or query,
                "status": status_text,
                "steps": detail.get("steps"),
                "reason": detail.get("reason") or "",
                "duration": duration_value,
                "reason_code_summary": reason_summary,
                "validation_summary": validation_summary,
                "validation_checks": validation_checks,
                "verification_report": (
                    detail.get("verification_report")
                    if isinstance(detail.get("verification_report"), dict)
                    else {}
                ),
            },
        )

    if line.startswith("/ai"):
        time_budget_seconds: int | None = None
        max_actions = 50
        parts = line.split()
        if len(parts) == 2 and parts[1].strip().isdigit():
            max_actions = max(1, int(parts[1].strip()))
        elif len(parts) >= 3 and parts[1].strip().lower() in {"time", "auto", "autonomous"} and parts[2].strip().isdigit():
            time_budget_seconds = max(60, int(parts[2].strip()))
        t0 = time.time()
        code, ai_summary = _run_ai(
            context,
            sink=sink,
            max_actions=max_actions,
            time_budget_seconds=time_budget_seconds,
        )
        _record_summary(
            memory_store,
            context=context,
            command="/ai",
            status="success" if code == 0 else "failed",
            summary=(
                f"max_actions={max_actions}, time_budget_seconds={time_budget_seconds}, "
                f"exit_code={code}, duration={time.time() - t0:.2f}s"
            ),
            metadata={
                "max_actions": max_actions,
                "time_budget_seconds": time_budget_seconds,
                "exit_code": code,
            },
        )
        return CommandResult(
            code=code,
            output=f"실행 종료 코드: {code}",
            data={
                "goal": "autonomous_exploration",
                "status": str(ai_summary.get("status") or ("success" if code == 0 else "failed")),
                "steps": _as_int(ai_summary.get("steps")),
                "reason": str(ai_summary.get("reason") or ""),
                "duration": _as_float(ai_summary.get("duration_seconds") or (time.time() - t0)),
                "reason_code_summary": (
                    ai_summary.get("reason_code_summary")
                    if isinstance(ai_summary.get("reason_code_summary"), dict)
                    else {}
                ),
                "validation_summary": (
                    ai_summary.get("validation_summary")
                    if isinstance(ai_summary.get("validation_summary"), dict)
                    else {}
                ),
                "validation_checks": (
                    ai_summary.get("validation_checks")
                    if isinstance(ai_summary.get("validation_checks"), list)
                    else []
                ),
                "verification_report": (
                    ai_summary.get("verification_report")
                    if isinstance(ai_summary.get("verification_report"), dict)
                    else {}
                ),
            },
        )

    if line.startswith("/autonomous"):
        minutes = 30
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip().isdigit():
            minutes = max(1, int(parts[1].strip()))
        time_budget_seconds = max(60, minutes * 60)
        t0 = time.time()
        code, ai_summary = _run_ai(
            context,
            sink=sink,
            max_actions=10_000_000,
            time_budget_seconds=time_budget_seconds,
        )
        _record_summary(
            memory_store,
            context=context,
            command="/autonomous",
            status="success" if code == 0 else "failed",
            summary=(
                f"time_budget_seconds={time_budget_seconds}, exit_code={code}, "
                f"duration={time.time() - t0:.2f}s"
            ),
            metadata={
                "time_budget_seconds": time_budget_seconds,
                "exit_code": code,
            },
        )
        return CommandResult(
            code=code,
            output=(
                f"자율 사이트 검증 실행 종료 코드: {code}\n"
                f"time_budget_seconds: {time_budget_seconds}"
            ),
            data={
                "goal": "time_budget_autonomous_validation",
                "status": str(ai_summary.get("status") or ("success" if code == 0 else "failed")),
                "steps": _as_int(ai_summary.get("steps")),
                "reason": str(ai_summary.get("reason") or ""),
                "duration": _as_float(ai_summary.get("duration_seconds") or (time.time() - t0)),
                "reason_code_summary": (
                    ai_summary.get("reason_code_summary")
                    if isinstance(ai_summary.get("reason_code_summary"), dict)
                    else {}
                ),
                "validation_summary": (
                    ai_summary.get("validation_summary")
                    if isinstance(ai_summary.get("validation_summary"), dict)
                    else {}
                ),
                "validation_checks": (
                    ai_summary.get("validation_checks")
                    if isinstance(ai_summary.get("validation_checks"), list)
                    else []
                ),
                "verification_report": (
                    ai_summary.get("verification_report")
                    if isinstance(ai_summary.get("verification_report"), dict)
                    else {}
                ),
            },
        )

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
        return CommandResult(
            code=code,
            output=f"실행 종료 코드: {code}",
            data={
                "goal": "plan_mode",
                "status": "success" if code == 0 else "failed",
                "steps": 0,
                "reason": "",
                "duration": 0.0,
                "reason_code_summary": {},
            },
        )

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
    sink.info(f"- workspace: {context.workspace}")
    sink.info(f"- session_key: {context.session_key}")
    sink.info(f"- session_id: {context.session_id}")
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
