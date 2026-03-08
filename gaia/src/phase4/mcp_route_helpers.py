"""Route helper functions for MCP host."""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect


async def close_session_impl(active_sessions: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    if session_id in active_sessions:
        session = active_sessions[session_id]
        try:
            await session.close()
        finally:
            active_sessions.pop(session_id, None)
        return {"success": True, "message": f"Session '{session_id}' closed"}
    return {"success": False, "message": f"Session '{session_id}' not found"}


async def websocket_screencast_loop(
    websocket: WebSocket,
    screencast_subscribers: List[WebSocket],
    get_current_frame: Callable[[], Optional[str]],
    logger: Any,
) -> None:
    await websocket.accept()
    screencast_subscribers.append(websocket)
    logger.info(
        "[WebSocket] New screencast subscriber connected (total: %s)",
        len(screencast_subscribers),
    )
    try:
        while True:
            data = await websocket.receive_text()
            if data == "get_current_frame":
                current = get_current_frame()
                if current:
                    await websocket.send_json(
                        {
                            "type": "screencast_frame",
                            "frame": current,
                            "timestamp": asyncio.get_event_loop().time(),
                        }
                    )
    except WebSocketDisconnect:
        logger.info("[WebSocket] Screencast subscriber disconnected")
    except Exception:
        logger.exception("[WebSocket] Error")
    finally:
        if websocket in screencast_subscribers:
            screencast_subscribers.remove(websocket)
        logger.info("[WebSocket] Subscriber removed (total: %s)", len(screencast_subscribers))


def build_root_payload(
    *,
    playwright_instance: Any,
    active_sessions: Dict[str, Any],
    screencast_subscribers: List[WebSocket],
) -> Dict[str, Any]:
    return {
        "message": "MCP Host is running.",
        "enabled": True,
        "profile": "default",
        "running": bool(playwright_instance),
        "chosenBrowser": "chromium",
        "headless": False,
        "active_sessions": len(active_sessions),
        "screencast_subscribers": len(screencast_subscribers),
        "screencast_active": any(s.screencast_active for s in active_sessions.values()),
    }
