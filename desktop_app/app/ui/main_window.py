"""Qt widgets composing the main application window."""
from __future__ import annotations

from typing import Callable, Iterable

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
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
)
from PyQt6.QtWebEngineWidgets import QWebEngineView


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


class MainWindow(QMainWindow):
    """Top level window wiring UI elements and controller callbacks."""

    fileDropped = pyqtSignal(str)
    startRequested = pyqtSignal()
    cancelRequested = pyqtSignal()
    urlSubmitted = pyqtSignal(str)

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
                background-color: #f8f9fb;
            }

            QWidget {
                color: #1d2333;
                font-family: 'Pretendard', 'Noto Sans KR', 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
                font-size: 13.5px;
            }

            QLabel#AppTitle {
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 0.8px;
            }

            QFrame#SidePanel {
                background: rgba(255, 255, 255, 0.95);
                border-radius: 20px;
                border: 1px solid rgba(157, 169, 196, 0.18);
            }

            QFrame#BrowserCard {
                background: rgba(255, 255, 255, 0.98);
                border-radius: 24px;
                border: 1px solid rgba(157, 169, 196, 0.18);
            }

            QLabel#SectionLabel, QLabel#BrowserTitle {
                font-weight: 600;
                font-size: 12.5px;
                color: #7d869c;
                letter-spacing: 0.5px;
            }

            QLabel#BrowserTitle {
                font-size: 14.5px;
                color: #2d3243;
            }

            QLabel#DropArea {
                border: 1.6px dashed rgba(255, 125, 150, 0.55);
                border-radius: 20px;
                padding: 22px;
                color: #ff5d8a;
                background: rgba(255, 206, 220, 0.16);
            }

            QListWidget {
                background: rgba(248, 249, 253, 0.94);
                border-radius: 16px;
                border: 1px solid rgba(188, 197, 219, 0.28);
                padding: 8px;
            }

            QListWidget::item {
                padding: 6px 10px;
                margin: 3px 0;
                border-radius: 10px;
            }

            QListWidget::item:selected {
                background: rgba(255, 120, 160, 0.16);
                color: #1d2333;
            }

            QTextEdit {
                background: rgba(248, 249, 253, 0.96);
                border-radius: 16px;
                border: 1px solid rgba(188, 197, 219, 0.24);
                padding: 14px;
                color: #434961;
            }

            QLineEdit {
                background: rgba(255, 255, 255, 0.98);
                border-radius: 14px;
                border: 1px solid rgba(176, 187, 210, 0.5);
                padding: 11px 15px;
            }

            QLineEdit:focus {
                border: 1px solid rgba(255, 100, 150, 0.65);
                box-shadow: 0 0 0 2px rgba(255, 100, 150, 0.18);
            }

            QPushButton {
                border-radius: 16px;
                padding: 10px 18px;
                color: #ffffff;
                font-weight: 600;
                border: none;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff7ca0, stop:1 #ffa66a);
            }

            QPushButton:disabled {
                background: rgba(170, 180, 200, 0.4);
                color: rgba(255, 255, 255, 0.75);
            }

            QPushButton#GhostButton {
                background: rgba(255, 255, 255, 0.0);
                border: 1px solid rgba(255, 140, 180, 0.45);
                color: #ff6d93;
            }

            QPushButton#GhostButton:hover {
                background: rgba(255, 140, 180, 0.12);
            }

            QPushButton#DangerButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff6676, stop:1 #ff8a62);
            }

            QSplitter::handle {
                background: transparent;
                width: 16px;
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
        self._feedback_input: QTextEdit
        self._url_input: QLineEdit
        self._browser_view: QWebEngineView
        self._workflow_stage: str
        self._is_busy: bool

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
        root_layout.setContentsMargins(28, 24, 28, 24)
        root_layout.setSpacing(24)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        header_title = QLabel("GAIA", central)
        header_title.setObjectName("AppTitle")
        header_row.addWidget(header_title)
        header_row.addStretch()

        root_layout.addLayout(header_row)
        root_layout.addSpacing(8)

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
        browser_layout.setContentsMargins(28, 28, 28, 28)
        browser_layout.setSpacing(18)

        browser_title = QLabel("실행 화면 미리보기", browser_card)
        browser_title.setObjectName("BrowserTitle")
        browser_layout.addWidget(browser_title)

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

        title_label = QLabel("2단계. 자동화 검증", page)
        title_label.setObjectName("SectionLabel")
        layout.addWidget(title_label)

        control_row = QHBoxLayout()
        control_row.setSpacing(12)

        self._back_to_setup_button = QPushButton("입력 단계로", page)
        self._back_to_setup_button.setObjectName("GhostButton")
        self._back_to_setup_button.clicked.connect(self.show_setup_stage)
        control_row.addWidget(self._back_to_setup_button)

        self._cancel_button = QPushButton("중단", page)
        self._cancel_button.setObjectName("DangerButton")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.cancelRequested.emit)
        control_row.addWidget(self._cancel_button)

        control_row.addStretch()
        layout.addLayout(control_row)

        scenario_label = QLabel("자동화 시나리오", page)
        scenario_label.setObjectName("SectionLabel")
        layout.addWidget(scenario_label)

        self._checklist_view = QListWidget(page)
        self._checklist_view.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._checklist_view.setSpacing(4)
        layout.addWidget(self._checklist_view, stretch=2)

        logs_label = QLabel("라이브 로그", page)
        logs_label.setObjectName("SectionLabel")
        layout.addWidget(logs_label)

        self._log_output = QTextEdit(page)
        self._log_output.setPlaceholderText("실행 로그가 여기에 표시됩니다…")
        self._log_output.setReadOnly(True)
        self._log_output.setMinimumHeight(140)
        layout.addWidget(self._log_output, stretch=1)

        feedback_label = QLabel("검증 피드백", page)
        feedback_label.setObjectName("SectionLabel")
        layout.addWidget(feedback_label)

        self._feedback_input = QTextEdit(page)
        self._feedback_input.setPlaceholderText("발견한 오류나 보완이 필요한 내용을 입력해 주세요…")
        self._feedback_input.setMinimumHeight(110)
        layout.addWidget(self._feedback_input, stretch=1)

        layout.addStretch(1)
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

    def get_feedback_text(self) -> str:
        """Return the trimmed feedback message authored by the operator."""
        return self._feedback_input.toPlainText().strip()

    # ------------------------------------------------------------------
    # Slots exposed to the controller
    # ------------------------------------------------------------------
    def show_checklist(self, items: Iterable[str]) -> None:
        self._checklist_view.clear()
        for item in items:
            QListWidgetItem(item, self._checklist_view)

    def append_log(self, message: str) -> None:
        self._log_output.append(message)

    def set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        if busy:
            self.show_review_stage()

        self._start_button.setEnabled(not busy)
        self._cancel_button.setEnabled(busy)
        self._back_to_setup_button.setEnabled(self._workflow_stage == "review" and not busy)
        self._drop_area.setEnabled(not busy)
        self._url_input.setEnabled(not busy)
        if busy:
            self._drop_area.setText("자동화를 진행 중이에요… 잠시만 기다려 주세요 ☄️")
        else:
            self._drop_area.setText("체크리스트 PDF를 드래그하거나 선택해 주세요")

    def load_url(self, url: str) -> None:
        self._browser_view.setUrl(QUrl(url))

    def set_url_field(self, url: str) -> None:
        self._url_input.setText(url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _open_file_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select checklist PDF",
            "",
            "PDF Files (*.pdf)",
        )
        if file_path:
            self.fileDropped.emit(file_path)

    def _emit_url_submitted(self) -> None:
        url = self._url_input.text().strip()
        if url:
            self.urlSubmitted.emit(url)
