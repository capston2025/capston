"""Background worker responsible for Playwright-powered automation."""
from __future__ import annotations

import time
from typing import Sequence

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class AutomationWorker(QObject):
    """Runs checklist-driven automation in a background Qt thread."""

    progress = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, target_url: str, checklist: Sequence[str]) -> None:
        super().__init__()
        self._target_url = target_url
        self._checklist = list(checklist)
        self._cancel_requested = False

    @pyqtSlot()
    def start(self) -> None:
        if not self._checklist:
            self.progress.emit("ℹ️ Checklist is empty; nothing to automate.")
            self.finished.emit()
            return

        self.progress.emit(f"🚀 Starting automation for {self._target_url} (stub implementation)…")
        for idx, item in enumerate(self._checklist, start=1):
            if self._cancel_requested:
                self.progress.emit("⏹️ Automation cancelled by user.")
                break

            self.progress.emit(f"[{idx}/{len(self._checklist)}] TODO: automate '{item}'")
            time.sleep(0.1)  # Placeholder pacing; remove when Playwright integration lands

        self.finished.emit()

    def request_cancel(self) -> None:
        self._cancel_requested = True
