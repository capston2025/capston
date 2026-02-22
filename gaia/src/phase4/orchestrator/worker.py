"""Worker execution wrapper for step-level actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass(slots=True)
class WorkerResult:
    success: bool
    changed: bool
    reason_code: str = "ok"
    reason: str = ""
    state_change: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)


class StepWorker:
    def execute(
        self,
        fn: Callable[[], tuple[bool, Optional[str], Dict[str, Any] | None]],
    ) -> WorkerResult:
        success, error, state_change = fn()
        changed = bool((state_change or {}).get("dom_changed") or (state_change or {}).get("url_changed"))
        return WorkerResult(
            success=bool(success),
            changed=changed,
            reason_code="ok" if success else "failed",
            reason=error or "",
            state_change=dict(state_change or {}),
        )

