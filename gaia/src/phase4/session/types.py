"""Session state types for GAIA CLI/Hub continuity."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class SessionState:
    session_key: str
    mcp_session_id: str
    current_tab_id: str = ""
    last_snapshot_id: str = ""
    epoch: int = 0
    dom_hash: str = ""
    goal_context: Dict[str, Any] = field(default_factory=dict)
    pending_user_input: Dict[str, Any] = field(default_factory=dict)
    auth_state: str = "unknown"
    last_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SessionState":
        return cls(
            session_key=str(raw.get("session_key") or ""),
            mcp_session_id=str(raw.get("mcp_session_id") or ""),
            current_tab_id=str(raw.get("current_tab_id") or ""),
            last_snapshot_id=str(raw.get("last_snapshot_id") or ""),
            epoch=int(raw.get("epoch") or 0),
            dom_hash=str(raw.get("dom_hash") or ""),
            goal_context=dict(raw.get("goal_context") or {}),
            pending_user_input=dict(raw.get("pending_user_input") or {}),
            auth_state=str(raw.get("auth_state") or "unknown"),
            last_url=str(raw.get("last_url") or ""),
        )


@dataclass(slots=True)
class PlanState:
    phase: str = "COLLECT"
    subgoals: List[str] = field(default_factory=list)
    progress_signals: Dict[str, Any] = field(default_factory=dict)
    blocked_reason: str = ""
    next_action_policy: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

