"""메인 애플리케이션 창을 구성하는 Qt 위젯 모음입니다."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, List, Sequence

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QGridLayout,
    QLayout,
)

from gaia.src.gui.screencast_client import ScreencastClient
from gaia.src.gui.exploration_viewer import ExplorationViewer


class _WebViewFallback(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        self._status = QLabel(
            "브라우저 미리보기 기능이 비활성화되었습니다.\n"
            "PySide6 WebEngine을 사용할 수 없습니다.",
            self,
        )
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def setUrl(self, url: QUrl | str) -> None:
        text = url.toString() if hasattr(url, "toString") else str(url)
        self._status.setText(f"브라우저 미리보기 기능이 비활성화되어 있습니다.\n주소: {text}")

    def setHtml(self, html: str, base_url: Any | None = None) -> None:  # noqa: ARG002
        self._status.setText(html[:120] + ("..." if len(html) > 120 else ""))


def _build_browser_view(parent: QWidget) -> QWidget:
    return _WebViewFallback(parent)


class DropArea(QLabel):
    """로컬 파일 드래그 앤 드롭을 지원하는 라벨 위젯입니다."""

    def __init__(
        self,
        on_file_dropped: Callable[[str], None],
        *,
        title: str,
        parent: QWidget | None = None,
    ) -> None:
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
    """QPainter로 그린 원형 스피너 위젯입니다."""

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

        # 스피너 호에 잔상을 주는 설정
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
    """스피너와 상태 레이블, 경과 시간을 포함한 반투명 오버레이입니다."""

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
        container_layout.addWidget(
            self._spinner, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self._message = QLabel("분석 중입니다…", container)
        self._message.setObjectName("OverlayLabel")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self._message)

        # 경과 시간 레이블
        self._elapsed_label = QLabel("", container)
        self._elapsed_label.setObjectName("OverlayElapsedLabel")
        self._elapsed_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self._elapsed_label)

        # 예상 소요 시간 안내
        self._hint_label = QLabel("⏱️  예상 소요 시간: 3-8분", container)
        self._hint_label.setObjectName("OverlayHintLabel")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(self._hint_label)

        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)

        # 경과 시간 갱신을 위한 타이머
        self._elapsed_seconds = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)

    def show_with_message(self, message: str) -> None:
        self._message.setText(message)
        self._elapsed_seconds = 0
        self._update_elapsed_time()
        self._spinner.start()
        self._elapsed_timer.start(1000)  # 매초 업데이트
        self.show()

    def hide_overlay(self) -> None:
        self._spinner.stop()
        self._elapsed_timer.stop()
        self.hide()

    def _update_elapsed_time(self) -> None:
        """경과 시간 표시를 업데이트합니다"""
        minutes = self._elapsed_seconds // 60
        seconds = self._elapsed_seconds % 60
        self._elapsed_label.setText(f"⏱️  경과 시간: {minutes}분 {seconds:02d}초")
        self._elapsed_seconds += 1


class CircularProgressWidget(QWidget):
    """전체 진행률을 원형 그래프로 보여주는 위젯."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value: float = 0.0
        self._track_color = QColor(226, 232, 240)
        self._progress_color = QColor(16, 185, 129)
        self._pen_width = 16
        self.setMinimumSize(180, 180)

    def set_value(self, value: float) -> None:
        clamped = max(0.0, min(100.0, float(value)))
        if abs(clamped - self._value) > 0.1:
            self._value = clamped
            self.update()

    def paintEvent(self, event) -> None:  # noqa: D401
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = event.rect().adjusted(12, 12, -12, -12)

        # 배경 원
        track_pen = QPen(self._track_color, self._pen_width)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # 진행률 아크
        progress_pen = QPen(self._progress_color, self._pen_width)
        progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        progress_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(progress_pen)
        span_angle = int(-self._value / 100 * 360 * 16)
        painter.drawArc(rect, 90 * 16, span_angle)

        # 텍스트
        painter.setPen(QColor(26, 27, 61))
        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter,
            f"{int(round(self._value))}%",
        )


class TestProgressBadge(QFrame):
    """테스트 케이스 완료 여부를 나타내는 원형 배지."""

    def __init__(
        self,
        title: str,
        percent: float,
        status: str = "pending",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TestProgressBadge")
        self._percent = 0.0
        self._status = status

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._indicator = QLabel(self)
        self._indicator.setObjectName("TestProgressIndicator")
        self._indicator.setFixedSize(40, 40)
        self._indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._indicator, alignment=Qt.AlignmentFlag.AlignCenter)

        self._title_label = QLabel(title, self)
        self._title_label.setObjectName("TestProgressCode")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)

        self.set_progress(percent, status)

    def set_progress(self, percent: float, status: str | None = None) -> None:
        self._percent = max(0.0, min(100.0, float(percent)))
        if status is not None:
            self._status = status

        normalized_status = (self._status or "pending").lower()

        if normalized_status == "failed":
            indicator_style = (
                "background: #ef4444;"
                "border: 2px solid #ef4444;"
                "border-radius: 20px;"
                "color: white;"
                "font-weight: 700;"
            )
            self._indicator.setText("✕")
        elif normalized_status == "skipped":
            indicator_style = (
                "background: rgba(255,255,255,0.95);"
                "border: 2px dashed #d1d5db;"
                "border-radius: 20px;"
                "color: #9ca3af;"
                "font-weight: 700;"
            )
            self._indicator.setText("—")
        elif normalized_status == "partial":
            indicator_style = (
                "background: #f97316;"
                "border: 2px solid #f97316;"
                "border-radius: 20px;"
                "color: white;"
                "font-weight: 700;"
            )
            self._indicator.setText("~")
        elif self._percent >= 99.0 or normalized_status == "success":
            indicator_style = (
                "background: #10b981;"
                "border: 2px solid #10b981;"
                "border-radius: 20px;"
                "color: white;"
                "font-weight: 700;"
            )
            self._indicator.setText("✓")
        else:
            indicator_style = (
                "background: rgba(255,255,255,0.95);"
                "border: 2px solid #d1d5db;"
                "border-radius: 20px;"
                "color: #9ca3af;"
                "font-weight: 700;"
            )
            self._indicator.setText("")
        self._indicator.setStyleSheet(indicator_style)


