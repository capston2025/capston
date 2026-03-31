"""Phase 4 (Agent orchestration) helpers."""

from __future__ import annotations

from typing import Any

__all__ = ["AgentOrchestrator", "MCPClient"]


def __getattr__(name: str) -> Any:
    if name == "AgentOrchestrator":
        from gaia.src.phase4.agent import AgentOrchestrator

        return AgentOrchestrator
    if name == "MCPClient":
        from gaia.src.phase4.agent import MCPClient

        return MCPClient
    raise AttributeError(name)
