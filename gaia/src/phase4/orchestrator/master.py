"""Lightweight master orchestration primitives."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from gaia.src.phase4.session.types import PlanState


@dataclass(slots=True)
class MasterDirective:
    kind: str
    reason: str = ""
    phase: str = "COLLECT"
    handoff_payload: Dict[str, Any] = field(default_factory=dict)


class MasterOrchestrator:
    def __init__(self) -> None:
        self.plan = PlanState()
        self._no_progress_count = 0
        self._progress_count = 0
        self._no_progress_threshold = 2

    def set_phase(self, phase: str) -> None:
        normalized = (phase or "COLLECT").strip().upper()
        self.plan.phase = normalized or "COLLECT"

    def record_progress(self, *, changed: bool, signal: Dict[str, Any] | None = None) -> None:
        if changed:
            self._progress_count += 1
            self._no_progress_count = 0
        else:
            self._no_progress_count += 1
        if signal:
            merged = dict(signal)
            merged["no_progress_count"] = self._no_progress_count
            merged["progress_count"] = self._progress_count
            self.plan.progress_signals = merged

    def next_directive(self, *, auth_required: bool = False) -> MasterDirective:
        if auth_required:
            return MasterDirective(
                kind="handoff",
                reason="auth_required",
                phase=self.plan.phase,
                handoff_payload={"kind": "auth_required"},
            )
        if self._no_progress_count >= self._no_progress_threshold:
            payload: Dict[str, Any] = {
                "kind": "no_progress",
                "count": self._no_progress_count,
            }
            if self.plan.progress_signals:
                payload["signal"] = dict(self.plan.progress_signals)
            return MasterDirective(
                kind="handoff",
                reason="no_progress",
                phase=self.plan.phase,
                handoff_payload=payload,
            )
        return MasterDirective(kind="run", phase=self.plan.phase)
