"""Qt worker for executing automation scenarios sequentially."""
from __future__ import annotations

import time
from typing import Sequence

from PySide6.QtCore import QObject, Signal, Slot

from gaia.src.phase4.agent import AgentOrchestrator
from gaia.src.utils.models import TestScenario


class AutomationWorker(QObject):
    """Runs generated scenarios inside a background Qt thread."""

    progress = Signal(str)
    finished = Signal()

    def __init__(
        self,
        target_url: str,
        plan: Sequence[TestScenario],
        orchestrator: AgentOrchestrator | None = None,
    ) -> None:
        super().__init__()
        self._target_url = target_url
        self._plan = list(plan)
        self._orchestrator = orchestrator or AgentOrchestrator()
        self._cancel_requested = False

    @Slot()
    def start(self) -> None:
        if not self._plan:
            self.progress.emit("ℹ️ No generated test scenarios; nothing to automate.")
            self.finished.emit()
            return

        self.progress.emit(f"🚀 Starting automation for {self._target_url} (MCP mode)")

        for scenario_index, scenario in enumerate(self._plan, start=1):
            if self._cancel_requested:
                self.progress.emit("⏹️ Automation cancelled by user.")
                break

            self.progress.emit(
                f"📋 {scenario.id} ({scenario.priority}): {scenario.scenario}"
            )

            if self._cancel_requested:
                self.progress.emit("⏹️ Automation cancelled by user.")
                break

            result = self._orchestrator.execute_scenario(self._target_url, scenario)
            status = result.get("status", "failed")
            logs = result.get("logs") or []

            if status == "success":
                self.progress.emit("   ✅ Scenario executed successfully.")
            elif status == "skipped":
                self.progress.emit("   ⏭️ Scenario skipped by MCP host.")
            else:
                error = result.get("error", "Unknown failure.")
                self.progress.emit(f"   ❌ Scenario failed: {error}")

            for log in logs:
                self.progress.emit(f"      • {log}")

            time.sleep(0.2)

        self.finished.emit()

    def request_cancel(self) -> None:
        self._cancel_requested = True
