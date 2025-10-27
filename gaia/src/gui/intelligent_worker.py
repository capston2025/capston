"""
QThread worker for IntelligentOrchestrator execution.
Runs LLM-powered browser automation in background.
"""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QObject, Signal

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from gaia.src.utils.models import TestScenario


class IntelligentWorker(QObject):
    """Worker for executing IntelligentOrchestrator in background thread"""

    progress = Signal(str)
    screenshot = Signal(str)  # base64 screenshot
    finished = Signal()

    def __init__(
        self,
        url: str,
        scenarios: Sequence[TestScenario],
        orchestrator: IntelligentOrchestrator,
    ) -> None:
        super().__init__()
        self.url = url
        self.scenarios = scenarios
        self.orchestrator = orchestrator
        self._cancel_requested = False

    def start(self) -> None:
        """Execute scenarios using IntelligentOrchestrator"""
        try:
            self.progress.emit(f"🤖 Starting LLM-powered automation for {len(self.scenarios)} scenarios...")

            # Set screenshot callback on orchestrator
            if hasattr(self.orchestrator, '_screenshot_callback'):
                self.orchestrator._screenshot_callback = self._on_screenshot

            # For MasterOrchestrator, also set callback on internal IntelligentOrchestrator
            if hasattr(self.orchestrator, 'intelligent_orch'):
                self.orchestrator.intelligent_orch._screenshot_callback = self._on_screenshot

            # Execute scenarios with progress callback
            results = self.orchestrator.execute_scenarios(
                url=self.url,
                scenarios=self.scenarios,
                progress_callback=self._on_progress
            )

            # Log summary
            self.progress.emit(f"\n📊 Execution Results:")
            self.progress.emit(f"   ✅ Passed: {results['passed']}/{results['total']}")
            self.progress.emit(f"   ❌ Failed: {results['failed']}/{results['total']}")
            self.progress.emit(f"   ⏭️  Skipped: {results['skipped']}/{results['total']}")

            # Log detailed results with clear status
            self.progress.emit("\n상세 결과:")
            for idx, scenario_result in enumerate(results["scenarios"], 1):
                status_text = {
                    "passed": "PASS",
                    "failed": "FAIL",
                    "skipped": "SKIP"
                }.get(scenario_result["status"], "UNKNOWN")

                self.progress.emit(
                    f"[{idx}/{results['total']}] {status_text} - {scenario_result['id']}: {scenario_result['scenario']}"
                )

        except Exception as e:
            self.progress.emit(f"❌ Intelligent orchestrator failed: {e}")
            import traceback
            self.progress.emit(f"Traceback: {traceback.format_exc()}")

        finally:
            self.finished.emit()

    def _on_progress(self, message: str) -> None:
        """Forward progress messages to GUI"""
        self.progress.emit(message)

    def _on_screenshot(self, screenshot_base64: str) -> None:
        """Forward screenshot to GUI for real-time preview"""
        self.screenshot.emit(screenshot_base64)

    def request_cancel(self) -> None:
        """Request cancellation (not yet implemented)"""
        self._cancel_requested = True
        self.progress.emit("⚠️ Cancel requested (not yet fully supported)")


__all__ = ["IntelligentWorker"]
