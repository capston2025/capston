from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from playwright.async_api import Browser, CDPSession, Page, Playwright

from gaia.src.phase4.observability import SessionObservability


class BrowserSession:
    """상태 기반 테스트를 위해 지속적인 브라우저 세션을 유지합니다."""

    def __init__(
        self,
        session_id: str,
        *,
        playwright_getter: Callable[[], Optional[Playwright]],
        screencast_subscribers: List[Any],
        frame_setter: Callable[[str], None],
        logger: Optional[logging.Logger] = None,
    ):
        self.session_id = session_id
        self._playwright_getter = playwright_getter
        self._screencast_subscribers = screencast_subscribers
        self._frame_setter = frame_setter
        self._logger = logger

        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.current_url: str = ""
        self.cdp_session: Optional[CDPSession] = None
        self.screencast_active: bool = False
        self.stored_css_values: Dict[str, str] = {}
        self.snapshot_epoch: int = 0
        self.current_snapshot_id: str = ""
        self.current_dom_hash: str = ""
        self.snapshots: Dict[str, Dict[str, Any]] = {}
        self.observability = SessionObservability()
        self.trace_active: bool = False
        self.trace_path: str = ""
        self.dialog_listener_armed: bool = False
        self.dialog_mode: str = "dismiss"
        self.dialog_prompt_text: str = ""
        self.file_chooser_listener_armed: bool = False
        self.file_chooser_files: List[str] = []
        self.env_overrides: Dict[str, Any] = {}

    def _log_info(self, msg: str, *args: Any) -> None:
        if self._logger:
            self._logger.info(msg, *args)
        else:
            if args:
                msg = msg % args
            print(msg)

    def _log_warning(self, msg: str, *args: Any) -> None:
        if self._logger:
            self._logger.warning(msg, *args)
        else:
            if args:
                msg = msg % args
            print(msg)

    async def get_or_create_page(self) -> Page:
        """기존 페이지를 가져오거나 새 브라우저 세션을 생성합니다."""
        if not self.browser:
            playwright_instance = self._playwright_getter()
            if not playwright_instance:
                raise HTTPException(status_code=503, detail="Playwright not initialized")

            try:
                self.browser = await playwright_instance.chromium.launch(
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--disable-web-security",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                )
            except Exception as exc:
                msg = str(exc)
                if "Executable doesn't exist" in msg or "browserType.launch" in msg:
                    raise HTTPException(
                        status_code=503,
                        detail=(
                            "Chromium executable not found. "
                            "Run: python -m playwright install chromium"
                        ),
                    ) from exc
                raise

            self.page = await self.browser.new_page()
            await self.page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                });

                window.chrome = {
                    runtime: {},
                };

                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });

                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ko-KR', 'ko', 'en-US', 'en'],
                });
            """)
            await self.start_screencast()

        if self.page:
            self.observability.attach_page(self.page)
            self._ensure_dialog_listener()
            self._ensure_file_chooser_listener()
        return self.page

    def _ensure_dialog_listener(self) -> None:
        if not self.page or self.dialog_listener_armed:
            return

        async def _handle_dialog(dialog: Any):
            payload = {
                "type": dialog.type,
                "message": dialog.message,
                "default_value": dialog.default_value,
                "mode": self.dialog_mode,
            }
            self.observability.add_dialog_event(payload)
            try:
                if self.dialog_mode == "accept":
                    await dialog.accept(self.dialog_prompt_text or "")
                else:
                    await dialog.dismiss()
            except Exception as exc:
                self.observability.add_dialog_event(
                    {
                        "type": dialog.type,
                        "mode": self.dialog_mode,
                        "error": str(exc),
                    }
                )

        def _on_dialog(dialog: Any):
            asyncio.create_task(_handle_dialog(dialog))

        self.page.on("dialog", _on_dialog)
        self.dialog_listener_armed = True

    def _ensure_file_chooser_listener(self) -> None:
        if not self.page or self.file_chooser_listener_armed:
            return

        async def _handle_file_chooser(file_chooser: Any):
            files = [p for p in self.file_chooser_files if p]
            if not files:
                return
            try:
                await file_chooser.set_files(files)
            except Exception as exc:
                self.observability.add_dialog_event(
                    {"type": "file_chooser", "error": str(exc), "files": files}
                )

        def _on_file_chooser(file_chooser: Any):
            asyncio.create_task(_handle_file_chooser(file_chooser))

        self.page.on("filechooser", _on_file_chooser)
        self.file_chooser_listener_armed = True

    async def start_screencast(self):
        """CDP 스크린캐스트를 시작합니다."""
        if self.page and not self.cdp_session:
            try:
                self.cdp_session = await self.page.context.new_cdp_session(self.page)
                self.cdp_session.on("Page.screencastFrame", self._handle_screencast_frame)
                await self.cdp_session.send(
                    "Page.startScreencast",
                    {
                        "format": "jpeg",
                        "quality": 80,
                        "maxWidth": 1280,
                        "maxHeight": 720,
                        "everyNthFrame": 3,
                    },
                )
                self.screencast_active = True
                self._log_info("[CDP Screencast] Started for session %s", self.session_id)
            except Exception as exc:
                self._log_warning("[CDP Screencast] Failed to start: %s", exc)

    async def _handle_screencast_frame(self, payload: Dict[str, Any]):
        frame_data = payload.get("data")
        session_ack = payload.get("sessionId")

        if frame_data:
            self._frame_setter(frame_data)
            disconnected_clients = []
            for ws in self._screencast_subscribers:
                try:
                    await ws.send_json(
                        {
                            "type": "screencast_frame",
                            "session_id": self.session_id,
                            "frame": frame_data,
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
                except Exception as exc:
                    self._log_warning("[CDP Screencast] Failed to send to subscriber: %s", exc)
                    disconnected_clients.append(ws)

            for ws in disconnected_clients:
                if ws in self._screencast_subscribers:
                    self._screencast_subscribers.remove(ws)

        if self.cdp_session and session_ack:
            try:
                await self.cdp_session.send("Page.screencastFrameAck", {"sessionId": session_ack})
            except Exception as exc:
                self._log_warning("[CDP Screencast] Failed to ack frame: %s", exc)

    async def stop_screencast(self):
        if self.cdp_session and self.screencast_active:
            try:
                await self.cdp_session.send("Page.stopScreencast")
                self.screencast_active = False
                self._log_info("[CDP Screencast] Stopped for session %s", self.session_id)
            except Exception as exc:
                self._log_warning("[CDP Screencast] Failed to stop: %s", exc)

    async def close(self):
        if self.screencast_active:
            await self.stop_screencast()

        if self.cdp_session:
            await self.cdp_session.detach()
            self.cdp_session = None

        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None


def ensure_session(
    *,
    active_sessions: Dict[str, BrowserSession],
    session_id: str,
    playwright_getter: Callable[[], Optional[Playwright]],
    screencast_subscribers: List[Any],
    frame_setter: Callable[[str], None],
    logger: Optional[logging.Logger] = None,
) -> BrowserSession:
    session = active_sessions.get(session_id)
    if session is None:
        session = BrowserSession(
            session_id,
            playwright_getter=playwright_getter,
            screencast_subscribers=screencast_subscribers,
            frame_setter=frame_setter,
            logger=logger,
        )
        active_sessions[session_id] = session
    return session
