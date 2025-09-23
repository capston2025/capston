"""Qt widgets composing the main application window."""
from __future__ import annotations

from typing import Callable, Iterable

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QLineEdit,
    QPushButton,
    QSplitter,
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
        self.setMinimumHeight(160)
        self.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #5c6bc0;
                border-radius: 12px;
                color: #5c6bc0;
                font-size: 16px;
                padding: 16px;
                background-color: rgba(92, 107, 192, 0.05);
            }
            """
        )

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
        self.resize(1200, 800)

        self._drop_area: DropArea
        self._checklist_view: QListWidget
        self._log_output: QTextEdit
        self._start_button: QPushButton
        self._cancel_button: QPushButton
        self._url_input: QLineEdit
        self._browser_view: QWebEngineView

        self._build_layout()

        if controller_factory:
            controller_factory(self)

    # ------------------------------------------------------------------
    # UI construction helpers
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        control_panel = QWidget(splitter)
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(24, 24, 24, 24)
        control_layout.setSpacing(16)

        self._drop_area = DropArea(
            on_file_dropped=self.fileDropped.emit,
            title="Drop PDF checklist here or click 'Select PDF'",
            parent=control_panel,
        )
        control_layout.addWidget(self._drop_area)

        url_row = QHBoxLayout()
        self._url_input = QLineEdit(control_panel)
        self._url_input.setPlaceholderText("https://example.com")
        url_row.addWidget(self._url_input)

        load_button = QPushButton("Load", control_panel)
        load_button.clicked.connect(self._emit_url_submitted)
        url_row.addWidget(load_button)
        control_layout.addLayout(url_row)

        button_row = QHBoxLayout()
        select_button = QPushButton("Select PDF…", control_panel)
        select_button.clicked.connect(self._open_file_dialog)
        button_row.addWidget(select_button)

        self._start_button = QPushButton("Start Automation", control_panel)
        self._start_button.clicked.connect(self.startRequested.emit)
        button_row.addWidget(self._start_button)

        self._cancel_button = QPushButton("Cancel", control_panel)
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.cancelRequested.emit)
        button_row.addWidget(self._cancel_button)

        button_row.addStretch()
        control_layout.addLayout(button_row)

        self._checklist_view = QListWidget(control_panel)
        self._checklist_view.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        control_layout.addWidget(self._checklist_view, stretch=2)

        self._log_output = QTextEdit(control_panel)
        self._log_output.setPlaceholderText("Automation logs will appear here…")
        self._log_output.setReadOnly(True)
        self._log_output.setMinimumHeight(160)
        control_layout.addWidget(self._log_output, stretch=1)

        splitter.addWidget(control_panel)

        self._browser_view = QWebEngineView(splitter)
        self._browser_view.setUrl(QUrl("about:blank"))
        splitter.addWidget(self._browser_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.setCentralWidget(splitter)

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
        self._start_button.setEnabled(not busy)
        self._cancel_button.setEnabled(busy)
        if busy:
            self._drop_area.setText("Automation in progress…")
        else:
            self._drop_area.setText("Drop PDF checklist here or click 'Select PDF'")

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
