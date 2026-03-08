"""Interactive chat hub for GAIA."""
from __future__ import annotations

import atexit
import json
import os
import re
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

from gaia.src.phase4.mcp_host_runtime import ensure_mcp_host_running as _ensure_shared_mcp_host_running
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
    sticky_session: bool = False
    last_snapshot_id: str = ""
    steering_policy: Dict[str, Any] = field(default_factory=dict)
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
_CHAT_ROUTER_CLIENT: Any | None = None


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
        "/resume [otp=123456]             pending 개입을 proceed=true로 재개\n"
        "/steer <자연어 지시>             자연어 스티어링 정책 설정\n"
        "/steer status                    현재 스티어링 정책 조회\n"
        "/steer clear                     스티어링 정책 해제\n"
        "/rail smoke|full|status          Playwright 검증 레일 실행/조회\n"
        "/cancel                          pending 개입 요청 취소 응답 등록\n"
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
    _MCP_HOST_PROCESS = None
    if _MCP_HOST_LOG_FILE is not None:
        try:
            _MCP_HOST_LOG_FILE.close()
        except Exception:
            pass
        _MCP_HOST_LOG_FILE = None


def _ensure_mcp_host_running() -> bool:
    host, port, base_url = _resolve_mcp_target()
    return _ensure_shared_mcp_host_running(base_url, startup_timeout=10.0)


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
    if status in {"error", "failed", "success", "blocked_user_action", "skipped_not_applicable"}:
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


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


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
    if token in {"pass", "passed"}:
        return "PASS"
    if token in {"fail", "failed"}:
        return "FAIL"
    if token.startswith("skipped") or token == "skipped":
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
        "final_status": str(data.get("final_status") or ""),
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
    rail_summary = data.get("validation_rail_summary")
    if isinstance(rail_summary, dict):
        payload["validation_rail_summary"] = rail_summary
    rail_cases = data.get("validation_rail_cases")
    if isinstance(rail_cases, list):
        payload["validation_rail_cases"] = rail_cases
    rail_artifacts = data.get("validation_rail_artifacts")
    if isinstance(rail_artifacts, dict):
        payload["validation_rail_artifacts"] = rail_artifacts
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


def _looks_like_steering_text(text: str) -> bool:
    norm = str(text or "").strip().lower()
    if not norm:
        return False
    hard_tokens = (
        "하지마",
        "하지 말",
        "금지",
        "누르지마",
        "누르지 말",
        "제외",
        "빼고",
    )
    soft_tokens = (
        "우선",
        "먼저",
        "만 진행",
        "만 해",
        "prefer",
        "forbid",
        "스텝 안",
        "단계 안",
    )
    return any(token in norm for token in hard_tokens) or any(token in norm for token in soft_tokens)


