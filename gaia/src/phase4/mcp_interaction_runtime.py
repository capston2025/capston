from __future__ import annotations

from typing import Any, Dict, Optional

from gaia.src.phase4.mcp_interaction_handlers import build_interaction_handlers

_INTERACTION_HANDLERS: Optional[Dict[str, Any]] = None


def get_interaction_handlers(
    *,
    resolve_session_page_fn,
    get_tab_index_fn,
    build_error_fn,
    browser_state_store_cls,
) -> Dict[str, Any]:
    global _INTERACTION_HANDLERS
    if _INTERACTION_HANDLERS is None:
        _INTERACTION_HANDLERS = build_interaction_handlers(
            resolve_session_page_fn=resolve_session_page_fn,
            get_tab_index_fn=get_tab_index_fn,
            build_error_fn=build_error_fn,
            browser_state_store_cls=browser_state_store_cls,
        )
    return _INTERACTION_HANDLERS


async def browser_dialog_arm(params: Dict[str, Any], *, handlers: Dict[str, Any]) -> Dict[str, Any]:
    return await handlers["dialog_arm"](params)


async def browser_file_chooser_arm(params: Dict[str, Any], *, handlers: Dict[str, Any]) -> Dict[str, Any]:
    return await handlers["file_chooser_arm"](params)


async def browser_download_wait(params: Dict[str, Any], *, handlers: Dict[str, Any]) -> Dict[str, Any]:
    return await handlers["download_wait"](params)


async def browser_state(params: Dict[str, Any], *, handlers: Dict[str, Any]) -> Dict[str, Any]:
    return await handlers["state"](params)


async def browser_env(params: Dict[str, Any], *, handlers: Dict[str, Any]) -> Dict[str, Any]:
    return await handlers["env"](params)
