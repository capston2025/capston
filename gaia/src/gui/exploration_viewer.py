"""탐색 결과 뷰어 위젯 - 기능 중심 테스트 결과 표시"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QMovie, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QComboBox,
    QSplitter,
    QTextEdit,
    QFileDialog,
    QSizePolicy,
    QGroupBox,
    QGridLayout,
    QDialog,
    QDialogButtonBox,
    QApplication,
    QAbstractItemView,
)


class GifViewerDialog(QDialog):
    """GIF 전체화면 뷰어 다이얼로그"""

    def __init__(self, gif_path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("테스트 녹화 보기")
        self.setModal(True)
        self.resize(900, 700)

        self.setStyleSheet("""
            QDialog {
                background: #0f0f1a;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        # GIF 표시
        self._gif_label = QLabel(self)
        self._gif_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._movie = QMovie(gif_path)
        self._movie.setScaledSize(QSize(860, 600))
        self._gif_label.setMovie(self._movie)
        self._movie.start()
        layout.addWidget(self._gif_label)

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        open_file_btn = QPushButton("파일 위치 열기", self)
        open_file_btn.setStyleSheet("""
            QPushButton {
                background: rgba(99, 102, 241, 0.2);
                color: #a5b4fc;
                border: 1px solid rgba(99, 102, 241, 0.4);
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(99, 102, 241, 0.3);
            }
        """)
        open_file_btn.clicked.connect(lambda: self._open_file_location(gif_path))
        btn_layout.addWidget(open_file_btn)

        close_btn = QPushButton("닫기", self)
        close_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.1);
                color: #9ca3af;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.15);
            }
        """)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _open_file_location(self, path: str):
        """파일 위치 열기"""
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.run(["explorer", "/select,", path])
        else:
            subprocess.run(["xdg-open", os.path.dirname(path)])

    def closeEvent(self, event):
        if self._movie:
            self._movie.stop()
        super().closeEvent(event)


class StepDetailDialog(QDialog):
    """스텝 상세 정보 다이얼로그"""

    def __init__(
        self,
        step_data: Dict,
        step_index: int,
        screenshots_dir: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"스텝 #{step_index + 1} 상세 정보")
        self.setModal(True)
        self.resize(800, 600)

        self.setStyleSheet("""
            QDialog {
                background: #f8fafc;
            }
            QLabel {
                color: #1f2937;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 헤더
        header = QHBoxLayout()

        result = step_data.get("success", False)
        result_badge = QLabel("✅ PASS" if result else "❌ FAIL", self)
        result_badge.setStyleSheet(f"""
            QLabel {{
                background: {"#dcfce7" if result else "#fee2e2"};
                color: {"#166534" if result else "#991b1b"};
                padding: 6px 12px;
                border-radius: 16px;
                font-weight: 600;
                font-size: 13px;
            }}
        """)
        header.addWidget(result_badge)
        header.addStretch()

        step_label = QLabel(f"Step {step_index + 1}", self)
        step_label.setStyleSheet("font-size: 14px; color: #6b7280;")
        header.addWidget(step_label)

        layout.addLayout(header)

        # 메인 콘텐츠 (스플리터)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # 왼쪽: 스크린샷
        screenshot_frame = QFrame(splitter)
        screenshot_frame.setStyleSheet("""
            QFrame {
                background: #1a1a2e;
                border-radius: 12px;
            }
        """)
        screenshot_layout = QVBoxLayout(screenshot_frame)
        screenshot_layout.setContentsMargins(12, 12, 12, 12)

        screenshot_title = QLabel("📸 스크린샷", screenshot_frame)
        screenshot_title.setStyleSheet(
            "color: #9ca3af; font-size: 12px; font-weight: 600;"
        )
        screenshot_layout.addWidget(screenshot_title)

        self._screenshot_label = QLabel(screenshot_frame)
        self._screenshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._screenshot_label.setMinimumSize(350, 250)
        self._screenshot_label.setStyleSheet("color: #6b7280;")

        # 스크린샷 로드
        screenshot_loaded = False
        if screenshots_dir:
            screenshot_path = os.path.join(
                screenshots_dir, f"step_{step_index:03d}.png"
            )
            if os.path.exists(screenshot_path):
                pixmap = QPixmap(screenshot_path)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        350,
                        250,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._screenshot_label.setPixmap(scaled)
                    screenshot_loaded = True

        # base64 스크린샷 시도
        if not screenshot_loaded:
            screenshot_b64 = step_data.get("screenshot_before") or step_data.get(
                "screenshot_after"
            )
            if screenshot_b64:
                import base64

                try:
                    img_data = base64.b64decode(screenshot_b64)
                    pixmap = QPixmap()
                    pixmap.loadFromData(img_data)
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            350,
                            250,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        self._screenshot_label.setPixmap(scaled)
                        screenshot_loaded = True
                except:
                    pass

        if not screenshot_loaded:
            self._screenshot_label.setText("스크린샷 없음")

        screenshot_layout.addWidget(self._screenshot_label)
        screenshot_layout.addStretch()

        splitter.addWidget(screenshot_frame)

        # 오른쪽: 상세 정보
        info_frame = QFrame(splitter)
        info_frame.setStyleSheet("""
            QFrame {
                background: white;
                border-radius: 12px;
                border: 1px solid #e5e7eb;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(12)

        # 기능 설명
        feature_desc = step_data.get("feature_description", "")
        if feature_desc:
            self._add_info_row(info_layout, "🎯 테스트 기능", feature_desc)

        # 시나리오
        scenario = step_data.get("test_scenario", "")
        if scenario:
            self._add_info_row(info_layout, "📋 시나리오", scenario)

        # 비즈니스 영향
        impact = step_data.get("business_impact", "")
        if impact:
            self._add_info_row(info_layout, "💼 비즈니스 영향", impact)

        # 액션 정보
        decision = step_data.get("decision", {})
        action = decision.get("selected_action", {})
        if action:
            action_type = action.get("action_type", "N/A")
            action_desc = action.get("description", "N/A")
            self._add_info_row(
                info_layout, "🖱️ 수행 액션", f"{action_type}: {action_desc}"
            )

            reasoning = action.get("reasoning", "")
            if reasoning:
                self._add_info_row(info_layout, "💭 액션 이유", reasoning)

        # 예상 결과
        expected = decision.get("expected_outcome", "")
        if expected:
            self._add_info_row(info_layout, "📝 예상 결과", expected)

        # 에러 메시지
        error_msg = step_data.get("error_message", "")
        if error_msg:
            self._add_info_row(info_layout, "⚠️ 에러", error_msg, is_error=True)

        # URL
        url = step_data.get("url", "")
        if url:
            self._add_info_row(info_layout, "🔗 URL", url)

        info_layout.addStretch()

        splitter.addWidget(info_frame)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

        # 닫기 버튼
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        btn_box.rejected.connect(self.close)
        layout.addWidget(btn_box)

    def _add_info_row(
        self, layout: QVBoxLayout, label: str, value: str, is_error: bool = False
    ):
        """정보 행 추가"""
        row = QVBoxLayout()
        row.setSpacing(4)

        label_widget = QLabel(label, self)
        label_widget.setStyleSheet("font-size: 11px; color: #6b7280; font-weight: 600;")
        row.addWidget(label_widget)

        value_widget = QLabel(value, self)
        value_widget.setWordWrap(True)
        if is_error:
            value_widget.setStyleSheet(
                "font-size: 13px; color: #dc2626; background: #fef2f2; padding: 8px; border-radius: 6px;"
            )
        else:
            value_widget.setStyleSheet("font-size: 13px; color: #1f2937;")
        row.addWidget(value_widget)

        layout.addLayout(row)


class SummaryDialog(QDialog):
    """요약 정보를 별도 창으로 표시합니다."""

    def __init__(self, summary: Dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("요약")
        self.setModal(True)
        self.resize(520, 380)

        self.setStyleSheet("""
            QDialog {
                background: #f8fafc;
            }
            QLabel {
                color: #1f2937;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("요약", self)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        rows = [
            ("총 스텝", summary.get("total", 0)),
            ("성공", summary.get("success", 0)),
            ("실패", summary.get("fail", 0)),
            ("이슈", summary.get("issues", 0)),
            ("커버리지", summary.get("coverage", "0%")),
            ("소요 시간", summary.get("duration", "0s")),
        ]

        for row_index, (label, value) in enumerate(rows):
            label_widget = QLabel(label, self)
            label_widget.setStyleSheet("font-size: 12px; color: #6b7280;")
            value_widget = QLabel(str(value), self)
            value_widget.setStyleSheet(
                "font-size: 18px; font-weight: 700; color: #4f46e5;"
            )
            grid.addWidget(label_widget, row_index, 0)
            grid.addWidget(value_widget, row_index, 1)

        layout.addLayout(grid)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("닫기", self)
        close_btn.setObjectName("GhostButton")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)


class StepReplayWidget(QFrame):
    """스텝 단위 재생 위젯 (before/after 이미지 토글)"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._frames: List[QPixmap] = []
        self._frame_index = 0
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._advance_frame)
        self._play_remaining = 0

        self.setStyleSheet("""
            QFrame {
                background: #1a1a2e;
                border-radius: 12px;
                border: 1px solid rgba(100, 110, 200, 0.3);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._preview_label = QLabel("선택한 테스트를 재생할 수 있습니다", self)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumSize(320, 210)
        self._preview_label.setStyleSheet("color: #9ca3af;")
        layout.addWidget(self._preview_label)

        controls = QHBoxLayout()
        controls.addStretch()

        self._play_button = QPushButton("재생", self)
        self._play_button.setObjectName("GhostButton")
        self._play_button.clicked.connect(self.play)
        controls.addWidget(self._play_button)

        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("color: #9ca3af; font-size: 11px;")
        controls.addWidget(self._status_label)
        controls.addStretch()

        layout.addLayout(controls)

    def load_step(self, step_data: Dict, screenshots_dir: str | None):
        self._frames = []
        self._frame_index = 0
        self._status_label.setText("")

        before_pixmap = self._load_step_pixmap(step_data, screenshots_dir, "before")
        after_pixmap = self._load_step_pixmap(step_data, screenshots_dir, "after")

        for pixmap in [before_pixmap, after_pixmap]:
            if pixmap is not None and not pixmap.isNull():
                self._frames.append(pixmap)

        if not self._frames:
            self._preview_label.setText("스크린샷이 없습니다")
            self._play_button.setEnabled(False)
            return

        self._play_button.setEnabled(len(self._frames) > 1)
        self._render_frame(self._frames[0])
        if len(self._frames) > 1:
            self._status_label.setText("before/after 재생 가능")

    def play(self):
        if len(self._frames) < 2:
            return
        if self._play_timer.isActive():
            self._play_timer.stop()
            self._status_label.setText("재생 중지")
            return
        self._play_remaining = 8
        self._status_label.setText("재생 중...")
        self._play_timer.start(550)

    def _advance_frame(self):
        if self._play_remaining <= 0:
            self._play_timer.stop()
            self._status_label.setText("재생 완료")
            return
        self._play_remaining -= 1
        if not self._frames:
            self._play_timer.stop()
            return
        self._frame_index = (self._frame_index + 1) % len(self._frames)
        self._render_frame(self._frames[self._frame_index])

    def _render_frame(self, pixmap: QPixmap):
        scaled = pixmap.scaled(
            380,
            240,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._preview_label.setPixmap(scaled)

    def _load_step_pixmap(
        self,
        step_data: Dict,
        screenshots_dir: str | None,
        which: str,
    ) -> QPixmap | None:
        if screenshots_dir:
            step_index = step_data.get("step_number", 0)
            if step_index:
                screenshot_path = os.path.join(
                    screenshots_dir, f"step_{step_index:03d}.png"
                )
                if os.path.exists(screenshot_path):
                    pixmap = QPixmap(screenshot_path)
                    if not pixmap.isNull():
                        return pixmap

        screenshot_key = (
            "screenshot_before" if which == "before" else "screenshot_after"
        )
        screenshot_b64 = step_data.get(screenshot_key)
        if not screenshot_b64:
            return None
        try:
            import base64

            img_data = base64.b64decode(screenshot_b64)
            pixmap = QPixmap()
            pixmap.loadFromData(img_data)
            return pixmap if not pixmap.isNull() else None
        except Exception:
            return None


class ScenarioSummaryCard(QFrame):
    """테스트 시나리오 요약 카드"""

    def __init__(self, scenario: Dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ScenarioCard")

        result = scenario.get("result", "pass")
        border_color = (
            "rgba(5, 150, 105, 0.5)" if result == "pass" else "rgba(220, 38, 38, 0.5)"
        )
        bg_color = (
            "rgba(209, 250, 229, 0.3)"
            if result == "pass"
            else "rgba(254, 226, 226, 0.3)"
        )

        self.setStyleSheet(f"""
            QFrame#ScenarioCard {{
                background: {bg_color};
                border-radius: 12px;
                border: 1px solid {border_color};
                padding: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # 시나리오 이름
        name = scenario.get("name", "알 수 없음")
        name_label = QLabel(name, self)
        name_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #1f2937;")
        layout.addWidget(name_label)

        # 기능 설명
        feature = scenario.get("feature", "")
        if feature:
            feature_label = QLabel(feature, self)
            feature_label.setStyleSheet("font-size: 11px; color: #4b5563;")
            layout.addWidget(feature_label)

        # 스텝 수 및 결과
        stats = QHBoxLayout()
        steps_count = len(scenario.get("steps", []))
        passed = scenario.get("passed", 0)
        failed = scenario.get("failed", 0)

        result_icon = "✅" if result == "pass" else "❌"
        stats_label = QLabel(
            f"{result_icon} {steps_count} steps ({passed} passed, {failed} failed)",
            self,
        )
        stats_label.setStyleSheet("font-size: 10px; color: #6b7280;")
        stats.addWidget(stats_label)
        stats.addStretch()
        layout.addLayout(stats)


class ExplorationResultCard(QFrame):
    """개별 탐색 결과 카드"""

    clicked = Signal(str)

    def __init__(self, file_path: str, summary: Dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.file_path = file_path
        self.setObjectName("ExplorationCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QFrame#ExplorationCard {
                background: rgba(255, 255, 255, 0.7);
                border-radius: 16px;
                border: 1px solid rgba(200, 210, 255, 0.5);
                padding: 12px;
            }
            QFrame#ExplorationCard:hover {
                background: rgba(255, 255, 255, 0.9);
                border: 1px solid rgba(125, 135, 255, 0.6);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # 날짜/시간
        timestamp = summary.get("timestamp", "")
        date_label = QLabel(timestamp, self)
        date_label.setStyleSheet("font-size: 11px; color: #6b7280;")
        layout.addWidget(date_label)

        # URL
        url = summary.get("start_url", "Unknown URL")
        url_label = QLabel(url[:50] + "..." if len(url) > 50 else url, self)
        url_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #1f2937;")
        layout.addWidget(url_label)

        # 통계 행
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)

        steps = summary.get("total_steps", 0)
        success = summary.get("success_count", 0)
        issues = summary.get("issues_count", 0)
        has_gif = summary.get("has_gif", False)

        if has_gif:
            gif_label = QLabel("🎬", self)
            gif_label.setStyleSheet("font-size: 14px;")
            stats_row.addWidget(gif_label)

        steps_label = QLabel(f"🔄 {steps} steps", self)
        steps_label.setStyleSheet("font-size: 12px; color: #4b5563;")
        stats_row.addWidget(steps_label)

        success_label = QLabel(f"✅ {success} passed", self)
        success_label.setStyleSheet("font-size: 12px; color: #059669;")
        stats_row.addWidget(success_label)

        if issues > 0:
            issues_label = QLabel(f"🐛 {issues} issues", self)
            issues_label.setStyleSheet("font-size: 12px; color: #dc2626;")
            stats_row.addWidget(issues_label)

        stats_row.addStretch()
        layout.addLayout(stats_row)

    def mousePressEvent(self, event):
        self.clicked.emit(self.file_path)
        super().mousePressEvent(event)


class ExplorationDetailView(QWidget):
    """탐색 결과 상세 뷰 - 기능 중심"""

    replay_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._steps = []
        self._screenshots_dir = None
        self._summary_data: Dict[str, object] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._top_container = QFrame(self)
        top_layout = QVBoxLayout(self._top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)

        replay_container = QFrame(self._top_container)
        replay_layout = QVBoxLayout(replay_container)
        replay_layout.setContentsMargins(0, 0, 0, 0)

        replay_title = QLabel("🎞️ 스텝 재생", self)
        replay_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #4b5563;")
        replay_layout.addWidget(replay_title)

        self._step_replay = StepReplayWidget(replay_container)
        replay_layout.addWidget(self._step_replay)

        top_layout.addWidget(replay_container)

        # 헤더
        header = QHBoxLayout()
        self._title_label = QLabel("테스트 상세 결과", self)
        self._title_label.setStyleSheet(
            "font-size: 14px; font-weight: 600; color: #1f2937;"
        )
        header.addWidget(self._title_label)
        header.addStretch()

        self._toggle_top_button = QPushButton("상단 숨기기", self)
        self._toggle_top_button.setObjectName("GhostButton")
        self._toggle_top_button.clicked.connect(self._toggle_top_section)
        header.addWidget(self._toggle_top_button)

        self._summary_button = QPushButton("요약", self)
        self._summary_button.setObjectName("GhostButton")
        self._summary_button.clicked.connect(self._show_summary)
        header.addWidget(self._summary_button)

        self._export_button = QPushButton("CSV 내보내기", self)
        self._export_button.setObjectName("GhostButton")
        self._export_button.clicked.connect(self._export_csv)
        header.addWidget(self._export_button)

        layout.addLayout(header)

        # 요약 카드
        summary_card = QFrame(self)
        summary_card.setObjectName("SummaryCard")
        summary_card.setStyleSheet("""
            QFrame#SummaryCard {
                background: rgba(99, 102, 241, 0.1);
                border-radius: 12px;
                padding: 12px;
            }
        """)
        summary_layout = QHBoxLayout(summary_card)
        summary_layout.setSpacing(24)

        self._summary_labels = {}
        for key, label_text in [
            ("total", "총 스텝"),
            ("success", "성공"),
            ("fail", "실패"),
            ("issues", "이슈"),
            ("coverage", "커버리지"),
            ("duration", "소요 시간"),
        ]:
            item_layout = QVBoxLayout()
            value_label = QLabel("0", self)
            value_label.setStyleSheet(
                "font-size: 20px; font-weight: 700; color: #4f46e5;"
            )
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item_layout.addWidget(value_label)

            name_label = QLabel(label_text, self)
            name_label.setStyleSheet("font-size: 11px; color: #6b7280;")
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item_layout.addWidget(name_label)

            summary_layout.addLayout(item_layout)
            self._summary_labels[key] = value_label

        self._summary_card = summary_card
        self._summary_card.hide()

        # 테이블 - 기능 중심 컬럼
        self._table = QTableWidget(self)
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            [
                "테스트 기능",
                "시나리오",
                "수행 액션",
                "비즈니스 영향",
                "상세",
                "결과",
                "재생",
            ]
        )
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.setStretchLastSection(True)
        header_view.setSectionsMovable(True)
        header_view.setMinimumSectionSize(100)
        self._table.setWordWrap(True)
        self._table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setDefaultSectionSize(64)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet("""
            QTableWidget {
                background: rgba(255, 255, 255, 0.8);
                border-radius: 12px;
                border: 1px solid rgba(200, 210, 255, 0.4);
                gridline-color: rgba(200, 210, 255, 0.3);
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background: rgba(99, 102, 241, 0.15);
                padding: 10px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid rgba(200, 210, 255, 0.4);
            }
        """)
        self._table.cellDoubleClicked.connect(self._open_step_detail)
        self._table.currentCellChanged.connect(self._preview_step)

        self._content_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._content_splitter.addWidget(self._top_container)
        self._content_splitter.addWidget(self._table)
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 3)
        self._content_splitter.setSizes([280, 640])
        layout.addWidget(self._content_splitter, stretch=1)

        self._current_data = None
        self._current_file_path = None
        self._top_collapsed = False

    def load_result(self, file_path: str):
        """결과 파일 로드"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._current_data = data
            self._current_file_path = file_path
            self._display_data(data)
        except Exception as e:
            print(f"Failed to load result: {e}")

    def _display_data(self, data: Dict):
        """데이터 표시"""
        # 요약 업데이트
        steps = data.get("steps", [])
        self._steps = steps
        self._screenshots_dir = data.get("screenshots_dir")
        total = len(steps)
        success = sum(1 for s in steps if s.get("success", False))
        fail = total - success
        issues = len(data.get("issues_found", []))
        coverage = data.get("coverage", {}).get("coverage_percentage", 0)
        duration = data.get("duration_seconds", 0)

        self._summary_data = {
            "total": total,
            "success": success,
            "fail": fail,
            "issues": issues,
            "coverage": f"{coverage:.0f}%",
            "duration": f"{duration:.1f}s",
        }

        # 테이블 업데이트 - 기능 중심
        self._table.setRowCount(total)

        for row, step in enumerate(steps):
            # 기능 설명 (새 필드)
            feature_desc = step.get("feature_description", "")
            test_scenario = step.get("test_scenario", "")
            business_impact = step.get("business_impact", "")

            # 액션 상세
            decision = step.get("decision", {})
            action = decision.get("selected_action", {})

            if action:
                action_type = action.get("action_type", "")
                action_desc = action.get("description", "N/A")
                action_detail = f"{action_type}: {action_desc[:30]}"
            else:
                action_detail = "탐색 종료"
                if not feature_desc:
                    feature_desc = "탐색 완료"

            # 결과
            result = "PASS" if step.get("success") else "FAIL"
            error_msg = step.get("error_message", "")
            detail = (
                "성공"
                if step.get("success")
                else error_msg[:20]
                if error_msg
                else "실패"
            )

            # 테이블에 데이터 설정
            self._table.setItem(row, 0, QTableWidgetItem(feature_desc or action_detail))
            self._table.setItem(row, 1, QTableWidgetItem(test_scenario or "-"))
            self._table.setItem(row, 2, QTableWidgetItem(action_detail))
            self._table.setItem(
                row,
                3,
                QTableWidgetItem(business_impact or "-"),
            )
            self._table.setItem(row, 4, QTableWidgetItem(detail))

            result_item = QTableWidgetItem(result)
            if result == "PASS":
                result_item.setBackground(QColor(209, 250, 229))
                result_item.setForeground(QColor(5, 150, 105))
            else:
                result_item.setBackground(QColor(254, 226, 226))
                result_item.setForeground(QColor(220, 38, 38))
            result_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, result_item)

            play_button = QPushButton("재생", self)
            play_button.setObjectName("GhostButton")
            play_button.clicked.connect(lambda _, idx=row: self._emit_replay(idx))
            self._table.setCellWidget(row, 6, play_button)

            for col in range(6):
                item = self._table.item(row, col)
                if item:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    item.setToolTip(item.text())
            self._table.setRowHeight(row, 72)

        if total > 0:
            self._table.setCurrentCell(0, 0)
            self._preview_step(0, 0, -1, -1)

    def _open_step_detail(self, row: int, column: int):
        """테이블 더블클릭으로 스텝 상세 보기"""
        if not self._steps or row >= len(self._steps):
            return

        dialog = StepDetailDialog(
            self._steps[row],
            row,
            screenshots_dir=self._screenshots_dir,
            parent=self,
        )
        dialog.exec()

    def _preview_step(self, row: int, column: int, _prev_row: int, _prev_col: int):
        if not self._steps or row < 0 or row >= len(self._steps):
            return
        self._step_replay.load_step(self._steps[row], self._screenshots_dir)

    def _emit_replay(self, row: int):
        if not self._steps or row < 0 or row >= len(self._steps):
            return
        html = self._build_replay_html(self._steps[row])
        self.replay_requested.emit(html)

    def _toggle_top_section(self):
        self._top_collapsed = not self._top_collapsed
        if self._top_collapsed:
            self._top_container.hide()
            self._toggle_top_button.setText("상단 펼치기")
            self._content_splitter.setSizes([0, 1])
        else:
            self._top_container.show()
            self._toggle_top_button.setText("상단 숨기기")
            self._content_splitter.setSizes([280, 640])

    def _show_summary(self):
        dialog = SummaryDialog(self._summary_data, parent=self)
        dialog.exec()

    def _build_replay_html(self, step_data: Dict) -> str:
        frames = self._collect_step_frames(step_data)
        if not frames:
            return """
            <html><body style="margin:0; background:#0f172a; color:#cbd5f5; display:flex; align-items:center; justify-content:center; height:100vh;">
            <div>재생할 이미지가 없습니다</div>
            </body></html>
            """

        frame_tags = "".join(
            [
                f'<img class="frame" src="{frame}" style="display:none; width:100%; height:auto;"/>'
                for frame in frames
            ]
        )

        return f"""
        <html>
        <head>
            <style>
                body {{ margin:0; background:#0f172a; color:#e2e8f0; font-family: Arial, sans-serif; }}
                .wrap {{ display:flex; align-items:center; justify-content:center; height:100vh; flex-direction:column; gap:12px; }}
                .card {{ width:90%; max-width:860px; background:#111827; border-radius:12px; padding:16px; box-shadow:0 20px 40px rgba(0,0,0,0.35); }}
                .frame {{ border-radius:8px; }}
                .label {{ font-size:12px; color:#94a3b8; }}
            </style>
        </head>
        <body>
            <div class="wrap">
                <div class="card">
                    {frame_tags}
                </div>
                <div class="label">스텝 재생</div>
            </div>
            <script>
                const frames = document.querySelectorAll('.frame');
                let index = 0;
                function show(idx) {{
                    frames.forEach((frame, i) => frame.style.display = i === idx ? 'block' : 'none');
                }}
                if (frames.length) {{
                    show(0);
                    setInterval(() => {{
                        index = (index + 1) % frames.length;
                        show(index);
                    }}, 650);
                }}
            </script>
        </body>
        </html>
        """

    def _collect_step_frames(self, step_data: Dict) -> List[str]:
        import base64

        frames: List[str] = []
        if self._screenshots_dir:
            step_index = step_data.get("step_number", 0)
            if step_index:
                screenshot_path = os.path.join(
                    self._screenshots_dir, f"step_{step_index:03d}.png"
                )
                if os.path.exists(screenshot_path):
                    with open(screenshot_path, "rb") as file:
                        encoded = base64.b64encode(file.read()).decode("utf-8")
                        frames.append(f"data:image/png;base64,{encoded}")

        for key in ["screenshot_before", "screenshot_after"]:
            screenshot_b64 = step_data.get(key)
            if screenshot_b64:
                frames.append(f"data:image/png;base64,{screenshot_b64}")

        return frames

    def _export_csv(self):
        """CSV로 내보내기"""
        if not self._current_data:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "CSV 저장", "", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8-sig") as f:
                f.write("테스트 기능,시나리오,수행 액션,비즈니스 영향,상세,결과\n")
                for row in range(self._table.rowCount()):
                    row_data = []
                    for col in range(self._table.columnCount()):
                        item = self._table.item(row, col)
                        text = item.text() if item else ""
                        text = text.replace('"', '""')
                        row_data.append(f'"{text}"')
                    f.write(",".join(row_data) + "\n")
            print(f"Exported to {file_path}")
        except Exception as e:
            print(f"Export failed: {e}")


class ExplorationViewer(QWidget):
    """탐색 결과 뷰어 메인 위젯"""

    back_requested = Signal()
    replay_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._results_dir = (
            Path(__file__).parent.parent.parent.parent
            / "artifacts"
            / "exploration_results"
        )
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 헤더
        header = QHBoxLayout()
        header.setSpacing(12)

        back_button = QPushButton("← 뒤로", self)
        back_button.setObjectName("GhostButton")
        back_button.clicked.connect(self.back_requested.emit)
        header.addWidget(back_button)

        title = QLabel("🧪 탐색 테스트 결과", self)
        title.setStyleSheet("font-size: 18px; font-weight: 600; color: #1f2937;")
        header.addWidget(title)

        header.addStretch()

        refresh_button = QPushButton("새로고침", self)
        refresh_button.setObjectName("GhostButton")
        refresh_button.clicked.connect(self.refresh_results)
        header.addWidget(refresh_button)

        layout.addLayout(header)

        # 스플리터
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # 왼쪽: 결과 목록
        list_container = QWidget(splitter)
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)

        list_label = QLabel("최근 테스트 결과", list_container)
        list_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #4b5563;")
        list_layout.addWidget(list_label)

        scroll = QScrollArea(list_container)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._list_widget = QWidget(scroll)
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 8, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()

        scroll.setWidget(self._list_widget)
        list_layout.addWidget(scroll, stretch=1)

        splitter.addWidget(list_container)

        # 오른쪽: 상세 뷰
        self._detail_view = ExplorationDetailView(splitter)
        self._detail_view.replay_requested.connect(self.replay_requested.emit)
        splitter.addWidget(self._detail_view)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout.addWidget(splitter, stretch=1)

        # 초기 로드
        self.refresh_results()

    def refresh_results(self):
        """결과 목록 새로고침"""
        # 기존 카드 제거
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # 결과 파일 로드
        if not self._results_dir.exists():
            self._results_dir.mkdir(parents=True, exist_ok=True)
            return

        files = sorted(self._results_dir.glob("exploration_*.json"), reverse=True)

        # 디렉토리 내 JSON 파일도 찾기
        for subdir in self._results_dir.iterdir():
            if subdir.is_dir():
                for json_file in subdir.glob("*.json"):
                    if json_file.name.startswith("exploration_"):
                        continue
                    files.append(json_file)

        files = sorted(set(files), key=lambda x: x.stat().st_mtime, reverse=True)

        for file_path in files[:20]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                steps = data.get("steps", [])
                gif_path = data.get("recording_gif_path")

                summary = {
                    "timestamp": datetime.fromtimestamp(
                        file_path.stat().st_mtime
                    ).strftime("%Y-%m-%d %H:%M"),
                    "start_url": data.get("start_url", "Unknown"),
                    "total_steps": len(steps),
                    "success_count": sum(1 for s in steps if s.get("success", False)),
                    "issues_count": len(data.get("issues_found", [])),
                    "has_gif": gif_path and os.path.exists(gif_path),
                }

                card = ExplorationResultCard(str(file_path), summary, self._list_widget)
                card.clicked.connect(self._on_card_clicked)
                self._list_layout.insertWidget(self._list_layout.count() - 1, card)

            except Exception as e:
                print(f"Failed to load {file_path}: {e}")

        # 첫 번째 결과 자동 선택
        if files:
            self._detail_view.load_result(str(files[0]))

    def _on_card_clicked(self, file_path: str):
        """카드 클릭 처리"""
        self._detail_view.load_result(file_path)