def _extract_step_budget(text: str, default: int = 8) -> int:
    value = int(default)
    patterns = (
        r"(\d+)\s*(?:스텝|단계|step|steps)",
        r"(?:within|in)\s+(\d+)\s*(?:steps?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except Exception:
            continue
        break
    return max(3, min(15, int(value)))


def _detect_steering_scope(text: str) -> str:
    norm = str(text or "").strip().lower()
    if not norm:
        return "next_n_steps"
    if any(token in norm for token in ("이번 목표", "이 목표", "current goal", "for this goal")):
        return "current_goal"
    if any(token in norm for token in ("이번 단계", "현재 단계", "current phase", "this phase")):
        return "current_phase"
    if any(token in norm for token in ("이 사이트", "이 도메인", "same origin", "same site")):
        return "current_origin"
    return "next_n_steps"


def _compile_steering_policy(raw_text: str, context: HubContext) -> Dict[str, Any]:
    text = str(raw_text or "").strip()
    norm = text.lower()
    ttl_steps = _extract_step_budget(norm, default=8)
    scope = _detect_steering_scope(text)

    neg_tokens = ("하지마", "하지 말", "금지", "누르지마", "누르지 말", "말고", "제외", "빼고", "하지말")
    remove_tokens = ("제거", "삭제", "비우", "remove", "delete", "clear", "empty")
    quick_add_tokens = ("바로추가", "바로 추가", "quick add", "add now")
    wishlist_tokens = ("위시리스트", "wishlist")

    rules: list[Dict[str, Any]] = []
    assertions: list[Dict[str, Any]] = []

    if any(token in norm for token in quick_add_tokens) and any(token in norm for token in neg_tokens):
        rules.append(
            {
                "type": "forbid_action_tag",
                "tag": "intent.quick_add",
                "enforcement": "hard",
            }
        )

    if any(token in norm for token in remove_tokens):
        rules.append(
            {
                "type": "prefer_action_tag",
                "tag": "intent.remove_item",
                "enforcement": "soft",
            }
        )

    if any(token in norm for token in wishlist_tokens):
        rules.append(
            {
                "type": "prefer_target_text",
                "need": ["위시리스트", "wishlist"],
                "enforcement": "soft",
            }
        )

    # 간단 DSL: "금지: 바로추가, 클릭" / "우선: 제거"
    action_tag_map: Dict[str, str] = {
        "바로추가": "intent.quick_add",
        "quickadd": "intent.quick_add",
        "quick add": "intent.quick_add",
        "제거": "intent.remove_item",
        "삭제": "intent.remove_item",
        "비우기": "intent.remove_item",
        "click": "intent.click",
        "클릭": "intent.click",
        "select": "intent.select",
        "선택": "intent.select",
        "입력": "intent.fill",
        "fill": "intent.fill",
        "scroll": "intent.scroll",
        "스크롤": "intent.scroll",
        "wait": "intent.wait",
        "대기": "intent.wait",
    }
    for pattern, enforcement in (
        (r"(?:금지|forbid)\s*[:=]\s*([^\n]+)", "hard"),
        (r"(?:우선|prefer)\s*[:=]\s*([^\n]+)", "soft"),
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw_items = str(match.group(1) or "")
        for part in re.split(r"[,/|]+", raw_items):
            token = str(part or "").strip().lower()
            if not token:
                continue
            tag = action_tag_map.get(token)
            if not tag:
                continue
            rule_type = "forbid_action_tag" if enforcement == "hard" else "prefer_action_tag"
            rules.append(
                {
                    "type": rule_type,
                    "tag": tag,
                    "enforcement": enforcement,
                }
            )

    if "0개" in norm or "0 학점" in norm or "0학점" in norm or any(token in norm for token in ("비우", "empty", "clear")):
        assertions.append(
            {
                "type": "text_any",
                "need": ["총 0개", "0학점", "위시리스트가 비어있어요"],
                "where": "page",
            }
        )
    if re.search(r"count\s*==\s*0", norm):
        assertions.append(
            {
                "type": "text_any",
                "need": ["총 0개", "0개 과목", "0학점"],
                "where": "page",
            }
        )
    if re.search(r"(modal_open|모달)\s*==\s*(false|0)", norm):
        assertions.append(
            {
                "type": "modal_open",
                "value": False,
            }
        )

    # 중복 제거
    if rules:
        uniq_rules: list[Dict[str, Any]] = []
        seen_rules: set[str] = set()
        for row in rules:
            key = json.dumps(
                {
                    "type": row.get("type"),
                    "tag": row.get("tag"),
                    "need": row.get("need"),
                    "enforcement": row.get("enforcement"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if key in seen_rules:
                continue
            seen_rules.add(key)
            uniq_rules.append(row)
        rules = uniq_rules

    if assertions:
        uniq_assertions: list[Dict[str, Any]] = []
        seen_assertions: set[str] = set()
        for row in assertions:
            key = json.dumps(
                {
                    "type": row.get("type"),
                    "need": row.get("need"),
                    "where": row.get("where"),
                    "value": row.get("value"),
                    "pattern": row.get("pattern"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if key in seen_assertions:
                continue
            seen_assertions.add(key)
            uniq_assertions.append(row)
        assertions = uniq_assertions

    confidence = 0.55
    if rules:
        confidence += min(0.35, 0.1 * len(rules))
    if assertions:
        confidence += 0.1
    confidence = max(0.2, min(0.95, round(confidence, 2)))

    bound_origin = ""
    if scope == "current_origin":
        parsed = urlparse(context.url or "")
        if parsed.scheme and parsed.netloc:
            bound_origin = f"{parsed.scheme}://{parsed.netloc}"

    policy = {
        "version": "steering.v1",
        "raw_text": text,
        "scope": scope,
        "ttl_steps": ttl_steps,
        "ttl_remaining": ttl_steps,
        "priority": "normal",
        "rules": rules,
        "assertions": assertions,
        "bound_goal_id": "",
        "bound_phase": "",
        "bound_origin": bound_origin,
        "compile_confidence": confidence,
        "auto_relax_soft_once": True,
        "never_auto_relax_hard": True,
        "_soft_relaxed_once": False,
        "compiled_at": int(time.time()),
    }
    return policy


def _format_steering_status(policy: Dict[str, Any]) -> str:
    if not isinstance(policy, dict) or not policy:
        return "활성 스티어링 정책이 없습니다."
    lines: list[str] = []
    lines.append("활성 스티어링 정책")
    lines.append(f"- scope: {policy.get('scope') or '-'}")
    lines.append(f"- ttl_steps: {policy.get('ttl_steps')}")
    lines.append(f"- ttl_remaining: {policy.get('ttl_remaining')}")
    lines.append(f"- priority: {policy.get('priority') or '-'}")
    lines.append(f"- confidence: {policy.get('compile_confidence')}")
    lines.append(f"- raw: {policy.get('raw_text') or '-'}")
    rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
    assertions = policy.get("assertions") if isinstance(policy.get("assertions"), list) else []
    lines.append(f"- rules: {len(rules)}")
    for row in rules[:6]:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"  - {row.get('type')} ({row.get('enforcement')}) "
            f"{row.get('tag') or row.get('need') or ''}"
        )
    lines.append(f"- assertions: {len(assertions)}")
    for row in assertions[:4]:
        if not isinstance(row, dict):
            continue
        lines.append(f"  - {row.get('type')} {row.get('need') or ''}")
    return "\n".join(lines)


def _get_chat_router_client() -> Any | None:
    global _CHAT_ROUTER_CLIENT
    if _CHAT_ROUTER_CLIENT is not None:
        return _CHAT_ROUTER_CLIENT
    try:
        from gaia.src.phase4.llm_vision_client import get_vision_client
        _CHAT_ROUTER_CLIENT = get_vision_client()
        return _CHAT_ROUTER_CLIENT
    except Exception:
        return None


def _extract_json_object(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return raw
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last != -1 and last > first:
        return raw[first:last + 1].strip()
    return raw


def _interpret_user_message_with_llm(
    context: HubContext,
    text: str,
    *,
    pending_kind: str = "",
) -> Dict[str, Any]:
    if os.getenv("GAIA_CHAT_ROUTER_LLM", "1").strip().lower() in {"0", "false", "off", "no"}:
        return {}
    client = _get_chat_router_client()
    if client is None:
        return {}
    user_text = str(text or "").strip()
    if not user_text:
        return {}
    prompt = (
        "당신은 GAIA 채팅 라우터입니다. 사용자 자연어를 실행 의도로 분류하세요.\n"
        "반드시 JSON만 출력하세요.\n\n"
        f"현재 pending_kind: {pending_kind or 'none'}\n"
        f"사용자 입력: {user_text}\n\n"
        "JSON 스키마:\n"
        "{\n"
        '  "intent": "run_test|steer|handoff_reply|cancel|unknown",\n'
        '  "confidence": 0.0,\n'
        '  "goal_text": "",\n'
        '  "steering_text": "",\n'
        '  "handoff": {\n'
        '    "proceed": true,\n'
        '    "instruction": "",\n'
        '    "auth_mode": "",\n'
        '    "manual_done": false,\n'
        '    "username": "",\n'
        '    "email": "",\n'
        '    "password": ""\n'
        "  }\n"
        "}\n\n"
        "규칙:\n"
        "1) pending_kind가 auth/no_progress/clarification이면 기본 intent는 handoff_reply.\n"
        "2) 중단/취소 의도면 cancel.\n"
        "3) 사용자가 실행 지시면 run_test.\n"
        "4) 정책 지시(금지/우선/제외/몇 스텝 안에)면 steer.\n"
        "5) 확신이 낮으면 intent=unknown.\n"
    )
    try:
        response = client.analyze_text(prompt, max_completion_tokens=700, temperature=0.0)
        normalized = _extract_json_object(response)
        data = json.loads(normalized)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in {"run_test", "steer", "handoff_reply", "cancel", "unknown"}:
        return {}
    try:
        confidence = float(data.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    handoff = data.get("handoff") if isinstance(data.get("handoff"), dict) else {}
    return {
        "intent": intent,
        "confidence": max(0.0, min(1.0, confidence)),
        "goal_text": str(data.get("goal_text") or "").strip(),
        "steering_text": str(data.get("steering_text") or "").strip(),
        "handoff": {
            "proceed": handoff.get("proceed"),
            "instruction": str(handoff.get("instruction") or "").strip(),
            "auth_mode": str(handoff.get("auth_mode") or "").strip(),
            "manual_done": handoff.get("manual_done"),
            "username": str(handoff.get("username") or "").strip(),
            "email": str(handoff.get("email") or "").strip(),
            "password": str(handoff.get("password") or "").strip(),
        },
    }


def _handle_steer_command(context: HubContext, raw: str) -> CommandResult:
    parts = raw.split(maxsplit=1)
    if len(parts) == 1:
        return CommandResult(
            code=0,
            output=(
                "형식:\n"
                "/steer <자연어 지시>\n"
                "/steer status\n"
                "/steer clear"
            ),
        )

    arg = str(parts[1] or "").strip()
    if not arg:
        return CommandResult(code=2, status="error", output="스티어링 문장을 입력해주세요.")

    if arg.lower() == "status":
        return CommandResult(code=0, output=_format_steering_status(context.steering_policy))
    if arg.lower() == "clear":
        context.steering_policy = {}
        _notify_session_update(context)
        return CommandResult(code=0, output="스티어링 정책을 해제했습니다.")

    policy = _compile_steering_policy(arg, context)
    context.steering_policy = dict(policy)
    _notify_session_update(context)
    return CommandResult(code=0, output=_format_steering_status(policy))


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
            "추가 입력이 필요해 실행을 잠시 멈췄습니다.\n"
            f"{question}\n"
            "계속하려면 /handoff로 필요한 값을 보내주세요.\n"
            "예시: /handoff proceed=true username=<id> password=<pw>"
        )
        return {"action": "cancel", "proceed": False, "reason_code": "user_intervention_missing"}

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
        context.sticky_session = True
        context.last_snapshot_id = ""
        context.steering_policy = {}
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
        context.sticky_session = True
        context.last_snapshot_id = str(loaded.last_snapshot_id or "") if loaded else ""
        context.steering_policy = {}
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
        if kind == "no_progress":
            sink.info(
                "상태 변화가 반복 감지되어 진행 전략을 조정합니다. "
                "기본값으로 계속 진행(proceed=true)합니다. "
                "중단하려면 /cancel 을 사용하세요."
            )
            return {"action": "continue", "proceed": True}
        if kind == "auth":
            sink.info(
                "로그인 또는 회원가입이 필요한 화면이 열려 실행을 멈췄습니다.\n"
                "계정 정보를 함께 다시 실행하거나, 브라우저에서 직접 로그인한 뒤 다시 시도하세요.\n"
                "예시: /test <목표문장> username=<id_or_email> password=<pw>"
            )
        elif kind == "clarification":
            sink.info(
                "목표가 모호하거나 중요한 정보가 부족해 실행을 멈췄습니다.\n"
                "더 구체적인 목표와 필요한 입력값을 함께 전달해 주세요.\n"
                "예시:\n"
                "/test <구체 목표> username=<id> password=<pw> email=<email>"
            )
        else:
            sink.info("실행에 필요한 정보가 부족해 잠시 멈췄습니다. 필요한 값을 함께 다시 실행해 주세요.")
        return {"action": "cancel", "proceed": False, "reason_code": "user_intervention_missing"}

    return _callback


def _build_ai_intervention_callback(context: HubContext, sink: HubSink):
    def _callback(reason: str, current_url: str) -> dict:
        payload: dict[str, Any] = {
            "kind": "auth",
            "reason": str(reason or "").strip(),
            "url": str(current_url or "").strip(),
            "question": "로그인 또는 회원가입이 필요한 화면이 열렸습니다. 계정 정보를 보내거나, 브라우저에서 직접 로그인한 뒤 완료를 알려주세요.",
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
            "로그인 또는 회원가입이 필요한 화면이 열렸습니다.\n"
            "아래 방법 중 하나로 계속 진행할 수 있습니다.\n"
            "1. 계정 정보 전달\n"
            "/handoff proceed=true username=<id_or_email> password=<pw>\n"
            "2. 회원가입으로 진행\n"
            "/handoff proceed=true auth_mode=signup\n"
            "3. 브라우저에서 직접 로그인 후 완료 알림\n"
            "/handoff proceed=true manual_done=true"
        )
        return {"action": "cancel", "proceed": False, "reason_code": "user_intervention_missing"}

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

    if runtime == "gui":
        code = _run_gui(
            "--mode",
            "chat",
            "--url",
            context.url,
            "--feature-query",
            query,
            "--control",
            str(context.control_channel or "local"),
        )
        return code, {
            "goal": query,
            "status": "success" if code == 0 else "failed",
            "steps": None,
            "reason": "gui mode completed",
            "duration_seconds": None,
        }

    from gaia.terminal import run_chat_terminal_once

    if not bool(context.sticky_session):
        context.session_id = allocate_session_id(context.workspace or WORKSPACE_DEFAULT)
        context.session_new = True
        context.last_snapshot_id = ""
        _notify_session_update(context)

    cb = intervention_callback
    if cb is None:
        if context.control_channel == "telegram":
            cb = _build_telegram_intervention_callback(context, sink)
        else:
            cb = _build_hub_intervention_callback(context, sink)
    prev_provider = os.getenv("GAIA_LLM_PROVIDER")
    prev_model = os.getenv("GAIA_LLM_MODEL")
    if str(context.provider or "").strip():
        os.environ["GAIA_LLM_PROVIDER"] = str(context.provider).strip()
    if str(context.model or "").strip():
        os.environ["GAIA_LLM_MODEL"] = str(context.model).strip()
    try:
        code, summary = run_chat_terminal_once(
            url=context.url,
            query=query,
            session_id=context.session_id,
            steering_policy=(context.steering_policy if isinstance(context.steering_policy, dict) else None),
            intervention_callback=cb,
        )
    finally:
        if prev_provider is None:
            os.environ.pop("GAIA_LLM_PROVIDER", None)
        else:
            os.environ["GAIA_LLM_PROVIDER"] = prev_provider
        if prev_model is None:
            os.environ.pop("GAIA_LLM_MODEL", None)
        else:
            os.environ["GAIA_LLM_MODEL"] = prev_model
    if isinstance(context.steering_policy, dict) and context.steering_policy:
        try:
            steps_used = int(summary.get("steps") or 0) if isinstance(summary, dict) else 0
        except Exception:
            steps_used = 0
        try:
            ttl_remaining = int(
                context.steering_policy.get("ttl_remaining")
                if context.steering_policy.get("ttl_remaining") is not None
                else context.steering_policy.get("ttl_steps")
                or 0
            )
        except Exception:
            ttl_remaining = 0
        ttl_remaining = max(0, ttl_remaining - max(0, steps_used))
        if ttl_remaining <= 0:
            context.steering_policy = {}
        else:
            context.steering_policy["ttl_remaining"] = ttl_remaining
        _notify_session_update(context)
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

    runtime = context.runtime
    if runtime == "gui":
        if time_budget_seconds and int(time_budget_seconds) > 0:
            runtime = "terminal"
        else:
            code = _run_gui(
                "--mode",
                "ai",
                "--url",
                context.url,
                "--max-actions",
                str(max_actions),
                "--control",
                str(context.control_channel or "local"),
            )
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
        pending_kind = str((context.pending_user_input or {}).get("kind") or "").strip().lower()
        nlu = _interpret_user_message_with_llm(context, line, pending_kind=pending_kind)
        nlu_intent = str(nlu.get("intent") or "").strip().lower()
        try:
            nlu_conf = float(nlu.get("confidence") or 0.0)
        except Exception:
            nlu_conf = 0.0
        nlu_handoff = nlu.get("handoff") if isinstance(nlu.get("handoff"), dict) else {}

        if nlu_intent == "cancel" and nlu_conf >= 0.45:
            context.pending_user_response = {"action": "cancel", "proceed": "false"}
            _notify_session_update(context)
            return CommandResult(code=0, output="개입 응답을 취소로 저장했습니다.")

        # pending 개입 상태에서는 자연어를 우선 handoff 응답으로 처리한다.
        if context.pending_user_input:
            kind = pending_kind
            if nlu_intent == "handoff_reply" and nlu_conf >= 0.45:
                response: Dict[str, Any] = {
                    "action": "continue",
                    "proceed": str(_as_bool(nlu_handoff.get("proceed"), default=True)).lower(),
                }
                instruction_text = str(nlu_handoff.get("instruction") or line).strip()
                if instruction_text:
                    response["instruction"] = instruction_text

                auth_mode = str(nlu_handoff.get("auth_mode") or "").strip()
                username = str(nlu_handoff.get("username") or "").strip()
                email = str(nlu_handoff.get("email") or "").strip()
                password = str(nlu_handoff.get("password") or "").strip()
                manual_done = nlu_handoff.get("manual_done")
                if auth_mode:
                    response["auth_mode"] = auth_mode
                if username:
                    response["username"] = username
                if email:
                    response["email"] = email
                if password:
                    response["password"] = password
                if manual_done is not None:
                    response["manual_done"] = str(_as_bool(manual_done, default=False)).lower()

                if kind in {"no_progress", "clarification"}:
                    steer_text = str(nlu.get("steering_text") or "").strip()
                    if steer_text or _looks_like_steering_text(line):
                        policy = _compile_steering_policy(steer_text or line, context)
                        context.steering_policy = dict(policy)
                        _notify_session_update(context)

                context.pending_user_response = response
                _notify_session_update(context)
                return CommandResult(code=0, output="개입 응답을 저장했습니다. 다음 실행에서 반영됩니다.")

            lowered = line.lower()
            if any(token in lowered for token in ("중단", "취소", "멈춰", "stop", "cancel")):
                context.pending_user_response = {"action": "cancel", "proceed": "false"}
                _notify_session_update(context)
                return CommandResult(code=0, output="개입 응답을 취소로 저장했습니다.")

            response: Dict[str, Any] = {"action": "continue", "proceed": "true"}
            if kind in {"no_progress", "clarification"}:
                response["instruction"] = line
                if _looks_like_steering_text(line):
                    policy = _compile_steering_policy(line, context)
                    context.steering_policy = dict(policy)
                    _notify_session_update(context)
            elif kind == "auth":
                response["instruction"] = line
            else:
                response["instruction"] = line
            context.pending_user_response = response
            _notify_session_update(context)
            return CommandResult(code=0, output="개입 응답을 저장했습니다. 다음 실행에서 반영됩니다.")

        if nlu_intent == "steer" and nlu_conf >= 0.45:
            steer_text = str(nlu.get("steering_text") or "").strip() or line
            policy = _compile_steering_policy(steer_text, context)
            context.steering_policy = dict(policy)
            _notify_session_update(context)
            return CommandResult(code=0, output=_format_steering_status(policy))

        if nlu_intent == "run_test" and nlu_conf >= 0.35:
            goal_text = str(nlu.get("goal_text") or "").strip()
            line = f"/test {goal_text or line}"

        # 일반 입력도 스티어링 문장 패턴이면 /steer 없이 정책으로 적용
        if not line.startswith("/") and _looks_like_steering_text(line):
            policy = _compile_steering_policy(line, context)
            context.steering_policy = dict(policy)
            _notify_session_update(context)
            return CommandResult(code=0, output=_format_steering_status(policy))

    if not line.startswith("/"):
        line = f"/test {line}"

    if line == "/exit":
        return CommandResult(code=0, status="exit", output="종료합니다.")
    if line == "/help":
        return CommandResult(code=0, output=_help_text())
    if line == "/status":
        steering_policy = context.steering_policy if isinstance(context.steering_policy, dict) else {}
        ttl_steps = steering_policy.get("ttl_steps") if steering_policy else None
        ttl_remaining = (
            steering_policy.get("ttl_remaining")
            if steering_policy and steering_policy.get("ttl_remaining") is not None
            else ttl_steps
        )
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
            "steering_active": bool(steering_policy),
            "steering_ttl_steps": ttl_steps,
            "steering_ttl_remaining": ttl_remaining,
            "pending_user_input": bool(context.pending_user_input),
        }
        return CommandResult(code=0, output=json.dumps(payload, ensure_ascii=False, indent=2))
    if line.startswith("/session"):
        return _handle_session_command(context, line)
    if line.startswith("/steer"):
        return _handle_steer_command(context, line)
    if line.startswith("/handoff"):
        return _handle_handoff_command(context, line)
    if line.startswith("/resume"):
        otp = ""
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            kv = _parse_kv_tokens(parts[1])
            otp = str(kv.get("otp") or "").strip()
        if not context.pending_user_input:
            return CommandResult(code=0, output="대기 중인 개입 요청이 없어 /resume 대상이 없습니다.")
        response: Dict[str, Any] = {"action": "continue", "proceed": "true"}
        if otp:
            response["otp"] = otp
        context.pending_user_response = response
        _notify_session_update(context)
        return CommandResult(code=0, output="개입 완료 응답을 저장했습니다. 다음 실행에서 재개됩니다.")
    if line == "/cancel":
        context.pending_user_response = {"action": "cancel", "proceed": "false"}
        _notify_session_update(context)
        return CommandResult(code=0, output="대기 중인 개입 요청에 cancel 응답을 등록했습니다.")
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
        final_status = str(detail.get("final_status") or "").strip()
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
        if final_status:
            lines.append(f"final_status: {final_status}")
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
        rail_summary = (
            detail.get("validation_rail_summary")
            if isinstance(detail.get("validation_rail_summary"), dict)
            else {}
        )
        rail_cases = (
            detail.get("validation_rail_cases")
            if isinstance(detail.get("validation_rail_cases"), list)
            else []
        )
        rail_artifacts = (
            detail.get("validation_rail_artifacts")
            if isinstance(detail.get("validation_rail_artifacts"), dict)
            else {}
        )
        if rail_summary:
            lines.append("validation_rail:")
            lines.append(f"  scope: {rail_summary.get('scope') or '-'}")
            lines.append(f"  mode: {rail_summary.get('mode') or '-'}")
            lines.append(f"  status: {rail_summary.get('status') or '-'}")
            lines.append(f"  total: {rail_summary.get('total', 0)}")
            lines.append(f"  passed: {rail_summary.get('passed', 0)}")
            lines.append(f"  failed: {rail_summary.get('failed', 0)}")
            lines.append(f"  skipped: {rail_summary.get('skipped', 0)}")
            lines.append(f"  duration: {rail_summary.get('duration_seconds', 0)}s")
            top_failed = [
                row for row in rail_cases
                if isinstance(row, dict) and str(row.get("status") or "").strip().lower() in {"failed", "timedout", "timeout", "error"}
            ][:3]
            if top_failed:
                lines.append("  top_failed:")
                for row in top_failed:
                    lines.append(f"    - {row.get('title') or row.get('id') or 'unknown'}")
            summary_path = rail_artifacts.get("summary_path")
            if summary_path:
                lines.append(f"  summary_path: {summary_path}")
        if validation_checks:
            lines.append("checks:")
            for check in validation_checks[:8]:
                if not isinstance(check, dict):
                    continue
                status_token = str(check.get("status") or "").strip().lower()
                status_label = (
                    "PASS"
                    if status_token in {"pass", "passed"}
                    else ("FAIL" if status_token in {"fail", "failed"} else "SKIP")
                )
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
        detail_attachments = detail.get("attachments")
        if isinstance(detail_attachments, list):
            for item in detail_attachments:
                if isinstance(item, dict):
                    attachments.append(item)
        if context.control_channel == "telegram" and not attachments:
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
                "final_status": final_status,
                "steps": detail.get("steps"),
                "reason": detail.get("reason") or "",
                "duration": duration_value,
                "step_timeline": (
                    detail.get("step_timeline")
                    if isinstance(detail.get("step_timeline"), list)
                    else []
                ),
                "reason_code_summary": reason_summary,
                "validation_summary": validation_summary,
                "validation_checks": validation_checks,
                "verification_report": (
                    detail.get("verification_report")
                    if isinstance(detail.get("verification_report"), dict)
                    else {}
                ),
                "validation_rail_summary": rail_summary,
                "validation_rail_cases": rail_cases,
                "validation_rail_artifacts": rail_artifacts,
            },
        )

    if line.startswith("/rail"):
        parts = line.split(maxsplit=1)
        mode_token = parts[1].strip().lower() if len(parts) == 2 else "smoke"
        if mode_token not in {"smoke", "full", "status"}:
            return CommandResult(code=2, status="error", output="형식: /rail smoke|full|status")
        from gaia.src.phase4.validation_rail import run_validation_rail

        if mode_token == "status":
            data = {
                "enabled": str(os.getenv("GAIA_RAIL_ENABLED", "1")).strip(),
                "scope_default": str(os.getenv("GAIA_RAIL_SCOPE_DEFAULT", "smoke")).strip(),
                "mode": str(os.getenv("GAIA_RAIL_MODE", "soft")).strip(),
                "timeout_sec": str(os.getenv("GAIA_RAIL_TIMEOUT_SEC", "300")).strip(),
                "target_url": context.url,
            }
            return CommandResult(code=0, output=json.dumps(data, ensure_ascii=False, indent=2), data=data)

        started = time.time()
        rail_result = run_validation_rail(
            target_url=context.url,
            run_id=context.session_id,
            scope=mode_token,
        )
        elapsed = round(time.time() - started, 2)
        rail_summary = rail_result.get("summary") if isinstance(rail_result, dict) else {}
        rail_cases = rail_result.get("cases") if isinstance(rail_result, dict) else []
        rail_artifacts = rail_result.get("artifacts") if isinstance(rail_result, dict) else {}
        if not isinstance(rail_summary, dict):
            rail_summary = {}
        if not isinstance(rail_cases, list):
            rail_cases = []
        if not isinstance(rail_artifacts, dict):
            rail_artifacts = {}
        output_lines = [
            f"rail_scope: {mode_token}",
            f"status: {rail_summary.get('status')}",
            f"reason: {rail_summary.get('reason')}",
            f"total: {rail_summary.get('total', 0)}",
            f"passed: {rail_summary.get('passed', 0)}",
            f"failed: {rail_summary.get('failed', 0)}",
            f"skipped: {rail_summary.get('skipped', 0)}",
            f"duration: {rail_summary.get('duration_seconds', elapsed)}s",
        ]
        summary_path = rail_artifacts.get("summary_path")
        if summary_path:
            output_lines.append(f"summary_path: {summary_path}")
        _record_summary(
            memory_store,
            context=context,
            command="/rail",
            status="success" if str(rail_summary.get("status") or "") in {"passed", "skipped"} else "failed",
            summary=f"scope={mode_token}, result={rail_summary}",
            metadata={"scope": mode_token, "result": rail_result},
        )
        return CommandResult(
            code=0 if str(rail_summary.get("status") or "") in {"passed", "skipped"} else 1,
            output="\n".join(output_lines),
            data={
                "goal": f"validation_rail_{mode_token}",
                "status": "success" if str(rail_summary.get("status") or "") in {"passed", "skipped"} else "failed",
                "steps": rail_summary.get("total", 0),
                "reason": rail_summary.get("reason", ""),
                "duration": rail_summary.get("duration_seconds", elapsed),
                "validation_rail_summary": rail_summary,
                "validation_rail_cases": rail_cases,
                "validation_rail_artifacts": rail_artifacts,
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
                "step_timeline": (
                    ai_summary.get("step_timeline")
                    if isinstance(ai_summary.get("step_timeline"), list)
                    else []
                ),
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
                "step_timeline": (
                    ai_summary.get("step_timeline")
                    if isinstance(ai_summary.get("step_timeline"), list)
                    else []
                ),
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
