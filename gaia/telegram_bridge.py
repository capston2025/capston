"""Telegram bridge for GAIA Chat Hub."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from gaia.chat_hub import HubContext, build_command_payload, dispatch_command
from gaia.src.phase4.memory.store import MemoryStore

TELEGRAM_BRIDGE_STATUS_FILE = Path.home() / ".gaia" / "telegram_bridge.status.json"


@dataclass(slots=True)
class TelegramConfig:
    mode: str = "polling"
    token_file: str = str(Path.home() / ".gaia" / "telegram_bot_token")
    allowlist: tuple[int, ...] = ()  # admin chat_id allowlist
    webhook_url: str = ""
    webhook_bind: str = "127.0.0.1:8088"
    pairing_file: str = str(Path.home() / ".gaia" / "telegram_pairing.json")


@dataclass(slots=True)
class _CommandEnvelope:
    chat_id: int
    raw_command: str
    reply_to_message_id: int | None


@dataclass(slots=True)
class _PairRequest:
    request_id: str
    chat_id: int
    username: str
    full_name: str
    created_at: int


@dataclass(slots=True)
class _PendingIntervention:
    kind: str
    question: str
    fields: list[str]
    event: threading.Event
    response_text: str = ""


@dataclass(slots=True)
class _ActiveRun:
    chat_id: int
    raw_command: str
    started_at: float


class _BufferedSink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, text: str) -> None:
        self.lines.append(text)

    def error(self, text: str) -> None:
        self.lines.append(f"[error] {text}")


class _PairingState:
    def __init__(self, path: Path, configured_admins: tuple[int, ...]) -> None:
        self.path = path
        self.admin_ids: set[int] = set(configured_admins)
        self.approved_ids: set[int] = set(configured_admins)
        self.pending_by_id: dict[str, _PairRequest] = {}
        self._load()

    @staticmethod
    def _to_int_list(values) -> list[int]:
        out: list[int] = []
        if not isinstance(values, list):
            return out
        for value in values:
            try:
                out.append(int(value))
            except Exception:
                continue
        return out

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return

        self.admin_ids.update(self._to_int_list(payload.get("admins")))
        self.approved_ids.update(self._to_int_list(payload.get("approved")))
        self.approved_ids.update(self.admin_ids)

        pending = payload.get("pending")
        if not isinstance(pending, list):
            return
        for row in pending:
            if not isinstance(row, dict):
                continue
            request_id = str(row.get("request_id") or "").strip()
            if not request_id:
                continue
            try:
                chat_id = int(row.get("chat_id"))
            except Exception:
                continue
            req = _PairRequest(
                request_id=request_id,
                chat_id=chat_id,
                username=str(row.get("username") or ""),
                full_name=str(row.get("full_name") or ""),
                created_at=int(row.get("created_at") or int(time.time())),
            )
            self.pending_by_id[request_id] = req

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "admins": sorted(self.admin_ids),
            "approved": sorted(self.approved_ids),
            "pending": [
                {
                    "request_id": req.request_id,
                    "chat_id": req.chat_id,
                    "username": req.username,
                    "full_name": req.full_name,
                    "created_at": req.created_at,
                }
                for req in sorted(self.pending_by_id.values(), key=lambda x: x.created_at)
            ],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_admin(self, chat_id: int) -> bool:
        return chat_id in self.admin_ids

    def is_approved(self, chat_id: int) -> bool:
        return chat_id in self.approved_ids or chat_id in self.admin_ids

    def ensure_bootstrap_admin(self, chat_id: int, username: str, full_name: str) -> bool:
        if self.admin_ids:
            return False
        self.admin_ids.add(chat_id)
        self.approved_ids.add(chat_id)
        self._drop_pending_for_chat(chat_id)
        self.save()
        return True

    def _drop_pending_for_chat(self, chat_id: int) -> None:
        stale_ids = [req_id for req_id, req in self.pending_by_id.items() if req.chat_id == chat_id]
        for req_id in stale_ids:
            self.pending_by_id.pop(req_id, None)

    def request_pairing(self, chat_id: int, username: str, full_name: str) -> _PairRequest:
        for req in self.pending_by_id.values():
            if req.chat_id == chat_id:
                return req
        request_id = f"r{int(time.time())}{abs(chat_id) % 10000:04d}"
        req = _PairRequest(
            request_id=request_id,
            chat_id=chat_id,
            username=username,
            full_name=full_name,
            created_at=int(time.time()),
        )
        self.pending_by_id[request_id] = req
        self.save()
        return req

    def approve(self, request_id: str) -> _PairRequest | None:
        req = self.pending_by_id.pop(request_id, None)
        if req is None:
            return None
        self.approved_ids.add(req.chat_id)
        self.save()
        return req

    def reject(self, request_id: str) -> _PairRequest | None:
        req = self.pending_by_id.pop(request_id, None)
        if req is None:
            return None
        self.save()
        return req

    def revoke(self, chat_id: int) -> bool:
        if chat_id in self.admin_ids:
            return False
        if chat_id not in self.approved_ids:
            return False
        self.approved_ids.remove(chat_id)
        self._drop_pending_for_chat(chat_id)
        self.save()
        return True

    def pending_rows(self) -> list[_PairRequest]:
        return sorted(self.pending_by_id.values(), key=lambda row: row.created_at)


class _TelegramBridge:
    def __init__(
        self,
        *,
        hub_context: HubContext,
        config: TelegramConfig,
        memory_store: MemoryStore,
    ):
        self.hub_context = hub_context
        self.config = config
        self.memory_store = memory_store
        self.queue: asyncio.Queue[_CommandEnvelope | None] = asyncio.Queue()
        self.worker_task: Optional[asyncio.Task] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._pending_interventions: dict[int, _PendingIntervention] = {}
        self._pending_lock = threading.Lock()
        self._active_runs: dict[int, _ActiveRun] = {}
        self._queued_count_by_chat: dict[int, int] = {}
        self._state_lock = threading.Lock()
        self.pairing = _PairingState(
            path=Path(config.pairing_file),
            configured_admins=config.allowlist,
        )

    async def post_init(self, _application) -> None:
        self.loop = asyncio.get_running_loop()
        self._write_status("running")
        self.worker_task = asyncio.create_task(self._worker_loop(_application))

    async def post_shutdown(self, _application) -> None:
        await self.queue.put(None)
        if self.worker_task:
            await self.worker_task
        self._write_status("stopped")

    def _write_status(self, state: str) -> None:
        try:
            TELEGRAM_BRIDGE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TELEGRAM_BRIDGE_STATUS_FILE.write_text(
                json.dumps(
                    {
                        "state": str(state or "").strip() or "unknown",
                        "updated_at": int(time.time()),
                        "mode": self.config.mode,
                        "url": self.hub_context.url,
                        "runtime": self.hub_context.runtime,
                        "control_channel": self.hub_context.control_channel,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _allowed(self, chat_id: int) -> bool:
        return self.pairing.is_approved(chat_id)

    async def _send_text(self, bot, chat_id: int, text: str, reply_to_message_id: int | None) -> None:
        chunks = _split_text(text, limit=3900)
        for chunk in chunks:
            kwargs = {"chat_id": chat_id, "text": chunk}
            if reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            await bot.send_message(**kwargs)

    async def _send_attachments(
        self,
        bot,
        chat_id: int,
        attachments: list[dict],
        reply_to_message_id: int | None,
    ) -> None:
        if not attachments:
            return
        max_images = 3
        try:
            max_images = max(1, min(10, int(os.getenv("GAIA_TG_MAX_IMAGES_PER_RUN", "3"))))
        except Exception:
            max_images = 3
        photo_items: list[tuple[io.BytesIO, str]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            kind = str(attachment.get("kind") or "").strip().lower()
            if kind != "image_base64":
                continue
            encoded = attachment.get("data")
            if not isinstance(encoded, str) or not encoded.strip():
                continue
            try:
                binary = base64.b64decode(encoded)
            except Exception:
                continue
            photo = io.BytesIO(binary)
            photo.name = "gaia_result.png"
            caption = str(attachment.get("caption") or attachment.get("label") or "").strip()
            photo_items.append((photo, caption))
            if len(photo_items) >= max_images:
                break

        if not photo_items:
            return

        # Telegram media-group limit: up to 10 items.
        # Prefer grouped delivery for better mobile readability.
        async def _send_group(items: list[tuple[io.BytesIO, str]], is_first_group: bool) -> None:
            try:
                from telegram import InputMediaPhoto  # type: ignore

                media = []
                for idx, (photo, caption) in enumerate(items):
                    cap = caption if idx == 0 else ""
                    media.append(InputMediaPhoto(media=photo, caption=cap or None))
                kwargs: Dict[str, Any] = {"chat_id": chat_id, "media": media}
                if reply_to_message_id is not None and is_first_group:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                await bot.send_media_group(**kwargs)
            except Exception:
                # Fallback: sequential photo sends.
                for idx, (photo, caption) in enumerate(items):
                    kwargs: Dict[str, Any] = {"chat_id": chat_id, "photo": photo}
                    if caption:
                        kwargs["caption"] = caption
                    if reply_to_message_id is not None and is_first_group and idx == 0:
                        kwargs["reply_to_message_id"] = reply_to_message_id
                    await bot.send_photo(**kwargs)

        for start in range(0, len(photo_items), 10):
            group = photo_items[start : start + 10]
            await _send_group(group, is_first_group=(start == 0))

    @staticmethod
    def _sanitize_payload_for_text(payload_obj: Dict[str, Any]) -> Dict[str, Any]:
        safe: Dict[str, Any] = dict(payload_obj or {})
        attachments = safe.get("attachments")
        if not isinstance(attachments, list):
            return safe

        sanitized_attachments: list[Dict[str, Any]] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            item: Dict[str, Any] = {}
            for key, value in attachment.items():
                if key == "data":
                    continue
                item[key] = value
            encoded = attachment.get("data")
            if isinstance(encoded, str) and encoded:
                item["data_bytes"] = len(encoded)
            sanitized_attachments.append(item)
        safe["attachments"] = sanitized_attachments
        return safe

    @staticmethod
    def _format_reason_code_summary(summary: Any) -> str:
        if not isinstance(summary, dict) or not summary:
            return "-"
        parts: list[str] = []
        for key, value in summary.items():
            try:
                count = int(value)
            except Exception:
                count = 0
            name = str(key or "").strip()
            if not name:
                continue
            parts.append(f"{name}={count}")
        return ", ".join(parts) if parts else "-"

    @staticmethod
    def _truncate(value: Any, limit: int = 120) -> str:
        text = str(value if value is not None else "").replace("\n", " ").strip()
        if not text:
            return "-"
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    @staticmethod
    def _resolve_report_mode() -> str:
        mode = str(os.getenv("GAIA_TG_REPORT_MODE", "summary_with_json") or "").strip().lower()
        if mode in {"summary_with_json", "summary_only", "legacy_json_text"}:
            return mode
        return "summary_with_json"

    @staticmethod
    def _status_label_ko(status: Any) -> str:
        token = str(status or "").strip().lower()
        if token in {"blocked_user_action", "blocked"}:
            return "사용자 개입 필요"
        if token in {"skipped_not_applicable", "skipped"}:
            return "적용 불가"
        if token in {"ok", "success"}:
            return "성공"
        if token in {"failed", "error"}:
            return "실패"
        if token == "empty":
            return "결과 없음"
        if token == "exit":
            return "종료"
        return token or "-"

    async def _send_json_report(
        self,
        bot,
        chat_id: int,
        payload_obj: Dict[str, Any],
        reply_to_message_id: int | None,
    ) -> bool:
        try:
            compact_payload = self._build_compact_report_payload(payload_obj)
            blob = json.dumps(compact_payload, ensure_ascii=False, indent=2).encode("utf-8")
            doc = io.BytesIO(blob)
            doc.name = f"report_{int(time.time())}.json"
            kwargs: Dict[str, Any] = {
                "chat_id": chat_id,
                "document": doc,
                "caption": "상세 실행 결과(JSON)",
            }
            if reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            await bot.send_document(**kwargs)
            return True
        except Exception:
            return False

    @staticmethod
    def _compact_validation_checks(rows: Any, limit: int = 50) -> list[Dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        compact: list[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            compact.append(
                {
                    "check_id": row.get("check_id"),
                    "step": row.get("step"),
                    "status": row.get("status"),
                    "name": row.get("name"),
                    "action": row.get("action"),
                    "input_value": row.get("input_value"),
                    "error": row.get("error") or "",
                }
            )
            if len(compact) >= limit:
                break
        return compact

    @staticmethod
    def _compact_step_timeline(rows: Any, limit: int = 12) -> list[Dict[str, Any]]:
        if not isinstance(rows, list):
            return []
        compact: list[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            compact.append(
                {
                    "step": row.get("step"),
                    "action": row.get("action"),
                    "duration_seconds": row.get("duration_seconds"),
                    "success": row.get("success"),
                    "reasoning": row.get("reasoning") or "",
                    "error": row.get("error") or "",
                }
            )
            if len(compact) >= limit:
                break
        return compact

    @classmethod
    def _build_compact_report_payload(cls, payload_obj: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        validation_summary = payload.get("validation_summary")
        validation_rail_summary = payload.get("validation_rail_summary")
        validation_rail_cases = payload.get("validation_rail_cases")
        checks = cls._compact_validation_checks(payload.get("validation_checks"), limit=50)
        step_timeline = cls._compact_step_timeline(payload.get("step_timeline"), limit=20)
        reason_codes = payload.get("reason_code_summary")
        attachments = payload.get("attachments")

        compact: Dict[str, Any] = {
            "schema_version": "gaia.telegram.report.v1",
            "generated_at": int(time.time()),
            "result": {
                "status": payload.get("status"),
                "final_status": payload.get("final_status"),
                "goal": payload.get("goal") or payload.get("command"),
                "steps": payload.get("steps"),
                "duration": payload.get("duration"),
                "reason": payload.get("reason"),
                "exit_code": payload.get("exit_code"),
            },
            "timeline": {
                "steps": step_timeline,
            },
            "validation": {
                "summary": validation_summary if isinstance(validation_summary, dict) else {},
                "checks": checks,
            },
            "validation_rail": {
                "summary": (
                    validation_rail_summary
                    if isinstance(validation_rail_summary, dict)
                    else {}
                ),
                "cases": (
                    validation_rail_cases[:50]
                    if isinstance(validation_rail_cases, list)
                    else []
                ),
            },
            "diagnostics": {
                "reason_code_summary": reason_codes if isinstance(reason_codes, dict) else {},
                "url": payload.get("url"),
                "runtime": payload.get("runtime"),
            },
            "artifacts": {
                "attachments": attachments if isinstance(attachments, list) else [],
            },
        }

        # 실패 시 핵심 실패 체크만 추가 제공
        if str(payload.get("status") or "").strip().lower() in {"failed", "error"}:
            failed_checks = [row for row in checks if str(row.get("status") or "").strip().lower() == "failed"]
            compact["diagnostics"]["failed_checks"] = failed_checks[:10]

        return compact

    @classmethod
    def _format_payload_text(cls, payload: Dict[str, Any], *, mode: str = "summary_with_json") -> str:
        if not isinstance(payload, dict):
            return ""
        goal = cls._truncate(payload.get("goal") or payload.get("command"), 130)
        reason = cls._truncate(payload.get("reason"), 180)
        status_label = cls._status_label_ko(payload.get("final_status") or payload.get("status"))

        steps = payload.get("steps")
        steps_text = f"{steps}단계" if steps is not None else "-"
        duration = payload.get("duration")
        if duration is None:
            duration_text = "-"
        else:
            try:
                duration_text = f"{float(duration):.2f}초"
            except Exception:
                duration_text = f"{duration}초"

        lines: list[str] = [
            f"🔥실행 결과 {status_label}🔥",
            "",
            f"  목표: {goal}",
            "",
            "  단계/시간",
            f"  {steps_text} / {duration_text}",
            "",
            "  판정 사유",
            f"  {reason}",
        ]

        step_timeline = payload.get("step_timeline")
        if isinstance(step_timeline, list) and step_timeline:
            lines.extend(["", "  단계별 실행"])
            for row in step_timeline[:6]:
                if not isinstance(row, dict):
                    continue
                step_no = row.get("step")
                action = cls._truncate(row.get("action"), 20)
                try:
                    sec = float(row.get("duration_seconds") or 0.0)
                    sec_text = f"{sec:.2f}초"
                except Exception:
                    sec_text = "-"
                reasoning = cls._truncate(row.get("reasoning"), 90)
                lines.append(f"    - {step_no}단계 | {action} | {sec_text}")
                lines.append(f"      {reasoning}")

        attachments = payload.get("attachments")
        proof_labels: list[str] = []
        if isinstance(attachments, list):
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                if str(item.get("kind") or "").strip().lower() != "image_base64":
                    continue
                label = cls._truncate(item.get("label") or item.get("caption"), 60)
                if label == "-":
                    label = "대표 실행 화면"
                proof_labels.append(label)
        if proof_labels:
            lines.extend(["", "  대표 증빙"])
            lines.append(f"    - 이미지 {len(proof_labels)}건 첨부")
            for label in proof_labels[:3]:
                lines.append(f"    - {label}")

        validation_summary = payload.get("validation_summary")
        if isinstance(validation_summary, dict) and validation_summary:
            total = validation_summary.get("total_checks", 0)
            passed = validation_summary.get("passed_checks", 0)
            failed = validation_summary.get("failed_checks", 0)
            success_rate = validation_summary.get("success_rate", 0)
            goal_satisfied = validation_summary.get("goal_satisfied")
            lines.extend(
                [
                    "",
                    "  검증 요약",
                    f"    - 총 {total}건",
                    f"    - 성공 {passed}건",
                    f"    - 실패 {failed}건",
                    f"    - 성공률 {success_rate}%",
                ]
            )
            if goal_satisfied is not None:
                lines.append(f"    - 목표 충족 {'예' if bool(goal_satisfied) else '아니오'}")
        rail_summary = payload.get("validation_rail_summary")
        if isinstance(rail_summary, dict) and rail_summary:
            lines.extend(
                [
                    "",
                    "  검증 레일",
                    f"    - 범위 {rail_summary.get('scope', '-')}",
                    f"    - 모드 {rail_summary.get('mode', '-')}",
                    f"    - 상태 {rail_summary.get('status', '-')}",
                    f"    - 통과 {rail_summary.get('passed', 0)}건",
                    f"    - 실패 {rail_summary.get('failed', 0)}건",
                    f"    - 스킵 {rail_summary.get('skipped', 0)}건",
                ]
            )
            failed_cases = payload.get("validation_rail_cases")
            if isinstance(failed_cases, list) and failed_cases:
                top_failed = [
                    row for row in failed_cases
                    if isinstance(row, dict) and str(row.get("status") or "").strip().lower() in {"failed", "timedout", "timeout", "error"}
                ][:3]
                if top_failed:
                    lines.append("    - 실패 케이스 상위 3개")
                    for row in top_failed:
                        lines.append(f"      · {cls._truncate(row.get('title') or row.get('id'), 80)}")
        if mode == "summary_with_json":
            lines.extend(
                [
                    "",
                    "  상세 결과",
                    "    - 첨부된 report.json 확인",
                ]
            )
        elif mode == "summary_only":
            lines.extend(
                [
                    "",
                    "  상세 결과",
                    "    - 요약 모드(첨부 없음)",
                ]
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_kv(text: str) -> Dict[str, str]:
        aliases = {
            "id": "username",
            "user": "username",
            "username": "username",
            "email": "email",
            "pw": "password",
            "password": "password",
            "goal": "goal_text",
            "goal_text": "goal_text",
        }
        out: Dict[str, str] = {}
        for token in (text or "").split():
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            key = aliases.get(key.strip().lower(), key.strip().lower())
            value = value.strip().strip('"').strip("'")
            if value:
                out[key] = value
        return out

    @staticmethod
    def _parse_intervention_response(kind: str, text: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        low = raw.lower()
        if low in {"cancel", "/cancel", "n", "no", "취소"}:
            return {"action": "cancel", "proceed": False}

        kv = _TelegramBridge._parse_kv(raw)
        if kind == "auth":
            if low in {"manual", "manual_done", "done", "수동완료"}:
                return {"manual_done": True, "proceed": True}
            wants_signup = any(
                token in low for token in ("회원가입", "signup", "sign up", "register")
            )
            asks_credentials = (
                ("아이디" in raw and "비밀번호" in raw and ("알려" in raw or "공유" in raw))
                or "credential" in low
            )

            def _clean(v: str) -> str:
                return v.strip().strip('"').strip("'").strip(".,!?")

            # 자유형 문장에서 부가 필드 추출
            dept = ""
            year = ""
            m_dept = re.search(r"(?:학과|과)\s*(?:는|은|:)?\s*([^\s,.!?]+)", raw)
            if m_dept:
                dept = _clean(m_dept.group(1))
            m_year = re.search(r"([1-6])\s*학년", raw)
            if m_year:
                year = _clean(m_year.group(1))

            # 자유형 아이디/비밀번호 추출
            if "username" not in kv and "email" not in kv:
                m_id = re.search(r"(?:아이디|id|username)\s*(?:는|은|:)?\s*([^\s,]+)", raw, re.IGNORECASE)
                m_email = re.search(r"(?:이메일|email)\s*(?:는|은|:)?\s*([^\s,]+)", raw, re.IGNORECASE)
                if m_id:
                    kv["username"] = _clean(m_id.group(1))
                if m_email:
                    kv["email"] = _clean(m_email.group(1))
            if "password" not in kv:
                m_pw = re.search(r"(?:비밀번호|패스워드|password|pw)\s*(?:는|은|:)?\s*([^\s,]+)", raw, re.IGNORECASE)
                if m_pw:
                    kv["password"] = _clean(m_pw.group(1))

            if wants_signup:
                resp: Dict[str, Any] = {"auth_mode": "signup", "proceed": True}
                resp.update(kv)
                if dept:
                    resp["department"] = dept
                if year:
                    resp["grade_year"] = year
                if asks_credentials:
                    resp["return_credentials"] = True
                return resp

            if kv:
                if "username" in kv or "email" in kv:
                    kv.setdefault("proceed", "true")
                    return kv
                return {"action": "cancel", "proceed": False}
            return {"action": "cancel", "proceed": False}

        if kind == "clarification":
            if not kv and raw:
                return {"goal_text": raw, "proceed": True}
            if kv:
                kv.setdefault("proceed", "true")
                return kv
            return {"action": "cancel", "proceed": False}

        return {"action": "cancel", "proceed": False}

    def _build_intervention_callback(self, application, chat_id: int, reply_to_message_id: int | None):
        def _callback(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            loop = self.loop
            if loop is None:
                return {"action": "cancel", "proceed": False}

            kind = str(payload.get("kind") or "input").strip().lower()
            question = str(payload.get("question") or "추가 입력이 필요합니다.")
            fields = payload.get("fields")
            if not isinstance(fields, list):
                fields = []
            attachments = payload.get("attachments")
            attachment_items: list[dict] = []
            if isinstance(attachments, list):
                attachment_items = [item for item in attachments if isinstance(item, dict)]

            helper_lines = []
            if kind == "auth":
                helper_lines.append("1) 계정 정보 전달: username=<id_or_email> password=<pw> [email=<email>]")
                helper_lines.append("2) 회원가입 진행: auth_mode=signup")
                helper_lines.append("3) 브라우저에서 직접 로그인 후 완료 알림: manual_done")
            elif kind == "clarification":
                helper_lines.append("1) 구체 목표 문장만 보내기")
                helper_lines.append("2) goal=\"...\" username=<id> password=<pw> [email=<email>]")
            helper_lines.append("취소: /cancel")
            text = "\n".join(["추가 입력이 필요해 실행을 잠시 멈췄습니다.", question, *helper_lines]).strip()

            pending = _PendingIntervention(
                kind=kind,
                question=question,
                fields=[str(v) for v in fields],
                event=threading.Event(),
            )
            with self._pending_lock:
                self._pending_interventions[chat_id] = pending

            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._send_text(application.bot, chat_id, text, reply_to_message_id),
                    loop,
                )
                fut.result(timeout=15)
                if attachment_items:
                    fut_attach = asyncio.run_coroutine_threadsafe(
                        self._send_attachments(
                            application.bot,
                            chat_id,
                            attachment_items[:1],
                            reply_to_message_id,
                        ),
                        loop,
                    )
                    fut_attach.result(timeout=15)
            except Exception:
                with self._pending_lock:
                    self._pending_interventions.pop(chat_id, None)
                return {"action": "cancel", "proceed": False}

            waited = pending.event.wait(timeout=600)
            with self._pending_lock:
                self._pending_interventions.pop(chat_id, None)
            if not waited:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._send_text(
                            application.bot,
                            chat_id,
                            "입력 대기 시간(10분) 초과로 실행을 취소했습니다.",
                            reply_to_message_id,
                        ),
                        loop,
                    )
                except Exception:
                    pass
                return {"action": "cancel", "proceed": False}
            return self._parse_intervention_response(kind, pending.response_text)

        return _callback

    @staticmethod
    def _looks_like_live_status_query(text: str) -> bool:
        normalized = re.sub(r"\s+", "", (text or "").strip().lower())
        if not normalized:
            return False
        tokens = (
            "지금뭐하고있어",
            "뭐하고있어",
            "현재뭐해",
            "현재상태",
            "진행상황",
            "어디까지했어",
            "상태어때",
            "whatyoudoing",
            "currentstatus",
        )
        return any(token in normalized for token in tokens)

    def _format_live_status(self, chat_id: int) -> str:
        with self._pending_lock:
            pending = self._pending_interventions.get(chat_id)

        if pending is not None:
            kind = pending.kind or "input"
            question = str(pending.question or "추가 입력 대기 중")
            return (
                "현재 상태\n"
                f"- 추가 입력 대기 중 ({kind})\n"
                f"- 요청 내용: {question}\n"
                "- 응답을 보내면 실행이 계속됩니다."
            )

        with self._state_lock:
            active = self._active_runs.get(chat_id)
            queued = int(self._queued_count_by_chat.get(chat_id, 0) or 0)

        if active is not None:
            elapsed = max(0, int(time.time() - active.started_at))
            cmd = (active.raw_command or "").strip()
            if len(cmd) > 120:
                cmd = cmd[:117] + "..."
            return (
                "현재 상태\n"
                "- 실행 중\n"
                f"- 요청: {cmd}\n"
                f"- 경과: {elapsed}초\n"
                f"- 대기열: {queued}건"
            )

        if queued > 0:
            return (
                "현재 상태\n"
                "- 대기 중\n"
                f"- 대기열: {queued}건"
            )

        return "현재 상태\n- 실행 중인 작업이 없습니다."

    async def _worker_loop(self, application) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            with self._state_lock:
                queued_now = int(self._queued_count_by_chat.get(item.chat_id, 0) or 0)
                if queued_now > 0:
                    self._queued_count_by_chat[item.chat_id] = queued_now - 1
                self._active_runs[item.chat_id] = _ActiveRun(
                    chat_id=item.chat_id,
                    raw_command=item.raw_command,
                    started_at=time.time(),
                )
            sink = _BufferedSink()
            try:
                intervention_cb = self._build_intervention_callback(
                    application,
                    item.chat_id,
                    item.reply_to_message_id,
                )
                result = await asyncio.to_thread(
                    dispatch_command,
                    self.hub_context,
                    item.raw_command,
                    sink,
                    self.memory_store,
                    intervention_cb,
                )
                payload_obj = build_command_payload(self.hub_context, item.raw_command, result)
                if sink.lines:
                    payload_obj["logs"] = sink.lines
                payload_for_text = self._sanitize_payload_for_text(payload_obj)
                report_mode = self._resolve_report_mode()
                if report_mode == "legacy_json_text":
                    payload = json.dumps(payload_for_text, ensure_ascii=False, indent=2)
                else:
                    payload = self._format_payload_text(payload_for_text, mode=report_mode)
                    if not payload:
                        payload = json.dumps(payload_for_text, ensure_ascii=False, indent=2)
                await self._send_text(application.bot, item.chat_id, payload, item.reply_to_message_id)

                attachment_failed = False
                if result.attachments:
                    try:
                        await self._send_attachments(
                            application.bot,
                            item.chat_id,
                            result.attachments,
                            item.reply_to_message_id,
                        )
                    except Exception:
                        attachment_failed = True

                json_failed = False
                if report_mode == "summary_with_json":
                    sent = await self._send_json_report(
                        application.bot,
                        item.chat_id,
                        payload_for_text,
                        item.reply_to_message_id,
                    )
                    json_failed = not sent

                if attachment_failed or json_failed:
                    notes: list[str] = []
                    if attachment_failed:
                        notes.append("스크린샷 첨부 실패")
                    if json_failed:
                        notes.append("상세 JSON 첨부 실패")
                    await self._send_text(
                        application.bot,
                        item.chat_id,
                        "알림: " + ", ".join(notes),
                        item.reply_to_message_id,
                    )
            except Exception as exc:
                await self._send_text(
                    application.bot,
                    item.chat_id,
                    f"명령 실행 중 오류: {exc}",
                    item.reply_to_message_id,
                )
            finally:
                with self._state_lock:
                    self._active_runs.pop(item.chat_id, None)

    async def _normalize_document_command(self, message, context) -> str:
        raw = (message.text or message.caption or "").strip()
        if not message.document:
            return raw
        if not raw.startswith("/plan"):
            return raw
        upload_dir = Path.home() / ".gaia" / "telegram_uploads" / str(message.chat_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = message.document.file_name or f"{message.document.file_unique_id}.bin"
        safe_name = f"{int(time.time())}_{filename.replace('/', '_')}"
        dest = upload_dir / safe_name
        tg_file = await context.bot.get_file(message.document.file_id)
        await tg_file.download_to_drive(custom_path=str(dest))

        parts = raw.split(maxsplit=2)
        if len(parts) >= 2:
            return raw if len(parts) >= 3 else f"{raw} {dest}"
        suffix = dest.suffix.lower()
        if suffix == ".pdf":
            return f"/plan spec {dest}"
        if suffix == ".json":
            return f"/plan plan {dest}"
        return f"/plan resume {dest}"

    async def _notify_admins_pair_request(self, bot, req: _PairRequest) -> None:
        if not self.pairing.admin_ids:
            return
        who = req.full_name or req.username or str(req.chat_id)
        text = (
            "새 페어링 요청이 도착했습니다.\n"
            f"- request_id: {req.request_id}\n"
            f"- chat_id: {req.chat_id}\n"
            f"- user: {who}\n"
            f"- approve: /pair approve {req.request_id}\n"
            f"- reject: /pair reject {req.request_id}"
        )
        for admin_id in sorted(self.pairing.admin_ids):
            try:
                await bot.send_message(chat_id=admin_id, text=text)
            except Exception:
                continue

    async def _handle_pair_command(self, raw: str, chat_id: int, message, context) -> bool:
        normalized = raw.strip()
        lowered = normalized.lower()
        if lowered in {"/whoami", "/chatid"}:
            await message.reply_text(
                f"chat_id={chat_id}\n"
                f"admin={self.pairing.is_admin(chat_id)}\n"
                f"approved={self.pairing.is_approved(chat_id)}"
            )
            return True

        is_start = lowered == "/start"
        if not is_start and not lowered.startswith("/pair"):
            return False

        user = message.from_user
        username = user.username if user and user.username else ""
        full_name = user.full_name if user and user.full_name else ""

        if self.pairing.ensure_bootstrap_admin(chat_id, username=username, full_name=full_name):
            await message.reply_text(
                "초기 관리자 등록 완료: 현재 chat_id가 관리자/승인 사용자로 설정되었습니다.\n"
                "다른 사용자는 /pair request 후 관리자 승인(/pair approve <request_id>)이 필요합니다."
            )
            if is_start:
                return True

        tokens = normalized.split()
        sub = "request"
        if len(tokens) >= 2 and tokens[0].lower() == "/pair":
            sub = tokens[1].lower()
        elif is_start:
            sub = "request"

        if sub in {"help", "h"}:
            await message.reply_text(
                "/pair request\n"
                "/pair status\n"
                "/pair pending (admin)\n"
                "/pair approve <request_id> (admin)\n"
                "/pair reject <request_id> (admin)\n"
                "/pair revoke <chat_id> (admin)\n"
                "/whoami"
            )
            return True

        if sub in {"status"}:
            pending = any(req.chat_id == chat_id for req in self.pairing.pending_rows())
            await message.reply_text(
                f"admin={self.pairing.is_admin(chat_id)}\n"
                f"approved={self.pairing.is_approved(chat_id)}\n"
                f"pending={pending}"
            )
            return True

        if sub in {"request", "req"}:
            if self._allowed(chat_id):
                await message.reply_text("이미 승인된 사용자입니다.")
                return True
            req = self.pairing.request_pairing(chat_id, username=username, full_name=full_name)
            await message.reply_text(
                f"페어링 요청이 접수되었습니다. request_id={req.request_id}\n"
                "관리자 승인 후 명령을 사용할 수 있습니다."
            )
            await self._notify_admins_pair_request(context.bot, req)
            return True

        if not self.pairing.is_admin(chat_id):
            await message.reply_text("관리자만 실행할 수 있는 명령입니다.")
            return True

        if sub in {"pending", "list"}:
            rows = self.pairing.pending_rows()
            if not rows:
                await message.reply_text("대기 중인 페어링 요청이 없습니다.")
                return True
            lines = ["대기 중 요청:"]
            for req in rows[:30]:
                who = req.full_name or req.username or str(req.chat_id)
                lines.append(f"- {req.request_id} chat_id={req.chat_id} user={who}")
            await message.reply_text("\n".join(lines))
            return True

        if sub in {"approve"}:
            if len(tokens) < 3:
                await message.reply_text("사용법: /pair approve <request_id>")
                return True
            request_id = tokens[2].strip()
            req = self.pairing.approve(request_id)
            if not req:
                await message.reply_text(f"요청을 찾지 못했습니다: {request_id}")
                return True
            await message.reply_text(f"승인 완료: request_id={request_id}, chat_id={req.chat_id}")
            try:
                await context.bot.send_message(
                    chat_id=req.chat_id,
                    text="GAIA 사용 승인이 완료되었습니다. 이제 명령을 사용할 수 있습니다.",
                )
            except Exception:
                pass
            return True

        if sub in {"reject", "deny"}:
            if len(tokens) < 3:
                await message.reply_text("사용법: /pair reject <request_id>")
                return True
            request_id = tokens[2].strip()
            req = self.pairing.reject(request_id)
            if not req:
                await message.reply_text(f"요청을 찾지 못했습니다: {request_id}")
                return True
            await message.reply_text(f"거절 완료: request_id={request_id}, chat_id={req.chat_id}")
            try:
                await context.bot.send_message(
                    chat_id=req.chat_id,
                    text="GAIA 사용 요청이 거절되었습니다.",
                )
            except Exception:
                pass
            return True

        if sub in {"revoke", "remove"}:
            if len(tokens) < 3:
                await message.reply_text("사용법: /pair revoke <chat_id>")
                return True
            try:
                target_chat_id = int(tokens[2].strip())
            except ValueError:
                await message.reply_text("chat_id는 숫자여야 합니다.")
                return True
            if self.pairing.revoke(target_chat_id):
                await message.reply_text(f"권한 해제 완료: chat_id={target_chat_id}")
            else:
                await message.reply_text(f"권한 해제 실패(없거나 관리자): chat_id={target_chat_id}")
            return True

        await message.reply_text("알 수 없는 /pair 명령입니다. /pair help")
        return True

    async def handle_message(self, update, context) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        chat_id = int(chat.id)

        raw = (message.text or "").strip()
        if message.document:
            raw = await self._normalize_document_command(message, context)
        if not raw:
            return

        pending: Optional[_PendingIntervention] = None
        with self._pending_lock:
            pending = self._pending_interventions.get(chat_id)
        if pending is not None:
            if self._looks_like_live_status_query(raw):
                await message.reply_text(self._format_live_status(chat_id))
                return
            lowered = raw.strip().lower()
            if lowered.startswith("/pair"):
                await message.reply_text("현재 실행이 추가 입력을 기다리는 중입니다. 응답 텍스트 또는 /cancel을 보내주세요.")
                return
            if lowered.startswith("/") and lowered not in {"/cancel"}:
                await message.reply_text("현재 실행이 추가 입력을 기다리는 중입니다. 응답 텍스트를 보내거나 /cancel을 입력하세요.")
                return
            pending.response_text = "cancel" if lowered in {"/cancel", "cancel", "취소"} else raw
            pending.event.set()
            await message.reply_text("응답을 받았습니다. 실행을 계속합니다.")
            return

        if await self._handle_pair_command(raw, chat_id, message, context):
            return

        if not self._allowed(chat_id):
            await message.reply_text(
                "미승인 사용자입니다. /pair request 로 승인 요청 후 관리자 승인을 받아주세요.\n"
                "chat_id 확인: /whoami"
            )
            return

        if self._looks_like_live_status_query(raw):
            await message.reply_text(self._format_live_status(chat_id))
            return

        position = self.queue.qsize() + 1
        with self._state_lock:
            queued_now = int(self._queued_count_by_chat.get(chat_id, 0) or 0)
            self._queued_count_by_chat[chat_id] = queued_now + 1
        await self.queue.put(
            _CommandEnvelope(
                chat_id=chat_id,
                raw_command=raw,
                reply_to_message_id=message.message_id,
            )
        )
        await message.reply_text(f"queued #{position}: {raw[:120]}")


def _split_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        parts.append(text[start : start + limit])
        start += limit
    return parts


def _load_token(token_file: str) -> str:
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return token


def _parse_bind(raw: str) -> tuple[str, int]:
    text = (raw or "").strip()
    if ":" not in text:
        return text or "127.0.0.1", 8088
    host, port = text.rsplit(":", 1)
    try:
        return host or "127.0.0.1", int(port)
    except ValueError:
        return host or "127.0.0.1", 8088


def run_telegram_bridge(hub_context: HubContext, config: TelegramConfig) -> int:
    try:
        from telegram import Update
        from telegram.ext import ApplicationBuilder, MessageHandler, filters
    except Exception:
        print(
            "Telegram bridge requires python-telegram-bot. "
            "Install dependency and retry.",
        )
        return 2

    token = _load_token(config.token_file)
    if not token:
        print(f"Telegram token file not found or empty: {config.token_file}")
        return 2
    if not config.allowlist:
        print(
            "Telegram admin allowlist 미설정: 첫 번째 /start 사용자가 초기 관리자로 자동 등록됩니다."
        )

    memory_store = MemoryStore(enabled=True)
    try:
        memory_store.garbage_collect(retention_days=30)
    except Exception:
        pass
    bridge = _TelegramBridge(
        hub_context=hub_context,
        config=config,
        memory_store=memory_store,
    )

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(bridge.post_init)
        .post_shutdown(bridge.post_shutdown)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT | filters.COMMAND | filters.Document.ALL, bridge.handle_message))

    if config.mode == "webhook":
        if not config.webhook_url:
            print("Webhook mode requires --tg-webhook-url.")
            return 2
        host, port = _parse_bind(config.webhook_bind)
        print(f"Telegram bridge started (webhook): {host}:{port}")
        app.run_webhook(
            listen=host,
            port=port,
            webhook_url=config.webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        return 0

    print("Telegram bridge started (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0
