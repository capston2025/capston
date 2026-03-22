from __future__ import annotations

import base64
from typing import Any, Optional


def is_retryable_page_detach_error(exc: BaseException) -> bool:
    message = str(exc or "").strip().lower()
    if not message:
        return False
    return (
        "frame has been detached" in message
        or "target page, context or browser has been closed" in message
    )


async def safe_capture_page_screenshot_base64(page: Any) -> Optional[str]:
    try:
        screenshot_bytes = await page.screenshot(full_page=False)
    except Exception as exc:
        if is_retryable_page_detach_error(exc):
            return None
        return None
    try:
        return base64.b64encode(screenshot_bytes).decode("utf-8")
    except Exception:
        return None


def safe_page_url(page: Any, fallback: str = "") -> str:
    try:
        return str(getattr(page, "url", "") or fallback or "")
    except Exception:
        return str(fallback or "")


async def goto_with_retry(
    page: Any,
    url: str,
    *,
    timeout: int,
    wait_for_networkidle: bool = True,
) -> None:
    try:
        await page.goto(url, timeout=timeout)
    except Exception as exc:
        if not is_retryable_page_detach_error(exc):
            raise
        await page.wait_for_timeout(150)
        await page.goto(url, timeout=timeout)
    if wait_for_networkidle:
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
