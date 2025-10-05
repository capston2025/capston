"""Background worker responsible for Playwright-powered automation."""
from __future__ import annotations

import time
from typing import Any, Dict, Sequence

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class AutomationWorker(QObject):
    """Runs checklist-driven automation in a background Qt thread."""

    progress = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, target_url: str, plan: Sequence[Dict[str, Any]]) -> None:
        super().__init__()
        self._target_url = target_url
        self._plan = list(plan)
        self._cancel_requested = False

    @pyqtSlot()
    def start(self) -> None:
        if not self._plan:
            self.progress.emit("ℹ️ No generated test scenarios; nothing to automate.")
            self.finished.emit()
            return

        self.progress.emit(f"🚀 Starting automation for {self._target_url} (demo mode)")

        for scenario_index, scenario in enumerate(self._plan, start=1):
            if self._cancel_requested:
                self.progress.emit("⏹️ Automation cancelled by user.")
                break

            scenario_title = scenario.get("scenario", "Unnamed scenario")
            scenario_id = scenario.get("id", f"TC_{scenario_index:03d}")
            self.progress.emit(
                f"📋 {scenario_id} ({scenario.get('priority', 'N/A')}): {scenario_title}"
            )

            steps = scenario.get("steps", [])
            for step_index, step in enumerate(steps, start=1):
                if self._cancel_requested:
                    self.progress.emit("⏹️ Automation cancelled by user.")
                    break

                description = step.get("description", "")
                action = step.get("action", "")
                selector = step.get("selector", "")
                self.progress.emit(
                    f"   ↳ Step {step_index}/{len(steps)} | {action.upper()} {selector} — {description}"
                )
                time.sleep(0.2)

            assertion = scenario.get("assertion", {})
            if assertion:
                self.progress.emit(
                    "   ✅ Assertion: "
                    f"{assertion.get('description', '')} (@ {assertion.get('selector', '')})"
                )

            time.sleep(0.2)

        self.finished.emit()

    def request_cancel(self) -> None:
        self._cancel_requested = True
