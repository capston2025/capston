"""Telegram bridge for GAIA Chat Hub."""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from gaia.chat_hub import HubContext, dispatch_command
from gaia.src.phase4.memory.store import MemoryStore


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
        self.pairing = _PairingState(
            path=Path(config.pairing_file),
            configured_admins=config.allowlist,
        )

    async def post_init(self, _application) -> None:
        self.loop = asyncio.get_running_loop()
        self.worker_task = asyncio.create_task(self._worker_loop(_application))

    async def post_shutdown(self, _application) -> None:
        await self.queue.put(None)
        if self.worker_task:
            await self.worker_task

    def _allowed(self, chat_id: int) -> bool:
        return self.pairing.is_approved(chat_id)

    async def _send_text(self, bot, chat_id: int, text: str, reply_to_message_id: int | None) -> None:
        chunks = _split_text(text, limit=3900)
        for chunk in chunks:
            kwargs = {"chat_id": chat_id, "text": chunk}
            if reply_to_message_id is not None:
                kwargs["reply_to_message_id"] = reply_to_message_id
            await bot.send_message(**kwargs)

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

            helper_lines = []
            if kind == "auth":
                helper_lines.append("응답 형식: username=<id_or_email> password=<pw> [email=<email>]")
                helper_lines.append("또는: manual_done")
            elif kind == "clarification":
                helper_lines.append("응답 형식: <구체 목표 문장>")
                helper_lines.append("또는: goal=\"...\" username=<id> password=<pw> [email=<email>]")
            helper_lines.append("취소: /cancel")
            text = "\n".join(["추가 입력 요청", question, *helper_lines]).strip()

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

    async def _worker_loop(self, application) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                return
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
                lines = [f"command: {item.raw_command[:120]}", f"status: {result.status}"]
                lines.extend(sink.lines)
                if result.output:
                    lines.append(result.output)
                if not any("exit_code" in line for line in lines):
                    lines.append(f"exit_code={result.code}")
                payload = "\n".join(line for line in lines if line).strip()
                if not payload:
                    payload = "완료"
                await self._send_text(application.bot, item.chat_id, payload, item.reply_to_message_id)
            except Exception as exc:
                await self._send_text(
                    application.bot,
                    item.chat_id,
                    f"명령 실행 중 오류: {exc}",
                    item.reply_to_message_id,
                )

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

        position = self.queue.qsize() + 1
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
