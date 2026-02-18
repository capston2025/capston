"""Data models for GAIA execution memory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MemoryActionRecord:
    episode_id: int
    domain: str
    url: str
    step_number: int
    action: str
    selector: str = ""
    full_selector: str = ""
    ref_id: str = ""
    success: bool = False
    effective: bool = False
    changed: bool = False
    reason_code: str = ""
    reason: str = ""
    snapshot_id: str = ""
    dom_hash: str = ""
    epoch: int = 0
    frame_index: int | None = None
    tab_index: int | None = None
    state_change: dict[str, Any] | None = None
    attempt_logs: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class MemorySummaryRecord:
    domain: str
    command: str
    summary: str
    status: str
    episode_id: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class MemorySuggestion:
    source: str
    reason_code: str
    summary: str
    selector_hint: str = ""
    action: str = ""
    confidence: float = 0.0

