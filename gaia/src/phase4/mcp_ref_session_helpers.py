from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from weakref import WeakKeyDictionary

from playwright.async_api import Browser, CDPSession, Page

from gaia.src.phase4.mcp_browser_session import BrowserSession
from gaia.src.phase4.mcp_tab_resolution import (
    resolve_page_from_tab_identifier as _resolve_page_from_tab_identifier_impl,
    resolve_session_page as _resolve_session_page_impl,
)

_page_target_id_cache: "WeakKeyDictionary[Page, str]" = WeakKeyDictionary()


async def _get_page_target_id(page: Page) -> str:
    cached = _page_target_id_cache.get(page)
    if cached:
        return cached

    cdp_session: Optional[CDPSession] = None
    try:
        cdp_session = await page.context.new_cdp_session(page)
        info = await cdp_session.send("Target.getTargetInfo")
        target_info = info.get("targetInfo") if isinstance(info, dict) else {}
        target_id = str((target_info or {}).get("targetId") or "").strip()
        if target_id:
            _page_target_id_cache[page] = target_id
        return target_id
    except Exception:
        return ""
    finally:
        if cdp_session is not None:
            try:
                await cdp_session.detach()
            except Exception:
                pass


async def _list_browser_targets(browser: Optional[Browser]) -> List[Dict[str, str]]:
    if browser is None:
        return []
    browser_cdp: Optional[CDPSession] = None
    try:
        browser_cdp = await browser.new_browser_cdp_session()
        payload = await browser_cdp.send("Target.getTargets")
        infos = payload.get("targetInfos") if isinstance(payload, dict) else []
        out: List[Dict[str, str]] = []
        if isinstance(infos, list):
            for info in infos:
                if not isinstance(info, dict):
                    continue
                target_id = str(info.get("targetId") or "").strip()
                target_url = str(info.get("url") or "").strip()
                if target_id:
                    out.append({"targetId": target_id, "url": target_url})
        return out
    except Exception:
        return []
    finally:
        if browser_cdp is not None:
            try:
                await browser_cdp.detach()
            except Exception:
                pass


async def _resolve_page_from_tab_identifier(
    pages: List[Page],
    tab_identifier: Any,
    browser: Optional[Browser] = None,
) -> Tuple[str, Optional[int], Optional[Page], List[str]]:
    return await _resolve_page_from_tab_identifier_impl(
        pages=pages,
        tab_identifier=tab_identifier,
        browser=browser,
        get_page_target_id_fn=_get_page_target_id,
        list_browser_targets_fn=_list_browser_targets,
    )


async def resolve_session_page_for_ref_action(
    *,
    session_id: str,
    tab_id: Optional[Any],
    active_sessions: Dict[str, BrowserSession],
    ensure_session_fn: Callable[[str], Any],
    playwright_getter_fn: Callable[[], Any],
    screencast_subscribers: List[Any],
    frame_setter: Callable[[Optional[str]], Any],
    logger: Any,
) -> Tuple[BrowserSession, Page]:
    return await _resolve_session_page_impl(
        session_id=session_id,
        tab_id=tab_id,
        active_sessions=active_sessions,
        ensure_session_fn=ensure_session_fn,
        playwright_getter_fn=playwright_getter_fn,
        screencast_subscribers=screencast_subscribers,
        frame_setter=frame_setter,
        logger=logger,
        resolve_page_from_tab_identifier_fn=_resolve_page_from_tab_identifier,
    )
