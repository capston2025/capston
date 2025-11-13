"""
IntelligentOrchestrator 실행을 위한 QThread 워커.
백그라운드에서 LLM 기반 브라우저 자동화를 수행합니다.
"""
from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QObject, Signal

from gaia.src.phase4.intelligent_orchestrator import IntelligentOrchestrator
from gaia.src.utils.models import TestScenario


class IntelligentWorker(QObject):
    """백그라운드 스레드에서 IntelligentOrchestrator를 실행하는 워커"""

    progress = Signal(str)
    screenshot = Signal(str, object)  # (base64, click_position dict 또는 None)
    scenario_started = Signal(str)  # scenario_id
    scenario_finished = Signal(str)  # scenario_id
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
        """IntelligentOrchestrator로 시나리오를 실행합니다"""
        try:
            self.progress.emit(f"🤖 Starting LLM-powered automation for {len(self.scenarios)} scenarios...")

            # 오케스트레이터에 스크린샷 콜백 설정
            if hasattr(self.orchestrator, '_screenshot_callback'):
                self.orchestrator._screenshot_callback = self._on_screenshot

            # MasterOrchestrator인 경우 내부 IntelligentOrchestrator에도 콜백 설정
            if hasattr(self.orchestrator, 'intelligent_orch'):
                self.orchestrator.intelligent_orch._screenshot_callback = self._on_screenshot

            # 진행 콜백과 함께 시나리오 실행
            results = self.orchestrator.execute_scenarios(
                url=self.url,
                scenarios=self.scenarios,
                progress_callback=self._on_progress
            )

            # 요약 로그 출력
            self.progress.emit(f"\n📊 Execution Results:")
            self.progress.emit(f"   ✅ Passed: {results['passed']}/{results['total']}")
            self.progress.emit(f"   ❌ Failed: {results['failed']}/{results['total']}")
            self.progress.emit(f"   ⏭️  Skipped: {results['skipped']}/{results['total']}")

            # 상태가 분명한 상세 결과 로그
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
        """진행 메시지를 GUI에 전달합니다"""
        # 특별한 마커를 감지하여 시나리오 시작/완료 신호 발생
        if message.startswith("[SCENARIO_START:"):
            scenario_id = message.split(":", 1)[1].split("]")[0]
            self.scenario_started.emit(scenario_id)
        elif message.startswith("[SCENARIO_END:"):
            scenario_id = message.split(":", 1)[1].split("]")[0]
            self.scenario_finished.emit(scenario_id)
        else:
            self.progress.emit(message)

    def _on_screenshot(self, screenshot_base64: str, click_position: dict = None) -> None:
        """실시간 미리보기를 위해 스크린샷을 GUI로 전달합니다"""
        self.screenshot.emit(screenshot_base64, click_position)

    def request_cancel(self) -> None:
        """취소를 요청합니다(아직 완전 구현되지 않음)"""
        self._cancel_requested = True
        self.progress.emit("⚠️ Cancel requested (not yet fully supported)")


__all__ = ["IntelligentWorker"]
