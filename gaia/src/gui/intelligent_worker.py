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

            # Log detailed results
            for scenario_result in results["scenarios"]:
                status_emoji = {"passed": "✅", "failed": "❌", "skipped": "⏭️"}.get(
                    scenario_result["status"], "❓"
                )
                self.progress.emit(
                    f"{status_emoji} {scenario_result['id']}: {scenario_result['scenario']}"
                )

                # Show logs if available
                for log in scenario_result.get("logs", []):
                    self.progress.emit(f"     {log}")

        except Exception as e:
            self.progress.emit(f"❌ Intelligent orchestrator failed: {e}")
            import traceback
            self.progress.emit(f"Traceback: {traceback.format_exc()}")

        finally:
            self.finished.emit()

    def _on_progress(self, message: str) -> None:
        """Forward progress messages to GUI"""
        self.progress.emit(message)

    def request_cancel(self) -> None:
        """Request cancellation (not yet implemented)"""
        self._cancel_requested = True
        self.progress.emit("⚠️ Cancel requested (not yet fully supported)")


__all__ = ["IntelligentWorker"]
