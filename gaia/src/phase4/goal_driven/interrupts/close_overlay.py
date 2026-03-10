from __future__ import annotations

from typing import Any

from ..evidence_bundle import InterruptResult


class CloseOverlayInterruptPolicy:
    name = "close_overlay_interrupt"

    def match(self, semantics: Any, evidence: Any) -> bool:
        return False

    def run(self, ctx: Any, semantics: Any, evidence: Any) -> InterruptResult:
        return InterruptResult(matched=False, policy_name=self.name)
