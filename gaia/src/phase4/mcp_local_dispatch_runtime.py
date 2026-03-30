from __future__ import annotations

import atexit
import asyncio
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from fastapi import HTTPException
from playwright.async_api import async_playwright

from gaia.src.phase4.mcp_host_runtime import resolve_mcp_target
from gaia.src.phase4.mcp_openclaw_dispatch_runtime import (
    dispatch_openclaw_action,
    dispatch_openclaw_close,
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_LOCAL_PLAYWRIGHT_OWNED = False
_LOCAL_HOST_MODULE: Any = None
_LOCAL_LOOP_THREAD: Optional[threading.Thread] = None
_LOCAL_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOCAL_LOOP_READY = threading.Event()
_LOCAL_LOOP_LOCK = threading.Lock()


@dataclass
class DispatchResult:
    status_code: int
    payload: Dict[str, Any]
    text: str = ""


def _is_local_target(raw_base_url: str | None) -> bool:
    host, _, _ = resolve_mcp_target(raw_base_url)
    return host in _LOCAL_HOSTS


def _openclaw_fallback_enabled() -> bool:
    raw = str(os.getenv("GAIA_OPENCLAW_FALLBACK_BACKEND", "disabled") or "disabled").strip().lower()
    return raw not in {"", "0", "false", "no", "off", "disabled", "none"}


def _should_fallback_from_openclaw(payload: Dict[str, Any], text: str) -> bool:
    if not _openclaw_fallback_enabled():
        return False
    message = " ".join(
        [
            str((payload or {}).get("reason") or ""),
            str((payload or {}).get("error") or ""),
            str(text or ""),
        ]
    ).strip().lower()
    reason_code = str((payload or {}).get("reason_code") or "").strip().lower()
    if reason_code not in {"action_timeout", "request_exception", "http_5xx", "failed"}:
        return False
    return any(
        needle in message
        for needle in (
            "failed to start chrome cdp",
            "chrome cdp websocket",
            "embedded openclaw browser server failed to start",
            "browser not running",
            "gateway closed",
        )
    )


def _should_fallback_from_openclaw_exception(exc: BaseException) -> bool:
    if not _openclaw_fallback_enabled():
        return False
    message = str(exc or "").strip().lower()
    return any(
        needle in message
        for needle in (
            "failed to start chrome cdp",
            "chrome cdp websocket",
            "embedded openclaw browser server failed to start",
            "browser not running",
            "gateway closed",
            "timed out",
            "read timeout",
            "connect timeout",
            "connection refused",
            "max retries exceeded",
        )
    )


def current_browser_backend(raw_base_url: str | None = None) -> str:
    backend = str(os.getenv("GAIA_BROWSER_BACKEND", "") or "").strip().lower()
    if backend in {"gaia", "local", "legacy"}:
        return "gaia"
    if backend in {"openclaw", "open-claw", "oc"}:
        return "openclaw"
    if str(os.getenv("GAIA_OPENCLAW_BASE_URL", "") or "").strip():
        return "openclaw"
    return "openclaw"


async def _ensure_local_host_module() -> Any:
    global _LOCAL_HOST_MODULE
    global _LOCAL_PLAYWRIGHT_OWNED
    if _LOCAL_HOST_MODULE is None:
        import gaia.src.phase4.mcp_host as mcp_host

        _LOCAL_HOST_MODULE = mcp_host
    mcp_host = _LOCAL_HOST_MODULE
    if mcp_host.playwright_instance is None:
        mcp_host.playwright_instance = await async_playwright().start()
        _LOCAL_PLAYWRIGHT_OWNED = True
    return mcp_host


async def _shutdown_local_playwright_async() -> None:
    global _LOCAL_PLAYWRIGHT_OWNED
    if _LOCAL_HOST_MODULE is None or not _LOCAL_PLAYWRIGHT_OWNED:
        return
    try:
        if _LOCAL_HOST_MODULE.playwright_instance is not None:
            await _LOCAL_HOST_MODULE.playwright_instance.stop()
    except Exception:
        pass
    finally:
        try:
            _LOCAL_HOST_MODULE.playwright_instance = None
        except Exception:
            pass
        _LOCAL_PLAYWRIGHT_OWNED = False


def _shutdown_local_playwright() -> None:
    try:
        _run_sync(_shutdown_local_playwright_async())
    except Exception:
        pass
    try:
        _shutdown_local_loop()
    except Exception:
        pass


atexit.register(_shutdown_local_playwright)


def _ensure_local_loop() -> asyncio.AbstractEventLoop:
    global _LOCAL_LOOP_THREAD, _LOCAL_LOOP
    if _LOCAL_LOOP is not None and _LOCAL_LOOP.is_running():
        return _LOCAL_LOOP
    with _LOCAL_LOOP_LOCK:
        if _LOCAL_LOOP is not None and _LOCAL_LOOP.is_running():
            return _LOCAL_LOOP
        _LOCAL_LOOP_READY.clear()

        def _runner() -> None:
            global _LOCAL_LOOP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _LOCAL_LOOP = loop
            _LOCAL_LOOP_READY.set()
            loop.run_forever()
            pending = asyncio.all_tasks(loop)
            if pending:
                for task in pending:
                    task.cancel()
                try:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
            loop.close()

        _LOCAL_LOOP_THREAD = threading.Thread(target=_runner, daemon=True, name="gaia-local-mcp-loop")
        _LOCAL_LOOP_THREAD.start()
        _LOCAL_LOOP_READY.wait(timeout=5.0)
        if _LOCAL_LOOP is None:
            raise RuntimeError("failed to start local MCP event loop")
        return _LOCAL_LOOP


def _shutdown_local_loop() -> None:
    global _LOCAL_LOOP, _LOCAL_LOOP_THREAD
    loop = _LOCAL_LOOP
    thread = _LOCAL_LOOP_THREAD
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(loop.stop)
    except Exception:
        pass
    if thread is not None:
        thread.join(timeout=2.0)
    _LOCAL_LOOP = None
    _LOCAL_LOOP_THREAD = None
    _LOCAL_LOOP_READY.clear()


def _run_sync(awaitable: Any) -> Any:
    loop = _ensure_local_loop()
    future = asyncio.run_coroutine_threadsafe(awaitable, loop)
    return future.result()


async def _dispatch_local_execute_async(
    raw_base_url: str | None,
    *,
    action: str,
    params: Dict[str, Any],
) -> DispatchResult:
    mcp_host = await _ensure_local_host_module()
    request = mcp_host.McpRequest(action=action, params=params)
    try:
        if action == "close_session":
            payload = await mcp_host.close_session(request)
        else:
            payload = await mcp_host.dispatch_execute_action_route(
                request=request,
                namespace=mcp_host.__dict__,
                close_session_fn=mcp_host.close_session,
                mcp_request_cls=mcp_host.McpRequest,
                handle_legacy_action_fn=mcp_host.handle_legacy_action,
                execute_simple_action_fn=mcp_host.execute_simple_action,
                browser_act_fn=mcp_host._browser_act,
                browser_console_get_fn=mcp_host._browser_console_get,
                resolve_session_page_fn=mcp_host._resolve_session_page,
                browser_snapshot_fn=mcp_host._browser_snapshot,
                capture_screenshot_fn=mcp_host.capture_screenshot,
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return DispatchResult(status_code=int(exc.status_code), payload={"detail": detail}, text=str(detail))
    if not isinstance(payload, dict):
        payload = {"result": payload}
    return DispatchResult(status_code=200, payload=payload, text="")


def execute_mcp_action(
    raw_base_url: str | None,
    *,
    action: str,
    params: Dict[str, Any],
    timeout: Any = None,
) -> DispatchResult:
    backend = current_browser_backend(raw_base_url)
    if backend == "openclaw" and action in {"browser_snapshot", "browser_act", "browser_screenshot", "capture_screenshot"}:
        try:
            status_code, payload, text = dispatch_openclaw_action(
                raw_base_url,
                action=action,
                params=dict(params or {}),
                timeout=timeout,
            )
        except Exception as exc:
            if (
                _is_local_target(raw_base_url)
                and _should_fallback_from_openclaw_exception(exc)
            ):
                return _run_sync(_dispatch_local_execute_async(raw_base_url, action=action, params=params))
            raise
        if (
            _is_local_target(raw_base_url)
            and _should_fallback_from_openclaw(payload, text)
        ):
            return _run_sync(_dispatch_local_execute_async(raw_base_url, action=action, params=params))
        return DispatchResult(status_code=int(status_code), payload=payload, text=str(text or ""))
    if _is_local_target(raw_base_url):
        return _run_sync(_dispatch_local_execute_async(raw_base_url, action=action, params=params))

    response = requests.post(
        f"{str(raw_base_url or '').rstrip('/')}/execute",
        json={"action": action, "params": params},
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text or "invalid_json_response"}
    return DispatchResult(status_code=int(response.status_code), payload=payload, text=str(response.text or ""))


def close_mcp_session(
    raw_base_url: str | None,
    *,
    session_id: str,
    timeout: Any = None,
) -> DispatchResult:
    backend = current_browser_backend(raw_base_url)
    if backend == "openclaw":
        status_code, payload, text = dispatch_openclaw_close(
            raw_base_url,
            session_id=session_id,
            timeout=timeout,
        )
        return DispatchResult(status_code=int(status_code), payload=payload, text=str(text or ""))
    params = {"session_id": session_id}
    if _is_local_target(raw_base_url):
        return _run_sync(_dispatch_local_execute_async(raw_base_url, action="close_session", params=params))
    response = requests.post(
        f"{str(raw_base_url or '').rstrip('/')}/close_session",
        json={"action": "close_session", "params": params},
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"error": response.text or "invalid_json_response"}
    return DispatchResult(status_code=int(response.status_code), payload=payload, text=str(response.text or ""))