class ScenarioCard(QFrame):
    """생성된 테스트 시나리오를 표현하는 글래스모피즘 카드입니다."""

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
        self.setProperty(
            "scenario_id", str(scenario_id)
        )  # Store scenario ID for highlight tracking
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

        title_text = getattr(scenario, "scenario", None) or getattr(
            scenario, "name", "Unnamed scenario"
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
        if not expected_text:
            success_criteria = getattr(scenario, "success_criteria", None)
            if success_criteria:
                expected_text = ", ".join(success_criteria)

        if expected_text:
            assertion = QLabel(f"✅ {expected_text}", self)
            assertion.setProperty("role", "assertion-text")
            assertion.setWordWrap(True)
            layout.addWidget(assertion)


class MainWindow(QMainWindow):
    """UI 요소와 컨트롤러 콜백을 연결하는 최상위 창입니다."""

    fileDropped = Signal(str)
    startRequested = Signal()
    cancelRequested = Signal()
    urlSubmitted = Signal(str)
    chatMessageSubmitted = Signal(str)
    planFileSelected = Signal(str)
    bugJsonSelected = Signal(str)

    def __init__(
        self, *, controller_factory: Callable[["MainWindow"], object] | None = None
    ) -> None:
        super().__init__()
        self.setWindowTitle("QA Automation Desktop")

        # 특정 기능 테스트 쿼리 저장
        self._current_feature_query = ""
        self._result_screenshot_history: list[str] = []

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

            QPushButton[modeButton="true"] {
                background: transparent;
                border: 1px solid rgba(125, 135, 255, 0.5);
                color: #5b5ff7;
            }

            QPushButton[modeButton="true"][modeSelected="true"] {
                background: rgba(91, 95, 247, 0.14);
                border: 1.4px solid rgba(91, 95, 247, 0.85);
                color: #3e43d6;
            }

            QPushButton#DangerButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ff6b8a, stop:1 #ff8f70);
            }

            QFrame#ResultSummaryCard {
                background: rgba(255, 255, 255, 0.85);
                border-radius: 22px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }

            QLabel#ResultSummaryStatus {
                font-size: 18px;
                font-weight: 700;
                color: #181b3d;
            }

            QLabel#ResultSummaryMeta {
                color: #4b4f73;
                font-size: 13px;
            }

            QLabel#ResultSummaryReason {
                color: #2c2f48;
                font-size: 13px;
            }

            QLabel#ResultSummaryHint {
                color: #636b86;
                font-size: 12.5px;
                font-weight: 600;
            }

            QLabel[role="stateLabel"] {
                color: #252a46;
                font-size: 13px;
                font-weight: 600;
            }

            QTextEdit#ResultTimelineView {
                background: rgba(247, 249, 255, 0.92);
                border-radius: 16px;
                border: 1px solid rgba(198, 205, 255, 0.85);
                color: #1f2745;
                padding: 12px;
            }

            QFrame#ResultScreenshotCard {
                background: rgba(247, 249, 255, 0.92);
                border-radius: 16px;
                border: 1px solid rgba(198, 205, 255, 0.85);
            }

            QLabel#ResultScreenshotThumb {
                background: rgba(255, 255, 255, 0.95);
                border-radius: 12px;
                border: 1px solid rgba(198, 205, 255, 0.85);
                padding: 4px;
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

            QFrame#LogsControls {
                background: transparent;
            }

            QFrame#OverallProgressCard {
                background: rgba(255, 255, 255, 0.85);
                border-radius: 26px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }

            QLabel#OverallProgressDetail {
                font-size: 14px;
                color: #1f2937;
                font-weight: 600;
            }

            QFrame#ScenarioProgressPanel {
                background: rgba(255, 255, 255, 0.85);
                border-radius: 26px;
                border: 1px solid rgba(255, 255, 255, 0.4);
            }

            QScrollArea#ScenarioProgressScroll {
                background: transparent;
                border: none;
            }

            QWidget#ScenarioProgressContent {
                background: transparent;
            }

            QFrame#TestProgressBadge {
                background: transparent;
            }

            QLabel#TestProgressCode {
                font-weight: 600;
                color: #1f2937;
                font-size: 12px;
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
        self._log_output: QTextEdit | None
        self._start_button: QPushButton
        self._cancel_button: QPushButton
        self._back_to_setup_button: QPushButton
        self._view_logs_button: QPushButton | None
        self._url_input: QLineEdit
        self._browser_view: QWidget
        self._workflow_stage: str
        self._selected_run_mode: str = "quick"
        self._control_channel: str = "local"
        self._full_execution_logs: List[str] = []
        self._log_mode: str = "summary"  # "summary" or "full"
        self._is_busy: bool
        self._busy_overlay: BusyOverlay | None = None
        self._screencast_client: ScreencastClient | None = None
        self._overall_progress_widget: CircularProgressWidget | None = None
        self._overall_progress_detail: QLabel | None = None
        self._test_progress_layout: QGridLayout | None = None
        self._test_progress_empty_label: QLabel | None = None

        self._log_output = None
        self._view_logs_button = None
        self._is_busy = False
        self._build_layout()
        self._setup_screencast()

        if controller_factory:
            controller_factory(self)

    # ------------------------------------------------------------------
    # UI 구성 헬퍼
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        central = QWidget(self)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(20, 16, 20, 20)  # 상단 여백 축소
        root_layout.setSpacing(16)  # 간격 축소

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        header_title = QLabel("GAIA", central)
        header_title.setObjectName("AppTitle")
        header_row.addWidget(header_title)
        header_row.addStretch()

        root_layout.addLayout(header_row)
        root_layout.addSpacing(4)  # 간격 축소

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
        self._exploration_page = ExplorationViewer(control_panel)
        self._exploration_page.back_requested.connect(self.show_setup_stage)
        self._exploration_page.replay_requested.connect(self._show_replay_html)
        self._workflow_stack.addWidget(self._setup_page)
        self._workflow_stack.addWidget(self._review_page)
        self._workflow_stack.addWidget(self._exploration_page)
        control_layout.addWidget(self._workflow_stack, stretch=1)

        splitter.addWidget(control_panel)

        browser_card = QFrame(splitter)
        browser_card.setObjectName("BrowserCard")
        browser_layout = QVBoxLayout(browser_card)
        browser_layout.setContentsMargins(0, 0, 0, 0)  # 모든 여백 제거
        browser_layout.setSpacing(0)  # 간격 제거

        # 브라우저 제목을 숨겨 콘텐츠 영역을 최대화
        self._browser_view = _build_browser_view(browser_card)
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
        self.set_selected_run_mode("quick")
        self.set_control_channel("local")
        self.show_setup_stage()

    def _create_setup_stage(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title_label = QLabel("테스트 준비", page)
        title_label.setObjectName("SectionLabel")
        layout.addWidget(title_label)

        self._drop_area = DropArea(
            on_file_dropped=self._handle_file_drop,
            title="기획서 파일 또는 PRD 번들을 드래그하거나 선택해 주세요",
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

        source_label = QLabel("1. 입력 소스 선택", page)
        source_label.setObjectName("SectionLabel")
        layout.addWidget(source_label)

        source_row = QHBoxLayout()
        source_row.setSpacing(12)

        select_button = QPushButton("기획서 파일 선택", page)
        select_button.setObjectName("GhostButton")
        select_button.clicked.connect(self._open_file_dialog)
        source_row.addWidget(select_button)

        self._load_plan_button = QPushButton("기존 번들 열기", page)
        self._load_plan_button.setObjectName("GhostButton")
        self._load_plan_button.clicked.connect(self._open_plan_dialog)
        source_row.addWidget(self._load_plan_button)
        source_row.addStretch()
        layout.addLayout(source_row)

        source_hint = QLabel(
            "PDF, DOCX, MD, TXT 기획서를 바로 분석하거나 저장된 JSON 번들을 다시 열 수 있습니다.",
            page,
        )
        source_hint.setWordWrap(True)
        layout.addWidget(source_hint)

        mode_label = QLabel("2. 실행 모드", page)
        mode_label.setObjectName("SectionLabel")
        layout.addWidget(mode_label)

        self._control_status_label = QLabel("제어 채널: 로컬", page)
        self._control_status_label.setWordWrap(True)
        layout.addWidget(self._control_status_label)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self._run_mode_group = QButtonGroup(page)

        self._quick_mode_button = QPushButton("빠른 목표 실행", page)
        self._quick_mode_button.setCheckable(True)
        self._quick_mode_button.setProperty("modeButton", True)
        self._quick_mode_button.clicked.connect(lambda: self.set_selected_run_mode("quick"))
        self._run_mode_group.addButton(self._quick_mode_button)
        mode_row.addWidget(self._quick_mode_button)

        self._ai_mode_button = QPushButton("완전 자율", page)
        self._ai_mode_button.setCheckable(True)
        self._ai_mode_button.setProperty("modeButton", True)
        self._ai_mode_button.clicked.connect(lambda: self.set_selected_run_mode("ai"))
        self._run_mode_group.addButton(self._ai_mode_button)
        mode_row.addWidget(self._ai_mode_button)

        self._bundle_mode_button = QPushButton("기획서/번들 실행", page)
        self._bundle_mode_button.setCheckable(True)
        self._bundle_mode_button.setProperty("modeButton", True)
        self._bundle_mode_button.clicked.connect(lambda: self.set_selected_run_mode("bundle"))
        self._run_mode_group.addButton(self._bundle_mode_button)
        mode_row.addWidget(self._bundle_mode_button)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        action_label = QLabel("3. 실행 준비", page)
        action_label.setObjectName("SectionLabel")
        layout.addWidget(action_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self._start_button = QPushButton("테스트 실행", page)
        self._start_button.clicked.connect(self.startRequested.emit)
        action_row.addWidget(self._start_button)

        action_row.addStretch()
        layout.addLayout(action_row)

        # 탐색 결과 보기 버튼 행
        results_row = QHBoxLayout()
        results_row.setSpacing(12)

        self._view_results_button = QPushButton("📊 탐색 결과 보기", page)
        self._view_results_button.setObjectName("GhostButton")
        self._view_results_button.clicked.connect(self.show_exploration_results)
        results_row.addWidget(self._view_results_button)
        results_row.addStretch()
        layout.addLayout(results_row)

        # 특정 기능 테스트 입력창 (처음엔 숨김)
        self._feature_input_container = QFrame(page)
        self._feature_input_container.setObjectName("FeatureInputContainer")
        feature_input_layout = QVBoxLayout(self._feature_input_container)
        feature_input_layout.setContentsMargins(12, 12, 12, 12)
        feature_input_layout.setSpacing(8)

        feature_label = QLabel(
            "테스트할 기능을 설명해주세요:", self._feature_input_container
        )
        feature_label.setObjectName("FeatureLabel")
        feature_input_layout.addWidget(feature_label)

        self._feature_input = QLineEdit(self._feature_input_container)
        self._feature_input.setPlaceholderText(
            "예: 로그인 버튼이 보이는지, 학점 필터링이 작동하는지"
        )
        self._feature_input.setObjectName("FeatureInput")
        self._feature_input.textChanged.connect(self._sync_feature_query)
        feature_input_layout.addWidget(self._feature_input)

        layout.addWidget(self._feature_input_container)

        chat_label = QLabel("4. 대화형 입력", page)
        chat_label.setObjectName("SectionLabel")
        layout.addWidget(chat_label)

        self._chat_transcript = QTextEdit(page)
        self._chat_transcript.setReadOnly(True)
        self._chat_transcript.setMinimumHeight(160)
        self._chat_transcript.setPlaceholderText("여기에 실행 대화 기록이 표시됩니다.")
        layout.addWidget(self._chat_transcript)

        chat_row = QHBoxLayout()
        chat_row.setSpacing(12)
        self._chat_input = QLineEdit(page)
        self._chat_input.setPlaceholderText("예: 로그인 버튼이 보이는지 확인해줘 / 지금 뭐하고 있어?")
        self._chat_input.returnPressed.connect(self._emit_chat_message)
        chat_row.addWidget(self._chat_input)

        self._chat_send_button = QPushButton("보내기", page)
        self._chat_send_button.setObjectName("GhostButton")
        self._chat_send_button.clicked.connect(self._emit_chat_message)
        chat_row.addWidget(self._chat_send_button)
        layout.addLayout(chat_row)

        layout.addStretch(1)

        return page

    def _create_review_stage(self, parent: QWidget) -> QWidget:
        page = QWidget(parent)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # 상단의 제목
        title_row = QHBoxLayout()
        title_label = QLabel("자동화 검증", page)
        title_label.setObjectName("SectionLabel")
        title_row.addWidget(title_label)
        title_row.addStretch()

        layout.addLayout(title_row)

        self._result_summary_card = QFrame(page)
        self._result_summary_card.setObjectName("ResultSummaryCard")
        result_summary_layout = QVBoxLayout(self._result_summary_card)
        result_summary_layout.setContentsMargins(18, 18, 18, 18)
        result_summary_layout.setSpacing(8)

        self._result_summary_status = QLabel("실행 결과 대기 중", self._result_summary_card)
        self._result_summary_status.setObjectName("ResultSummaryStatus")
        result_summary_layout.addWidget(self._result_summary_status)

        self._result_summary_meta = QLabel("모드와 검증 요약이 여기에 표시됩니다.", self._result_summary_card)
        self._result_summary_meta.setObjectName("ResultSummaryMeta")
        self._result_summary_meta.setWordWrap(True)
        result_summary_layout.addWidget(self._result_summary_meta)

        self._result_summary_reason = QLabel("실행이 시작되면 판정 사유가 표시됩니다.", self._result_summary_card)
        self._result_summary_reason.setObjectName("ResultSummaryReason")
        self._result_summary_reason.setWordWrap(True)
        result_summary_layout.addWidget(self._result_summary_reason)

        self._result_live_goal = QLabel("현재 목표: -", self._result_summary_card)
        self._result_live_goal.setProperty("role", "stateLabel")
        self._result_live_goal.setWordWrap(True)
        result_summary_layout.addWidget(self._result_live_goal)

        self._result_live_step = QLabel("현재 단계: -", self._result_summary_card)
        self._result_live_step.setProperty("role", "stateLabel")
        self._result_live_step.setWordWrap(True)
        result_summary_layout.addWidget(self._result_live_step)

        self._result_live_blocked = QLabel("차단 사유: 없음", self._result_summary_card)
        self._result_live_blocked.setProperty("role", "stateLabel")
        self._result_live_blocked.setWordWrap(True)
        result_summary_layout.addWidget(self._result_live_blocked)

        timeline_label = QLabel("단계별 실행", self._result_summary_card)
        timeline_label.setObjectName("ResultSummaryHint")
        result_summary_layout.addWidget(timeline_label)

        self._result_timeline_view = QTextEdit(self._result_summary_card)
        self._result_timeline_view.setObjectName("ResultTimelineView")
        self._result_timeline_view.setReadOnly(True)
        self._result_timeline_view.setMinimumHeight(150)
        self._result_timeline_view.setPlaceholderText("실행이 시작되면 단계별 내역과 검증 근거가 여기에 표시됩니다.")
        result_summary_layout.addWidget(self._result_timeline_view)

        screenshot_label = QLabel("대표 캡처", self._result_summary_card)
        screenshot_label.setObjectName("ResultSummaryHint")
        result_summary_layout.addWidget(screenshot_label)

        self._result_screenshot_card = QFrame(self._result_summary_card)
        self._result_screenshot_card.setObjectName("ResultScreenshotCard")
        screenshot_card_layout = QVBoxLayout(self._result_screenshot_card)
        screenshot_card_layout.setContentsMargins(10, 10, 10, 10)
        screenshot_card_layout.setSpacing(8)

        self._result_screenshot_scroll = QScrollArea(self._result_screenshot_card)
        self._result_screenshot_scroll.setWidgetResizable(True)
        self._result_screenshot_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._result_screenshot_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._result_screenshot_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._result_screenshot_container = QWidget(self._result_screenshot_scroll)
        self._result_screenshot_layout = QHBoxLayout(self._result_screenshot_container)
        self._result_screenshot_layout.setContentsMargins(0, 0, 0, 0)
        self._result_screenshot_layout.setSpacing(8)
        self._result_screenshot_layout.addStretch()
        self._result_screenshot_scroll.setWidget(self._result_screenshot_container)
        screenshot_card_layout.addWidget(self._result_screenshot_scroll)

        result_summary_layout.addWidget(self._result_screenshot_card)
        self._render_result_screenshot_strip()

        layout.addWidget(self._result_summary_card)

        # 상단 진행 현황 영역
        progress_container = QFrame(page)
        progress_container.setObjectName("LogsContainer")
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(12)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(18)
        progress_layout.addLayout(progress_row)

        overall_card = QFrame(progress_container)
        overall_card.setObjectName("OverallProgressCard")
        overall_layout = QVBoxLayout(overall_card)
        overall_layout.setContentsMargins(20, 20, 20, 20)
        overall_layout.setSpacing(10)

        overall_title = QLabel("전체 진행률", overall_card)
        overall_title.setObjectName("SectionLabel")
        overall_layout.addWidget(overall_title)

        self._overall_progress_widget = CircularProgressWidget(overall_card)
        overall_layout.addWidget(
            self._overall_progress_widget, alignment=Qt.AlignmentFlag.AlignCenter
        )

        self._overall_progress_detail = QLabel("0 / 0 완료", overall_card)
        self._overall_progress_detail.setObjectName("OverallProgressDetail")
        self._overall_progress_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overall_layout.addWidget(self._overall_progress_detail)

        progress_row.addWidget(overall_card, stretch=1)

        scenario_progress_card = QFrame(progress_container)
        scenario_progress_card.setObjectName("ScenarioProgressPanel")
        scenario_progress_layout = QVBoxLayout(scenario_progress_card)
        scenario_progress_layout.setContentsMargins(20, 20, 20, 20)
        scenario_progress_layout.setSpacing(10)

        scenario_progress_header = QHBoxLayout()
        scenario_progress_header.setContentsMargins(0, 0, 0, 0)
        scenario_progress_header.setSpacing(8)
        scenario_progress_label = QLabel("테스트 케이스 진행률", scenario_progress_card)
        scenario_progress_label.setObjectName("SectionLabel")
        scenario_progress_header.addWidget(scenario_progress_label)
        scenario_progress_header.addStretch()
        scenario_progress_layout.addLayout(scenario_progress_header)

        progress_scroll = QScrollArea(scenario_progress_card)
        progress_scroll.setObjectName("ScenarioProgressScroll")
        progress_scroll.setWidgetResizable(True)
        progress_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        progress_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        progress_scroll.setFrameShape(QFrame.Shape.NoFrame)

        progress_scroll_content = QWidget(progress_scroll)
        progress_scroll_content.setObjectName("ScenarioProgressContent")
        self._test_progress_layout = QGridLayout(progress_scroll_content)
        self._test_progress_layout.setContentsMargins(0, 0, 0, 0)
        self._test_progress_layout.setHorizontalSpacing(16)
        self._test_progress_layout.setVerticalSpacing(14)
        progress_scroll.setWidget(progress_scroll_content)
        scenario_progress_layout.addWidget(progress_scroll)

        # 초기 비어 있는 상태 메시지
        empty_label = QLabel("아직 진행률 정보가 없습니다.", progress_scroll_content)
        empty_label.setProperty("role", "empty-state")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._test_progress_empty_label = empty_label
        self._test_progress_layout.addWidget(
            empty_label, 0, 0, Qt.AlignmentFlag.AlignCenter
        )

        progress_row.addWidget(scenario_progress_card, stretch=2)

        controls_bar = QFrame(progress_container)
        controls_bar.setObjectName("LogsControls")
        controls_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        controls_row = QHBoxLayout(controls_bar)
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(12)
        controls_row.addStretch()

        self._back_to_setup_button = QPushButton("입력 단계로", controls_bar)
        self._back_to_setup_button.setObjectName("GhostButton")
        self._back_to_setup_button.clicked.connect(self.show_setup_stage)
        controls_row.addWidget(self._back_to_setup_button)

        self._cancel_button = QPushButton("중단", controls_bar)
        self._cancel_button.setObjectName("DangerButton")
        self._cancel_button.setEnabled(False)
        self._cancel_button.clicked.connect(self.cancelRequested.emit)
        controls_row.addWidget(self._cancel_button)

        self._view_logs_button = QPushButton("상세 로그 보기", controls_bar)
        self._view_logs_button.setObjectName("GhostButton")
        self._view_logs_button.setEnabled(False)
        self._view_logs_button.clicked.connect(self._show_detailed_logs)
        controls_row.addWidget(self._view_logs_button)

        progress_layout.addWidget(controls_bar)
        layout.addWidget(progress_container, stretch=2)

        # 시나리오 영역(하단)
        scenario_label = QLabel("자동화 시나리오", page)
        scenario_label.setObjectName("SectionLabel")
        layout.addWidget(scenario_label)

        self._checklist_view = QListWidget(page)
        self._checklist_view.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._checklist_view.setSpacing(12)
        layout.addWidget(self._checklist_view, stretch=2)

        # 피드백 섹션 제거(더 이상 필요 없음)

        return page

    # ------------------------------------------------------------------
    # 워크플로 단계 헬퍼
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

    def show_exploration_results(self) -> None:
        """탐색 결과 뷰어 페이지 표시"""
        self._workflow_stage = "exploration"
        self._exploration_page.refresh_results()
        if self._workflow_stack.currentWidget() is not self._exploration_page:
            self._workflow_stack.setCurrentWidget(self._exploration_page)

    # ------------------------------------------------------------------
    # 컨트롤러에 노출되는 슬롯
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

    def highlight_current_scenario(self, scenario_id: str) -> None:
        """현재 실행 중인 시나리오만 보이게 하고 나머지는 숨깁니다."""
        for i in range(self._checklist_view.count()):
            item = self._checklist_view.item(i)
            card = self._checklist_view.itemWidget(item)
            if isinstance(card, ScenarioCard):
                card_id = card.property("scenario_id")
                if card_id == scenario_id:
                    # 현재 실행 중인 시나리오: 보이기
                    item.setHidden(False)
                    card.setStyleSheet(
                        "QFrame#ScenarioCard { border: 2px solid #5b5ff7; }"
                    )
                else:
                    # 나머지: 숨기기
                    item.setHidden(True)

    def reset_scenario_highlights(self) -> None:
        """모든 시나리오 다시 보이게 복원 (테스트 완료 후)"""
        for i in range(self._checklist_view.count()):
            item = self._checklist_view.item(i)
            card = self._checklist_view.itemWidget(item)
            if isinstance(card, ScenarioCard):
                item.setHidden(False)  # 모두 다시 보이기
                card.setStyleSheet("")  # 기본 스타일로 복원

    def update_overall_progress(
        self, percent: float, completed: int | None = None, total: int | None = None
    ) -> None:
        """전체 진행률 원형 그래프를 갱신합니다."""
        if self._overall_progress_widget:
            self._overall_progress_widget.set_value(percent)

        if self._overall_progress_detail:
            if completed is not None and total is not None:
                self._overall_progress_detail.setText(f"{completed} / {total} 완료")
            else:
                self._overall_progress_detail.setText(
                    f"{int(round(max(0.0, min(100.0, percent))))}% 진행"
                )

    def update_test_progress(self, progress_items: Sequence[tuple]) -> None:
        """개별 테스트 케이스 진행률 막대를 갱신합니다."""
        if not self._test_progress_layout:
            return

        normalized_items: list[tuple[str, float, str]] = []
        for entry in progress_items:
            if len(entry) >= 3:
                title, percent, status = entry[0], entry[1], entry[2]
            else:
                title, percent = entry[0], entry[1]
                status = "pending"
            normalized_items.append((str(title), float(percent), str(status)))

        # 기존 항목 제거
        while self._test_progress_layout.count():
            item = self._test_progress_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if widget is self._test_progress_empty_label:
                    self._test_progress_empty_label = None
                widget.deleteLater()

        if not normalized_items:
            empty_label = QLabel("아직 진행률 정보가 없습니다.", self)
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setProperty("role", "empty-state")
            self._test_progress_empty_label = empty_label
            self._test_progress_layout.addWidget(
                empty_label, 0, 0, 1, 5, Qt.AlignmentFlag.AlignCenter
            )
            return

        cols = 5
        for idx, (title, percent, status) in enumerate(normalized_items):
            row = idx // cols
            col = idx % cols
            item_widget = TestProgressBadge(title, percent, status, self)
            self._test_progress_layout.addWidget(
                item_widget, row, col, Qt.AlignmentFlag.AlignCenter
            )

        final_row = (len(normalized_items) + cols - 1) // cols
        self._test_progress_layout.setRowStretch(final_row, 1)

    def append_log(self, message: str) -> None:
        """로그 메시지를 추가합니다. UI에는 요약만 표시하고 전체 로그는 저장합니다."""
        # 항상 전체 로그를 저장
        self._full_execution_logs.append(message)

        log_output = self._log_output
        if not log_output:
            return

        # 요약 모드에서는 중요한 메시지만 표시
        if self._log_mode == "summary":
            # 상태 아이콘이나 핵심 키워드가 있는 메시지만 표시
            important_keywords = (
                "Step ",
                "Exploring",
                "Discovered",
                "Page ",
                "Executing",
                "PASS",
                "FAIL",
                "SKIP",
                "Execution Results",
                "상세 결과",
                "Passed:",
                "Failed:",
                "Skipped:",
                "complete",
                # 실시간 진행 표시(UI 반응성 향상을 위해 추가)
                "🤖 Step",
                "📜 Scroll",
                "⬇️",
                "📸 Re-analyzing",
                "🎯 Trying",
                "✅ Found",
                "❌ Element not found",
                "🔍 Low confidence",
                "💡 Reason:",
                "🌐 Current URL",
                "📊 Available DOM",
                "🤖 Using GPT-5",
                "🤖 Asking GPT-5",
            )
            if any(keyword in message for keyword in important_keywords):
                log_output.append(message)
                # 실시간 피드백을 위해 즉시 UI 업데이트 강제
                from PySide6.QtCore import QCoreApplication

                QCoreApplication.processEvents()
        else:
            # 전체 모드에서는 모든 로그 표시
            log_output.append(message)
            # 전체 모드에서도 즉시 UI 업데이트 강제
            from PySide6.QtCore import QCoreApplication

            QCoreApplication.processEvents()

    def set_busy(self, busy: bool, *, message: str | None = None) -> None:
        self._is_busy = busy
        if busy:
            self.show_review_stage()
            # 실행 시작: 로그를 비우고 요약 모드 사용
            self._full_execution_logs = []
            self._log_mode = "summary"
            if self._log_output:
                self._log_output.clear()
            if self._view_logs_button:
                self._view_logs_button.setEnabled(False)
            # 브라우저 뷰를 초기화하고 실시간 미리보기 준비
            self._browser_view.setHtml("""
                <html>
                <body style="margin:0; padding:0; background:#1a1a1a; display:flex; align-items:center; justify-content:center; color:#666;">
                    <div style="text-align:center;">
                        <h2>자동화 시작 중...</h2>
                        <p>실시간 브라우저 화면이 곧 표시됩니다</p>
                    </div>
                </body>
                </html>
            """)
        else:
            # 실행 완료: 상세 로그 보기 활성화
            if self._view_logs_button:
                self._view_logs_button.setEnabled(True)

        self._start_button.setEnabled(not busy)
        self._cancel_button.setEnabled(busy)
        self._back_to_setup_button.setEnabled(
            self._workflow_stage == "review" and not busy
        )
        self._drop_area.setEnabled(not busy)
        self._url_input.setEnabled(not busy)
        if hasattr(self, "_chat_input"):
            self._chat_input.setEnabled(True)
        if hasattr(self, "_chat_send_button"):
            self._chat_send_button.setEnabled(True)
        if hasattr(self, "_load_plan_button"):
            self._load_plan_button.setEnabled(not busy)
        if busy:
            self._drop_area.setText("자동화를 진행 중이에요… 잠시만 기다려 주세요 ☄️")
            # 로딩 오버레이는 표시하지 않음 - 실시간 미리보기가 제공됨
            # self.show_loading_overlay(message or "시나리오를 실행 중입니다…")
        else:
            self._drop_area.setText("기획서 파일 또는 PRD 번들을 드래그하거나 선택해 주세요")
            # self.hide_loading_overlay()

    def load_url(self, url: str) -> None:
        self._browser_view.setUrl(QUrl(url))

    def set_url_field(self, url: str) -> None:
        self._url_input.setText(url)

    def set_feature_query(self, query: str) -> None:
        if not hasattr(self, "_feature_input"):
            return
        self._feature_input.setText(query)
        self._current_feature_query = query.strip()
        self._feature_input_container.setVisible(self._selected_run_mode == "quick")

    def show_html_in_browser(self, html_content: str) -> None:
        """브라우저 뷰에 HTML 콘텐츠를 표시합니다"""
        self._browser_view.setHtml(html_content)

    def _show_replay_html(self, html_content: str) -> None:
        if not html_content:
            self._browser_view.setHtml("""
                <html>
                <body style="margin:0; padding:0; background:#0f172a; display:flex; align-items:center; justify-content:center; color:#94a3b8;">
                    <div>재생할 이미지가 없습니다</div>
                </body>
                </html>
            """)
            return
        self._browser_view.setHtml(html_content)

    def update_live_preview(
        self, screenshot_base64: str, click_position: dict = None
    ) -> None:
        """Playwright 실시간 스크린샷을 브라우저 뷰에 업데이트합니다"""
        import base64
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QPixmap

        try:
            # base64를 바이트로 디코딩
            image_data = base64.b64decode(screenshot_base64)

            # QPixmap으로 변환
            pixmap = QPixmap()
            pixmap.loadFromData(QByteArray(image_data))

            # 좌표가 주어지면 클릭 애니메이션과 마우스 커서를 오버레이
            click_overlay = ""
            if click_position and "x" in click_position and "y" in click_position:
                x = click_position["x"]
                y = click_position["y"]
                click_overlay = f"""
                <!-- Mouse cursor (always visible) -->
                <div class="mouse-cursor" style="
                    position: absolute;
                    left: {x}px;
                    top: {y}px;
                    width: 24px;
                    height: 24px;
                    margin-left: -2px;
                    margin-top: -2px;
                    pointer-events: none;
                    z-index: 9999;
                    filter: drop-shadow(0 2px 4px rgba(0,0,0,0.8));
                ">
                    <!-- SVG cursor icon -->
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M3 3L10.07 19.97L12.58 12.58L19.97 10.07L3 3Z" fill="white" stroke="black" stroke-width="1.5"/>
                    </svg>
                </div>

                <!-- Click animation (ripple effect) -->
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
                """

            # HTML img 태그로 표시하고 애니메이션 오버레이 적용
            html = f"""
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
            """
            self._browser_view.setHtml(html)
            self._record_result_screenshot(screenshot_base64)
        except Exception as e:
            print(f"Failed to update live preview: {e}")

    def _clear_layout(self, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                self._clear_layout(child_layout)

    def _record_result_screenshot(self, screenshot_base64: str) -> None:
        shot = str(screenshot_base64 or "").strip()
        if not shot:
            return
        if self._result_screenshot_history and self._result_screenshot_history[-1] == shot:
            return
        self._result_screenshot_history.append(shot)
        if len(self._result_screenshot_history) > 4:
            self._result_screenshot_history = self._result_screenshot_history[-4:]
        self._render_result_screenshot_strip()

    def _render_result_screenshot_strip(self) -> None:
        if not hasattr(self, "_result_screenshot_layout"):
            return
        import base64
        from PySide6.QtCore import QByteArray

        self._clear_layout(self._result_screenshot_layout)
        shots = list(self._result_screenshot_history[-4:])
        if not shots:
            empty = QLabel("실행 중 캡처가 수집되면 여기에 표시됩니다.", self._result_screenshot_container)
            empty.setObjectName("ResultSummaryHint")
            empty.setWordWrap(True)
            self._result_screenshot_layout.addWidget(empty)
            self._result_screenshot_layout.addStretch()
            return

        for idx, shot in enumerate(shots, start=1):
            pixmap = QPixmap()
            try:
                pixmap.loadFromData(QByteArray(base64.b64decode(shot)))
            except Exception:
                pixmap = QPixmap()
            frame = QFrame(self._result_screenshot_container)
            frame.setObjectName("ResultScreenshotCard")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(6, 6, 6, 6)
            frame_layout.setSpacing(4)

            image_label = QLabel(frame)
            image_label.setObjectName("ResultScreenshotThumb")
            image_label.setFixedSize(180, 110)
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if not pixmap.isNull():
                image_label.setPixmap(
                    pixmap.scaled(
                        172,
                        102,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            else:
                image_label.setText("이미지 로드 실패")
            frame_layout.addWidget(image_label)

            caption = QLabel(f"캡처 {idx}", frame)
            caption.setObjectName("ResultSummaryHint")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            frame_layout.addWidget(caption)
            self._result_screenshot_layout.addWidget(frame)

        self._result_screenshot_layout.addStretch()

    def show_loading_overlay(self, message: str) -> None:
        if self._busy_overlay:
            self._busy_overlay.setGeometry(self.centralWidget().rect())
            self._busy_overlay.raise_()
            self._busy_overlay.show_with_message(message)

    def hide_loading_overlay(self) -> None:
        if self._busy_overlay:
            self._busy_overlay.hide_overlay()

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _show_detailed_logs(self) -> None:
        """대화 상자로 상세 실행 로그를 표시합니다."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton

        dialog = QDialog(self)
        dialog.setWindowTitle("상세 실행 로그")
        dialog.resize(800, 600)

        layout = QVBoxLayout(dialog)

        # 로그 텍스트 영역
        log_view = QTextEdit(dialog)
        log_view.setReadOnly(True)
        log_view.setPlainText("\n".join(self._full_execution_logs))
        layout.addWidget(log_view)

        # 닫기 버튼
        close_button = QPushButton("닫기", dialog)
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        dialog.exec()

    def _open_file_dialog(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "기획서 파일 선택",
            "",
            "Supported Files (*.pdf *.docx *.md *.txt *.json);;Documents (*.pdf *.docx *.md *.txt);;JSON Files (*.json);;All Files (*)",
        )
        if file_path:
            # feature_input에 값이 있으면 특정 기능 테스트 모드
            feature_query = (
                self._feature_input.text().strip()
                if hasattr(self, "_feature_input")
                else ""
            )
            self._current_feature_query = feature_query
            self.fileDropped.emit(file_path)

    def _handle_file_drop(self, file_path: str) -> None:
        """파일이 드롭되었을 때 feature_query를 저장하고 시그널을 발생시킵니다."""
        feature_query = (
            self._feature_input.text().strip()
            if hasattr(self, "_feature_input")
            else ""
        )
        self._current_feature_query = feature_query
        self.fileDropped.emit(file_path)

    def _toggle_feature_input(self) -> None:
        """특정 기능 테스트 입력창을 토글합니다."""
        if self._feature_input_container.isVisible():
            self._feature_input_container.hide()
        else:
            self._feature_input_container.show()

    def get_feature_query(self) -> str:
        """현재 설정된 feature query를 반환합니다."""
        if hasattr(self, "_feature_input"):
            self._current_feature_query = self._feature_input.text().strip()
        return self._current_feature_query

    def _sync_feature_query(self, text: str) -> None:
        self._current_feature_query = str(text or "").strip()

    def set_selected_run_mode(self, mode: str) -> None:
        normalized = mode if mode in {"quick", "ai", "bundle"} else "quick"
        self._selected_run_mode = normalized
        mapping = {
            "quick": getattr(self, "_quick_mode_button", None),
            "ai": getattr(self, "_ai_mode_button", None),
            "bundle": getattr(self, "_bundle_mode_button", None),
        }
        for key, button in mapping.items():
            if button is None:
                continue
            selected = key == normalized
            button.setChecked(selected)
            button.setProperty("modeSelected", selected)
            button.style().unpolish(button)
            button.style().polish(button)
        if hasattr(self, "_feature_input_container"):
            self._feature_input_container.setVisible(normalized == "quick")

    def get_selected_run_mode(self) -> str:
        return self._selected_run_mode

    def set_control_channel(self, channel: str) -> None:
        self._control_channel = "telegram" if str(channel or "").strip().lower() == "telegram" else "local"
        if hasattr(self, "_control_status_label"):
            if self._control_channel == "telegram":
                self._control_status_label.setText("제어 채널: 텔레그램 선택됨. 실행 모드는 이 창에서 고릅니다.")
            else:
                self._control_status_label.setText("제어 채널: 로컬")

    def set_bridge_status(self, text: str) -> None:
        if hasattr(self, "_control_status_label"):
            self._control_status_label.setText(str(text or "").strip() or "제어 채널 상태 없음")

    def reset_result_summary(self) -> None:
        self._result_screenshot_history = []
        if hasattr(self, "_result_summary_status"):
            self._result_summary_status.setText("실행 결과 대기 중")
        if hasattr(self, "_result_summary_meta"):
            self._result_summary_meta.setText("모드와 검증 요약이 여기에 표시됩니다.")
        if hasattr(self, "_result_summary_reason"):
            self._result_summary_reason.setText("실행이 시작되면 판정 사유가 표시됩니다.")
        if hasattr(self, "_result_live_goal"):
            self._result_live_goal.setText("현재 목표: -")
        if hasattr(self, "_result_live_step"):
            self._result_live_step.setText("현재 단계: -")
        if hasattr(self, "_result_live_blocked"):
            self._result_live_blocked.setText("차단 사유: 없음")
        if hasattr(self, "_result_timeline_view"):
            self._result_timeline_view.clear()
        self._render_result_screenshot_strip()

    def set_execution_status(
        self,
        *,
        goal: str | None = None,
        step: str | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        if hasattr(self, "_result_live_goal") and goal is not None:
            self._result_live_goal.setText(f"현재 목표: {str(goal or '').strip() or '-'}")
        if hasattr(self, "_result_live_step") and step is not None:
            self._result_live_step.setText(f"현재 단계: {str(step or '').strip() or '-'}")
        if hasattr(self, "_result_live_blocked") and blocked_reason is not None:
            self._result_live_blocked.setText(f"차단 사유: {str(blocked_reason or '').strip() or '없음'}")

    def show_result_summary(self, summary: dict[str, Any]) -> None:
        mode = str(summary.get("mode") or "unknown")
        status = str(summary.get("status") or "unknown").upper()
        reason = str(summary.get("reason") or "-").strip()
        if mode == "goal":
            meta = (
                f"Goal-Driven · 성공 {int(summary.get('successful_goals') or 0)}개"
                f" / 실패 {int(summary.get('failed_goals') or 0)}개"
                f" / 전체 {int(summary.get('total_goals') or 0)}개"
            )
        elif mode == "exploratory":
            meta = (
                f"Exploratory · 액션 {int(summary.get('total_actions') or 0)}회"
                f" / 페이지 {int(summary.get('pages') or 0)}개"
                f" / 이슈 {int(summary.get('issues') or 0)}개"
            )
        else:
            meta = "실행 요약 정보가 아직 없습니다."
        if hasattr(self, "_result_summary_status"):
            self._result_summary_status.setText(f"실행 결과 {status}")
        if hasattr(self, "_result_summary_meta"):
            self._result_summary_meta.setText(meta)
        if hasattr(self, "_result_summary_reason"):
            self._result_summary_reason.setText(reason or "-")
        self.set_execution_status(
            goal=str(summary.get("current_goal") or summary.get("goal_name") or "-"),
            step=str(summary.get("current_step") or "-"),
            blocked_reason=str(summary.get("blocked_reason") or "없음"),
        )
        if hasattr(self, "_result_timeline_view"):
            lines: list[str] = []
            step_timeline = summary.get("step_timeline")
            if isinstance(step_timeline, list):
                for row in step_timeline[:12]:
                    if not isinstance(row, dict):
                        continue
                    step_no = row.get("step")
                    action = str(row.get("action") or "-").strip() or "-"
                    try:
                        sec_text = f"{float(row.get('duration_seconds') or 0.0):.2f}초"
                    except Exception:
                        sec_text = "-"
                    lines.append(f"[{step_no}] {action} · {sec_text}")
                    reasoning = str(row.get("reasoning") or "").strip()
                    error = str(row.get("error") or "").strip()
                    if reasoning:
                        lines.append(f"  - {reasoning}")
                    if error:
                        lines.append(f"  - 오류: {error}")
            proof_lines = summary.get("proof_lines")
            if isinstance(proof_lines, list) and proof_lines:
                if lines:
                    lines.append("")
                lines.append("근거")
                for proof in proof_lines[:6]:
                    proof_text = str(proof or "").strip()
                    if proof_text:
                        lines.append(f"- {proof_text}")
            validation_summary = summary.get("validation_summary")
            if isinstance(validation_summary, dict) and validation_summary:
                if lines:
                    lines.append("")
                lines.append("검증 요약")
                lines.append(
                    f"- 총 {validation_summary.get('total_checks', 0)}건 / "
                    f"성공 {validation_summary.get('passed_checks', 0)}건 / "
                    f"실패 {validation_summary.get('failed_checks', 0)}건 / "
                    f"성공률 {validation_summary.get('success_rate', 0)}%"
                )
            self._result_timeline_view.setPlainText("\n".join(lines).strip())

    def append_chat_message(self, role: str, text: str) -> None:
        if not hasattr(self, "_chat_transcript"):
            return
        safe_role = str(role or "").strip() or "GAIA"
        safe_text = str(text or "").strip()
        if not safe_text:
            return
        self._chat_transcript.append(f"[{safe_role}] {safe_text}")

    def _emit_chat_message(self) -> None:
        if not hasattr(self, "_chat_input"):
            return
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.chatMessageSubmitted.emit(text)

    def _open_plan_dialog(self) -> None:
        # 수동 생성 플랜은 mock_data를 먼저 확인하고, 없으면 plans 디렉터리를 확인
        if self._last_plan_directory.exists():
            initial_dir = str(self._last_plan_directory)
        else:
            mock_data_dir = Path.cwd() / "artifacts" / "mock_data"
            plans_dir = Path.cwd() / "artifacts" / "plans"
            # mock_data가 있으면 우선 사용하고 없으면 plans를 시도
            if mock_data_dir.exists():
                initial_dir = str(mock_data_dir)
            elif plans_dir.exists():
                initial_dir = str(plans_dir)
            else:
                initial_dir = ""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "PRD 번들 또는 이전 테스트 플랜 불러오기",
            initial_dir,
            "JSON Files (*.json);;All Files (*)",
        )
        if file_path:
            self._last_plan_directory = Path(file_path).parent
            self.planFileSelected.emit(file_path)

    def ask_for_bug_json(self) -> None:
        """플랜 불러오기 후 bug.json 선택 여부를 묻습니다."""
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Bug JSON 파일 선택",
            "ER (Error Rate) 측정을 위한 bug.json 파일을 선택하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            # bug.json 선택 다이얼로그 열기
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Bug JSON 파일 선택",
                str(Path.cwd()),
                "JSON Files (*.json);;All Files (*)",
            )
            if file_path:
                self.bugJsonSelected.emit(file_path)
            else:
                # 선택 안 함
                self.bugJsonSelected.emit("")
        else:
            # 선택 안 함
            self.bugJsonSelected.emit("")

    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: D401
        super().resizeEvent(event)
        if self._busy_overlay and self.centralWidget():
            self._busy_overlay.setGeometry(self.centralWidget().rect())

    def _emit_url_submitted(self) -> None:
        url = self._url_input.text().strip()
        if url:
            self.urlSubmitted.emit(url)

    def _setup_screencast(self) -> None:
        """CDP 스크린캐스트 WebSocket 클라이언트를 설정하고 연결합니다"""
        self._screencast_client = ScreencastClient()
        self._screencast_client.frame_received.connect(self._update_screencast_frame)
        self._screencast_client.connection_status_changed.connect(
            self._on_screencast_connection_changed
        )
        self._screencast_client.error_occurred.connect(self._on_screencast_error)
        # 자동 연결 시작
        self._screencast_client.start()
        print("[GUI] Screencast client started")

    def _update_screencast_frame(self, frame_base64: str) -> None:
        """
        CDP 스크린캐스트에서 수신한 프레임을 브라우저 뷰에 표시합니다
        기존 update_live_preview 메서드의 간소화 버전 (클릭 애니메이션 제거)
        """
        html = f"""
        <html>
        <head>
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    background: #1a1a1a;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    overflow: hidden;
                }}
                img {{
                    max-width: 100%;
                    max-height: 100vh;
                    object-fit: contain;
                    box-shadow: 0 0 20px rgba(59, 130, 246, 0.3);
                    border: 1px solid rgba(59, 130, 246, 0.2);
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <img src="data:image/jpeg;base64,{frame_base64}" alt="Browser screencast">
        </body>
        </html>
        """
        self._browser_view.setHtml(html)

    def _on_screencast_connection_changed(self, connected: bool) -> None:
        """스크린캐스트 연결 상태 변경 핸들러"""
        if connected:
            print("[GUI] Screencast connected")
        else:
            print("[GUI] Screencast disconnected")
            # 연결 끊김 시 안내 메시지 표시
            if not self._is_busy:  # busy가 아닐 때만 메시지 표시
                self._browser_view.setHtml("""
                    <html>
                    <body style="margin:0; padding:0; background:#1a1a1a; display:flex; align-items:center; justify-content:center; color:#666;">
                        <div style="text-align:center;">
                            <h2>브라우저 세션 없음</h2>
                            <p>테스트를 시작하면 실시간 화면이 표시됩니다</p>
                        </div>
                    </body>
                    </html>
                """)

    def _on_screencast_error(self, error_message: str) -> None:
        """스크린캐스트 에러 핸들러"""
        print(f"[GUI] Screencast error: {error_message}")

    def closeEvent(self, event) -> None:
        """창 닫기 이벤트 - 스크린캐스트 클라이언트 정리"""
        if self._screencast_client:
            self._screencast_client.stop()
            print("[GUI] Screencast client stopped")
        super().closeEvent(event)
