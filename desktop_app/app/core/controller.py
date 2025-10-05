"""Application controller tying UI events to services and workers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PyQt6.QtCore import QObject, QThread, pyqtSlot

from app.services.backend_bridge import BackendBridge
from app.services.pdf_service import PDFService
from app.system.input_controller import InputController
from app.ui.main_window import MainWindow
from app.workers.playwright_worker import AutomationWorker


@dataclass(slots=True)
class ControllerConfig:
    """Configuration hooks for dependency injection during testing."""

    pdf_service: PDFService | None = None
    input_controller: InputController | None = None
    automation_worker_factory: type[AutomationWorker] | None = None


class AppController(QObject):
    """Central coordinator for UI, services, and automation workers."""

    def __init__(self, window: MainWindow, config: ControllerConfig | None = None) -> None:
        super().__init__(window)
        self._window = window
        self._config = config or ControllerConfig()

        self._pdf_service = self._config.pdf_service or PDFService()
        self._input_controller = self._config.input_controller or InputController()
        self._automation_worker_cls = self._config.automation_worker_factory or AutomationWorker
        self._backend_bridge = BackendBridge()

        self._current_pdf: Path | None = None
        self._current_url: str | None = None
        self._checklist_items: Sequence[str] = ()
        self._current_plan: Sequence[dict] = ()
        self._worker_thread: QThread | None = None
        self._worker: AutomationWorker | None = None

        self._connect_signals()

    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self._window.fileDropped.connect(self._on_file_dropped)
        self._window.startRequested.connect(self._on_start_requested)
        self._window.cancelRequested.connect(self._on_cancel_requested)
        self._window.urlSubmitted.connect(self._on_url_submitted)

    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def _on_file_dropped(self, file_path: str) -> None:
        pdf_path = Path(file_path)
        if not pdf_path.exists():
            self._window.append_log(f"⚠️ File not found: {pdf_path}")
            return

        if pdf_path.suffix.lower() != ".pdf":
            self._window.append_log("⚠️ Only PDF files are supported at this time.")
            return

        self._current_pdf = pdf_path
        self._window.append_log(f"📄 Loaded checklist: {pdf_path.name}")
        self._load_checklist(pdf_path)

    # ------------------------------------------------------------------
    def _load_checklist(self, pdf_path: Path) -> None:
        try:
            result = self._pdf_service.extract_checklist(pdf_path)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._window.append_log(f"❌ Failed to parse PDF: {exc}")
            self._checklist_items = ()
            self._window.show_checklist([])
            return

        self._checklist_items = result.items
        self._window.show_checklist(self._checklist_items)
        if result.notes:
            for note in result.notes:
                self._window.append_log(f"📝 {note}")

        if result.suggested_url:
            self._current_url = result.suggested_url
            self._window.set_url_field(result.suggested_url)
            self._window.append_log(f"🌐 Suggested test URL: {result.suggested_url}")

    # ------------------------------------------------------------------
    @pyqtSlot()
    def _on_start_requested(self) -> None:
        if not self._current_url:
            self._window.append_log("⚠️ 테스트할 URL을 입력하거나 PDF에서 URL을 추출해주세요.")
            return

        if self._worker_thread:
            self._window.append_log("⚠️ Automation already in progress.")
            return

        try:
            plan = self._backend_bridge.analyze_url_and_generate_plan(self._current_url)
        except Exception as exc:
            self._window.append_log(f"❌ 테스트 플랜 생성 실패: {exc}")
            return

        if not plan:
            self._window.append_log("⚠️ 생성된 테스트 시나리오가 없습니다.")
            return

        self._current_plan = [
            {
                "id": scenario.id,
                "priority": scenario.priority,
                "scenario": scenario.scenario,
                "steps": [
                    {
                        "description": step.description,
                        "action": step.action,
                        "selector": step.selector,
                        "params": list(step.params),
                    }
                    for step in scenario.steps
                ],
                "assertion": {
                    "description": scenario.assertion.description,
                    "selector": scenario.assertion.selector,
                    "condition": scenario.assertion.condition,
                },
            }
            for scenario in plan
        ]

        self._checklist_items = [
            f"{item['id']} [{item['priority']}] {item['scenario']}"
            for item in self._current_plan
        ]
        self._window.show_checklist(self._checklist_items)

        self._window.set_busy(True)
        self._start_worker(self._current_plan, self._current_url)

    # ------------------------------------------------------------------
    def _start_worker(self, plan: Iterable[dict], target_url: str) -> None:
        thread = QThread(self)
        worker = self._automation_worker_cls(target_url, list(plan))
        worker.moveToThread(thread)

        thread.started.connect(worker.start)
        worker.progress.connect(self._window.append_log)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._worker_thread = thread
        self._worker = worker

        thread.start()

    # ------------------------------------------------------------------
    @pyqtSlot()
    def _on_worker_finished(self) -> None:
        self._window.append_log("✅ Automation completed.")
        self._window.set_busy(False)
        self._worker_thread = None
        self._worker = None

    # ------------------------------------------------------------------
    @pyqtSlot()
    def _on_cancel_requested(self) -> None:
        if self._worker:
            self._worker.request_cancel()
            self._window.append_log("⏹️ Cancel requested.")
        else:
            self._window.append_log("ℹ️ No automation in progress.")

    # ------------------------------------------------------------------
    @pyqtSlot(str)
    def _on_url_submitted(self, url: str) -> None:
        self._current_url = url
        self._window.append_log(f"🌐 Loading URL: {url}")
        self._window.load_url(url)
