"""Qt widgets composing the main application window."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Sequence

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)
from PySide6.QtWebEngineWidgets import QWebEngineView



class DropArea(QLabel):
    """Label widget that supports drag-and-drop for local files."""

    def __init__(self, on_file_dropped: Callable[[str], None], *, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._on_file_dropped = on_file_dropped
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(130)
        self.setObjectName("DropArea")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 (Qt naming)
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 (Qt naming)
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._on_file_dropped(url.toLocalFile())
                break
        event.acceptProposedAction()


class SpinnerWidget(QWidget):
    """Simple circular spinner drawn via QPainter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(56, 56)
        self.hide()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start(60)
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: D401
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        radius = min(self.width(), self.height()) / 2 - 4
        rect = event.rect().adjusted(6, 6, -6, -6)

        # Fade trail for spinner arc
        gradient_colors = [
            QColor(107, 91, 255, 230),
            QColor(140, 118, 255, 120),
            QColor(166, 136, 255, 40),
        ]

        for index, color in enumerate(gradient_colors):
            pen = QPen(color, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start_angle = (self._angle - index * 45) * 16
            span_angle = 120 * 16
            painter.drawArc(rect, start_angle, span_angle)


class BusyOverlay(QFrame):
    """Frosted overlay with spinner, status label, and elapsed time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BusyOverlay")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        container = QFrame(self)
        container.setObjectName("OverlayContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(40, 40, 40, 40)
        container_layout.setSpacing(12)

        self._spinner = SpinnerWidget(container)
        container_layout.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignCenter)

        self._message = QLabel("분석 중입니다…", container)
        self._message.setObjectName("OverlayLabel")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self._message)

        # Elapsed time label
        self._elapsed_label = QLabel("", container)
        self._elapsed_label.setObjectName("OverlayElapsedLabel")
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self._elapsed_label)

        # Expected time hint
        self._hint_label = QLabel("⏱️  예상 소요 시간: 3-8분", container)
        self._hint_label.setObjectName("OverlayHintLabel")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self._hint_label)

        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)

        # Timer for updating elapsed time
        self._elapsed_seconds = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)

    def show_with_message(self, message: str) -> None:
        self._message.setText(message)
        self._elapsed_seconds = 0
        self._update_elapsed_time()
        self._spinner.start()
        self._elapsed_timer.start(1000)  # Update every second
        self.show()

    def hide_overlay(self) -> None:
        self._spinner.stop()
        self._elapsed_timer.stop()
        self.hide()

    def _update_elapsed_time(self) -> None:
        """Update the elapsed time display"""
        minutes = self._elapsed_seconds // 60
        seconds = self._elapsed_seconds % 60
        self._elapsed_label.setText(f"⏱️  경과 시간: {minutes}분 {seconds:02d}초")
        self._elapsed_seconds += 1


class ScenarioCard(QFrame):
    """Glassmorphism card representing a generated test scenario."""

    def __init__(self, scenario: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ScenarioCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)

        scenario_id = getattr(scenario, "id", "TC")
        id_label = QLabel(str(scenario_id), self)
        id_label.setObjectName("ScenarioId")
        header.addWidget(id_label)

        priority_value = str(getattr(scenario, "priority", "MAY")).upper()
        priority_label = QLabel(priority_value, self)
        priority_label.setProperty("role", "priority-pill")
        priority_label.setProperty("priority", priority_value)
        header.addWidget(priority_label)

        header.addStretch(1)
        layout.addLayout(header)

        title_text = (
            getattr(scenario, "scenario", None)
            or getattr(scenario, "name", "Unnamed scenario")
        )
        title = QLabel(str(title_text), self)
        title.setObjectName("ScenarioTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        steps = list(getattr(scenario, "steps", []) or [])
        if steps:
            steps_container = QVBoxLayout()
            steps_container.setContentsMargins(0, 0, 0, 0)
            steps_container.setSpacing(4)
            for index, step in enumerate(steps, start=1):
                if isinstance(step, dict):
                    description = step.get("description", "")
                elif hasattr(step, "description"):
                    description = getattr(step, "description")
                else:
                    description = str(step)
                step_label = QLabel(f"{index}. {description}", self)
                step_label.setProperty("role", "step-text")
                step_label.setWordWrap(True)
                steps_container.addWidget(step_label)
            layout.addLayout(steps_container)

        assertion_source = getattr(scenario, "assertion", None)
        expected_text = None
        if assertion_source is not None:
            expected_text = getattr(assertion_source, "description", None)
        if not expected_text:
            expected_text = getattr(scenario, "expected_result", "")

        if expected_text:
            assertion = QLabel(f"✅ {expected_text}", self)
            assertion.setProperty("role", "assertion-text")
            assertion.setWordWrap(True)
            layout.addWidget(assertion)

class MainWindow(QMainWindow):
    """Top level window wiring UI elements and controller callbacks."""

    fileDropped = Signal(str)
    startRequested = Signal()
    cancelRequested = Signal()
    urlSubmitted = Signal(str)
    planFileSelected = Signal(str)

    def __init__(self, *, controller_factory: Callable[["MainWindow"], object] | None = None) -> None:
        super().__init__()
        self.setWindowTitle("QA Automation Desktop")

        app_instance = QApplication.instance()
        self._available_geometry = None
        self._safe_geometry = None
        if app_instance and app_instance.primaryScreen():
            self._available_geometry = app_instance.primaryScreen().availableGeometry()
            self._safe_geometry = self._available_geometry.adjusted(36, 28, -36, -36)
            self.setGeometry(self._safe_geometry)
        else:
            self.resize(1220, 860)
        self.setMinimumSize(1080, 760)

        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f5f7ff, stop:1 #e6f0ff);
            }

            QWidget {
                color: #12142b;
                font-family: 'Pretendard', 'Noto Sans KR', 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
                font-size: 13.5px;
            }

            QLabel#AppTitle {
                font-size: 26px;
                font-weight: 700;
                letter-spacing: 0.5px;
                color: #12142b;
            }

            QFrame#SidePanel {
                background: rgba(255, 255, 255, 0.62);
                border-radius: 26px;
                border: 1px solid rgba(255, 255, 255, 0.35);
            }

            QFrame#BrowserCard {
                background: rgba(255, 255, 255, 0.45);
                border-radius: 26px;
                border: 1px solid rgba(255, 255, 255, 0.3);
            }

            QLabel#SectionLabel, QLabel#BrowserTitle {
                font-weight: 600;
                font-size: 12.4px;
                letter-spacing: 0.6px;
                text-transform: uppercase;
                color: #64698b;
            }

            QLabel#BrowserTitle {
                font-size: 14.5px;
                color: #2c2f48;
                text-transform: none;
            }

            QLabel#DropArea {
                border: 1.4px dashed rgba(109, 119, 255, 0.55);
                border-radius: 24px;
                padding: 26px;
                color: #5b5ff7;
                background: rgba(99, 102, 241, 0.12);
            }

            QListWidget {
                background: transparent;
                border: none;
                padding: 0px;
            }

            QTextEdit {
                background: rgba(255, 255, 255, 0.5);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.30);
                padding: 16px;
                color: #22244c;
            }

            QLineEdit {
                background: rgba(255, 255, 255, 0.68);
                border-radius: 16px;
                border: 1px solid rgba(138, 142, 255, 0.45);
                padding: 12px 16px;
                color: #1c1f3b;
            }

            QLineEdit:focus {
                border: 1px solid rgba(110, 120, 255, 0.8);
                background: rgba(255, 255, 255, 0.85);
            }

            QPushButton {
                border-radius: 18px;
                padding: 11px 20px;
                color: #ffffff;
                font-weight: 600;
                border: none;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #7d5bff, stop:1 #5f9dff);
            }

            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8362ff, stop:1 #68a4ff);
            }

            QPushButton:disabled {
                background: rgba(142, 156, 200, 0.35);
                color: rgba(255, 255, 255, 0.8);
            }

            QPushButton#GhostButton {
                background: transparent;
                border: 1px solid rgba(125, 135, 255, 0.5);
                color: #5b5ff7;
            }

            QPushButton#GhostButton:hover {
                background: rgba(125, 135, 255, 0.12);
            }

            QPushButton#DangerButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff6b8a, stop:1 #ff8f70);
            }

            QSplitter::handle {
                background: transparent;
                width: 16px;
            }

            QFrame#ScenarioCard {
                background: rgba(255, 255, 255, 0.68);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.38);
            }

            QLabel#ScenarioId {
                font-weight: 600;
                font-size: 13px;
                color: #4b4f73;
            }

            QLabel#ScenarioTitle {
                font-size: 15px;
                font-weight: 600;
                color: #181b3d;
            }

            QLabel[role="step-text"] {
                color: #3a3d5e;
                font-size: 13px;
            }

            QLabel[role="assertion-text"] {
                color: #2563eb;
                font-weight: 600;
                font-size: 13px;
            }

            QLabel[role="priority-pill"] {
                padding: 4px 12px;
                border-radius: 999px;
                font-size: 11px;
                letter-spacing: 0.6px;
                font-weight: 600;
            }

            QLabel[role="priority-pill"][priority="MUST"] {
                background: rgba(244, 63, 94, 0.18);
                color: #e11d48;
            }

            QLabel[role="priority-pill"][priority="SHOULD"] {
                background: rgba(250, 204, 21, 0.2);
                color: #c2410c;
            }

            QLabel[role="priority-pill"][priority="MAY"] {
                background: rgba(16, 185, 129, 0.18);
                color: #047857;
            }

            QFrame#BusyOverlay {
                background: rgba(18, 23, 46, 0.25);
            }

            QFrame#OverlayContainer {
                background: rgba(255, 255, 255, 0.86);
                border-radius: 28px;
                border: 1px solid rgba(255, 255, 255, 0.45);
                min-width: 320px;
            }

            QLabel#OverlayLabel {
                color: #1e2349;
                font-size: 15px;
                font-weight: 600;
            }

            QLabel#OverlayElapsedLabel {
                color: #5b5ff7;
                font-size: 24px;
                font-weight: 700;
                margin-top: 8px;
            }

            QLabel#OverlayHintLabel {
                color: #64698b;
                font-size: 12px;
                font-weight: 500;
                margin-top: 4px;
            }
            """
        )

        self._workflow_stack: QStackedWidget
        self._setup_page: QWidget
        self._review_page: QWidget
        self._drop_area: DropArea
        self._checklist_view: QListWidget
        self._log_output: QTextEdit
        self._start_button: QPushButton
        self._cancel_button: QPushButton
        self._back_to_setup_button: QPushButton
        self._view_logs_button: QPushButton
        self._url_input: QLineEdit
        self._browser_view: QWebEngineView
        self._workflow_stage: str
        self._full_execution_logs: List[str] = []
        self._log_mode: str = "summary"  # "summary" or "full"
        self._is_busy: bool
        self._busy_overlay: BusyOverlay | None = None

        self._is_busy = False
        self._build_layout()

        if controller_factory:
            controller_factory(self)

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(20, 16, 20, 20)  # Reduced top margin
        root_layout.setSpacing(16)  # Reduced spacing

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        header_title = QLabel("GAIA", central)
        header_title.setObjectName("AppTitle")
        header_row.addWidget(header_title)
        header_row.addStretch()

        root_layout.addLayout(header_row)
        root_layout.addSpacing(4)  # Reduced spacing

        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setChildrenCollapsible(False)

        control_panel = QFrame(splitter)
        control_panel.setObjectName("SidePanel")
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(28, 28, 28, 28)
        control_layout.setSpacing(18)

        self._workflow_stack = QStackedWidget(control_panel)
        self._setup_page = self._create_setup_stage(control_panel)
        self._review_page = self._create_review_stage(control_panel)
        self._workflow_stack.addWidget(self._setup_page)
        self._workflow_stack.addWidget(self._review_page)
        control_layout.addWidget(self._workflow_stack, stretch=1)

        splitter.addWidget(control_panel)

        browser_card = QFrame(splitter)
        browser_card.setObjectName("BrowserCard")
        browser_layout = QVBoxLayout(browser_card)
        browser_layout.setContentsMargins(0, 0, 0, 0)  # Remove all margins
        browser_layout.setSpacing(0)  # Remove spacing

        # Remove browser title - maximize space for content
        self._browser_view = QWebEngineView(browser_card)
        self._browser_view.setUrl(QUrl("about:blank"))
        base_height = 820
        if self._safe_geometry:
            base_height = self._safe_geometry.height()
        elif self._available_geometry:
            base_height = self._available_geometry.height()
        min_preview_height = max(460, int(base_height * 0.55))
        self._browser_view.setMinimumHeight(min_preview_height)
        browser_layout.addWidget(self._browser_view)

        splitter.addWidget(browser_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 7)

        root_layout.addWidget(splitter, stretch=1)

        self.setCentralWidget(central)
        self._busy_overlay = BusyOverlay(central)
        self._busy_overlay.setGeometry(central.rect())
        self._busy_overlay.hide()
        self._busy_overlay.raise_()

        self._last_plan_directory = Path.cwd() / "artifacts" / "plans"
        self.show_setup_stage()

    def _create_setup_stage(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title_label = QLabel("1단계. 테스트 준비", page)
        title_label.setObjectName("SectionLabel")
        layout.addWidget(title_label)

        self._drop_area = DropArea(
            on_file_dropped=self.fileDropped.emit,
            title="체크리스트 PDF를 드래그하거나 선택해 주세요",
            parent=page,
        )
        layout.addWidget(self._drop_area)

        url_label = QLabel("테스트 대상 URL", page)
        url_label.setObjectName("SectionLabel")
        layout.addWidget(url_label)

        url_row = QHBoxLayout()
        url_row.setSpacing(12)
        self._url_input = QLineEdit(page)
        self._url_input.setPlaceholderText("https://서비스-테스트-주소.com")
        self._url_input.setClearButtonEnabled(True)
        url_row.addWidget(self._url_input)

        load_button = QPushButton("불러오기", page)
        load_button.setObjectName("GhostButton")
        load_button.clicked.connect(self._emit_url_submitted)
        url_row.addWidget(load_button)
        layout.addLayout(url_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        select_button = QPushButton("PDF 선택", page)
        select_button.setObjectName("GhostButton")
        select_button.clicked.connect(self._open_file_dialog)
        button_row.addWidget(select_button)

        self._load_plan_button = QPushButton("이전 테스트 불러오기", page)
        self._load_plan_button.setObjectName("GhostButton")
        self._load_plan_button.clicked.connect(self._open_plan_dialog)
        button_row.addWidget(self._load_plan_button)

        self._start_button = QPushButton("자동화 시작", page)
        self._start_button.clicked.connect(self.startRequested.emit)
        button_row.addWidget(self._start_button)

        button_row.addStretch()
        layout.addLayout(button_row)
        layout.addStretch(1)

        return page

    def _create_review_stage(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Title and control buttons at the top
        title_row = QHBoxLayout()
        title_label = QLabel("2단계. 자동화 검증", page)
        title_label.setObjectName("SectionLabel")
        title_row.addWidget(title_label)
        title_row.addStretch()

        self._back_to_setup_button = QPushButton("입력 단계로", page)
        self._back_to_setup_button.setObjectName("GhostButton")
        self._back_to_setup_button.clicked.connect(self.show_setup_stage)
        title_row.addWidget(self._back_to_setup_button)

        self._cancel_button = QPushButton("중단", page)
        self._cancel_button.setObjectName("DangerButton")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.cancelRequested.emit)
        title_row.addWidget(self._cancel_button)

        layout.addLayout(title_row)

        # Scenario section (expanded)
        scenario_label = QLabel("자동화 시나리오", page)
        scenario_label.setObjectName("SectionLabel")
        layout.addWidget(scenario_label)

        self._checklist_view = QListWidget(page)
        self._checklist_view.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._checklist_view.setSpacing(12)
        layout.addWidget(self._checklist_view, stretch=3)  # Increased from 2 to 3

        # Logs section (expanded, no emoji)
        logs_header = QHBoxLayout()
        logs_label = QLabel("실행 요약", page)
        logs_label.setObjectName("SectionLabel")
        logs_header.addWidget(logs_label)
        logs_header.addStretch()

        self._view_logs_button = QPushButton("상세 로그 보기", page)  # Removed emoji
        self._view_logs_button.setObjectName("GhostButton")
        self._view_logs_button.setEnabled(False)
        self._view_logs_button.clicked.connect(self._show_detailed_logs)
        logs_header.addWidget(self._view_logs_button)
        layout.addLayout(logs_header)

        self._log_output = QTextEdit(page)
        self._log_output.setPlaceholderText("실행 요약이 여기에 표시됩니다…")
        self._log_output.setReadOnly(True)
        layout.addWidget(self._log_output, stretch=2)  # Increased from 1 to 2

        # Feedback section removed - no longer needed

        return page

    # ------------------------------------------------------------------
    # Workflow stage helpers
    # ------------------------------------------------------------------
    def show_setup_stage(self) -> None:
        self._workflow_stage = "setup"
        if self._workflow_stack.currentWidget() is not self._setup_page:
            self._workflow_stack.setCurrentWidget(self._setup_page)
        self._back_to_setup_button.setEnabled(False)

    def show_review_stage(self) -> None:
        self._workflow_stage = "review"
        if self._workflow_stack.currentWidget() is not self._review_page:
            self._workflow_stack.setCurrentWidget(self._review_page)
        self._back_to_setup_button.setEnabled(not self._is_busy)


    # ------------------------------------------------------------------
    # Slots exposed to the controller
    # ------------------------------------------------------------------
    def show_checklist(self, items: Iterable[str]) -> None:
        self._checklist_view.clear()
        for item in items:
            QListWidgetItem(item, self._checklist_view)

    def show_scenarios(self, scenarios: Sequence[object]) -> None:
        self._checklist_view.clear()
        for scenario in scenarios:
            list_item = QListWidgetItem(self._checklist_view)
            list_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            card = ScenarioCard(scenario, self._checklist_view)
            list_item.setSizeHint(card.sizeHint())
            self._checklist_view.setItemWidget(list_item, card)

    def append_log(self, message: str) -> None:
        """Append log message. Shows summary in UI, stores full logs."""
        # Always store full logs
        self._full_execution_logs.append(message)

        # In summary mode, only show important messages
        if self._log_mode == "summary":
            # Show messages with status indicators or important keywords
            important_keywords = (
                "Step 1:", "Exploring", "Discovered", "Page ", "Executing",
                "PASS", "FAIL", "SKIP", "Execution Results", "상세 결과",
                "Passed:", "Failed:", "Skipped:", "complete"
            )
            if any(keyword in message for keyword in important_keywords):
                self._log_output.append(message)
        else:
            # In full mode, show everything
            self._log_output.append(message)

    def set_busy(self, busy: bool, *, message: str | None = None) -> None:
        self._is_busy = busy
        if busy:
            self.show_review_stage()
            # Start execution: clear logs and use summary mode
            self._full_execution_logs = []
            self._log_mode = "summary"
            self._log_output.clear()
            self._view_logs_button.setEnabled(False)
            # Clear browser view and prepare for live preview
            self._browser_view.setHtml('''
                <html>
                <body style="margin:0; padding:0; background:#1a1a1a; display:flex; align-items:center; justify-content:center; color:#666;">
                    <div style="text-align:center;">
                        <h2>자동화 시작 중...</h2>
                        <p>실시간 브라우저 화면이 곧 표시됩니다</p>
                    </div>
                </body>
                </html>
            ''')
        else:
            # Execution complete: enable detailed log view
            self._view_logs_button.setEnabled(True)

        self._start_button.setEnabled(not busy)
        self._cancel_button.setEnabled(busy)
        self._back_to_setup_button.setEnabled(self._workflow_stage == "review" and not busy)
        self._drop_area.setEnabled(not busy)
        self._url_input.setEnabled(not busy)
        if hasattr(self, "_load_plan_button"):
            self._load_plan_button.setEnabled(not busy)
        if busy:
            self._drop_area.setText("자동화를 진행 중이에요… 잠시만 기다려 주세요 ☄️")
            # Don't show loading overlay - we have live preview now!
            # self.show_loading_overlay(message or "시나리오를 실행 중입니다…")
        else:
            self._drop_area.setText("체크리스트 PDF를 드래그하거나 선택해 주세요")
            # self.hide_loading_overlay()

    def load_url(self, url: str) -> None:
        self._browser_view.setUrl(QUrl(url))

    def set_url_field(self, url: str) -> None:
        self._url_input.setText(url)

    def show_html_in_browser(self, html_content: str) -> None:
        """Display HTML content in the browser view"""
        self._browser_view.setHtml(html_content)

    def update_live_preview(self, screenshot_base64: str, click_position: dict = None) -> None:
        """Update browser view with real-time screenshot from Playwright"""
        import base64
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QPixmap

        try:
            # Decode base64 to bytes
            image_data = base64.b64decode(screenshot_base64)

            # Convert to QPixmap
            pixmap = QPixmap()
            pixmap.loadFromData(QByteArray(image_data))

            # Build click animation overlay if position provided
            click_overlay = ""
            if click_position and "x" in click_position and "y" in click_position:
                x = click_position["x"]
                y = click_position["y"]
                click_overlay = f'''
                <div class="click-animation" style="
                    position: absolute;
                    left: {x}px;
                    top: {y}px;
                    width: 20px;
                    height: 20px;
                    margin-left: -10px;
                    margin-top: -10px;
                    border-radius: 50%;
                    pointer-events: none;
                    animation: ripple 0.8s ease-out;
                "></div>
                '''

            # Display as HTML img tag (scaled to fit) with animation overlay
            html = f'''
            <html>
            <head>
                <style>
                    @keyframes ripple {{
                        0% {{
                            box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.8),
                                        0 0 0 0 rgba(59, 130, 246, 0.6);
                            transform: scale(0.5);
                            opacity: 1;
                        }}
                        50% {{
                            box-shadow: 0 0 0 10px rgba(59, 130, 246, 0.3),
                                        0 0 0 20px rgba(59, 130, 246, 0.1);
                            transform: scale(1.2);
                            opacity: 0.8;
                        }}
                        100% {{
                            box-shadow: 0 0 0 20px rgba(59, 130, 246, 0),
                                        0 0 0 40px rgba(59, 130, 246, 0);
                            transform: scale(1.5);
                            opacity: 0;
                        }}
                    }}
                </style>
            </head>
            <body style="margin:0; padding:0; background:#1a1a1a; display:flex; align-items:center; justify-content:center; position:relative;">
                <div style="position:relative; display:inline-block;">
                    <img src="data:image/png;base64,{screenshot_base64}"
                         style="max-width:100%; max-height:100%; object-fit:contain;
                                box-shadow: 0 0 20px rgba(59, 130, 246, 0.5);
                                border: 2px solid rgba(59, 130, 246, 0.3);
                                border-radius: 8px;">
                    {click_overlay}
                </div>
            </body>
            </html>
            '''
            self._browser_view.setHtml(html)
        except Exception as e:
            print(f"Failed to update live preview: {e}")

    def show_loading_overlay(self, message: str) -> None:
        if self._busy_overlay:
            self._busy_overlay.setGeometry(self.centralWidget().rect())
            self._busy_overlay.raise_()
            self._busy_overlay.show_with_message(message)

    def hide_loading_overlay(self) -> None:
        if self._busy_overlay:
            self._busy_overlay.hide_overlay()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _show_detailed_logs(self) -> None:
        """Show detailed execution logs in a dialog."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle("상세 실행 로그")
        dialog.resize(800, 600)

        layout = QVBoxLayout(dialog)

        # Log text area
        log_view = QTextEdit(dialog)
        log_view.setReadOnly(True)
        log_view.setPlainText("\n".join(self._full_execution_logs))
        layout.addWidget(log_view)

        # Close button
        close_button = QPushButton("닫기", dialog)
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        dialog.exec()

    def _open_file_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select checklist PDF",
            "",
            "PDF Files (*.pdf)",
        )
        if file_path:
            self.fileDropped.emit(file_path)

    def _open_plan_dialog(self) -> None:
        # Try mock_data first (for manually created plans), then plans directory (for cached plans)
        if self._last_plan_directory.exists():
            initial_dir = str(self._last_plan_directory)
        else:
            mock_data_dir = Path.cwd() / "artifacts" / "mock_data"
            plans_dir = Path.cwd() / "artifacts" / "plans"
            # Prefer mock_data if it exists, otherwise try plans
            if mock_data_dir.exists():
                initial_dir = str(mock_data_dir)
            elif plans_dir.exists():
                initial_dir = str(plans_dir)
            else:
                initial_dir = ""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "이전 테스트 플랜 불러오기",
            initial_dir,
            "Plan Files (*.json);;All Files (*)",
        )
        if file_path:
            self._last_plan_directory = Path(file_path).parent
            self.planFileSelected.emit(file_path)

    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: D401
        super().resizeEvent(event)
        if self._busy_overlay and self.centralWidget():
            self._busy_overlay.setGeometry(self.centralWidget().rect())

    def _emit_url_submitted(self) -> None:
        url = self._url_input.text().strip()
        if url:
            self.urlSubmitted.emit(url)
