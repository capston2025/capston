"""Route and dispatch helpers for the local MCP host."""

from .routes import (
    build_root_payload,
    close_session_impl,
    dispatch_execute_action_route,
    handle_legacy_action,
    websocket_screencast_loop,
)

__all__ = [
    "build_root_payload",
    "close_session_impl",
    "dispatch_execute_action_route",
    "handle_legacy_action",
    "websocket_screencast_loop",
]
