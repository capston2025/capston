from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError


def _clamp_ms(value: int, *, low: int = 50, high: int = 8000) -> int:
    try:
        num = int(value)
    except Exception:
        num = low
    return int(max(low, min(high, num)))


async def watch_after_trusted_click(
    page: Any,
    click_fn: Callable[[], Awaitable[None]],
    *,
    watch_ms: int = 1200,
    settle_ms: Optional[int] = None,
    wait_until: str = "commit",
    watch_popup: bool = True,
    watch_navigation: bool = True,
    watch_dialog: bool = True,
    auto_dismiss_dialog: bool = True,
    auto_close_popup: bool = False,
) -> Dict[str, Any]:
    """Run a trusted click and watch for strong post-click signals.

    Returned keys are JSON-safe and can be stored in attempt logs.
    """
    started = time.monotonic()
    watch_ms = _clamp_ms(watch_ms, low=100, high=8000)
    if settle_ms is None:
        settle_ms = watch_ms
    settle_ms = _clamp_ms(settle_ms, low=50, high=watch_ms)

    before_url = str(getattr(page, "url", "") or "")
    result: Dict[str, Any] = {
        "before_url": before_url,
        "after_url": None,
        "nav_detected": False,
        "popup_detected": False,
        "popup_url": None,
        "popup_closed": False,
        "dialog_detected": False,
        "dialog_type": None,
        "dialog_message": None,
        "click_error": None,
        "watch_ms": watch_ms,
        "settle_ms": settle_ms,
        "duration_ms": 0,
    }

    popup_page: Optional[Any] = None
    nav_task: Optional[asyncio.Task] = None
    popup_task: Optional[asyncio.Task] = None

    async def _on_dialog(dialog: Any) -> None:
        result["dialog_detected"] = True
        result["dialog_type"] = str(getattr(dialog, "type", "") or "")
        result["dialog_message"] = str(getattr(dialog, "message", "") or "")
        if auto_dismiss_dialog:
            try:
                await dialog.dismiss()
            except Exception:
                pass

    if watch_dialog:
        try:
            page.once("dialog", lambda d: asyncio.create_task(_on_dialog(d)))
        except Exception:
            pass

    try:
        if watch_navigation:
            nav_task = asyncio.create_task(
                page.wait_for_url(
                    lambda url: str(url) != before_url,
                    timeout=watch_ms,
                    wait_until=wait_until,
                )
            )
        if watch_popup:
            popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=watch_ms))

        try:
            await click_fn()
        except Exception as click_exc:
            result["click_error"] = str(click_exc)

        pending = [task for task in (nav_task, popup_task) if isinstance(task, asyncio.Task)]
        if pending:
            try:
                await asyncio.wait(pending, timeout=(settle_ms / 1000.0))
            except Exception:
                pass

        if nav_task is not None:
            if nav_task.done() and not nav_task.cancelled():
                try:
                    await nav_task
                    result["nav_detected"] = True
                except PlaywrightTimeoutError:
                    pass
                except Exception as nav_exc:
                    result["nav_error"] = str(nav_exc)
            else:
                nav_task.cancel()

        if popup_task is not None:
            if popup_task.done() and not popup_task.cancelled():
                try:
                    popup_page = await popup_task
                    result["popup_detected"] = True
                    result["popup_url"] = str(getattr(popup_page, "url", "") or "")
                    if auto_close_popup and popup_page is not None:
                        try:
                            await popup_page.close()
                            result["popup_closed"] = True
                        except Exception:
                            result["popup_closed"] = False
                except PlaywrightTimeoutError:
                    pass
                except Exception as popup_exc:
                    result["popup_error"] = str(popup_exc)
            else:
                popup_task.cancel()

        result["after_url"] = str(getattr(page, "url", "") or "")
        return result
    finally:
        for task in (nav_task, popup_task):
            if task is not None and not task.done():
                task.cancel()
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
