from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from playwright.async_api import Browser, Page


def coerce_tab_id(tab_id: Any) -> Optional[int]:
    if tab_id is None:
        return None
    if isinstance(tab_id, bool):
        return None
    if isinstance(tab_id, int):
        return tab_id
    text = str(tab_id).strip()
    if not text:
        return None
    lowered = text.lower()
    for prefix in ("tab:", "tab-", "tab_"):
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    try:
        return int(text)
    except Exception:
        return None


async def resolve_page_from_tab_identifier(
    *,
    pages: List[Page],
    tab_identifier: Any,
    browser: Optional[Browser],
    get_page_target_id_fn: Callable[[Page], Awaitable[str]],
    list_browser_targets_fn: Callable[[Optional[Browser]], Awaitable[List[Dict[str, str]]]],
) -> Tuple[str, Optional[int], Optional[Page], List[str]]:
    idx = coerce_tab_id(tab_identifier)
    if idx is not None:
        if 0 <= idx < len(pages):
            return "ok", idx, pages[idx], []
        return "not_found", None, None, []

    needle = str(tab_identifier or "").strip()
    if not needle:
        return "not_found", None, None, []

    exact_match: Optional[Tuple[int, Page, str]] = None
    prefix_matches: List[Tuple[int, Page, str]] = []
    lower = needle.lower()
    for idx2, candidate in enumerate(pages):
        target_id = await get_page_target_id_fn(candidate)
        if not target_id:
            continue
        if target_id == needle:
            exact_match = (idx2, candidate, target_id)
            break
        if target_id.lower().startswith(lower):
            prefix_matches.append((idx2, candidate, target_id))

    if exact_match is not None:
        idx2, candidate, _ = exact_match
        return "ok", idx2, candidate, []
    if len(prefix_matches) == 1:
        idx2, candidate, _ = prefix_matches[0]
        return "ok", idx2, candidate, []
    if len(prefix_matches) > 1:
        return "ambiguous", None, None, [item[2] for item in prefix_matches]

    targets = await list_browser_targets_fn(browser)
    if targets:
        target_exact = next((t for t in targets if t.get("targetId") == needle), None)
        if target_exact is None:
            target_prefixes = [t for t in targets if str(t.get("targetId", "")).lower().startswith(lower)]
            if len(target_prefixes) > 1:
                return "ambiguous", None, None, [str(t.get("targetId") or "") for t in target_prefixes]
            target_exact = target_prefixes[0] if len(target_prefixes) == 1 else None
        if target_exact is not None:
            target_id = str(target_exact.get("targetId") or "")
            target_url = str(target_exact.get("url") or "")
            url_matches = [idx3 for idx3, p in enumerate(pages) if str(p.url or "") == target_url]
            if len(url_matches) == 1:
                idx3 = url_matches[0]
                return "ok", idx3, pages[idx3], []
            if len(url_matches) > 1:
                same_url_targets = [t for t in targets if str(t.get("url") or "") == target_url]
                if len(same_url_targets) == len(url_matches):
                    target_index = next(
                        (i for i, t in enumerate(same_url_targets) if str(t.get("targetId") or "") == target_id),
                        -1,
                    )
                    if 0 <= target_index < len(url_matches):
                        idx3 = url_matches[target_index]
                        return "ok", idx3, pages[idx3], []

    return "not_found", None, None, []


async def resolve_session_page(
    *,
    session_id: str,
    tab_id: Optional[Any],
    active_sessions: Dict[str, Any],
    ensure_session_fn: Callable[..., Any],
    playwright_getter_fn: Callable[[], Any],
    screencast_subscribers: List[Any],
    frame_setter: Callable[[str], None],
    logger: Any,
    resolve_page_from_tab_identifier_fn: Callable[..., Awaitable[Tuple[str, Optional[int], Optional[Page], List[str]]]],
) -> Tuple[Any, Page]:
    session = ensure_session_fn(
        active_sessions=active_sessions,
        session_id=session_id,
        playwright_getter=playwright_getter_fn,
        screencast_subscribers=screencast_subscribers,
        frame_setter=frame_setter,
        logger=logger,
    )
    page = await session.get_or_create_page()

    if tab_id is not None:
        pages = list(page.context.pages)
        status, _, resolved_page, matches = await resolve_page_from_tab_identifier_fn(
            pages,
            tab_id,
            session.browser,
        )
        if status == "ok" and resolved_page is not None:
            page = resolved_page
        elif len(pages) == 1:
            page = pages[0]
        elif status == "ambiguous":
            raise HTTPException(
                status_code=400,
                detail={
                    "reason_code": "ambiguous_target_id",
                    "message": "ambiguous target id prefix",
                    "matches": matches,
                },
            )
        else:
            raise HTTPException(
                status_code=404,
                detail={
                    "reason_code": "not_found",
                    "message": f"tab not found: {tab_id}",
                },
            )

    if session.page is not page:
        session.page = page
        session.dialog_listener_armed = False
        session.file_chooser_listener_armed = False

    session.observability.attach_page(page)
    session._ensure_dialog_listener()
    session._ensure_file_chooser_listener()
    return session, page
