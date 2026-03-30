from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from playwright.async_api import Browser, BrowserContext, CDPSession, Page, Playwright

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
        self.context: Optional[BrowserContext] = None
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
        self._screencast_tasks: set[asyncio.Task[Any]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self.last_target_abort_reason: str = ""
        self.last_target_abort_at: float = 0.0
        self._teardown_in_progress: bool = False
        self._closed: bool = False

    def _browser_alive(self) -> bool:
        if self.browser is None:
            return False
        try:
            return bool(self.browser.is_connected())
        except Exception:
            return False

    def _context_alive(self) -> bool:
        if self.context is None or not self._browser_alive():
            return False
        try:
            _ = self.context.pages
            return True
        except Exception:
            return False

    def _page_alive(self) -> bool:
        if self.page is None or self.context is None or not self._browser_alive():
            return False
        try:
            return not bool(self.page.is_closed())
        except Exception:
            return False

    def _should_start_screencast(self) -> bool:
        if self._screencast_subscribers:
            return True
        value = str(
            self.env_overrides.get("force_screencast")
            or os.getenv("GAIA_ENABLE_IDLE_SCREENCAST", "0")
        ).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _reset_page_runtime_state(self, *, preserve_current_url: bool) -> None:
        self.screencast_active = False
        self.dialog_listener_armed = False
        self.file_chooser_listener_armed = False
        self.current_snapshot_id = ""
        self.current_dom_hash = ""
        self.snapshots = {}
        self.stored_css_values = {}
        if not preserve_current_url:
            self.current_url = ""

    def _mark_target_abort(self, reason: str) -> None:
        self.last_target_abort_reason = str(reason or "").strip()
        self.last_target_abort_at = asyncio.get_event_loop().time()

    async def _ensure_cdp_session(self) -> Optional[CDPSession]:
        if self.cdp_session is not None:
            return self.cdp_session
        if not self.page or not self.context or not self._page_alive():
            return None
        try:
            self.cdp_session = await self.page.context.new_cdp_session(self.page)
            return self.cdp_session
        except Exception:
            return None

    async def _terminate_target_execution(self, *, reason: str = "") -> None:
        session = await self._ensure_cdp_session()
        if session is None:
            return
        try:
            await session.send("Runtime.terminateExecution")
            if reason:
                self._mark_target_abort(reason)
        except Exception:
            pass

    async def _apply_page_stealth(self, page: Page) -> None:
        await page.add_init_script("""
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

    async def _recreate_page(self) -> Page:
        if self.browser is None:
            raise HTTPException(status_code=503, detail="Browser not initialized")
        await self._terminate_target_execution(reason="recreate_page")
        if self.cdp_session is not None:
            try:
                await self.cdp_session.detach()
            except Exception:
                pass
            self.cdp_session = None
        self._reset_page_runtime_state(preserve_current_url=True)
        if self.page is not None:
            try:
                if not self.page.is_closed():
                    await self.page.close()
            except Exception:
                pass
            finally:
                self.page = None
        reused_existing_context = False
        if self._context_alive():
            try:
                self.page = await self.context.new_page()
                reused_existing_context = True
            except Exception:
                try:
                    await self.context.close()
                except Exception:
                    pass
                finally:
                    self.context = None
        if not reused_existing_context:
            if self.context is not None:
                try:
                    await self.context.close()
                except Exception:
                    pass
                finally:
                    self.context = None
            self.context = await self.browser.new_context()
            self.page = await self.context.new_page()
        await self._apply_page_stealth(self.page)
        if self._should_start_screencast():
            await self.start_screencast()
        if self.current_url:
            try:
                await self.page.goto(self.current_url, timeout=30000)
            except Exception:
                pass
        return self.page

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
        async with self._lifecycle_lock:
            if not self._browser_alive():
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

                self.context = await self.browser.new_context()
                self.page = await self.context.new_page()
                await self._apply_page_stealth(self.page)
                if self._should_start_screencast():
                    await self.start_screencast()
            elif not self._page_alive():
                await self._recreate_page()

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
            if self._teardown_in_progress or self._closed:
                return
            self._track_background_task(asyncio.create_task(_handle_dialog(dialog)))

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
            if self._teardown_in_progress or self._closed:
                return
            self._track_background_task(asyncio.create_task(_handle_file_chooser(file_chooser)))

        self.page.on("filechooser", _on_file_chooser)
        self.file_chooser_listener_armed = True

    async def start_screencast(self):
        """CDP 스크린캐스트를 시작합니다."""
        if not self._should_start_screencast():
            return
        if self.page and not self.cdp_session:
            try:
                self.cdp_session = await self._ensure_cdp_session()
                if not self.cdp_session:
                    return
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

    def _track_background_task(self, task: asyncio.Task[Any]) -> None:
        self._screencast_tasks.add(task)

        def _cleanup(done_task: asyncio.Task[Any]) -> None:
            self._screencast_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._log_warning("[CDP Screencast] Background task failed: %s", exc)

        task.add_done_callback(_cleanup)

    async def _fanout_screencast_frame(self, frame_data: str) -> None:
        if not frame_data or not self._screencast_subscribers:
            return
        disconnected_clients = []
        payload = {
            "type": "screencast_frame",
            "session_id": self.session_id,
            "frame": frame_data,
            "timestamp": asyncio.get_event_loop().time(),
        }
        for ws in list(self._screencast_subscribers):
            try:
                await ws.send_json(payload)
            except Exception as exc:
                self._log_warning("[CDP Screencast] Failed to send to subscriber: %s", exc)
                disconnected_clients.append(ws)
        for ws in disconnected_clients:
            if ws in self._screencast_subscribers:
                self._screencast_subscribers.remove(ws)
        if not self._screencast_subscribers and self.screencast_active:
            self._track_background_task(asyncio.create_task(self.stop_screencast()))

    async def _handle_screencast_frame(self, payload: Dict[str, Any]):
        if self._teardown_in_progress or self._closed:
            return
        frame_data = payload.get("data")
        session_ack = payload.get("sessionId")

        if frame_data:
            self._frame_setter(frame_data)

        if self.cdp_session and session_ack:
            try:
                await self.cdp_session.send("Page.screencastFrameAck", {"sessionId": session_ack})
            except Exception as exc:
                self._log_warning("[CDP Screencast] Failed to ack frame: %s", exc)
                self.screencast_active = False
                try:
                    await self.cdp_session.detach()
                except Exception:
                    pass
                self.cdp_session = None
                return

        if frame_data and self._screencast_subscribers:
            self._track_background_task(asyncio.create_task(self._fanout_screencast_frame(frame_data)))

    async def stop_screencast(self):
        if self.cdp_session and self.screencast_active:
            try:
                await self.cdp_session.send("Page.stopScreencast")
                self.screencast_active = False
                self._log_info("[CDP Screencast] Stopped for session %s", self.session_id)
            except Exception as exc:
                self._log_warning("[CDP Screencast] Failed to stop: %s", exc)

    async def force_disconnect_target(self, *, reason: str = "", hard: bool = False) -> None:
        async with self._lifecycle_lock:
            self._teardown_in_progress = True
            pending_tasks = list(self._screencast_tasks)
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                try:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                except Exception:
                    pass
            self._screencast_tasks.clear()
            if self._page_alive():
                await self._terminate_target_execution(reason=reason or "force_disconnect_target")
            if self.screencast_active:
                try:
                    await self.stop_screencast()
                except Exception as exc:
                    self._log_warning("[BrowserSession.force_disconnect_target] stop_screencast failed: %s", exc)
                finally:
                    self.screencast_active = False
            if self.cdp_session:
                try:
                    await self.cdp_session.detach()
                except Exception as exc:
                    self._log_warning("[BrowserSession.force_disconnect_target] cdp detach failed: %s", exc)
                finally:
                    self.cdp_session = None
            if self.page:
                try:
                    if not self.page.is_closed():
                        await self.page.close()
                except Exception as exc:
                    self._log_warning("[BrowserSession.force_disconnect_target] page close failed: %s", exc)
                finally:
                    self.page = None
            if hard and self.context:
                try:
                    await self.context.close()
                except Exception as exc:
                    self._log_warning("[BrowserSession.force_disconnect_target] context close failed: %s", exc)
                finally:
                    self.context = None
            if hard and self.browser:
                try:
                    await self.browser.close()
                except Exception as exc:
                    self._log_warning("[BrowserSession.force_disconnect_target] browser close failed: %s", exc)
                finally:
                    self.browser = None
            self._reset_page_runtime_state(preserve_current_url=True)
            self._teardown_in_progress = False
            if reason:
                self._log_warning("[BrowserSession.force_disconnect_target] session=%s reason=%s", self.session_id, reason)

    async def close(self):
        async with self._lifecycle_lock:
            self._teardown_in_progress = True
            pending_tasks = list(self._screencast_tasks)
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                try:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                except Exception:
                    pass
            self._screencast_tasks.clear()
            if self._page_alive():
                await self._terminate_target_execution(reason="session_close")
            if self.screencast_active:
                try:
                    await self.stop_screencast()
                except Exception as exc:
                    self._log_warning("[BrowserSession.close] stop_screencast failed: %s", exc)
                finally:
                    self.screencast_active = False

            if self.cdp_session:
                try:
                    await self.cdp_session.detach()
                except Exception as exc:
                    self._log_warning("[BrowserSession.close] cdp detach failed: %s", exc)
                finally:
                    self.cdp_session = None

            if self.page:
                try:
                    if not self.page.is_closed():
                        await self.page.close()
                except Exception as exc:
                    self._log_warning("[BrowserSession.close] page close failed: %s", exc)
                finally:
                    self.page = None

            if self.context:
                try:
                    await self.context.close()
                except Exception as exc:
                    self._log_warning("[BrowserSession.close] context close failed: %s", exc)
                finally:
                    self.context = None

            if self.browser:
                try:
                    await self.browser.close()
                except Exception as exc:
                    self._log_warning("[BrowserSession.close] browser close failed: %s", exc)
                finally:
                    self.browser = None
            self._reset_page_runtime_state(preserve_current_url=False)
            self._closed = True


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
