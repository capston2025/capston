from __future__ import annotations

from typing import Any, Dict


async def collect_ref_action_result_context(
    *,
    page: Any,
    session: Any,
    get_tab_index_fn,
    safe_page_url_fn,
    safe_capture_page_screenshot_base64_fn,
) -> Dict[str, Any]:
    current_url = safe_page_url_fn(page, getattr(session, "current_url", ""))
    session.current_url = current_url
    screenshot_base64 = await safe_capture_page_screenshot_base64_fn(page)
    tab_id = get_tab_index_fn(page)

    return {
        "current_url": current_url,
        "screenshot_base64": screenshot_base64,
        "tab_id": tab_id,
    }
