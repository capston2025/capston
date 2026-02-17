"""Configuration helpers for GAIA services."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass(slots=True)
class LLMConfig:
    """Settings for the GPT-powered planners."""

    api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    model: str = os.getenv("GAIA_LLM_MODEL", "gpt-5")
    reasoning_effort: Optional[str] = os.getenv("GAIA_LLM_REASONING_EFFORT")
    verbosity: Optional[str] = os.getenv("GAIA_LLM_VERBOSITY")
    max_completion_tokens: Optional[int] = None

    def __post_init__(self) -> None:
        max_tokens = os.getenv("GAIA_LLM_MAX_COMPLETION_TOKENS") or os.getenv("OPENAI_MAX_COMPLETION_TOKENS")
        if max_tokens:
            try:
                self.max_completion_tokens = int(max_tokens)
            except ValueError:
                self.max_completion_tokens = None


@dataclass(slots=True)
class MCPConfig:
    """Connection details for the Playwright MCP host."""

    host_url: str = os.getenv("MCP_HOST_URL", "http://localhost:8001")
    request_timeout: int = int(os.getenv("MCP_TIMEOUT", "45"))


@dataclass(slots=True)
class AppConfig:
    """Aggregated configuration for the orchestrator."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)


CONFIG = AppConfig()
