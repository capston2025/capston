"""Phase 4 (Agent orchestration) helpers."""

from __future__ import annotations

from typing import Any

__all__ = ["AgentOrchestrator", "MCPClient", "mcp_app"]


def __getattr__(name: str) -> Any:
    if name == "AgentOrchestrator":
        from gaia.src.phase4.agent import AgentOrchestrator

        return AgentOrchestrator
    if name == "MCPClient":
        from gaia.src.phase4.agent import MCPClient

        return MCPClient
    if name == "mcp_app":
        from gaia.src.phase4.mcp_host import app

        return app
    raise AttributeError(name)
