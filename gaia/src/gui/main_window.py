"""메인 애플리케이션 창을 구성하는 Qt 위젯 모음입니다."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, List, Sequence

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, QByteArray
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPen, QFont, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
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
from gaia.src.gui.asset_widgets import GuiAssetLabel
from gaia.src.screenshot_quality import is_low_information_screenshot


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
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

        view = QWebEngineView(parent)
        setattr(view, "_gaia_preview_enabled", True)
        return view
    except Exception:
        fallback = _WebViewFallback(parent)
        setattr(fallback, "_gaia_preview_enabled", False)
        return fallback


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

        # 트랙 (옅은 회색 원)
        track_pen = QPen(QColor(229, 232, 235), 4)  # #e5e8eb
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # 회전하는 브랜드 컬러 호 (Toss-style)
        gradient_colors = [
            QColor(49, 130, 246, 240),   # #3182f6 main
            QColor(49, 130, 246, 130),
            QColor(49, 130, 246, 50),
        ]

        for index, color in enumerate(gradient_colors):
            pen = QPen(color, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            start_angle = (self._angle - index * 45) * 16
            span_angle = 120 * 16
            painter.drawArc(rect, start_angle, span_angle)


class DotsLoaderWidget(QWidget):
    """status pill 옆에 표시되는 작은 3-dot wave 로더.

    set_active(True/False)로 시작/정지. 정지 시에도 위젯은 보이며 도트는 흐릿하게 표시.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._phase: float = 0.0
        self._active: bool = False
        # 카드 외 status header에서 자연스럽게 inline 표시되도록 작은 사이즈
        self.setFixedSize(36, 18)
        # 50ms = 20FPS 부드러운 모션
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if self._active and not self._timer.isActive():
            self._timer.start()
        elif not self._active and self._timer.isActive():
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.08) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: D401
        import math
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2
        base_r = 2.5
        spacing = 9
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            if self._active:
                phase = (self._phase + i * 0.18) % 1.0
                amp = (math.sin(phase * math.pi * 2) + 1.0) / 2.0
                alpha = int(80 + amp * 160)  # 80~240
                radius = base_r + amp * 1.4
            else:
                # 정지 상태 — 흐릿한 회색 도트로 정적 표시
                alpha = 90
                radius = base_r
            color = QColor(49, 130, 246)  # brand blue
            color.setAlpha(alpha)
            painter.setBrush(color)
            dx = cx + (i - 1) * spacing
            painter.drawEllipse(
                int(dx - radius), int(cy - radius),
                int(radius * 2), int(radius * 2),
            )


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
    """전체 진행률을 원형 그래프로 보여주는 위젯.

    - target value로 즉시 점프하지 않고 부드럽게 보간하여 시각적으로 자연스럽게 증가.
    - 중앙 % 숫자 아래에 3-dot wave 로딩 모션 (active일 때만).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value: float = 0.0           # 현재 표시값 (smoothed)
        self._target_value: float = 0.0    # 목표값 (set_value로 받은 최신)
        self._track_color = QColor(229, 232, 235)  # #e5e8eb
        self._progress_color = QColor(49, 130, 246)  # #3182f6 brand color
        self._pen_width = 14
        # KPI 카드 안에서 사용 — 작은 영역에서도 잘리지 않도록 최소 사이즈 축소
        self.setMinimumSize(80, 36)

        # 3-dot 애니메이션 phase
        self._dot_phase: float = 0.0
        self._dots_active: bool = False

        # 부드러운 값 전환 + 도트 애니메이션 한 번에 처리하는 단일 타이머 (50ms = 20 FPS)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(50)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_timer.start()

    def set_value(self, value: float) -> None:
        clamped = max(0.0, min(100.0, float(value)))
        # 목표값만 갱신 — 실제 표시값은 타이머가 부드럽게 보간
        self._target_value = clamped

    def set_dots_active(self, active: bool) -> None:
        """로딩 도트 애니메이션 on/off — set_busy(True/False)에 맞춰 호출."""
        self._dots_active = bool(active)
        self.update()

    def _on_anim_tick(self) -> None:
        changed = False
        # 1) 부드러운 값 전환 — 매 tick마다 target의 18%씩 따라감 (easeOut 느낌)
        if abs(self._target_value - self._value) > 0.05:
            delta = (self._target_value - self._value) * 0.18
            # 매우 작은 변화는 즉시 적용해서 끝맺기
            if abs(delta) < 0.05:
                self._value = self._target_value
            else:
                self._value += delta
            changed = True
        # 2) 도트 애니메이션 phase 증가
        if self._dots_active:
            self._dot_phase = (self._dot_phase + 0.08) % 1.0
            changed = True
        if changed:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: D401
        # 큰 검정색 % 텍스트만 (3-dot 로더는 status pill 옆 DotsLoaderWidget이 담당).
        # 위젯 높이에 맞춰 폰트 사이즈를 동적 계산해서 high-DPI에서도 클리핑되지 않도록 함.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        full_rect = event.rect()

        # 위젯 실제 높이 기반 폰트 사이즈 — 박스의 ~62%를 글자 높이로 사용 (descender 여유)
        widget_h = max(20, full_rect.height())
        # pixelSize 사용 → high-DPI 스케일링과 무관하게 정확한 픽셀 크기 보장
        px_size = max(14, min(34, int(widget_h * 0.62)))

        # % 텍스트 — 블랙 (#191f28)
        painter.setPen(QColor(25, 31, 40))
        font = QFont()
        font.setPixelSize(px_size)
        font.setWeight(QFont.Weight.Black)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 95)
        painter.setFont(font)
        painter.drawText(
            full_rect,
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

        # Toss-style 디자인 토큰 적용
        if normalized_status == "failed":
            indicator_style = (
                "background: #ef4444;"
                "border: 2px solid #ef4444;"
                "border-radius: 20px;"
                "color: #ffffff;"
                "font-weight: 800;"
            )
            self._indicator.setText("✕")
        elif normalized_status == "skipped":
            indicator_style = (
                "background: #ffffff;"
                "border: 2px dashed #e5e8eb;"
                "border-radius: 20px;"
                "color: #8b95a1;"
                "font-weight: 800;"
            )
            self._indicator.setText("—")
        elif normalized_status == "partial":
            indicator_style = (
                "background: #f59e0b;"
                "border: 2px solid #f59e0b;"
                "border-radius: 20px;"
                "color: #ffffff;"
                "font-weight: 800;"
            )
            self._indicator.setText("~")
        elif self._percent >= 99.0 or normalized_status == "success":
            indicator_style = (
                "background: #10b981;"
                "border: 2px solid #10b981;"
                "border-radius: 20px;"
                "color: #ffffff;"
                "font-weight: 800;"
            )
            self._indicator.setText("✓")
        else:
            indicator_style = (
                "background: #ffffff;"
                "border: 2px solid #e5e8eb;"
                "border-radius: 20px;"
                "color: #8b95a1;"
                "font-weight: 800;"
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


# ----------------------------------------------------------------------
# Step 1 — 사이트 카드 (Toss-style)
# ----------------------------------------------------------------------

# 기본 사이트 카탈로그 — 처음 페인트는 브랜드 색상 + 이니셜 배지
DEFAULT_SITE_CATALOG: list[dict[str, str]] = [
    {"label": "네이버",       "url": "https://www.naver.com",    "initial": "N",  "color": "#03c75a"},
    {"label": "위키피디아",   "url": "https://ko.wikipedia.org", "initial": "W",  "color": "#000000"},
    {"label": "유튜브",       "url": "https://www.youtube.com",  "initial": "▶",  "color": "#ff0000"},
    {"label": "GitHub",       "url": "https://github.com",       "initial": "G",  "color": "#181717"},
    {"label": "다음",         "url": "https://www.daum.net",     "initial": "D",  "color": "#0066ff"},
    {"label": "카카오맵",     "url": "https://map.kakao.com",    "initial": "M",  "color": "#fae100"},
    {"label": "11번가",       "url": "https://www.11st.co.kr",   "initial": "11", "color": "#ff1a1a"},
    {"label": "디시인사이드", "url": "https://www.dcinside.com", "initial": "DC", "color": "#0066c0"},
    {"label": "네이버 뉴스",  "url": "https://news.naver.com",   "initial": "N",  "color": "#03c75a"},
    {"label": "KBS 뉴스",     "url": "https://news.kbs.co.kr",   "initial": "K",  "color": "#0064b0"},
    {"label": "MBC 뉴스",     "url": "https://imnews.imbc.com",  "initial": "M",  "color": "#3d3d3d"},
    {"label": "SBS 뉴스",     "url": "https://news.sbs.co.kr",   "initial": "S",  "color": "#0066cc"},
]


class SiteCard(QFrame):
    """단일 사이트 카드.

    초기에는 브랜드 색상 + 이니셜 배지를 보여주다가, favicon이 비동기로 로드되면
    원형 클립으로 덮어 그립니다. favicon 실패 시 배지 그대로 유지.
    """

    clicked = Signal(str)  # 사이트 URL 전달

    def __init__(
        self,
        label: str,
        url: str,
        initial: str,
        color: str,
        parent: QWidget | None = None,
        network_manager: QNetworkAccessManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SiteCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(72)

        self._url = url
        self._brand_color = color
        self._initial_text = initial[:2] if initial else (label[:1] or "?")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # 브랜드 배지 (40x40 원형) — fallback 페인트
        self._badge = QLabel(self._initial_text, self)
        self._badge.setObjectName("SiteCardBadge")
        self._badge.setFixedSize(40, 40)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_initial_badge_style()
        layout.addWidget(self._badge)

        # 텍스트 (이름 + URL)
        text_container = QWidget(self)
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        name_label = QLabel(label, text_container)
        name_label.setObjectName("SiteCardName")
        text_layout.addWidget(name_label)

        url_label = QLabel(url, text_container)
        url_label.setObjectName("SiteCardURL")
        text_layout.addWidget(url_label)

        layout.addWidget(text_container, stretch=1)

        # 우측 chevron / ✓
        self._indicator = QLabel("›", self)
        self._indicator.setObjectName("SiteCardIndicator")
        self._indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._indicator.setFixedSize(20, 20)
        layout.addWidget(self._indicator)

        # Favicon 비동기 요청 (Google s2 favicons API)
        if network_manager is not None:
            self._request_favicon(network_manager)

    def _apply_initial_badge_style(self) -> None:
        self._badge.setText(self._initial_text)
        self._badge.setStyleSheet(
            f"background: {self._brand_color};"
            f"color: #ffffff;"
            f"border-radius: 20px;"
            f"font-weight: 800;"
            f"font-size: 13px;"
        )

    def _request_favicon(self, manager: QNetworkAccessManager) -> None:
        from urllib.parse import urlparse

        try:
            parsed = urlparse(self._url)
        except Exception:
            return
        domain = parsed.netloc or parsed.path
        if not domain:
            return
        favicon_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=64"
        request = QNetworkRequest(QUrl(favicon_url))
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            "Mozilla/5.0 (GAIA QA Desktop)",
        )
        reply = manager.get(request)
        self._pending_favicon_reply = reply
        # 카드가 deleteLater()로 사라지기 직전에 reply를 끊어서 callback이 아예 안 불리게 함
        self.destroyed.connect(lambda _=None, r=reply: SiteCard._abort_reply(r))
        reply.finished.connect(lambda r=reply: self._on_favicon_loaded(r))

    @staticmethod
    def _abort_reply(reply: QNetworkReply) -> None:
        """SiteCard가 destroy되기 직전에 pending reply를 abort."""
        try:
            if reply is not None and reply.isRunning():
                reply.abort()
        except RuntimeError:
            pass
        except Exception:
            pass

    def _on_favicon_loaded(self, reply: QNetworkReply) -> None:
        # 그리드가 재구성되어 카드(self)나 배지가 이미 deleteLater()됐을 수 있음.
        # 1) shiboken6.isValid로 C++ 객체 살아있는지 명시 체크
        # 2) 그 외 경우는 try/except로 belt-and-suspenders
        try:
            import shiboken6
            if not shiboken6.isValid(self) or not shiboken6.isValid(self._badge):
                try:
                    reply.deleteLater()
                except RuntimeError:
                    pass
                return
        except Exception:
            # shiboken6 없으면 try/except 만으로 진행
            pass

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data: QByteArray = reply.readAll()
            if data.isEmpty():
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return
            if pixmap.isNull() or pixmap.width() < 8:
                return
            # 36×36으로 스케일 다운 (badge 40 안쪽으로)
            scaled = pixmap.scaled(
                36,
                36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            try:
                self._badge.setText("")
                self._badge.setPixmap(scaled)
                self._badge.setStyleSheet(
                    "background: #ffffff;"
                    "border-radius: 20px;"
                    "border: 1px solid #e5e8eb;"
                    "padding: 0px;"
                )
            except RuntimeError:
                # C++ 객체가 이미 삭제됨 — 무시
                return
        except RuntimeError:
            # reply 자체가 해제된 케이스 등
            return
        finally:
            try:
                reply.deleteLater()
            except RuntimeError:
                pass

    def mousePressEvent(self, event) -> None:  # noqa: D401
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._url)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        self._indicator.setText("✓" if selected else "›")
        self._indicator.setProperty("selected", bool(selected))
        # 강제 restyle
        for w in (self, self._indicator):
            style = w.style()
            if style is not None:
                style.unpolish(w)
                style.polish(w)
                w.update()


# ----------------------------------------------------------------------
# Step 2 — 테스트 케이스 row (Toss-style)
# ----------------------------------------------------------------------

# 모드별 아이콘 — 통일된 브랜드 블루 색상 + 모던 단색 글리프 (Toss-style)
# 모든 아이콘은 같은 brand light bg + brand color 글리프로 시각적 일관성 유지.
CASE_MODE_PALETTE: dict[str, tuple[str, str, str]] = {
    # (글리프, 배경색, 글리프색) — 모두 brand light/blue 톤
    "benchmark_all": ("▶",  "#eff6ff", "#3182f6"),
    "benchmark":     ("◉",  "#eff6ff", "#3182f6"),
    "quick":         ("⚡",  "#eff6ff", "#3182f6"),
    "ai":            ("✦",  "#eff6ff", "#3182f6"),
    "spec":          ("▤",  "#eff6ff", "#3182f6"),
    "bundle":        ("▦",  "#eff6ff", "#3182f6"),
}

# 디폴트 테스트 케이스 카탈로그 — 사이트와 무관하게 항상 노출
DEFAULT_TEST_CASES: list[dict[str, Any]] = [
    {
        "id": "benchmark_all",
        "mode": "benchmark",
        "icon_mode": "benchmark_all",
        "title": "등록된 전체 시나리오 실행",
        "desc": "이 사이트에 등록된 모든 시나리오를 순서대로 실행합니다.",
        "eta": "예상 소요 5~10분",
        "recommended": True,
    },
    {
        "id": "main_areas",
        "mode": "benchmark",
        "icon_mode": "benchmark",
        "title": "주요 영역 확인",
        "desc": "홈 화면의 핵심 영역과 주요 기능 동작을 검증합니다.",
        "eta": "예상 소요 2분",
        "recommended": True,
    },
    {
        "id": "search",
        "mode": "benchmark",
        "icon_mode": "benchmark",
        "title": "검색 기능 확인",
        "desc": "검색창 입력·결과 노출·정렬 동작을 검증합니다.",
        "eta": "예상 소요 2분",
        "recommended": False,
    },
    {
        "id": "quick_goal",
        "mode": "quick",
        "icon_mode": "quick",
        "title": "빠른 목표 직접 입력",
        "desc": "AI가 직접 목표를 이해하고 수행합니다 (단일 실행).",
        "eta": "예상 소요 1~3분",
        "recommended": False,
    },
    {
        "id": "ai_explore",
        "mode": "ai",
        "icon_mode": "ai",
        "title": "AI 자율 탐색",
        "desc": "AI가 사이트를 자유 탐색하며 이상 여부를 보고합니다.",
        "eta": "예상 소요 5~10분",
        "recommended": False,
    },
    {
        "id": "from_spec",
        "mode": "bundle",
        "icon_mode": "spec",
        "title": "기획서 업로드해서 자동 생성",
        "desc": "PDF/DOCX 기획서를 업로드하면 시나리오 자동 생성.",
        "eta": "예상 소요 2~5분",
        "recommended": False,
    },
]


class TestCaseRow(QFrame):
    """체크박스 + 색상 아이콘 + 제목/설명 + ETA pill + ★ 즐겨찾기.

    행 어디든 클릭 → 체크 토글. ★ 클릭 → 즐겨찾기 토글 (행 토글 안 함).
    """

    toggled = Signal(str, bool)            # case_id, selected
    favorite_changed = Signal(str, bool)   # case_id, favorited

    def __init__(
        self,
        *,
        case_id: str,
        title: str,
        description: str,
        eta_text: str,
        icon_mode: str,
        recommended: bool = False,
        favorite: bool = False,
        scenario_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TestCaseRow")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(76)

        self._case_id = case_id
        self._selected = False
        self._favorite = bool(favorite)
        self._recommended = bool(recommended)
        # 카테고리 필터 (Step 2 콤보)에서 사용할 icon_mode 저장
        self._icon_mode = str(icon_mode or "benchmark")
        # 벤치마크 시나리오 1개에만 해당하는 케이스이면 해당 scenario_id 저장
        # ""(빈 문자열)이면 전체 시나리오 실행 또는 비-벤치 모드
        self._scenario_id = str(scenario_id or "")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        # 체크 인디케이터 (실제 QCheckBox 대신 라벨로 — 토스 스타일 통제)
        self._check = QLabel("", self)
        self._check.setObjectName("CaseRowCheck")
        self._check.setFixedSize(22, 22)
        self._check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 마우스 이벤트가 row(부모)로 전달되도록 transparent — 체크 자체를 클릭해도 row 토글
        self._check.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._update_check_style()
        layout.addWidget(self._check, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 색상 아이콘 배지 (36×36 라운드 10)
        icon, bg, fg = CASE_MODE_PALETTE.get(icon_mode, CASE_MODE_PALETTE["benchmark"])
        self._icon_badge = QLabel(icon, self)
        self._icon_badge.setFixedSize(36, 36)
        self._icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_badge.setStyleSheet(
            f"background: {bg};"
            f"color: {fg};"
            f"border-radius: 10px;"
            f"font-size: 16px;"
            f"font-weight: 700;"
        )
        # 마우스 이벤트 row로 통과 — 아이콘 클릭해도 row 토글
        self._icon_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._icon_badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        # 제목 + 설명
        text_col = QWidget(self)
        text_col.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)
        self._title_label = QLabel(title, text_col)
        self._title_label.setObjectName("CaseRowTitle")
        self._title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_layout.addWidget(self._title_label)
        self._desc_label = QLabel(description, text_col)
        self._desc_label.setObjectName("CaseRowDesc")
        self._desc_label.setWordWrap(True)
        self._desc_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_layout.addWidget(self._desc_label)
        layout.addWidget(text_col, stretch=1)

        # ETA pill
        self._eta_pill = QLabel(eta_text, self)
        self._eta_pill.setObjectName("CaseRowEtaPill")
        # 마우스 이벤트 row로 통과 — ETA pill 클릭해도 row 토글
        self._eta_pill.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._eta_pill, alignment=Qt.AlignmentFlag.AlignVCenter)

        # ★ 즐겨찾기
        self._star = QLabel("★" if self._favorite else "☆", self)
        self._star.setObjectName("CaseRowStar")
        self._star.setProperty("favorited", self._favorite)
        self._star.setFixedSize(24, 24)
        self._star.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._star.setCursor(Qt.CursorShape.PointingHandCursor)
        # 별도 클릭 핸들링을 위해 mousePressEvent를 monkey-patch
        self._star.mousePressEvent = self._on_star_pressed  # type: ignore[assignment]
        layout.addWidget(self._star, alignment=Qt.AlignmentFlag.AlignVCenter)

    # --- 공개 API ---
    def case_id(self) -> str:
        return self._case_id

    def is_selected(self) -> bool:
        return self._selected

    def is_favorite(self) -> bool:
        return self._favorite

    def is_recommended(self) -> bool:
        return self._recommended

    def icon_mode(self) -> str:
        return self._icon_mode

    def scenario_id(self) -> str:
        return self._scenario_id

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setProperty("selected", self._selected)
        self._update_check_style()
        for w in (self,):
            style = w.style()
            if style is not None:
                style.unpolish(w)
                style.polish(w)
                w.update()

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = bool(favorite)
        self._star.setText("★" if self._favorite else "☆")
        self._star.setProperty("favorited", self._favorite)
        style = self._star.style()
        if style is not None:
            style.unpolish(self._star)
            style.polish(self._star)
            self._star.update()

    # --- 내부 ---
    def _update_check_style(self) -> None:
        if self._selected:
            self._check.setText("✓")
            self._check.setStyleSheet(
                "background: #3182f6;"
                "color: #ffffff;"
                "border: 2px solid #3182f6;"
                "border-radius: 5px;"
                "font-weight: 800;"
                "font-size: 13px;"
            )
        else:
            self._check.setText("")
            self._check.setStyleSheet(
                "background: #ffffff;"
                "border: 2px solid #d1d6db;"
                "border-radius: 5px;"
            )

    def _on_star_pressed(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_favorite(not self._favorite)
            self.favorite_changed.emit(self._case_id, self._favorite)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: D401
        if event.button() == Qt.MouseButton.LeftButton:
            # 별(★) 영역 클릭은 favorite 토글만 — row selection 변화 없음
            star_rect = self._star.geometry() if hasattr(self, "_star") else None
            if star_rect and star_rect.contains(event.position().toPoint()):
                # _star.mousePressEvent가 이미 처리 (favorite 토글). row.toggle은 skip.
                super().mousePressEvent(event)
                return
            self.set_selected(not self._selected)
            self.toggled.emit(self._case_id, self._selected)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    """UI 요소와 컨트롤러 콜백을 연결하는 최상위 창입니다."""

    fileDropped = Signal(str)
    startRequested = Signal()
    cancelRequested = Signal()
    urlSubmitted = Signal(str)
    chatMessageSubmitted = Signal(str)
    planFileSelected = Signal(str)
    bugJsonSelected = Signal(str)
    inputSourceCleared = Signal()
    benchmarkManageRequested = Signal(str, str)
    benchmarkSaveRequested = Signal(str, str)
    benchmarkRunRequested = Signal(str, str)
    benchmarkViewRequested = Signal(str, str)

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
            self.resize(1280, 860)
        # 사용자가 윈도우를 반쪽 화면 또는 더 작게 줄일 수 있도록 최소 크기 축소.
        # (Step 3은 단일 컬럼 레이아웃이라 좁은 폭에서도 자연스럽게 동작.)
        self.setMinimumSize(480, 600)

        self.setStyleSheet(
            """
            /* ==========================================================
             * GAIA Design System — Toss-style Light Theme
             * Tokens: brand #3182f6 / hover #1b64da / light bg #eff6ff
             *         text #191f28 / secondary #6b7684 / muted #8b95a1
             *         success #10b981 / fail #ef4444 / warn #f59e0b
             * Shape:  card 12-14px / button 8-12px / pill 999px
             * ========================================================== */

            QMainWindow {
                background: #f9fafb;
            }

            QWidget {
                color: #191f28;
                font-family: 'Pretendard', 'Noto Sans KR', 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
                font-size: 13px;
            }

            /* ---- Page title (브랜드 메인 타이틀) ---------------------- */
            QLabel#AppTitle {
                font-size: 24px;
                font-weight: 800;
                letter-spacing: -0.4px;
                color: #3182f6;
            }

            /* ===========================================================
             * Sidebar (240px fixed left navigation)
             * =========================================================== */
            QWidget#CentralRoot {
                background: #f9fafb;
            }

            QWidget#RightContainer {
                background: #f9fafb;
            }

            QFrame#Sidebar {
                background: #ffffff;
                border: none;
                border-right: 1px solid #e5e8eb;
            }

            /* RootSplitter handle — 사이드바 드래그 핸들 (호버 시 brand color) */
            QSplitter#RootSplitter::handle {
                background: #e5e8eb;
            }
            QSplitter#RootSplitter::handle:hover {
                background: #3182f6;
            }
            QSplitter#RootSplitter::handle:pressed {
                background: #1b64da;
            }

            QLabel#SidebarBrand {
                color: #3182f6;
                font-size: 24px;
                font-weight: 800;
                letter-spacing: -0.4px;
            }

            QLabel#SidebarBrandSub {
                color: #8b95a1;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.3px;
            }

            QFrame#SidebarStepRow {
                background: transparent;
                border-radius: 8px;
            }

            QFrame#SidebarStepRow[state="active"] {
                background: #eff6ff;
            }

            QLabel#SidebarStepDot {
                background: #f2f4f6;
                color: #8b95a1;
                border-radius: 14px;
                font-size: 12px;
                font-weight: 800;
            }

            QLabel#SidebarStepDot[state="active"] {
                background: #3182f6;
                color: #ffffff;
            }

            QLabel#SidebarStepDot[state="done"] {
                background: #3182f6;
                color: #ffffff;
            }

            QLabel#SidebarStepLabel {
                color: #8b95a1;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }

            QLabel#SidebarStepLabel[state="active"] {
                color: #1b64da;
                font-weight: 700;
            }

            QLabel#SidebarStepLabel[state="done"] {
                color: #191f28;
                font-weight: 600;
            }

            QFrame#SidebarStepConnector {
                background: #e5e8eb;
                border: none;
            }

            QFrame#SidebarStepConnector[state="done"] {
                background: #3182f6;
            }

            QFrame#SidebarDivider {
                background: #f2f4f6;
                border: none;
                max-height: 1px;
            }

            QPushButton#SidebarMenuItem {
                background: transparent;
                border: none;
                color: #4e5968;
                font-size: 13px;
                font-weight: 600;
                text-align: left;
                padding: 9px 12px;
                border-radius: 8px;
                min-height: 0px;
            }

            QPushButton#SidebarMenuItem:hover {
                background: #f2f4f6;
                color: #191f28;
            }

            QPushButton#SidebarMenuItem:pressed {
                background: #e5e8eb;
            }

            QFrame#SidebarUserCard {
                background: #f9fafb;
                border-radius: 10px;
                border: 1px solid #f2f4f6;
            }

            QLabel#SidebarUserAvatar {
                background: #3182f6;
                color: #ffffff;
                border-radius: 16px;
                font-size: 13px;
                font-weight: 800;
            }

            QLabel#SidebarUserName {
                color: #191f28;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }

            /* ===========================================================
             * Top header with stepper pill (centered)
             * =========================================================== */
            QFrame#TopHeader {
                background: #ffffff;
                border: none;
                border-bottom: 1px solid #e5e8eb;
            }

            QFrame#StepperPill {
                background: transparent;
                border: none;
            }

            QFrame#StepperStep {
                background: transparent;
            }

            QLabel#StepperDot {
                background: #f2f4f6;
                color: #8b95a1;
                border-radius: 11px;
                font-size: 11px;
                font-weight: 800;
            }

            QLabel#StepperDot[state="active"] {
                background: #3182f6;
                color: #ffffff;
            }

            QLabel#StepperDot[state="done"] {
                background: #3182f6;
                color: #ffffff;
            }

            QLabel#StepperLabel {
                color: #8b95a1;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }

            QLabel#StepperLabel[state="active"] {
                color: #1b64da;
                font-weight: 700;
            }

            QLabel#StepperLabel[state="done"] {
                color: #191f28;
                font-weight: 600;
            }

            QFrame#StepperBar {
                background: #e5e8eb;
                border: none;
            }

            QFrame#StepperBar[state="done"] {
                background: #3182f6;
            }

            /* ===========================================================
             * Step 1 — 사이트 카드 / 사이트 그리드 / 직접 URL 패널
             * =========================================================== */
            QLabel#PageTitle {
                color: #191f28;
                font-size: 24px;
                font-weight: 800;
                letter-spacing: -0.4px;
            }

            QLabel#PageSubtitle {
                color: #6b7684;
                font-size: 13px;
            }

            QLabel#SectionHeading {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
            }

            QFrame#SiteCard {
                background: #ffffff;
                border-radius: 12px;
                border: 1px solid #e5e8eb;
            }

            QFrame#SiteCard:hover {
                border: 1.5px solid #b2d4ff;
                background: #f9fbff;
            }

            QFrame#SiteCard[selected="true"] {
                border: 2px solid #3182f6;
                background: #eff6ff;
            }

            QLabel#SiteCardName {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
            }

            QLabel#SiteCardURL {
                color: #8b95a1;
                font-size: 11px;
                background: transparent;
            }

            QLabel#SiteCardIndicator {
                color: #c5cdd5;
                font-size: 18px;
                font-weight: 800;
                background: transparent;
            }

            QLabel#SiteCardIndicator[selected="true"] {
                color: #3182f6;
            }

            QFrame#DirectUrlPanel {
                background: #f9fbff;
                border: 1.5px dashed #b2d4ff;
                border-radius: 12px;
            }

            QLabel#DirectUrlGlobe {
                color: #3182f6;
                font-size: 22px;
                background: transparent;
            }

            QLabel#DirectUrlTitle {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
            }

            QLabel#DirectUrlDescription {
                color: #6b7684;
                font-size: 12.5px;
                background: transparent;
            }

            QPushButton#DirectUrlSubmit {
                background: #3182f6;
                color: #ffffff;
                border: 1px solid #3182f6;
                border-radius: 10px;
                font-weight: 800;
                font-size: 16px;
                min-width: 44px;
                min-height: 36px;
                padding: 0px;
            }

            QPushButton#DirectUrlSubmit:hover {
                background: #1b64da;
                border: 1px solid #1b64da;
            }

            QPushButton#AddUrlOutlineButton {
                background: #ffffff;
                color: #1b64da;
                border: 1.5px solid #b2d4ff;
                border-radius: 10px;
                font-weight: 700;
                padding: 8px 16px;
            }

            QPushButton#AddUrlOutlineButton:hover {
                background: #eff6ff;
                border: 1.5px solid #3182f6;
            }

            /* ===========================================================
             * Step 2 — 테스트 케이스 row / 탭 / footer / 섹션 라벨
             * =========================================================== */
            QLabel#CaseSectionLabel {
                color: #191f28;
                font-size: 14px;
                font-weight: 800;
                background: transparent;
                padding: 8px 0px 4px 0px;
                letter-spacing: -0.2px;
            }
            QLabel#CaseSectionSubLabel {
                color: #8b95a1;
                font-size: 12px;
                background: transparent;
                padding: 0px 0px 8px 0px;
            }

            QFrame#TestCaseRow {
                background: #ffffff;
                border-radius: 12px;
                border: 1px solid #e5e8eb;
            }

            QFrame#TestCaseRow:hover {
                border: 1.5px solid #b2d4ff;
            }

            QFrame#TestCaseRow[selected="true"] {
                border: 1.5px solid #3182f6;
                background: #eff6ff;
            }

            QLabel#CaseRowTitle {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
            }

            QLabel#CaseRowDesc {
                color: #6b7684;
                font-size: 12px;
                background: transparent;
            }

            QLabel#CaseRowEtaPill {
                color: #4e5968;
                font-size: 11px;
                font-weight: 700;
                background: #f2f4f6;
                border-radius: 999px;
                padding: 4px 10px;
            }

            QLabel#CaseRowStar {
                color: #c5cdd5;
                font-size: 16px;
                background: transparent;
            }

            QLabel#CaseRowStar[favorited="true"] {
                color: #f59e0b;
            }

            QPushButton#CaseTabButton {
                background: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                color: #6b7684;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 14px;
                border-radius: 0px;
                min-height: 0px;
            }

            QPushButton#CaseTabButton:hover {
                color: #191f28;
            }

            QPushButton#CaseTabButton[active="true"] {
                color: #3182f6;
                border-bottom: 2px solid #3182f6;
            }

            QFrame#CaseStageFooter {
                background: #ffffff;
                border-top: 1px solid #e5e8eb;
            }

            QLabel#FooterSummary {
                color: #6b7684;
                font-size: 12.5px;
                font-weight: 600;
            }

            QLabel#FooterSummaryStrong {
                color: #3182f6;
                font-size: 13px;
                font-weight: 800;
            }

            QPushButton#FooterPrimaryButton {
                background: #3182f6;
                color: #ffffff;
                border: 1px solid #3182f6;
                border-radius: 10px;
                font-weight: 800;
                padding: 11px 30px;
                font-size: 13px;
            }

            QPushButton#FooterPrimaryButton:hover {
                background: #1b64da;
                border: 1px solid #1b64da;
            }

            QPushButton#FooterPrimaryButton:disabled {
                background: #f2f4f6;
                border: 1px solid #f2f4f6;
                color: #b0b8c1;
            }

            /* ===========================================================
             * Step 3 — 테스트 진행 (ExecRunCard, metrics, current case)
             * =========================================================== */
            QFrame#ExecRunCard {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #d6e8ff;
            }

            QLabel#ExecCardTitle {
                color: #191f28;
                font-size: 18px;
                font-weight: 800;
            }

            QLabel#ExecStatusPill {
                background: #ecfdf5;
                color: #047857;
                padding: 4px 10px;
                border-radius: 999px;
                font-size: 11px;
                font-weight: 800;
            }

            QLabel#ExecStatusPill[state="done"] {
                background: #eff6ff;
                color: #1b64da;
            }

            QLabel#ExecStatusPill[state="failed"] {
                background: #fef2f2;
                color: #ef4444;
            }

            QLabel#ExecSubtitle {
                color: #6b7684;
                font-size: 13px;
            }

            QFrame#ExecMetricsBox {
                background: #f9fafb;
                border: 1px solid #e5e8eb;
                border-radius: 12px;
            }

            QFrame#ExecMetricsDivider {
                background: #e5e8eb;
                border: none;
                max-width: 1px;
            }

            QLabel#MetricLabel {
                color: #6b7684;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }

            QLabel#MetricValue {
                color: #191f28;
                font-size: 26px;
                font-weight: 800;
                background: transparent;
            }

            QLabel#MetricValuePass {
                color: #10b981;
                font-size: 26px;
                font-weight: 800;
                background: transparent;
            }

            QLabel#MetricValueFail {
                color: #ef4444;
                font-size: 26px;
                font-weight: 800;
                background: transparent;
            }

            QLabel#MetricSlash {
                color: #c5cdd5;
                font-size: 22px;
                font-weight: 700;
                background: transparent;
            }

            QLabel#MetricSubLabel {
                color: #8b95a1;
                font-size: 11px;
                background: transparent;
            }

            QPushButton#ReviewTabButton {
                background: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                color: #6b7684;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 16px;
                border-radius: 0px;
                min-height: 0px;
            }

            QPushButton#ReviewTabButton:hover {
                color: #191f28;
            }

            QPushButton#ReviewTabButton[active="true"] {
                color: #3182f6;
                border-bottom: 2px solid #3182f6;
            }

            QFrame#ReviewUrlBar {
                background: #f2f4f6;
                border-radius: 10px;
                border: 1px solid #e5e8eb;
            }

            QLabel#ReviewUrlGlobe {
                color: #6b7684;
                font-size: 14px;
                background: transparent;
            }

            QLabel#ReviewUrl {
                color: #4e5968;
                font-size: 12.5px;
                font-weight: 600;
                background: transparent;
            }

            QFrame#CurrentCaseCard {
                background: #ffffff;
                border-radius: 12px;
                border: 1px solid #e5e8eb;
            }

            QLabel#CurrentCasePin {
                font-size: 16px;
                background: transparent;
            }

            QLabel#CurrentCaseLabel {
                color: #6b7684;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
            }

            QLabel#CurrentCaseTitle {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
            }

            QLabel#CurrentCaseDesc {
                color: #6b7684;
                font-size: 12px;
                background: transparent;
            }

            QLabel#CurrentCaseStepPill {
                background: #eff6ff;
                color: #1b64da;
                padding: 6px 14px;
                border-radius: 999px;
                font-size: 11px;
                font-weight: 800;
            }

            /* ===========================================================
             * Step 3 신규 3-zone 레이아웃: KPI 컬럼 / 브라우저 / 터미널 로그
             * =========================================================== */
            QFrame#Step3KpiColumn {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 14px;
            }
            QLabel#Step3SectionTitle {
                color: #191f28;
                font-size: 15px;
                font-weight: 800;
                background: transparent;
            }
            QFrame#KpiCard {
                background: transparent;
                border: none;
                border-top: 1px solid #f2f4f6;
            }
            QFrame#KpiCard[first="true"] {
                border-top: none;
            }
            QLabel#KpiLabel {
                color: #6b7684;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#KpiValue {
                color: #191f28;
                font-size: 22px;
                font-weight: 800;
                background: transparent;
            }
            QLabel#KpiSubLabel {
                color: #8b95a1;
                font-size: 11px;
                background: transparent;
            }
            QLabel#KpiValuePass {
                color: #10b981;
                font-size: 22px;
                font-weight: 800;
                background: transparent;
            }
            QLabel#KpiValueFail {
                color: #ef4444;
                font-size: 22px;
                font-weight: 800;
                background: transparent;
            }

            /* 브라우저 모니터링 zone */
            QFrame#BrowserMonitorZone {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 14px;
            }
            QLabel#BrowserMonitorTitle {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
                background: transparent;
            }
            QLabel#BrowserMonitorDot {
                color: #10b981;
                font-size: 12px;
                background: transparent;
            }
            QFrame#BrowserUrlBar {
                background: #f9fafb;
                border: 1px solid #e5e8eb;
                border-radius: 8px;
            }
            QLabel#BrowserUrlBarGlobe {
                color: #6b7684;
                background: transparent;
            }
            QLabel#BrowserUrlBarText {
                color: #4e5968;
                font-size: 12.5px;
                font-weight: 600;
                background: transparent;
            }
            QPushButton#DeviceButton {
                background: transparent;
                border: 1px solid transparent;
                color: #8b95a1;
                font-size: 14px;
                font-weight: 700;
                border-radius: 8px;
                padding: 6px 8px;
                min-height: 0px;
            }
            QPushButton#DeviceButton:hover {
                background: #f2f4f6;
                color: #191f28;
            }
            QPushButton#DeviceButton[active="true"] {
                background: #eff6ff;
                color: #3182f6;
                border: 1px solid #b2d4ff;
            }

            /* 터미널 로그 zone */
            QFrame#TerminalLogZone {
                background: #0f172a;
                border-radius: 14px;
                border: 1px solid #1e293b;
            }
            QLabel#TerminalLogTitle {
                color: #f3f4f6;
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }
            QTextEdit#TerminalLog {
                background: #0f172a;
                color: #e2e8f0;
                border: none;
                font-family: 'Consolas', 'D2Coding', 'Cascadia Code', monospace;
                font-size: 12px;
                padding: 12px 14px;
                selection-background-color: #1e40af;
            }
            QCheckBox#TerminalAutoScroll {
                color: #94a3b8;
                font-size: 11.5px;
                background: transparent;
                spacing: 6px;
            }
            QCheckBox#TerminalAutoScroll::indicator {
                width: 14px;
                height: 14px;
                background: #1e293b;
                border: 1.5px solid #475569;
                border-radius: 3px;
            }
            QCheckBox#TerminalAutoScroll::indicator:checked {
                background: #3182f6;
                border: 1.5px solid #3182f6;
            }
            QPushButton#TerminalLogButton {
                background: transparent;
                border: 1px solid #334155;
                color: #94a3b8;
                font-size: 11px;
                font-weight: 600;
                border-radius: 6px;
                padding: 4px 10px;
                min-height: 0px;
            }
            QPushButton#TerminalLogButton:hover {
                background: #1e293b;
                color: #e2e8f0;
            }
            QComboBox#TerminalLogLevelCombo {
                background: transparent;
                border: 1px solid #334155;
                color: #cbd5e1;
                border-radius: 6px;
                padding: 3px 22px 3px 10px;
                min-height: 0px;
                font-size: 11px;
                font-weight: 600;
            }
            QComboBox#TerminalLogLevelCombo::drop-down {
                border: none; width: 18px;
            }
            QComboBox#TerminalLogLevelCombo QAbstractItemView {
                background: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                selection-background-color: #3182f6;
                selection-color: #ffffff;
            }

            /* ===========================================================
             * Step 3 신규 50:50 split 레이아웃
             * =========================================================== */
            QFrame#Step3StatusCard {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }
            QLabel#Step3StatusTitle {
                color: #191f28;
                font-size: 17px;
                font-weight: 800;
                letter-spacing: -0.2px;
                background: transparent;
            }
            QLabel#Step3StatusSub {
                color: #6b7684;
                font-size: 11.5px;
                background: transparent;
            }
            QPushButton#Step3PauseButton {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                color: #4e5968;
                border-radius: 8px;
                font-size: 11.5px;
                font-weight: 700;
                padding: 6px 11px;
                min-height: 0px;
            }
            QPushButton#Step3PauseButton:hover {
                background: #f9fafb;
                border: 1px solid #d1d6db;
            }
            QPushButton#Step3StopButton {
                background: #ef4444;
                border: 1px solid #ef4444;
                color: #ffffff;
                border-radius: 8px;
                font-size: 11.5px;
                font-weight: 700;
                padding: 6px 11px;
                min-height: 0px;
            }
            QPushButton#Step3StopButton:hover {
                background: #dc2626;
                border: 1px solid #dc2626;
            }
            QPushButton#Step3StopButton:disabled {
                background: #fef2f2;
                border: 1px solid #fee2e2;
                color: #fca5a5;
            }

            /* ===========================================================
             * Step 3 Scroll Area — 컨텐츠가 윈도우 높이 초과 시 스크롤
             * =========================================================== */
            QScrollArea#Step3ScrollArea {
                background: transparent;
                border: none;
            }
            QWidget#Step3ScrollContent {
                background: transparent;
            }
            QScrollArea#Step3ScrollArea QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 4px 2px 4px 2px;
            }
            QScrollArea#Step3ScrollArea QScrollBar::handle:vertical {
                background: #d1d6db;
                border-radius: 5px;
                min-height: 24px;
            }
            QScrollArea#Step3ScrollArea QScrollBar::handle:vertical:hover {
                background: #b0b8c1;
            }
            QScrollArea#Step3ScrollArea QScrollBar::add-line:vertical,
            QScrollArea#Step3ScrollArea QScrollBar::sub-line:vertical {
                background: transparent;
                height: 0px;
            }

            /* ===========================================================
             * Result Action Bar — test 완료 시 표시
             * =========================================================== */
            QFrame#ResultActionBar {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 12px;
            }
            QFrame#ResultActionBar[state="success"] {
                background: #f0fdf4;
                border: 1px solid #bbf7d0;
            }
            QFrame#ResultActionBar[state="failed"] {
                background: #fef2f2;
                border: 1px solid #fecaca;
            }
            QLabel#ResultActionStatusIcon {
                background: #10b981;
                color: #ffffff;
                border-radius: 14px;
                font-size: 16px;
                font-weight: 900;
            }
            QLabel#ResultActionStatusIcon[state="failed"] {
                background: #ef4444;
            }
            QLabel#ResultActionStatusIcon[state="warn"] {
                background: #f59e0b;
            }
            QLabel#ResultActionTitle {
                color: #191f28;
                font-size: 14px;
                font-weight: 800;
                background: transparent;
            }
            QLabel#ResultActionReason {
                color: #4e5968;
                font-size: 12px;
                background: transparent;
                padding-left: 38px;
            }
            QPushButton#ResultGrafanaButton {
                background: #3182f6;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 12.5px;
                font-weight: 700;
                padding: 8px 16px;
                min-height: 0px;
            }
            QPushButton#ResultGrafanaButton:hover {
                background: #1b64da;
            }
            QPushButton#ResultOpenFolderButton {
                background: #ffffff;
                color: #4e5968;
                border: 1px solid #e5e8eb;
                border-radius: 8px;
                font-size: 12.5px;
                font-weight: 700;
                padding: 8px 14px;
                min-height: 0px;
            }
            QPushButton#ResultOpenFolderButton:hover {
                background: #f9fafb;
                border: 1px solid #d1d6db;
            }

            /* KPI grid card — 컴팩트한 110px 고정 카드 */
            QFrame#KpiGridCard {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 12px;
                min-height: 110px;
            }
            QLabel#KpiGridLabel {
                color: #6b7684;
                font-size: 11.5px;
                font-weight: 600;
                background: transparent;
                min-height: 16px;
                max-height: 20px;
            }
            QLabel#KpiGridValue {
                color: #191f28;
                font-size: 19px;
                font-weight: 800;
                background: transparent;
                min-height: 28px;
                max-height: 34px;
                padding: 1px 0px;
            }
            QLabel#KpiGridValueBlue {
                color: #3182f6;
                font-size: 19px;
                font-weight: 800;
                background: transparent;
                min-height: 28px;
                max-height: 34px;
                padding: 1px 0px;
            }
            QLabel#KpiGridValuePass {
                color: #10b981;
                font-size: 19px;
                font-weight: 800;
                background: transparent;
                min-height: 28px;
                max-height: 34px;
                padding: 1px 0px;
            }
            QLabel#KpiGridValueFail {
                color: #ef4444;
                font-size: 19px;
                font-weight: 800;
                background: transparent;
                min-height: 28px;
                max-height: 34px;
                padding: 1px 0px;
            }
            QLabel#KpiGridSub {
                color: #8b95a1;
                font-size: 11px;
                background: transparent;
                min-height: 16px;
                max-height: 20px;
                padding-top: 1px;
            }

            /* Browser window decoration */
            QFrame#BrowserWindow {
                background: #ffffff;
                border: 1px solid #d1d6db;
                border-radius: 10px;
            }
            QFrame#BrowserTabBar {
                background: #f2f4f6;
                border-bottom: 1px solid #d1d6db;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QFrame#BrowserTab {
                background: #ffffff;
                border: 1px solid #d1d6db;
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
            QLabel#BrowserTabFavicon {
                color: #03c75a;
                font-size: 12px;
                font-weight: 800;
                background: transparent;
            }
            QLabel#BrowserTabTitle {
                color: #4e5968;
                font-size: 11.5px;
                font-weight: 600;
                background: transparent;
            }
            QPushButton#BrowserTabClose, QPushButton#BrowserNewTab,
            QPushButton#BrowserWindowControl {
                background: transparent;
                border: none;
                color: #8b95a1;
                font-size: 13px;
                font-weight: 700;
                padding: 0px 4px;
                min-height: 0px;
                min-width: 18px;
            }
            QPushButton#BrowserTabClose:hover, QPushButton#BrowserNewTab:hover,
            QPushButton#BrowserWindowControl:hover {
                background: #e5e8eb;
                border-radius: 4px;
            }
            QPushButton#BrowserWindowControl[role="close"]:hover {
                background: #ef4444;
                color: #ffffff;
            }

            QFrame#BrowserUrlRow {
                background: #f9fafb;
                border-bottom: 1px solid #e5e8eb;
            }
            QPushButton#BrowserNavButton {
                background: transparent;
                border: none;
                color: #6b7684;
                font-size: 14px;
                font-weight: 700;
                border-radius: 6px;
                padding: 4px 8px;
                min-height: 0px;
                min-width: 22px;
            }
            QPushButton#BrowserNavButton:hover {
                background: #e5e8eb;
                color: #191f28;
            }
            QFrame#BrowserAddressBar {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 999px;
            }
            QLabel#BrowserAddressLock {
                color: #10b981;
                font-size: 12px;
                background: transparent;
            }
            QLabel#BrowserAddressUrl {
                color: #4e5968;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }

            /* ---- Cards / Panels -------------------------------------- */
            QFrame#SidePanel {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }

            QFrame#BrowserCard {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }

            /* ---- Section / Browser titles ---------------------------- */
            QLabel#SectionLabel {
                font-weight: 700;
                font-size: 13px;
                letter-spacing: 0px;
                color: #191f28;
            }

            QLabel#BrowserTitle {
                font-weight: 700;
                font-size: 14px;
                color: #191f28;
            }

            /* ---- Drop area (점선 테두리 placeholder) ----------------- */
            QLabel#DropArea {
                border: 1.5px dashed #b2d4ff;
                border-radius: 12px;
                padding: 24px;
                color: #1b64da;
                background: #eff6ff;
                font-weight: 600;
            }

            /* ---- Lists ---------------------------------------------- */
            QListWidget {
                background: transparent;
                border: none;
                padding: 0px;
                outline: none;
            }

            QListWidget::item {
                background: transparent;
                border: none;
            }

            QListWidget::item:selected {
                background: transparent;
                color: #191f28;
            }

            /* ---- Inputs --------------------------------------------- */
            QTextEdit {
                background: #ffffff;
                border-radius: 10px;
                border: 1px solid #e5e8eb;
                padding: 12px 14px;
                color: #191f28;
                selection-background-color: #eff6ff;
                selection-color: #1b64da;
            }

            QLineEdit, QComboBox {
                background: #ffffff;
                border-radius: 10px;
                border: 1px solid #e5e8eb;
                padding: 9px 14px;
                min-height: 22px;
                color: #191f28;
                selection-background-color: #eff6ff;
                selection-color: #1b64da;
            }

            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
                border: 2px solid #3182f6;
                background: #ffffff;
                padding: 8px 13px;
            }

            QTextEdit:focus {
                padding: 11px 13px;
            }

            QLineEdit:disabled, QComboBox:disabled, QTextEdit:disabled {
                background: #f2f4f6;
                color: #8b95a1;
                border: 1px solid #e5e8eb;
            }

            QComboBox::drop-down {
                border: none;
                width: 28px;
            }

            QComboBox QAbstractItemView {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                border-radius: 10px;
                color: #191f28;
                selection-background-color: #eff6ff;
                selection-color: #1b64da;
                padding: 4px;
            }

            /* ---- Buttons --------------------------------------------- */
            /* Primary: brand background + white text */
            QPushButton {
                border-radius: 10px;
                padding: 9px 22px;
                min-height: 22px;
                color: #ffffff;
                font-weight: 700;
                border: 1px solid #3182f6;
                background: #3182f6;
            }

            QPushButton:hover {
                background: #1b64da;
                border: 1px solid #1b64da;
            }

            QPushButton:pressed {
                background: #1b4ea8;
                border: 1px solid #1b4ea8;
            }

            QPushButton:disabled {
                background: #f2f4f6;
                border: 1px solid #f2f4f6;
                color: #b0b8c1;
            }

            /* Ghost: subtle gray secondary action */
            QPushButton#GhostButton {
                background: #ffffff;
                border: 1px solid #e5e8eb;
                color: #6b7684;
                font-weight: 600;
            }

            QPushButton#GhostButton:hover {
                background: #f2f4f6;
                border: 1px solid #d1d6db;
                color: #333d4b;
            }

            QPushButton#GhostButton:disabled {
                background: #f9fafb;
                border: 1px solid #f2f4f6;
                color: #b0b8c1;
            }

            /* ModeButton: outline-style toggle (mode/source select chips) */
            QPushButton[modeButton="true"] {
                background: #ffffff;
                border: 1.5px solid #e5e8eb;
                color: #6b7684;
                font-weight: 600;
                border-radius: 10px;
            }

            QPushButton[modeButton="true"]:hover {
                background: #f9fafb;
                border: 1.5px solid #b2d4ff;
                color: #1b64da;
            }

            QPushButton[modeButton="true"][modeSelected="true"] {
                background: #eff6ff;
                border: 2px solid #3182f6;
                color: #1b64da;
                font-weight: 700;
            }

            /* Danger: outline red for destructive actions */
            QPushButton#DangerButton {
                background: #ef4444;
                border: 1px solid #ef4444;
                color: #ffffff;
            }

            QPushButton#DangerButton:hover {
                background: #dc2626;
                border: 1px solid #dc2626;
            }

            QPushButton#DangerButton:disabled {
                background: #fef2f2;
                border: 1px solid #fee2e2;
                color: #fca5a5;
            }

            /* ---- Feature input panel (점선 테두리 inline 패널) ------- */
            QFrame#FeatureInputContainer {
                background: #ffffff;
                border-radius: 12px;
                border: 1.5px dashed #b2d4ff;
            }

            QLabel#FeatureLabel {
                font-size: 13px;
                font-weight: 700;
                color: #191f28;
            }

            QLineEdit#FeatureInput {
                background: #f9fafb;
            }

            QLabel#BenchmarkStatusLabel, QLabel#FeatureHintLabel {
                color: #6b7684;
                font-size: 12.5px;
                line-height: 1.5;
            }

            /* ---- Benchmark stage cards ------------------------------- */
            QFrame#BenchmarkStageCard {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }

            QLabel#BenchmarkStageTitle {
                color: #191f28;
                font-size: 24px;
                font-weight: 800;
                letter-spacing: -0.3px;
            }

            QLabel#BenchmarkStageSubtitle {
                color: #6b7684;
                font-size: 13px;
            }

            QLabel#BenchmarkStageMetric {
                color: #191f28;
                font-size: 13px;
                font-weight: 700;
                padding: 10px 12px;
                background: #f9fafb;
                border: 1px solid #e5e8eb;
                border-radius: 10px;
            }

            QFrame#BenchmarkPortalPanel {
                background: #eff6ff;
                border: 1px solid #dbeafe;
                border-radius: 14px;
            }

            QLabel#BenchmarkPortalImage {
                background: transparent;
            }

            QLabel#BenchmarkHeroImage {
                background: #eff6ff;
                border: 1px solid #dbeafe;
                border-radius: 14px;
            }

            QLabel#BenchmarkHeroKicker {
                color: #1b64da;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 0.6px;
            }

            QLabel#BenchmarkHeroHeadline {
                color: #191f28;
                font-size: 20px;
                font-weight: 800;
                line-height: 1.3;
            }

            QLabel#BenchmarkStageChip {
                color: #4e5968;
                font-size: 12px;
                font-weight: 700;
                padding: 6px 12px;
                background: #eff6ff;
                border: 1px solid #dbeafe;
                border-radius: 999px;
            }

            /* ---- Result summary card --------------------------------- */
            QFrame#ResultSummaryCard {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }

            QLabel#ResultSummaryStatus {
                font-size: 18px;
                font-weight: 800;
                color: #191f28;
            }

            QLabel#ResultSummaryMeta {
                color: #6b7684;
                font-size: 12.5px;
            }

            QLabel#ResultSummaryReason {
                color: #333d4b;
                font-size: 13px;
            }

            QLabel#ResultSummaryHint {
                color: #8b95a1;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.2px;
            }

            QLabel[role="stateLabel"] {
                color: #191f28;
                font-size: 13px;
                font-weight: 600;
            }

            QLabel[role="empty-state"] {
                color: #8b95a1;
                font-size: 13px;
                padding: 20px;
            }

            QTextEdit#ResultTimelineView {
                background: #f9fafb;
                border-radius: 10px;
                border: 1px solid #e5e8eb;
                color: #333d4b;
                padding: 12px 14px;
            }

            QFrame#ResultScreenshotCard {
                background: #f9fafb;
                border-radius: 12px;
                border: 1px solid #e5e8eb;
            }

            QLabel#ResultScreenshotThumb {
                background: #ffffff;
                border-radius: 8px;
                border: 1px solid #e5e8eb;
                padding: 4px;
            }

            /* ---- Splitter handle (drag to resize browser preview) ---- */
            QSplitter::handle {
                background: transparent;
            }

            QSplitter::handle:horizontal {
                background: #e5e8eb;
                width: 4px;
                margin: 10px 6px;
                border-radius: 2px;
            }

            QSplitter::handle:horizontal:hover {
                background: #3182f6;
            }

            QSplitter::handle:horizontal:pressed {
                background: #1b64da;
            }

            /* ---- Scenario card (test scenario row) ------------------- */
            QFrame#ScenarioCard {
                background: #ffffff;
                border-radius: 12px;
                border: 1px solid #e5e8eb;
            }

            QFrame#ScenarioCard:hover {
                border: 1.5px solid #b2d4ff;
            }

            QLabel#ScenarioId {
                font-weight: 700;
                font-size: 12px;
                color: #6b7684;
            }

            QLabel#ScenarioTitle {
                font-size: 14px;
                font-weight: 700;
                color: #191f28;
            }

            QLabel[role="step-text"] {
                color: #6b7684;
                font-size: 12.5px;
            }

            QLabel[role="assertion-text"] {
                color: #1b64da;
                font-weight: 700;
                font-size: 12.5px;
            }

            /* ---- Priority pills -------------------------------------- */
            QLabel[role="priority-pill"] {
                padding: 4px 10px;
                border-radius: 999px;
                font-size: 10.5px;
                letter-spacing: 0.5px;
                font-weight: 800;
            }

            QLabel[role="priority-pill"][priority="MUST"] {
                background: #fef2f2;
                color: #ef4444;
            }

            QLabel[role="priority-pill"][priority="SHOULD"] {
                background: #fffbeb;
                color: #f59e0b;
            }

            QLabel[role="priority-pill"][priority="MAY"] {
                background: #ecfdf5;
                color: #10b981;
            }

            /* ---- Logs controls bar ----------------------------------- */
            QFrame#LogsControls {
                background: transparent;
            }

            /* ---- Progress cards (Step 3 metrics) --------------------- */
            QFrame#OverallProgressCard {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }

            QLabel#OverallProgressDetail {
                font-size: 14px;
                color: #191f28;
                font-weight: 700;
            }

            QFrame#ScenarioProgressPanel {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
            }

            QScrollArea#ScenarioProgressScroll,
            QScrollArea#StageScrollArea {
                background: transparent;
                border: none;
            }

            QScrollArea#StageScrollArea > QWidget > QWidget {
                background: transparent;
            }

            QWidget#ScenarioProgressContent {
                background: transparent;
            }

            QFrame#TestProgressBadge {
                background: transparent;
            }

            QLabel#TestProgressCode {
                font-weight: 700;
                color: #191f28;
                font-size: 12px;
            }

            /* ---- Busy overlay & dialog-like container ---------------- */
            QFrame#BusyOverlay {
                background: rgba(25, 31, 40, 0.28);
            }

            QFrame#OverlayContainer {
                background: #ffffff;
                border-radius: 14px;
                border: 1px solid #e5e8eb;
                min-width: 320px;
            }

            QLabel#OverlayLabel {
                color: #191f28;
                font-size: 14px;
                font-weight: 700;
            }

            QLabel#OverlayElapsedLabel {
                color: #3182f6;
                font-size: 24px;
                font-weight: 800;
                margin-top: 8px;
            }

            QLabel#OverlayHintLabel {
                color: #6b7684;
                font-size: 12px;
                font-weight: 500;
                margin-top: 4px;
            }

            /* ---- Scrollbars (subtle, Toss-style) --------------------- */
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 4px 2px 4px 2px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background: #d1d6db;
                border-radius: 4px;
                min-height: 32px;
            }

            QScrollBar::handle:vertical:hover {
                background: #b0b8c1;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
                background: transparent;
                border: none;
            }

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }

            QScrollBar:horizontal {
                background: transparent;
                height: 10px;
                margin: 2px 4px 2px 4px;
                border: none;
            }

            QScrollBar::handle:horizontal {
                background: #d1d6db;
                border-radius: 4px;
                min-width: 32px;
            }

            QScrollBar::handle:horizontal:hover {
                background: #b0b8c1;
            }

            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                width: 0px;
                background: transparent;
                border: none;
            }

            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: transparent;
            }

            /* ---- ToolTip --------------------------------------------- */
            QToolTip {
                background-color: #191f28;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            """
        )

        self._workflow_stack: QStackedWidget
        self._setup_page: QWidget
        self._benchmark_page: QWidget
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
        self._browser_card: QFrame | None = None
        self._main_splitter: QSplitter | None = None
        self._browser_preview_enabled: bool = False
        self._workflow_stage: str
        self._selected_run_mode: str = "quick"
        self._selected_input_source: str = "none"
        self._control_channel: str = "local"
        self._full_execution_logs: List[str] = []
        self._log_mode: str = "summary"  # "summary" or "full"
        self._is_busy: bool
        self._busy_overlay: BusyOverlay | None = None
        self._screencast_client: ScreencastClient | None = None
        self._screencast_started: bool = False
        self._overall_progress_widget: CircularProgressWidget | None = None
        self._overall_progress_detail: QLabel | None = None
        self._test_progress_layout: QGridLayout | None = None
        self._test_progress_empty_label: QLabel | None = None

        self._log_output = None
        self._view_logs_button = None
        self._is_busy = False
        self._workflow_stage = "site_selection"
        self._benchmark_catalog: list[dict[str, Any]] = []
        self._selected_benchmark_site_key: str = ""
        self._selected_benchmark_url: str = ""
        # 선택된 시나리오 ID 리스트 (None = 전체 실행) — controller가 BenchmarkWorker 생성 시 읽음
        self._pending_scenario_ids: list[str] | None = None
        self._site_cards: list[SiteCard] = []
        self._selected_site_url: str = ""
        # Favicon 비동기 fetch용 네트워크 매니저 (전체 카드 공유)
        self._favicon_manager = QNetworkAccessManager(self)
        # Step 3 ExecRunCard용 elapsed timer
        self._elapsed_seconds: int = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._on_elapsed_tick)
        # 시나리오 캐시 (현재 실행 케이스 표시용)
        self._scenarios_by_id: dict[str, Any] = {}
        # Live preview file watcher — agent가 dump하는 screenshot을 polling하여 browser_view에 표시
        self._live_preview_timer = QTimer(self)
        self._live_preview_timer.setInterval(1200)  # 1.2초마다 폴링
        self._live_preview_timer.timeout.connect(self._poll_live_preview_file)
        self._live_preview_path: Path | None = None
        self._live_preview_last_mtime: float = 0.0
        self._build_layout()
        self._setup_screencast()

        if controller_factory:
            controller_factory(self)

    # ------------------------------------------------------------------
    # UI 구성 헬퍼
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        central = QWidget(self)
        central.setObjectName("CentralRoot")
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── 사이드바 + 우측 컨테이너 사이를 QSplitter로 분할 (사이드바 resize 가능) ─
        self._root_splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self._root_splitter.setObjectName("RootSplitter")
        self._root_splitter.setHandleWidth(2)
        self._root_splitter.setChildrenCollapsible(False)

        # ── 좌측: 사이드바 (resizable, 180~360px) ──────────────────────
        self._sidebar_panel = self._create_sidebar(self._root_splitter)
        self._root_splitter.addWidget(self._sidebar_panel)

        # ── 우측: 상단 stepper 헤더 + 메인 컨텐츠 ─────────────────────
        right_container = QWidget(self._root_splitter)
        right_container.setObjectName("RightContainer")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._top_header = self._create_top_header(right_container)
        right_layout.addWidget(self._top_header)

        content_area = QWidget(right_container)
        content_area_layout = QVBoxLayout(content_area)
        content_area_layout.setContentsMargins(20, 16, 20, 20)
        content_area_layout.setSpacing(0)

        # 신규 레이아웃: 외부 브라우저 패널 제거 — Step 3 페이지가 자체 브라우저 + 로그를 호스팅.
        # workflow_stack만 전체 너비를 차지.
        control_panel = QFrame(content_area)
        control_panel.setObjectName("SidePanel")
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(22, 22, 22, 22)
        control_layout.setSpacing(16)

        # Browser view를 먼저 생성하여 Step 3가 ownership을 가짐 (controller load_url 호환).
        self._browser_view = _build_browser_view(control_panel)
        self._browser_preview_enabled = bool(
            getattr(self._browser_view, "_gaia_preview_enabled", False)
        )
        self._browser_view.setUrl(QUrl("about:blank"))
        # 호환을 위해 legacy 참조 유지 (None 또는 dummy)
        self._main_splitter = None
        self._browser_card = None

        self._workflow_stack = QStackedWidget(control_panel)
        # Step 1: 사이트 선택 (Toss-style 그리드)
        self._site_selection_page = self._create_site_selection_stage(control_panel)
        # Step 2: 테스트 케이스 선택 (체크박스 row + footer)
        self._test_case_page = self._create_test_case_stage(control_panel)
        # 기존 setup_page — controller 호환용으로 widget tree에 유지
        self._setup_page = self._create_setup_stage(control_panel)
        self._benchmark_page = self._create_benchmark_stage(control_panel)
        self._review_page = self._create_review_stage(control_panel)
        self._exploration_page = ExplorationViewer(control_panel)
        self._exploration_page.back_requested.connect(self.show_site_selection_stage)
        self._exploration_page.replay_requested.connect(self._show_replay_html)
        self._workflow_stack.addWidget(self._site_selection_page)
        self._workflow_stack.addWidget(self._test_case_page)
        self._workflow_stack.addWidget(self._setup_page)
        self._workflow_stack.addWidget(self._benchmark_page)
        self._workflow_stack.addWidget(self._review_page)
        self._workflow_stack.addWidget(self._exploration_page)
        control_layout.addWidget(self._workflow_stack, stretch=1)

        content_area_layout.addWidget(control_panel, stretch=1)
        right_layout.addWidget(content_area, stretch=1)

        self._root_splitter.addWidget(right_container)
        # 좌측 stretch=0 (Fixed-ish), 우측 stretch=1 (남는 공간 모두 흡수)
        self._root_splitter.setStretchFactor(0, 0)
        self._root_splitter.setStretchFactor(1, 1)
        self._root_splitter.setSizes([240, 1040])
        root_layout.addWidget(self._root_splitter)

        self.setCentralWidget(central)
        self._busy_overlay = BusyOverlay(central)
        self._busy_overlay.setGeometry(central.rect())
        self._busy_overlay.hide()
        self._busy_overlay.raise_()

        self._last_plan_directory = Path.cwd() / "artifacts" / "plans"
        self.set_selected_run_mode("quick")
        self.set_selected_input_source("none")
        self.set_control_channel("local")
        self.show_site_selection_stage()

    # ------------------------------------------------------------------
    # Sidebar / Header 헬퍼 (Toss-style 디자인 시스템)
    # ------------------------------------------------------------------
    def _restyle(self, widget: QWidget) -> None:
        """동적으로 변경된 property를 stylesheet에 즉시 반영."""
        style = widget.style()
        if style is None:
            return
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _create_sidebar(self, parent: QWidget) -> QFrame:
        sidebar = QFrame(parent)
        sidebar.setObjectName("Sidebar")
        # 사이드바 폭 — QSplitter로 사용자가 드래그해서 조절 가능 (180~360px 범위)
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(360)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(0)

        # 브랜드
        brand = QLabel("GAIA", sidebar)
        brand.setObjectName("SidebarBrand")
        layout.addWidget(brand)

        brand_sub = QLabel("AI QA Automation Platform", sidebar)
        brand_sub.setObjectName("SidebarBrandSub")
        layout.addWidget(brand_sub)

        layout.addSpacing(28)

        # 워크플로 3단계 (state는 _update_workflow_indicators에서 갱신)
        self._sidebar_steps: list[QFrame] = []
        step_definitions = [
            ("1", "사이트 선택"),
            ("2", "테스트 케이스 선택"),
            ("3", "테스트 진행"),
        ]
        self._sidebar_connectors: list[QFrame] = []
        for idx, (number, label) in enumerate(step_definitions):
            step_row = self._create_sidebar_step(sidebar, number, label)
            self._sidebar_steps.append(step_row)
            layout.addWidget(step_row)
            if idx < len(step_definitions) - 1:
                connector_holder = QWidget(sidebar)
                connector_layout = QHBoxLayout(connector_holder)
                connector_layout.setContentsMargins(21, 2, 0, 2)  # dot 중앙(14)에 맞춤
                connector_layout.setSpacing(0)
                connector = QFrame(connector_holder)
                connector.setObjectName("SidebarStepConnector")
                connector.setFixedWidth(2)
                connector.setFixedHeight(18)
                connector_layout.addWidget(connector)
                connector_layout.addStretch()
                self._sidebar_connectors.append(connector)
                layout.addWidget(connector_holder)

        layout.addSpacing(20)

        # 분리선
        divider = QFrame(sidebar)
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        layout.addSpacing(16)

        # 보조 메뉴 — 실 동작 있는 항목만 (설정은 backing 없으니 생략)
        history_btn = self._create_sidebar_menu(sidebar, "", "테스트 히스토리")
        history_btn.clicked.connect(self.show_exploration_results)
        layout.addWidget(history_btn)

        sites_btn = self._create_sidebar_menu(sidebar, "", "사이트 관리")
        sites_btn.clicked.connect(self._emit_benchmark_manage)
        layout.addWidget(sites_btn)

        layout.addStretch(1)

        return sidebar

    def _create_sidebar_step(self, parent: QWidget, number: str, label: str) -> QFrame:
        row = QFrame(parent)
        row.setObjectName("SidebarStepRow")
        row.setProperty("state", "pending")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(7, 6, 8, 6)
        row_layout.setSpacing(12)

        dot = QLabel(number, row)
        dot.setObjectName("SidebarStepDot")
        dot.setFixedSize(28, 28)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setProperty("state", "pending")
        row_layout.addWidget(dot)

        text = QLabel(label, row)
        text.setObjectName("SidebarStepLabel")
        text.setProperty("state", "pending")
        row_layout.addWidget(text, stretch=1)

        # state 갱신을 위해 위젯 참조 저장
        row.setProperty("_dot_number", number)
        # Python attribute으로 보관 (Qt 스타일시트 selector와 충돌 없음)
        row._gaia_dot = dot       # type: ignore[attr-defined]
        row._gaia_label = text    # type: ignore[attr-defined]
        row._gaia_number = number # type: ignore[attr-defined]
        return row

    def _create_sidebar_menu(self, parent: QWidget, icon: str, label: str) -> QPushButton:
        # 이모지 제거 — 깨끗한 텍스트만 표시 (icon 인자는 향후 SVG 확장용으로 시그니처 유지)
        btn = QPushButton(f"  {label}", parent)
        btn.setObjectName("SidebarMenuItem")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _create_user_card(self, parent: QWidget) -> QFrame:
        card = QFrame(parent)
        card.setObjectName("SidebarUserCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        avatar = QLabel("G", card)
        avatar.setObjectName("SidebarUserAvatar")
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(avatar)

        name = QLabel("GAIA", card)
        name.setObjectName("SidebarUserName")
        layout.addWidget(name, stretch=1)

        return card

    def _create_top_header(self, parent: QWidget) -> QFrame:
        header = QFrame(parent)
        header.setObjectName("TopHeader")
        header.setFixedHeight(64)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(12)

        layout.addStretch(1)

        # Stepper pill (가운데)
        self._stepper_pill = QFrame(header)
        self._stepper_pill.setObjectName("StepperPill")
        pill_layout = QHBoxLayout(self._stepper_pill)
        pill_layout.setContentsMargins(14, 7, 14, 7)
        pill_layout.setSpacing(10)

        self._stepper_steps: list[QFrame] = []
        self._stepper_bars: list[QFrame] = []
        step_labels = [
            ("1", "사이트 선택"),
            ("2", "테스트 케이스 선택"),
            ("3", "테스트 진행"),
        ]
        for idx, (number, label) in enumerate(step_labels):
            if idx > 0:
                bar = QFrame(self._stepper_pill)
                bar.setObjectName("StepperBar")
                bar.setFixedSize(28, 2)
                bar.setProperty("state", "pending")
                pill_layout.addWidget(bar, alignment=Qt.AlignmentFlag.AlignVCenter)
                self._stepper_bars.append(bar)

            step_widget = self._create_stepper_step(self._stepper_pill, number, label)
            self._stepper_steps.append(step_widget)
            pill_layout.addWidget(step_widget)

        layout.addWidget(self._stepper_pill)
        layout.addStretch(1)

        return header

    def _create_stepper_step(self, parent: QWidget, number: str, label: str) -> QFrame:
        row = QFrame(parent)
        row.setObjectName("StepperStep")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        dot = QLabel(number, row)
        dot.setObjectName("StepperDot")
        dot.setFixedSize(22, 22)
        dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot.setProperty("state", "pending")
        row_layout.addWidget(dot)

        text = QLabel(label, row)
        text.setObjectName("StepperLabel")
        text.setProperty("state", "pending")
        row_layout.addWidget(text)

        row._gaia_dot = dot         # type: ignore[attr-defined]
        row._gaia_label = text      # type: ignore[attr-defined]
        row._gaia_number = number   # type: ignore[attr-defined]
        return row

    def _update_workflow_indicators(self, active_index: int) -> None:
        """active_index: 0=사이트선택, 1=테스트케이스, 2=테스트진행, -1=비활성."""
        states: list[str] = []
        for i in range(3):
            if active_index < 0:
                states.append("pending")
            elif i < active_index:
                states.append("done")
            elif i == active_index:
                states.append("active")
            else:
                states.append("pending")

        # 사이드바 step 갱신
        for idx, step in enumerate(getattr(self, "_sidebar_steps", []) or []):
            state = states[idx]
            dot = getattr(step, "_gaia_dot", None)
            text_lbl = getattr(step, "_gaia_label", None)
            number = getattr(step, "_gaia_number", str(idx + 1))
            if dot is not None:
                dot.setText("✓" if state == "done" else number)
                dot.setProperty("state", state)
                self._restyle(dot)
            if text_lbl is not None:
                text_lbl.setProperty("state", state)
                self._restyle(text_lbl)
            step.setProperty("state", state)
            self._restyle(step)

        # 사이드바 connector 갱신 (앞 step이 done이면 채워짐)
        for idx, connector in enumerate(getattr(self, "_sidebar_connectors", []) or []):
            state = "done" if states[idx] == "done" else "pending"
            connector.setProperty("state", state)
            self._restyle(connector)

        # 상단 stepper pill 갱신
        for idx, step in enumerate(getattr(self, "_stepper_steps", []) or []):
            state = states[idx]
            dot = getattr(step, "_gaia_dot", None)
            text_lbl = getattr(step, "_gaia_label", None)
            number = getattr(step, "_gaia_number", str(idx + 1))
            if dot is not None:
                dot.setText("✓" if state == "done" else number)
                dot.setProperty("state", state)
                self._restyle(dot)
            if text_lbl is not None:
                text_lbl.setProperty("state", state)
                self._restyle(text_lbl)

        # 상단 stepper bar 갱신
        for idx, bar in enumerate(getattr(self, "_stepper_bars", []) or []):
            state = "done" if states[idx] == "done" else "pending"
            bar.setProperty("state", state)
            self._restyle(bar)

    # ------------------------------------------------------------------
    # Step 1 — 사이트 선택 페이지 (Toss-style 그리드)
    # ------------------------------------------------------------------
    def _create_site_selection_stage(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setObjectName("StageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget(scroll)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # 페이지 제목 / 부제
        title = QLabel("어느 사이트를 테스트할까요?", page)
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        subtitle = QLabel("사이트를 선택하면 곧바로 다음 단계로 넘어갑니다.", page)
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        # 검색 + 카테고리 + "+ 직접 URL 입력하기"
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        self._site_search_input = QLineEdit(page)
        self._site_search_input.setPlaceholderText("사이트 검색 또는 URL 입력")
        self._site_search_input.textChanged.connect(self._filter_site_grid)
        controls_row.addWidget(self._site_search_input, stretch=1)

        self._site_category = QComboBox(page)
        # 현재는 모두 기본 사이트 → "전체"/"기본"만 의미가 있음. 커스텀 모드는 향후 확장 시.
        self._site_category.addItems(["전체", "기본"])
        self._site_category.setMinimumWidth(120)
        # 카테고리 변경 시 현재 검색어로 재필터 (변경의 시각적 피드백)
        self._site_category.currentTextChanged.connect(
            lambda _t: self._filter_site_grid(self._site_search_input.text() if hasattr(self, "_site_search_input") else "")
        )
        controls_row.addWidget(self._site_category)

        add_url_btn = QPushButton("+ 직접 URL 입력하기", page)
        add_url_btn.setObjectName("AddUrlOutlineButton")
        add_url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_url_btn.clicked.connect(self._focus_direct_url_input)
        controls_row.addWidget(add_url_btn)

        layout.addLayout(controls_row)

        # 등록된 사이트 (섹션 제목)
        sites_label = QLabel("등록된 사이트", page)
        sites_label.setObjectName("SectionHeading")
        layout.addWidget(sites_label)

        # 사이트 카드 그리드 (4열, 자연 스크롤)
        self._site_grid_container = QWidget(page)
        self._site_grid_layout = QGridLayout(self._site_grid_container)
        self._site_grid_layout.setContentsMargins(0, 0, 0, 0)
        self._site_grid_layout.setHorizontalSpacing(12)
        self._site_grid_layout.setVerticalSpacing(12)
        layout.addWidget(self._site_grid_container)

        self._populate_site_grid(DEFAULT_SITE_CATALOG)

        layout.addSpacing(10)

        # 직접 URL로 시작하기 panel (점선 테두리)
        direct_panel = QFrame(page)
        direct_panel.setObjectName("DirectUrlPanel")
        direct_layout = QHBoxLayout(direct_panel)
        direct_layout.setContentsMargins(20, 16, 20, 16)
        direct_layout.setSpacing(14)

        globe = QLabel("⊕", direct_panel)
        globe.setObjectName("DirectUrlGlobe")
        direct_layout.addWidget(globe, alignment=Qt.AlignmentFlag.AlignTop)

        text_col = QWidget(direct_panel)
        text_col_layout = QVBoxLayout(text_col)
        text_col_layout.setContentsMargins(0, 0, 0, 0)
        text_col_layout.setSpacing(2)
        direct_title = QLabel("직접 URL로 시작하기", text_col)
        direct_title.setObjectName("DirectUrlTitle")
        text_col_layout.addWidget(direct_title)
        direct_desc = QLabel(
            "등록되지 않은 사이트도 바로 테스트할 수 있어요.",
            text_col,
        )
        direct_desc.setObjectName("DirectUrlDescription")
        direct_desc.setWordWrap(True)
        text_col_layout.addWidget(direct_desc)
        direct_layout.addWidget(text_col, stretch=1)

        # URL 입력 — 기존 self._url_input 을 여기에 배치 (controller 호환)
        self._url_input = QLineEdit(direct_panel)
        self._url_input.setPlaceholderText("https://example.com")
        self._url_input.setClearButtonEnabled(True)
        self._url_input.setMinimumWidth(240)
        self._url_input.returnPressed.connect(self._submit_direct_url)
        direct_layout.addWidget(self._url_input, stretch=2)

        submit_btn = QPushButton("→", direct_panel)
        submit_btn.setObjectName("DirectUrlSubmit")
        submit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        submit_btn.clicked.connect(self._submit_direct_url)
        direct_layout.addWidget(submit_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(direct_panel)
        layout.addStretch(1)

        scroll.setWidget(page)
        return scroll

    def _populate_site_grid(self, catalog: Sequence[Mapping[str, str]]) -> None:
        """사이트 카드 그리드를 새로 렌더링 (반응형 컬럼 수)."""
        # 카탈로그 캐시 (resize 시 재배치용)
        self._site_catalog_cache = [dict(item) for item in catalog]

        # 기존 카드 제거
        while self._site_grid_layout.count():
            item = self._site_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._site_cards = []

        for entry in catalog:
            card = SiteCard(
                label=str(entry.get("label", "")),
                url=str(entry.get("url", "")),
                initial=str(entry.get("initial", "")),
                color=str(entry.get("color", "#3182f6")),
                parent=self._site_grid_container,
                network_manager=getattr(self, "_favicon_manager", None),
            )
            card.clicked.connect(self._on_site_card_clicked)
            self._site_cards.append(card)

        self._rearrange_site_grid()

    def _rearrange_site_grid(self) -> None:
        """현재 컨테이너 너비에 맞춰 카드 컬럼 수를 재계산하고 재배치."""
        if not getattr(self, "_site_cards", None):
            return
        # 카드들을 layout에서 떼어냄 (deleteLater 안 함 — 카드는 살아있음)
        for card in self._site_cards:
            self._site_grid_layout.removeWidget(card)

        # 컨테이너 너비 기준으로 컬럼 수 결정 (최소 너비 220px, 간격 12px)
        width = max(1, self._site_grid_container.width())
        min_card = 220
        gap = 12
        cols = max(1, (width + gap) // (min_card + gap))
        cols = min(cols, 5)  # 최대 5열로 제한

        for idx, card in enumerate(self._site_cards):
            self._site_grid_layout.addWidget(card, idx // cols, idx % cols)

        # 마지막 row 아래에 stretch — 카드가 위로 정렬되게
        last_row = (len(self._site_cards) + cols - 1) // cols
        for r in range(last_row + 1):
            self._site_grid_layout.setRowStretch(r, 0)
        self._site_grid_layout.setRowStretch(last_row, 1)
        # 컬럼 stretch 균등화
        for c in range(cols):
            self._site_grid_layout.setColumnStretch(c, 1)

    def _filter_site_grid(self, query: str) -> None:
        """검색어에 따라 카드 가시성 토글."""
        q = (query or "").strip().lower()
        for card in self._site_cards:
            if not q:
                card.setVisible(True)
                continue
            label = card.findChild(QLabel, "SiteCardName")
            url_lbl = card.findChild(QLabel, "SiteCardURL")
            haystack = ""
            if label is not None:
                haystack += label.text().lower() + " "
            if url_lbl is not None:
                haystack += url_lbl.text().lower()
            card.setVisible(q in haystack)

    def _focus_direct_url_input(self) -> None:
        if hasattr(self, "_url_input") and self._url_input is not None:
            self._url_input.setFocus()
            self._url_input.selectAll()

    def _submit_direct_url(self) -> None:
        url = ""
        if hasattr(self, "_url_input") and self._url_input is not None:
            url = self._url_input.text().strip()
        if not url:
            return
        self._selected_site_url = url
        # 등록된 사이트면 site_key 매칭, 아니면 빈 문자열 (커스텀 URL)
        self._selected_benchmark_site_key = self._resolve_site_key_for_url(url)
        self._selected_benchmark_url = url
        self.urlSubmitted.emit(url)
        # 케이스 목록 갱신 (site_key 매칭 안 되면 DEFAULT로 폴백)
        self.refresh_test_cases_for_site()
        # 곧바로 다음 단계로
        self.show_setup_stage_with_browser()

    def _on_site_card_clicked(self, url: str) -> None:
        """사이트 카드 1클릭 = URL 채우고 즉시 Step 2 (테스트 케이스 선택)."""
        self._selected_site_url = url
        # benchmark catalog에서 site_key 매칭 (벤치 실행 시 필요)
        self._selected_benchmark_site_key = self._resolve_site_key_for_url(url)
        self._selected_benchmark_url = url
        if hasattr(self, "_url_input") and self._url_input is not None:
            self._url_input.setText(url)
        # 카드 selected 상태 갱신
        for card in self._site_cards:
            card.set_selected(card._url == url)  # noqa: SLF001
        self.urlSubmitted.emit(url)
        # 선택된 사이트의 실제 벤치마크 시나리오로 케이스 목록 갱신
        self.refresh_test_cases_for_site()
        self.show_test_case_stage()

    def _resolve_site_key_for_url(self, url: str) -> str:
        """benchmark catalog에서 url과 일치하는 site_key를 찾음. 없으면 빈 문자열."""
        clean = (url or "").strip().rstrip("/")
        if not clean:
            return ""
        for item in self._benchmark_catalog or []:
            default_url = str(item.get("default_url") or "").strip().rstrip("/")
            if default_url and default_url == clean:
                return str(item.get("key") or "")
        return ""

    @staticmethod
    def _guess_site_initial(label: str) -> str:
        """라벨에서 1~2자 이니셜 추출 (Toss-style 브랜드 배지용)."""
        text = (label or "").strip()
        if not text:
            return "?"
        # 알려진 한글 → 영문 이니셜 매핑
        special = {
            "네이버": "N",
            "유튜브": "▶",
            "위키피디아": "W",
            "위키": "W",
            "다음": "D",
            "카카오": "K",
            "11번가": "11",
            "디시인사이드": "DC",
            "애플": "🍎",
            "네이버 뉴스": "N",
            "KBS 뉴스": "K",
            "MBC 뉴스": "M",
            "SBS 뉴스": "S",
        }
        for k, v in special.items():
            if text.startswith(k):
                return v
        # ASCII면 앞 2자, 한글이면 앞 1자
        first = text[0]
        if first.isascii():
            return text[:2].upper()
        return first

    @staticmethod
    def _guess_brand_color(key: str) -> str:
        """site_key별 브랜드 색상 매핑 (fallback은 해시 기반 결정)."""
        palette = {
            "naver": "#03c75a",
            "wikipedia": "#000000",
            "youtube": "#ff0000",
            "github": "#181717",
            "daum": "#0066ff",
            "kakao_map": "#fae100",
            "11st": "#ff1a1a",
            "dcinside": "#0066c0",
            "naver_news": "#03c75a",
            "kbs": "#0064b0",
            "mbc": "#3d3d3d",
            "sbs": "#0066cc",
            "ytn": "#000000",
            "apple_store": "#1d1d1f",
            "hacker_news": "#ff6600",
            "fow_kr": "#ff7e1f",
            "pypi": "#3775a9",
            "inu_timetable": "#3182f6",
            "moneytoring": "#5b21b6",
        }
        clean = (key or "").strip().lower()
        if clean in palette:
            return palette[clean]
        # 결정적 fallback — 키 해시로 컬러 풀에서 선택
        colors = ["#3182f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4"]
        return colors[abs(hash(clean)) % len(colors)] if clean else "#3182f6"

    # ------------------------------------------------------------------
    # Step 2 — 테스트 케이스 선택 페이지
    # ------------------------------------------------------------------
    def _create_test_case_stage(self, parent: QWidget) -> QWidget:
        container = QWidget(parent)
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 스크롤 영역 (rows)
        scroll = QScrollArea(container)
        scroll.setObjectName("StageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget(scroll)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # 페이지 제목 / 부제
        title = QLabel("어떤 테스트를 실행할까요?", page)
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        subtitle = QLabel("케이스를 선택한 뒤 \"선택 완료\"를 누르세요.", page)
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addSpacing(6)

        # 탭 + 검색 + 카테고리 행
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)

        self._case_tab_buttons: dict[str, QPushButton] = {}
        for key, label in (("recommended", "추천 케이스"), ("all", "전체 케이스"), ("favorite", "즐겨찾기")):
            btn = QPushButton(label, page)
            btn.setObjectName("CaseTabButton")
            btn.setProperty("active", key == "recommended")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, k=key: self._set_case_tab(k))
            controls_row.addWidget(btn)
            self._case_tab_buttons[key] = btn

        controls_row.addStretch(1)

        self._case_search_input = QLineEdit(page)
        self._case_search_input.setPlaceholderText("테스트 케이스 검색")
        self._case_search_input.setMaximumWidth(280)
        self._case_search_input.textChanged.connect(self._filter_case_rows)
        controls_row.addWidget(self._case_search_input)

        self._case_category = QComboBox(page)
        self._case_category.addItems(["전체 카테고리", "기본", "검색", "AI"])
        self._case_category.setMinimumWidth(140)
        # 카테고리 변경 시 케이스 행 재필터링 (search query는 그대로 유지)
        self._case_category.currentTextChanged.connect(
            lambda _text: self._filter_case_rows(self._case_search_input.text() if hasattr(self, "_case_search_input") else "")
        )
        controls_row.addWidget(self._case_category)

        layout.addLayout(controls_row)

        # ─── 섹션 1: 등록된 테스트 케이스 (벤치마크 시나리오) ─────────
        section_main_label = QLabel("등록된 테스트 케이스", page)
        section_main_label.setObjectName("CaseSectionLabel")
        layout.addWidget(section_main_label)

        self._case_rows_container = QWidget(page)
        self._case_rows_layout = QVBoxLayout(self._case_rows_container)
        self._case_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._case_rows_layout.setSpacing(10)
        layout.addWidget(self._case_rows_container)

        # ─── 섹션 2: AI 자율 실행 (사용자 입력 기반) ─────────────────
        # 구분 spacing
        layout.addSpacing(20)
        section_freeform_label = QLabel("AI 자율 실행 (사용자 입력 기반)", page)
        section_freeform_label.setObjectName("CaseSectionLabel")
        layout.addWidget(section_freeform_label)
        section_freeform_sub = QLabel(
            "직접 목표를 입력하거나 기획서 파일을 업로드해 AI에게 단독 실행을 맡길 수 있어요.",
            page,
        )
        section_freeform_sub.setObjectName("CaseSectionSubLabel")
        section_freeform_sub.setWordWrap(True)
        layout.addWidget(section_freeform_sub)

        self._freeform_rows_container = QWidget(page)
        self._freeform_rows_layout = QVBoxLayout(self._freeform_rows_container)
        self._freeform_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._freeform_rows_layout.setSpacing(10)
        layout.addWidget(self._freeform_rows_container)

        # 두 섹션 모두 row 보관 — _selected_case_rows()는 둘 다 합쳐서 반환
        self._case_rows: list[TestCaseRow] = []           # 섹션 1 (테스트 케이스)
        self._freeform_rows: list[TestCaseRow] = []       # 섹션 2 (AI 자율)
        self._current_case_tab: str = "recommended"
        self._populate_test_case_rows(DEFAULT_TEST_CASES)

        layout.addStretch(1)

        scroll.setWidget(page)
        root_layout.addWidget(scroll, stretch=1)

        # ── 하단 footer ───────────────────────────────────────────────
        footer = QFrame(container)
        footer.setObjectName("CaseStageFooter")
        footer.setFixedHeight(72)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 12, 20, 12)
        footer_layout.setSpacing(14)

        back_btn = QPushButton("← 사이트 변경", footer)
        back_btn.setObjectName("GhostButton")
        back_btn.clicked.connect(self.show_site_selection_stage)
        footer_layout.addWidget(back_btn)

        footer_layout.addStretch(1)

        self._case_selection_summary = QLabel("선택된 케이스 0개", footer)
        self._case_selection_summary.setObjectName("FooterSummary")
        footer_layout.addWidget(self._case_selection_summary)

        self._case_eta_summary = QLabel("예상 - 분", footer)
        self._case_eta_summary.setObjectName("FooterSummaryStrong")
        footer_layout.addWidget(self._case_eta_summary)

        self._case_complete_button = QPushButton("선택 완료  →", footer)
        self._case_complete_button.setObjectName("FooterPrimaryButton")
        self._case_complete_button.setEnabled(False)
        self._case_complete_button.clicked.connect(self._on_case_selection_complete)
        footer_layout.addWidget(self._case_complete_button)

        root_layout.addWidget(footer)

        return container

    def _populate_test_case_rows(self, cases: Sequence[Mapping[str, Any]]) -> None:
        """테스트 케이스 row를 새로 렌더링.

        섹션 분리:
          - 섹션 1 (_case_rows_layout): mode in {benchmark, ai} → 등록된 테스트 케이스
          - 섹션 2 (_freeform_rows_layout): mode in {quick, bundle} → AI 자율 실행
        """
        # 양쪽 컨테이너 초기화
        for layout in (self._case_rows_layout, getattr(self, "_freeform_rows_layout", None)):
            if layout is None:
                continue
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._case_rows = []
        self._freeform_rows = []

        for case in cases:
            mode = str(case.get("mode", "benchmark"))
            # quick/bundle은 freeform 섹션, 나머지는 메인 섹션
            is_freeform = mode in ("quick", "bundle")
            target_parent = self._freeform_rows_container if is_freeform else self._case_rows_container
            target_layout = self._freeform_rows_layout if is_freeform else self._case_rows_layout

            row = TestCaseRow(
                case_id=str(case.get("id", "")),
                title=str(case.get("title", "")),
                description=str(case.get("desc", "")),
                eta_text=str(case.get("eta", "")),
                icon_mode=str(case.get("icon_mode", "benchmark")),
                recommended=bool(case.get("recommended", False)),
                favorite=False,
                scenario_id=str(case.get("scenario_id", "")),
                parent=target_parent,
            )
            row.setProperty("mode", mode)
            row.toggled.connect(self._on_case_row_toggled)
            if is_freeform:
                self._freeform_rows.append(row)
            else:
                self._case_rows.append(row)
            target_layout.addWidget(row)

        self._apply_case_tab_filter()
        self._update_case_selection_summary()

    def _load_scenarios_for_site_key(self, site_key: str) -> list[Mapping[str, Any]]:
        """주어진 site_key의 suite JSON에서 시나리오 리스트를 로드.

        Returns: scenarios list (각 항목 dict) — 빈 리스트면 suite 없거나 로드 실패.
        """
        if not site_key:
            return []
        # _benchmark_catalog에서 suite_path 찾음
        suite_path_rel = ""
        for item in self._benchmark_catalog or []:
            if str(item.get("key") or "").strip() == site_key:
                suite_path_rel = str(item.get("suite_path") or "").strip()
                break
        if not suite_path_rel:
            return []
        try:
            from pathlib import Path
            import json as _json
            workspace_root = Path(__file__).resolve().parents[3]  # gaia/src/gui → repo root
            suite_path = workspace_root / suite_path_rel
            if not suite_path.exists():
                return []
            data = _json.loads(suite_path.read_text(encoding="utf-8"))
            scenarios = data.get("scenarios") or []
            if not isinstance(scenarios, list):
                return []
            return [s for s in scenarios if isinstance(s, dict) and s.get("id")]
        except Exception:
            return []

    def _build_dynamic_test_cases(self, site_key: str) -> list[dict[str, Any]]:
        """사이트별 동적 테스트 케이스 목록 생성.

        Returns:
            전체 시나리오 실행 카드 1개 + 각 시나리오별 카드 N개 + 부가 모드들 (빠른 목표, AI, 기획서)
        """
        cases: list[dict[str, Any]] = []
        scenarios = self._load_scenarios_for_site_key(site_key)

        if scenarios:
            # 1. "전체 시나리오 실행" — 카탈로그에 등록된 모든 시나리오
            total_min = 0
            total_max = 0
            for s in scenarios:
                budget = int(s.get("time_budget_sec", 0)) or 60
                total_min += max(1, budget // 90)  # rough lower bound (분)
                total_max += max(2, budget // 60)
            cases.append({
                "id": "benchmark_all",
                "mode": "benchmark",
                "icon_mode": "benchmark_all",
                "title": f"전체 시나리오 실행 ({len(scenarios)}개)",
                "desc": "이 사이트에 등록된 모든 시나리오를 순서대로 실행합니다.",
                "eta": f"예상 소요 {total_min}~{total_max}분",
                "recommended": True,
                "scenario_id": "",  # 빈 문자열 → 전체 실행
            })

            # 2. 각 시나리오별 카드 (1:1)
            for s in scenarios:
                budget = int(s.get("time_budget_sec", 0)) or 60
                eta_min = max(1, budget // 90)
                eta_max = max(2, budget // 60)
                desc = str(s.get("goal", "") or "").strip()
                # 너무 길면 단축
                if len(desc) > 110:
                    desc = desc[:107] + "…"
                cases.append({
                    "id": str(s.get("id", "")),
                    "mode": "benchmark",
                    "icon_mode": "benchmark",
                    "title": str(s.get("name", s.get("id", "시나리오"))),
                    "desc": desc or "이 시나리오만 단독 실행합니다.",
                    "eta": f"예상 소요 {eta_min}~{eta_max}분",
                    "recommended": False,
                    "scenario_id": str(s.get("id", "")),
                })

        # 3. 부가 모드들 (사이트 무관) — 빠른 목표, AI 자율 탐색, 기획서
        cases.extend([
            {
                "id": "quick_goal",
                "mode": "quick",
                "icon_mode": "quick",
                "title": "빠른 목표 직접 입력",
                "desc": "AI가 직접 목표를 이해하고 수행합니다 (단일 실행).",
                "eta": "예상 소요 1~3분",
                "recommended": False,
                "scenario_id": "",
            },
            {
                "id": "ai_explore",
                "mode": "ai",
                "icon_mode": "ai",
                "title": "AI 자율 탐색",
                "desc": "AI가 사이트를 자유 탐색하며 이상 여부를 보고합니다.",
                "eta": "예상 소요 5~10분",
                "recommended": False,
                "scenario_id": "",
            },
            {
                "id": "from_spec",
                "mode": "bundle",
                "icon_mode": "spec",
                "title": "기획서 업로드해서 자동 생성",
                "desc": "PDF/DOCX 기획서를 업로드하면 시나리오 자동 생성.",
                "eta": "예상 소요 2~5분",
                "recommended": False,
                "scenario_id": "",
            },
        ])
        return cases

    def refresh_test_cases_for_site(self) -> None:
        """현재 선택된 site_key에 맞춰 테스트 케이스 행을 새로 채움.

        사이트 카드 클릭 → Step 2 진입 직전에 호출. site_key가 없으면 DEFAULT_TEST_CASES 사용.
        """
        site_key = (self._selected_benchmark_site_key or "").strip()
        if site_key:
            cases = self._build_dynamic_test_cases(site_key)
            if cases:
                self._populate_test_case_rows(cases)
                return
        # 폴백 — 정적 default
        self._populate_test_case_rows(DEFAULT_TEST_CASES)

    def _set_case_tab(self, key: str) -> None:
        self._current_case_tab = key
        for k, btn in self._case_tab_buttons.items():
            btn.setProperty("active", k == key)
            style = btn.style()
            if style is not None:
                style.unpolish(btn)
                style.polish(btn)
                btn.update()
        self._apply_case_tab_filter()
        # 검색어와 함께 적용
        self._filter_case_rows(self._case_search_input.text() if hasattr(self, "_case_search_input") else "")

    def _apply_case_tab_filter(self) -> None:
        # 섹션 1 (테스트 케이스) — 탭 필터 적용
        for row in self._case_rows:
            if self._current_case_tab == "recommended":
                row.setVisible(row.is_recommended())
            elif self._current_case_tab == "favorite":
                row.setVisible(row.is_favorite())
            else:
                row.setVisible(True)
        # 섹션 2 (AI 자율 실행) — 탭/카테고리/검색 무관하게 항상 표시
        for row in getattr(self, "_freeform_rows", []):
            row.setVisible(True)

    def _filter_case_rows(self, query: str) -> None:
        q = (query or "").strip().lower()
        # 카테고리 필터 (Step 2 콤보) — "전체 카테고리"는 패스, 외에는 매칭
        cat_sel = ""
        if hasattr(self, "_case_category") and self._case_category is not None:
            cat_sel = self._case_category.currentText().strip()
        # 섹션 1 — 탭 + 카테고리 + 검색 필터 적용
        for row in self._case_rows:
            tab_ok = (
                row.is_recommended() if self._current_case_tab == "recommended"
                else row.is_favorite() if self._current_case_tab == "favorite"
                else True
            )
            if not tab_ok:
                row.setVisible(False)
                continue
            if cat_sel and cat_sel != "전체 카테고리":
                mode = row.icon_mode().lower()
                title = row._title_label.text().lower()  # noqa: SLF001
                cat_ok = False
                if cat_sel == "기본":
                    cat_ok = mode.startswith("benchmark") or mode == "quick"
                elif cat_sel == "검색":
                    cat_ok = "검색" in title or "search" in mode
                elif cat_sel == "AI":
                    cat_ok = mode == "ai" or "ai" in title
                if not cat_ok:
                    row.setVisible(False)
                    continue
            if not q:
                row.setVisible(True)
                continue
            haystack = " ".join([
                row._title_label.text().lower(),  # noqa: SLF001
                row._desc_label.text().lower(),   # noqa: SLF001
            ])
            row.setVisible(q in haystack)
        # 섹션 2 (AI 자율 실행) — 항상 표시 (탭/카테고리/검색 무관)
        for row in getattr(self, "_freeform_rows", []):
            row.setVisible(True)

    def _on_case_row_toggled(self, case_id: str, selected: bool) -> None:
        """체크박스 토글 시 — 상호 배타 처리 + 요약 갱신.

        상호 배타 규칙:
        - "benchmark_all"(전체 시나리오) 선택 → 개별 시나리오 모두 해제
        - 개별 시나리오 선택 → "benchmark_all" 자동 해제
        - 섹션 2(AI 자율 실행: quick/bundle)는 단일 선택 — 하나 선택 시 다른 모두 해제
        - 섹션 2 카드 선택 시 → 섹션 1(테스트 케이스) 모두 자동 해제 (모드 충돌 방지)
        - 섹션 1 카드 선택 시 → 섹션 2 모두 자동 해제
        """
        if not selected:
            self._update_case_selection_summary()
            return

        all_freeform = list(getattr(self, "_freeform_rows", []))
        clicked_in_freeform = any(r.case_id() == case_id for r in all_freeform)

        if clicked_in_freeform:
            # 섹션 2 단일 선택 — 같은 섹션 내 다른 모두 해제 + 섹션 1 전체 해제
            for r in all_freeform:
                if r.case_id() != case_id and r.is_selected():
                    r.set_selected(False)
            for r in self._case_rows:
                if r.is_selected():
                    r.set_selected(False)
        else:
            # 섹션 1 (테스트 케이스) 선택 — 섹션 2 모두 해제 (모드 충돌 방지)
            for r in all_freeform:
                if r.is_selected():
                    r.set_selected(False)
            # 섹션 1 내부 상호 배타 — 전체 시나리오 ↔ 개별 시나리오
            if case_id == "benchmark_all":
                for r in self._case_rows:
                    if r.case_id() != "benchmark_all" and r.scenario_id() and r.is_selected():
                        r.set_selected(False)
            elif case_id and any(r.case_id() == case_id and r.scenario_id() for r in self._case_rows):
                for r in self._case_rows:
                    if r.case_id() == "benchmark_all" and r.is_selected():
                        r.set_selected(False)
        self._update_case_selection_summary()

    def _selected_case_rows(self) -> list[TestCaseRow]:
        # 두 섹션 모두 합쳐서 반환
        return [r for r in self._case_rows if r.is_selected()] + [
            r for r in getattr(self, "_freeform_rows", []) if r.is_selected()
        ]

    def _update_case_selection_summary(self) -> None:
        rows = self._selected_case_rows()
        count = len(rows)
        if hasattr(self, "_case_selection_summary"):
            self._case_selection_summary.setText(f"선택된 케이스 {count}개")
        # 예상 시간 합 — eta 텍스트에서 숫자 추출하여 간단히 누적
        import re
        total_min = 0
        total_max = 0
        for r in rows:
            text = r._eta_pill.text()  # noqa: SLF001
            nums = re.findall(r"\d+", text)
            if len(nums) >= 2:
                total_min += int(nums[0])
                total_max += int(nums[1])
            elif len(nums) == 1:
                total_min += int(nums[0])
                total_max += int(nums[0])
        if hasattr(self, "_case_eta_summary"):
            if count == 0:
                self._case_eta_summary.setText("예상 - 분")
            elif total_min == total_max:
                self._case_eta_summary.setText(f"예상 약 {total_min}분")
            else:
                self._case_eta_summary.setText(f"예상 {total_min}~{total_max}분")
        if hasattr(self, "_case_complete_button"):
            self._case_complete_button.setEnabled(count > 0)

    def _on_case_selection_complete(self) -> None:
        """\"선택 완료\" 클릭 — 모드/시그널 결정 후 Step 3로 자동 전환.

        모드별 분기:
        - benchmark: scenario_ids 저장 + benchmarkRunRequested emit
        - ai: startRequested emit (즉시 시작, 추가 입력 불필요)
        - quick: QInputDialog로 목표 입력받음 → set_feature_query → startRequested
        - bundle: QFileDialog로 기획서 파일 선택받음 → planFileSelected → startRequested
        """
        from PySide6.QtWidgets import QInputDialog, QFileDialog, QMessageBox

        rows = self._selected_case_rows()
        if not rows:
            return
        # Step 3 KPI에 표시할 "선택된 케이스 수" 저장
        self._selected_case_count = len(rows)
        if hasattr(self, "_metric_selected_cases"):
            self._metric_selected_cases.setText(str(len(rows)))
        # 가장 첫 선택된 row의 mode를 채택
        mode = str(rows[0].property("mode") or "benchmark")
        self.set_selected_run_mode(mode)

        if mode == "benchmark":
            # 시나리오 ID 수집 — "전체 시나리오"(scenario_id="")가 포함되면 None (필터 없이 전체 실행)
            scenario_ids: list[str] = []
            run_all = False
            for r in rows:
                sid = r.scenario_id()
                if r.case_id() == "benchmark_all" or not sid:
                    run_all = True
                    break
                scenario_ids.append(sid)
            # controller가 BenchmarkWorker 생성 시 읽어갈 수 있도록 저장
            self._pending_scenario_ids = None if run_all else (scenario_ids or None)

            # 벤치마킹 흐름: site_key + url로 controller에 요청.
            site_key = self._selected_benchmark_site_key or ""
            url = self._selected_benchmark_url or self._selected_site_url or ""
            if site_key:
                self.benchmarkRunRequested.emit(site_key, url)
                return
            # 등록되지 않은 사이트 → ai 자율 탐색으로 대체 (사용자가 어떤 식으로든 진행되도록)
            self.set_selected_run_mode("ai")
            self.append_log(
                "ℹ️ 등록된 벤치 시나리오가 없어 AI 자율 탐색으로 대체 실행합니다."
            )
            self._pending_scenario_ids = None
            self.startRequested.emit()
            return

        # 기본: scenario_ids 초기화
        self._pending_scenario_ids = None

        if mode == "quick":
            # 빠른 목표 — 이미 set 된 feature_query 있으면 그대로 사용, 없으면 dialog로 입력받음
            existing = self.get_feature_query().strip() if hasattr(self, "get_feature_query") else ""
            if not existing:
                goal, ok = QInputDialog.getText(
                    self,
                    "빠른 목표 입력",
                    "AI에게 실행시킬 목표를 한 줄로 입력하세요:\n"
                    "(예: 로그인 후 메인 페이지에서 검색창을 사용해 '아이폰'을 검색)",
                )
                if not ok:
                    return  # 사용자 취소
                goal = goal.strip()
                if not goal:
                    QMessageBox.information(self, "목표 필요", "빠른 목표 모드는 목표 텍스트가 필요합니다.")
                    return
                self.set_feature_query(goal)
            self.startRequested.emit()
            return

        if mode == "bundle":
            # 기획서/번들 업로드 — QFileDialog로 파일 선택받음
            # JSON 번들/플랜은 즉시 로드+실행 가능, PDF/DOCX는 controller의 비동기 분석 후
            # 자동 실행이 안 되므로 별도 안내. 우선 JSON 우선 노출.
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "기획서/번들 파일 선택 (JSON 번들 권장 — PDF는 분석 시간 필요)",
                "",
                "JSON 번들 (*.json);;PDF 기획서 (*.pdf);;Word 문서 (*.docx);;텍스트 (*.md *.txt);;All Files (*)",
            )
            if not file_path:
                return  # 사용자 취소

            from pathlib import Path as _Path
            suffix = _Path(file_path).suffix.lower()
            if suffix == ".json":
                # JSON 번들/플랜 → sync 로드 + 즉시 실행
                self.planFileSelected.emit(file_path)
                self.startRequested.emit()
            elif suffix in (".pdf", ".docx", ".md", ".txt"):
                # PDF/DOCX 등 — controller가 비동기 분석 → 분석 완료 후 사용자가 다시 실행
                self.append_log(
                    f"📄 기획서 분석 시작: {_Path(file_path).name} — 분석 완료 후 자동 실행됩니다."
                )
                self._pending_auto_start_after_analysis = True
                self.fileDropped.emit(file_path)
            else:
                QMessageBox.warning(self, "지원하지 않는 형식",
                                    "JSON 번들, PDF, DOCX, MD, TXT 파일만 지원합니다.")
            return

        # ai 모드 또는 fallback
        self.startRequested.emit()

    def show_test_case_stage(self) -> None:
        """Step 2 — 테스트 케이스 선택 페이지로 이동."""
        self._workflow_stage = "test_case"
        if self._workflow_stack.currentWidget() is not self._test_case_page:
            self._workflow_stack.setCurrentWidget(self._test_case_page)
        if hasattr(self, "_back_to_setup_button"):
            self._back_to_setup_button.setEnabled(False)
        self._set_browser_panel_visible(False)
        self._update_workflow_indicators(1)

    def _create_setup_stage(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setObjectName("StageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget(scroll)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
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

        # 선택된 사이트 (Step 1에서 결정)를 보여주는 작은 라벨 — read-only 표시 + "변경" 버튼
        url_summary_row = QHBoxLayout()
        url_summary_row.setSpacing(8)
        url_summary_label = QLabel("선택된 사이트", page)
        url_summary_label.setObjectName("SectionLabel")
        url_summary_row.addWidget(url_summary_label)
        url_summary_row.addStretch(1)
        change_site_btn = QPushButton("← 사이트 변경", page)
        change_site_btn.setObjectName("GhostButton")
        change_site_btn.clicked.connect(self.show_site_selection_stage)
        url_summary_row.addWidget(change_site_btn)
        layout.addLayout(url_summary_row)

        self._selected_site_summary = QLabel("Step 1에서 사이트를 먼저 선택해 주세요.", page)
        self._selected_site_summary.setProperty("role", "stateLabel")
        self._selected_site_summary.setWordWrap(True)
        layout.addWidget(self._selected_site_summary)

        source_label = QLabel("1. 입력 소스 선택", page)
        source_label.setObjectName("SectionLabel")
        layout.addWidget(source_label)

        source_row = QHBoxLayout()
        source_row.setSpacing(12)

        self._source_none_button = QPushButton("입력 소스 없음", page)
        self._source_none_button.setCheckable(True)
        self._source_none_button.setProperty("modeButton", True)
        self._source_none_button.clicked.connect(self._clear_input_source)
        source_row.addWidget(self._source_none_button)

        self._source_file_button = QPushButton("기획서 파일 선택", page)
        self._source_file_button.setCheckable(True)
        self._source_file_button.setProperty("modeButton", True)
        self._source_file_button.clicked.connect(self._open_file_dialog)
        source_row.addWidget(self._source_file_button)

        self._load_plan_button = QPushButton("기존 번들 열기", page)
        self._load_plan_button.setCheckable(True)
        self._load_plan_button.setProperty("modeButton", True)
        self._load_plan_button.clicked.connect(self._open_plan_dialog)
        source_row.addWidget(self._load_plan_button)
        source_row.addStretch()
        layout.addLayout(source_row)

        source_hint = QLabel(
            "PDF, DOCX, MD, TXT 기획서를 바로 분석하거나 저장된 JSON 번들을 다시 열 수 있습니다.",
            page,
        )
        source_hint.setWordWrap(True)
        source_hint.setObjectName("FeatureHintLabel")
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

        self._benchmark_mode_button = QPushButton("벤치마킹 모드", page)
        self._benchmark_mode_button.setCheckable(True)
        self._benchmark_mode_button.setProperty("modeButton", True)
        self._benchmark_mode_button.clicked.connect(self._activate_benchmark_mode)
        self._run_mode_group.addButton(self._benchmark_mode_button)
        mode_row.addWidget(self._benchmark_mode_button)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        action_label = QLabel("3. 실행 준비", page)
        action_label.setObjectName("SectionLabel")
        layout.addWidget(action_label)

        self._standard_action_container = QFrame(page)
        action_container_layout = QVBoxLayout(self._standard_action_container)
        action_container_layout.setContentsMargins(0, 0, 0, 0)
        action_container_layout.setSpacing(0)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self._start_button = QPushButton("테스트 실행", page)
        self._start_button.clicked.connect(self.startRequested.emit)
        action_row.addWidget(self._start_button)

        action_row.addStretch()
        action_container_layout.addLayout(action_row)
        layout.addWidget(self._standard_action_container)

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

        results_label = QLabel("4. 이전 테스트 결과 조회하기", page)
        results_label.setObjectName("SectionLabel")
        layout.addWidget(results_label)

        results_row = QHBoxLayout()
        results_row.setSpacing(12)

        self._view_results_button = QPushButton("탐색 결과 보기", page)
        self._view_results_button.setObjectName("GhostButton")
        self._view_results_button.clicked.connect(self.show_exploration_results)
        results_row.addWidget(self._view_results_button)
        results_row.addStretch()
        layout.addLayout(results_row)

        results_hint = QLabel("이전에 저장된 탐색 결과와 리플레이를 다시 확인할 수 있습니다.", page)
        results_hint.setWordWrap(True)
        layout.addWidget(results_hint)

        chat_label = QLabel("5. 대화형 입력", page)
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

        scroll.setWidget(page)
        return scroll

    def _create_benchmark_stage(self, parent: QWidget) -> QWidget:
        scroll = QScrollArea(parent)
        scroll.setObjectName("StageScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget(scroll)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        title_label = QLabel("벤치마킹", page)
        title_label.setObjectName("BenchmarkStageTitle")
        layout.addWidget(title_label)

        card = QFrame(page)
        card.setObjectName("BenchmarkStageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(14)

        portal = QFrame(card)
        portal.setObjectName("BenchmarkPortalPanel")
        portal_layout = QVBoxLayout(portal)
        portal_layout.setContentsMargins(16, 16, 16, 16)
        portal_layout.setSpacing(10)

        portal_image = GuiAssetLabel(
            "benchmark_empty_state.png",
            parent=portal,
            min_height=108,
            max_height=128,
            fit="contain",
        )
        portal_image.setObjectName("BenchmarkPortalImage")
        portal_layout.addWidget(portal_image)

        hero_kicker = QLabel("BENCHMARK", portal)
        hero_kicker.setObjectName("BenchmarkHeroKicker")
        portal_layout.addWidget(hero_kicker)

        card_title = QLabel("대상 선택 → 테스트 목록 → 실행", portal)
        card_title.setObjectName("BenchmarkHeroHeadline")
        card_title.setWordWrap(True)
        portal_layout.addWidget(card_title)
        card_layout.addWidget(portal)

        self._benchmark_stage_summary_label = QLabel(
            "아직 선택된 벤치가 없습니다. 벤치 관리에서 대상 사이트를 골라주세요.",
            card,
        )
        self._benchmark_stage_summary_label.setObjectName("BenchmarkStatusLabel")
        self._benchmark_stage_summary_label.setWordWrap(True)
        card_layout.addWidget(self._benchmark_stage_summary_label)

        self._benchmark_stage_detail_label = QLabel(
            "사이트 목록과 테스트 목록은 관리 화면에서 이어서 선택합니다.",
            card,
        )
        self._benchmark_stage_detail_label.setObjectName("BenchmarkStageSubtitle")
        self._benchmark_stage_detail_label.setWordWrap(True)
        card_layout.addWidget(self._benchmark_stage_detail_label)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        for text in ("1 대상", "2 테스트", "3 실행"):
            chip = QLabel(text, card)
            chip.setObjectName("BenchmarkStageChip")
            chip_row.addWidget(chip)
        chip_row.addStretch()
        card_layout.addLayout(chip_row)

        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(10)
        self._benchmark_stage_site_metric = QLabel("사이트 -", card)
        self._benchmark_stage_site_metric.setObjectName("BenchmarkStageMetric")
        metrics_row.addWidget(self._benchmark_stage_site_metric)
        self._benchmark_stage_url_metric = QLabel("링크 -", card)
        self._benchmark_stage_url_metric.setObjectName("BenchmarkStageMetric")
        self._benchmark_stage_url_metric.setWordWrap(True)
        metrics_row.addWidget(self._benchmark_stage_url_metric, stretch=1)
        card_layout.addLayout(metrics_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self._benchmark_add_button = QPushButton("벤치 관리 열기", card)
        self._benchmark_add_button.clicked.connect(self._emit_benchmark_manage)
        action_row.addWidget(self._benchmark_add_button)

        self._benchmark_view_button = QPushButton("최근 결과 보기", card)
        self._benchmark_view_button.setObjectName("GhostButton")
        self._benchmark_view_button.clicked.connect(self._emit_benchmark_view)
        action_row.addWidget(self._benchmark_view_button)
        action_row.addStretch()
        card_layout.addLayout(action_row)

        layout.addWidget(card)
        layout.addStretch(1)

        scroll.setWidget(page)
        return scroll

    def _create_review_stage(self, parent: QWidget) -> QWidget:
        """Step 3 — 테스트 진행 화면 (단일 컬럼, 반쪽 화면 폭 최적화).

        GAIA가 띄운 실제 Playwright 브라우저는 별도 OS 창으로 우측에 표시되므로,
        GUI는 좌측 절반 폭에서도 자연스러운 단일 컬럼 레이아웃을 사용한다.
        프리뷰 섹션(QWebEngineView 임베드 + 브라우저 chrome 데코레이션)은 제거됨.

        ┌─────────────────────────────────────────┐
        │ [Status header card]                    │
        │ ┌──────────────────────────────────┐    │
        │ │ 테스트 실행 중 [배지] [일시][중지] │    │
        │ └──────────────────────────────────┘    │
        │                                         │
        │ [KPI grid — 2 cols × 4 rows]            │
        │ ┌──────────┐ ┌──────────┐               │
        │ │ 진행률    │ │ 현재 단계 │               │
        │ ├──────────┤ ├──────────┤               │
        │ │ 통과/실패 │ │ 실행 시간 │               │
        │ ├──────────┤ ├──────────┤               │
        │ │ 케이스    │ │ 로그      │               │
        │ ├──────────┤ ├──────────┤               │
        │ │ 브라우저  │ │ 환경      │               │
        │ └──────────┘ └──────────┘               │
        │                                         │
        │ [Terminal log zone — 큰 영역, stretch]  │
        │ ┌──────────────────────────────────┐    │
        │ │ 14:32 INFO [STEP] ...            │    │
        │ └──────────────────────────────────┘    │
        │                                         │
        │ [← 사이트 변경]                          │
        └─────────────────────────────────────────┘
        """
        # 외부 page — QScrollArea를 호스팅 (컨텐츠가 윈도우 높이 초과 시 스크롤 가능)
        page = QWidget(parent)
        page.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        page_outer = QVBoxLayout(page)
        page_outer.setContentsMargins(0, 0, 0, 0)
        page_outer.setSpacing(0)

        # 스크롤 영역 — 세로만 스크롤, 가로는 부모 폭에 맞춤 (responsive)
        self._step3_scroll = QScrollArea(page)
        self._step3_scroll.setObjectName("Step3ScrollArea")
        self._step3_scroll.setWidgetResizable(True)
        self._step3_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._step3_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._step3_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        page_outer.addWidget(self._step3_scroll)

        # 실제 컨텐츠 widget — 모든 위젯의 부모는 이 widget이 됨
        # (자식 위젯들이 page를 부모로 갖던 기존 동작과 동일하게 보이도록 page 변수를 재할당)
        scroll_content = QWidget(self._step3_scroll)
        scroll_content.setObjectName("Step3ScrollContent")
        self._step3_scroll.setWidget(scroll_content)
        page = scroll_content  # 이후 코드는 page를 부모로 사용 — 기존 로직 유지

        # 단일 컬럼 layout — 좁은 폭(반쪽 화면)에서도 어색하지 않도록 설계
        page_v = QVBoxLayout(page)
        page_v.setContentsMargins(8, 0, 8, 4)
        page_v.setSpacing(14)

        # ── 상단 status header card (좁은 폭에서도 한 줄 유지) ──────
        status_card = QFrame(page)
        status_card.setObjectName("Step3StatusCard")
        status_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sh_layout = QHBoxLayout(status_card)
        sh_layout.setContentsMargins(16, 12, 16, 12)
        sh_layout.setSpacing(8)

        # 좌: 타이틀 + 부제 (수직 stack, title_row만 가로 배치)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)
        self._exec_title = QLabel("테스트 실행 중", status_card)
        self._exec_title.setObjectName("Step3StatusTitle")
        # 좁은 폭에서 잘리지 않도록 — title은 자체 sizeHint를 강요하지 않음
        self._exec_title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        # 텍스트가 너무 길면 elide (가운데 잘림 방지, 끝에 ... 표시)
        self._exec_title.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        title_row.addWidget(self._exec_title, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._exec_status_pill = QLabel("실행 중", status_card)
        self._exec_status_pill.setObjectName("ExecStatusPill")
        self._exec_status_pill.setProperty("state", "running")
        title_row.addWidget(self._exec_status_pill, alignment=Qt.AlignmentFlag.AlignVCenter)
        # status pill 옆에 인라인 3-dot 로더 (실행 중일 때 애니메이션)
        self._exec_status_dots = DotsLoaderWidget(status_card)
        title_row.addWidget(self._exec_status_dots, alignment=Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        title_col.addLayout(title_row)
        # subtitle — 짧게 + ellide on overflow
        self._exec_subtitle = QLabel("AI가 테스트를 실행 중입니다.", status_card)
        self._exec_subtitle.setObjectName("Step3StatusSub")
        # wordWrap 끄고 sizePolicy로 유연하게 — 좁아지면 ellide 됨
        self._exec_subtitle.setWordWrap(False)
        self._exec_subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        title_col.addWidget(self._exec_subtitle)
        sh_layout.addLayout(title_col, stretch=1)

        # 우: 일시정지 + 중지 (compact, fixed sizing)
        # NOTE: 일시정지는 백엔드 미지원 → 사용자 혼란 방지하기 위해 hidden (legacy 호환 위해 객체 자체는 유지)
        self._pause_button = QPushButton("일시정지", status_card)
        self._pause_button.setObjectName("Step3PauseButton")
        self._pause_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pause_button.setEnabled(False)
        self._pause_button.setVisible(False)  # 백엔드 미구현이므로 화면에 노출하지 않음
        self._pause_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sh_layout.addWidget(self._pause_button, alignment=Qt.AlignmentFlag.AlignVCenter)
        self._cancel_button = QPushButton("중지", status_card)
        self._cancel_button.setObjectName("Step3StopButton")
        self._cancel_button.setEnabled(False)
        self._cancel_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._cancel_button.clicked.connect(self.cancelRequested.emit)
        sh_layout.addWidget(self._cancel_button, alignment=Qt.AlignmentFlag.AlignVCenter)

        page_v.addWidget(status_card)

        # ── KPI 2x4 grid (8 cards, 좁은 폭 최적화: 2 cols × 4 rows) ──
        kpi_grid_widget = QWidget(page)
        kpi_grid = QGridLayout(kpi_grid_widget)
        kpi_grid.setContentsMargins(0, 0, 0, 0)
        kpi_grid.setHorizontalSpacing(12)
        kpi_grid.setVerticalSpacing(12)

        def _make_kpi_card(label_text, value_widget, sub_widget=None):
            card = QFrame(kpi_grid_widget)
            card.setObjectName("KpiGridCard")
            # 카드 정확히 110px 고정 — label(18) + 4 + value(34) + addStretch + sub(16) + margins(20+18) = 100~110px
            # setFixedHeight로 Grid가 임의로 늘리거나 줄이지 못하게 → 모든 카드 균일 + 페이지 오버플로 방지.
            # KPI 영역을 컴팩트하게 → 로그 영역에 더 많은 공간 배분.
            card.setFixedHeight(110)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(4)
            # Top: 라벨 — 고정 높이, top-left 정렬
            lbl = QLabel(label_text, card)
            lbl.setObjectName("KpiGridLabel")
            lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            cl.addWidget(lbl, stretch=0)
            # Middle: 값 — stretch 없이 자연 크기, sub 영역 침범 방지
            if isinstance(value_widget, QLabel):
                value_widget.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                value_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            cl.addWidget(value_widget, stretch=0)
            # 빈 공간을 value와 sub 사이로 — sub가 항상 카드 하단에 정착
            cl.addStretch(1)
            # Bottom: 서브 — 고정 높이, bottom-left 정렬
            if sub_widget is not None:
                sub_widget.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
                sub_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                cl.addWidget(sub_widget, stretch=0)
            return card

        # Row 0, Col 0 — 전체 진행률 (큰 % 텍스트 + 3-dot wave 로딩)
        self._overall_progress_widget = CircularProgressWidget()
        # 다른 카드 value (QLabel)와 같은 높이로 — sub 영역 침범 방지
        self._overall_progress_widget.setMinimumSize(80, 30)
        self._overall_progress_widget.setMaximumHeight(34)
        self._overall_progress_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._overall_progress_detail = QLabel("0 / 0 완료", kpi_grid_widget)
        self._overall_progress_detail.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("전체 진행률", self._overall_progress_widget, self._overall_progress_detail), 0, 0)

        # Row 0, Col 1 — 현재 단계
        self._metric_step_value = QLabel("- / -", kpi_grid_widget)
        self._metric_step_value.setObjectName("KpiGridValue")
        self._current_case_title = QLabel("실행 시작 대기 중", kpi_grid_widget)
        self._current_case_title.setObjectName("KpiGridSub")
        self._current_case_title.setWordWrap(True)
        kpi_grid.addWidget(_make_kpi_card("현재 단계", self._metric_step_value, self._current_case_title), 0, 1)

        # Row 1, Col 0 — 통과 / 실패
        pf_widget = QWidget(kpi_grid_widget)
        pf_widget.setMinimumHeight(28)
        pf_widget.setMaximumHeight(38)
        pf_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        pf_row = QHBoxLayout(pf_widget)
        pf_row.setContentsMargins(0, 0, 0, 0)
        pf_row.setSpacing(6)
        self._metric_pass_value = QLabel("0", kpi_grid_widget)
        self._metric_pass_value.setObjectName("KpiGridValuePass")
        self._metric_pass_value.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        pf_row.addWidget(self._metric_pass_value)
        pf_slash = QLabel("/", kpi_grid_widget)
        pf_slash.setStyleSheet(
            "color: #c5cdd5; font-size: 19px; font-weight: 700; background: transparent;"
            " min-height: 28px; max-height: 34px;"
        )
        pf_slash.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        pf_row.addWidget(pf_slash)
        self._metric_fail_value = QLabel("0", kpi_grid_widget)
        self._metric_fail_value.setObjectName("KpiGridValueFail")
        self._metric_fail_value.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        pf_row.addWidget(self._metric_fail_value)
        pf_row.addStretch(1)
        pf_sub = QLabel("성공 / 실패 카운트", kpi_grid_widget)
        pf_sub.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("통과 / 실패", pf_widget, pf_sub), 1, 0)

        # Row 1, Col 1 — 실행 시간
        self._metric_elapsed_value = QLabel("00:00:00", kpi_grid_widget)
        self._metric_elapsed_value.setObjectName("KpiGridValue")
        from datetime import datetime
        self._metric_start_time_label = QLabel(f"시작 {datetime.now().strftime('%H:%M:%S')}", kpi_grid_widget)
        self._metric_start_time_label.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("실행 시간", self._metric_elapsed_value, self._metric_start_time_label), 1, 1)

        # Row 2, Col 0 — 선택된 테스트 케이스 수
        self._metric_selected_cases = QLabel("-", kpi_grid_widget)
        self._metric_selected_cases.setObjectName("KpiGridValueBlue")
        cases_sub = QLabel("총 선택된 케이스", kpi_grid_widget)
        cases_sub.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("테스트 케이스", self._metric_selected_cases, cases_sub), 2, 0)

        # Row 2, Col 1 — 실행 로그 수
        self._metric_log_count = QLabel("0", kpi_grid_widget)
        self._metric_log_count.setObjectName("KpiGridValueBlue")
        log_sub = QLabel("총 로그 수", kpi_grid_widget)
        log_sub.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("실행 로그", self._metric_log_count, log_sub), 2, 1)

        # Row 3, Col 0 — 브라우저 환경
        browser_env = QLabel("Chromium", kpi_grid_widget)
        browser_env.setObjectName("KpiGridValue")
        browser_env.setStyleSheet(
            "color: #191f28; font-size: 15px; font-weight: 800; background: transparent;"
            " min-height: 26px; max-height: 32px; padding: 1px 0px;"
        )
        try:
            from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
            chromium_ver = qWebEngineChromiumVersion() or ""
            major = chromium_ver.split(".")[0] if chromium_ver else ""
            if major:
                browser_env.setText(f"Chromium {major}")
        except Exception:
            pass
        try:
            screen = QApplication.primaryScreen()
            if screen:
                size = screen.size()
                browser_sub = QLabel(f"{size.width()} x {size.height()}", kpi_grid_widget)
            else:
                browser_sub = QLabel("화면 정보 없음", kpi_grid_widget)
        except Exception:
            browser_sub = QLabel("", kpi_grid_widget)
        browser_sub.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("브라우저", browser_env, browser_sub), 3, 0)

        # Row 3, Col 1 — 실행 환경 (OS + locale)
        import platform as _platform
        import locale as _locale
        try:
            os_name = _platform.system()
            os_ver = _platform.release()
            env_text = f"{os_name} {os_ver}"
            if len(env_text) > 18:
                env_text = env_text[:18]
        except Exception:
            env_text = "Unknown"
        env_value = QLabel(env_text, kpi_grid_widget)
        env_value.setStyleSheet(
            "color: #191f28; font-size: 15px; font-weight: 800; background: transparent;"
            " min-height: 26px; max-height: 32px; padding: 1px 0px;"
        )
        try:
            loc = _locale.getdefaultlocale()
            loc_text = loc[0] if loc and loc[0] else ""
        except Exception:
            loc_text = ""
        env_sub = QLabel(loc_text or "지역 정보 없음", kpi_grid_widget)
        env_sub.setObjectName("KpiGridSub")
        kpi_grid.addWidget(_make_kpi_card("환경", env_value, env_sub), 3, 1)

        # 칸 균등 분배 (2 cols)
        kpi_grid.setColumnStretch(0, 1)
        kpi_grid.setColumnStretch(1, 1)

        page_v.addWidget(kpi_grid_widget)

        # ── 하단 터미널 로그 zone (가장 큰 영역, stretch=1) ──────────
        log_zone = QFrame(page)
        log_zone.setObjectName("TerminalLogZone")
        lz_layout = QVBoxLayout(log_zone)
        lz_layout.setContentsMargins(14, 12, 14, 12)
        lz_layout.setSpacing(8)

        lz_header = QHBoxLayout()
        lz_header.setSpacing(10)
        log_title = QLabel("실행 로그", log_zone)
        log_title.setObjectName("TerminalLogTitle")
        lz_header.addWidget(log_title)
        lz_header.addStretch(1)

        try:
            from PySide6.QtWidgets import QCheckBox
            self._terminal_autoscroll = QCheckBox("자동 스크롤", log_zone)
            self._terminal_autoscroll.setObjectName("TerminalAutoScroll")
            self._terminal_autoscroll.setChecked(True)
            lz_header.addWidget(self._terminal_autoscroll)
        except Exception:
            self._terminal_autoscroll = None

        # 로그 레벨 필터 콤보 — 선택 시 _log_output 재렌더링하여 해당 레벨만 표시
        try:
            self._terminal_level_combo = QComboBox(log_zone)
            self._terminal_level_combo.setObjectName("TerminalLogLevelCombo")
            self._terminal_level_combo.addItems(["로그 레벨", "ALL", "INFO", "WARN", "ERROR"])
            self._terminal_level_combo.setCurrentIndex(0)
            # 콤보 변경 시 필터 재적용
            self._terminal_level_combo.currentTextChanged.connect(self._apply_log_level_filter)
            lz_header.addWidget(self._terminal_level_combo)
        except Exception:
            self._terminal_level_combo = None

        self._view_logs_button = QPushButton("⬇  전체 다운로드", log_zone)
        self._view_logs_button.setObjectName("TerminalLogButton")
        self._view_logs_button.setEnabled(False)
        self._view_logs_button.clicked.connect(self._show_detailed_logs)
        lz_header.addWidget(self._view_logs_button)

        lz_layout.addLayout(lz_header)

        self._log_output = QTextEdit(log_zone)
        self._log_output.setObjectName("TerminalLog")
        self._log_output.setReadOnly(True)
        self._log_output.setPlaceholderText("[ready] 테스트가 시작되면 여기에 실행 로그가 표시됩니다.")
        # QScrollArea 안에서는 stretch=1이 무한 공간을 의미해서 layout이 깨질 수 있음.
        # 명확한 min/max로 안정적인 높이 보장 (스크롤 가능하므로 큰 컨텐츠도 OK).
        self._log_output.setMinimumHeight(220)
        self._log_output.setMaximumHeight(360)
        lz_layout.addWidget(self._log_output)

        # QScrollArea 안에서는 stretch 없이 자연 높이로 — 컨텐츠 초과 시 페이지 스크롤
        page_v.addWidget(log_zone)

        # ── 결과 액션 바 (test 완료 시 표시) ─────────────────────────
        # 평소에는 hidden, set_busy(False) + show_result_card 호출 시 visible.
        # Grafana 링크 + 결과 폴더 열기 버튼 + (실패 시) 이유 표시.
        self._result_action_bar = QFrame(page)
        self._result_action_bar.setObjectName("ResultActionBar")
        self._result_action_bar.setVisible(False)
        rab_layout = QVBoxLayout(self._result_action_bar)
        rab_layout.setContentsMargins(16, 12, 16, 12)
        rab_layout.setSpacing(8)

        # 상단 결과 요약 (status + reason)
        rab_summary_row = QHBoxLayout()
        rab_summary_row.setSpacing(10)
        self._result_action_status_icon = QLabel("✓", self._result_action_bar)
        self._result_action_status_icon.setObjectName("ResultActionStatusIcon")
        self._result_action_status_icon.setFixedSize(28, 28)
        self._result_action_status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rab_summary_row.addWidget(self._result_action_status_icon)
        self._result_action_title = QLabel("테스트 완료", self._result_action_bar)
        self._result_action_title.setObjectName("ResultActionTitle")
        rab_summary_row.addWidget(self._result_action_title)
        rab_summary_row.addStretch(1)
        rab_layout.addLayout(rab_summary_row)

        # 이유 (실패/차단 시에만 표시)
        self._result_action_reason = QLabel("", self._result_action_bar)
        self._result_action_reason.setObjectName("ResultActionReason")
        self._result_action_reason.setWordWrap(True)
        self._result_action_reason.setVisible(False)
        rab_layout.addWidget(self._result_action_reason)

        # 액션 버튼들
        rab_buttons_row = QHBoxLayout()
        rab_buttons_row.setSpacing(8)
        self._result_grafana_button = QPushButton("Grafana 대시보드 열기", self._result_action_bar)
        self._result_grafana_button.setObjectName("ResultGrafanaButton")
        self._result_grafana_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._result_grafana_button.clicked.connect(self._open_grafana_dashboard)
        rab_buttons_row.addWidget(self._result_grafana_button)
        self._result_open_folder_button = QPushButton("결과 폴더 열기", self._result_action_bar)
        self._result_open_folder_button.setObjectName("ResultOpenFolderButton")
        self._result_open_folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._result_open_folder_button.setVisible(False)
        self._result_open_folder_button.clicked.connect(self._open_result_folder)
        rab_buttons_row.addWidget(self._result_open_folder_button)
        rab_buttons_row.addStretch(1)
        rab_layout.addLayout(rab_buttons_row)

        # 내부 상태 저장 (버튼 클릭 시 사용)
        self._result_grafana_url: str = ""
        self._result_output_dir: str = ""

        page_v.addWidget(self._result_action_bar)

        # 하단 사이트 변경 버튼
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        bottom_row.setContentsMargins(0, 4, 0, 0)
        bottom_row.addStretch(1)
        self._back_to_setup_button = QPushButton("← 사이트 변경", page)
        self._back_to_setup_button.setObjectName("GhostButton")
        self._back_to_setup_button.clicked.connect(self.show_site_selection_stage)
        bottom_row.addWidget(self._back_to_setup_button)
        page_v.addLayout(bottom_row)

        # ─── 호환용 레거시 위젯 stub (보이지 않음) ─────────────────────
        # 우측 임베드 브라우저는 제거되었지만, controller가 setUrl/setHtml 호출 시
        # AttributeError가 나지 않도록 self._browser_view를 invisible 컨테이너로 이동.
        # Playwright/MCP 실 브라우저는 별도 OS 창으로 띄워짐 (외부에서 관리).
        _legacy_stubs = QWidget(page)
        _legacy_stubs.setVisible(False)
        _legacy_stubs.setMaximumSize(0, 0)
        _legacy_stubs_layout = QVBoxLayout(_legacy_stubs)
        _legacy_stubs_layout.setContentsMargins(0, 0, 0, 0)

        # 브라우저 뷰 — controller setUrl/setHtml 호환용 (보이지 않음)
        if self._browser_view is not None:
            try:
                self._browser_view.setParent(_legacy_stubs)
            except Exception:
                pass
            self._browser_view.setVisible(False)
            self._browser_view.setMaximumSize(0, 0)
            _legacy_stubs_layout.addWidget(self._browser_view)

        # set_url_field가 업데이트하는 라벨들 (보이지 않음, 호환용)
        self._review_url_label = QLabel("about:blank", _legacy_stubs)
        self._review_url_label.setVisible(False)
        _legacy_stubs_layout.addWidget(self._review_url_label)
        self._browser_tab_title = QLabel("새 탭", _legacy_stubs)
        self._browser_tab_title.setVisible(False)
        _legacy_stubs_layout.addWidget(self._browser_tab_title)

        # SpinnerWidget — start/stop 호출되어도 보이지 않도록 monkey-patch
        self._current_case_spinner = SpinnerWidget(_legacy_stubs)
        self._current_case_spinner.setVisible(False)
        self._current_case_spinner.start = lambda: None  # type: ignore[assignment]
        self._current_case_spinner.stop = lambda: None  # type: ignore[assignment]
        _legacy_stubs_layout.addWidget(self._current_case_spinner)

        # 탭 인터페이스는 신규 레이아웃에서 사용 안 함 — controller가 _set_review_tab을 호출해도 안전.
        self._review_tab_buttons: dict[str, QPushButton] = {}
        self._review_content_stack: QStackedWidget | None = None

        # 캡처 strip는 호환용으로만 유지
        self._result_screenshot_card = QFrame(_legacy_stubs)
        self._result_screenshot_card.setVisible(False)
        self._result_screenshot_layout = QHBoxLayout(self._result_screenshot_card)
        self._result_screenshot_container = QWidget(self._result_screenshot_card)
        self._result_screenshot_scroll = QScrollArea(self._result_screenshot_card)

        # _step3_root_split — splitter 제거됨, 호환용 None
        self._step3_root_split = None

        # ─── 히든 legacy 위젯들 (controller 호환용) ────────────────
        legacy_holder = QWidget(page)
        legacy_holder.setVisible(False)
        legacy_layout = QVBoxLayout(legacy_holder)
        legacy_layout.setContentsMargins(0, 0, 0, 0)

        self._result_summary_card = QFrame(legacy_holder)
        self._result_summary_card.setObjectName("ResultSummaryCard")
        rs_layout = QVBoxLayout(self._result_summary_card)
        self._result_summary_status = QLabel("실행 결과 대기 중", self._result_summary_card)
        rs_layout.addWidget(self._result_summary_status)
        self._result_summary_meta = QLabel("", self._result_summary_card)
        rs_layout.addWidget(self._result_summary_meta)
        self._result_summary_reason = QLabel("", self._result_summary_card)
        rs_layout.addWidget(self._result_summary_reason)
        self._result_live_goal = QLabel("현재 목표: -", self._result_summary_card)
        rs_layout.addWidget(self._result_live_goal)
        self._result_live_step = QLabel("현재 단계: -", self._result_summary_card)
        rs_layout.addWidget(self._result_live_step)
        self._result_live_blocked = QLabel("차단 사유: 없음", self._result_summary_card)
        rs_layout.addWidget(self._result_live_blocked)
        self._result_timeline_view = QTextEdit(self._result_summary_card)
        self._result_timeline_view.setObjectName("ResultTimelineView")
        self._result_timeline_view.setReadOnly(True)
        rs_layout.addWidget(self._result_timeline_view)
        legacy_layout.addWidget(self._result_summary_card)

        progress_scroll_content = QWidget(legacy_holder)
        progress_scroll_content.setObjectName("ScenarioProgressContent")
        self._test_progress_layout = QGridLayout(progress_scroll_content)
        self._test_progress_layout.setContentsMargins(0, 0, 0, 0)
        empty_label = QLabel("", progress_scroll_content)
        self._test_progress_empty_label = empty_label
        self._test_progress_layout.addWidget(empty_label, 0, 0)
        legacy_layout.addWidget(progress_scroll_content)

        self._checklist_view = QListWidget(legacy_holder)
        self._checklist_view.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        legacy_layout.addWidget(self._checklist_view)

        # legacy_holder를 page에 add (visible=False라 영향 없음)
        legacy_holder.setMaximumHeight(0)

        # NOTE: `page` 변수는 QScrollArea 내부의 scroll_content를 가리킴.
        # workflow_stack에 추가될 widget은 외부 host (self._step3_scroll의 parent)여야 함.
        # _step3_scroll.parent()는 outer page widget (QScrollArea 호스팅용).
        return self._step3_scroll.parentWidget()

    def _set_device_mode(self, key: str) -> None:
        """레거시 호환 no-op (신규 50:50 layout에선 디바이스 토글 없음)."""
        return

    def _toggle_browser_fullscreen(self) -> None:
        """레거시 호환 no-op (신규 단일 컬럼 레이아웃엔 splitter 없음).

        실제 Playwright 브라우저는 별도 OS 창으로 표시되므로 GUI 내부에서
        비율을 조정할 대상이 없음.
        """
        return

    # ------------------------------------------------------------------
    # Step 3 헬퍼
    # ------------------------------------------------------------------
    def _metric_column(self, parent: QWidget, label_text: str) -> QVBoxLayout:
        """Metric label + value 1열 (vertical stack)."""
        col = QVBoxLayout()
        col.setSpacing(6)
        col.setContentsMargins(0, 0, 0, 0)
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(label_text, parent)
        lbl.setObjectName("MetricLabel")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(lbl)
        return col

    def _metric_divider(self, parent: QWidget) -> QFrame:
        d = QFrame(parent)
        d.setObjectName("ExecMetricsDivider")
        d.setFixedWidth(1)
        d.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        return d

    def _set_review_tab(self, key: str) -> None:
        # 신규 Step 3 레이아웃에서는 탭이 없음 (브라우저 + 로그 동시 노출). 호환 no-op.
        return

    # ------------------------------------------------------------------
    # 워크플로 단계 헬퍼
    # ------------------------------------------------------------------
    def show_site_selection_stage(self) -> None:
        """Step 1 — 사이트 선택 페이지로 이동."""
        self._workflow_stage = "site_selection"
        if self._workflow_stack.currentWidget() is not self._site_selection_page:
            self._workflow_stack.setCurrentWidget(self._site_selection_page)
        if hasattr(self, "_back_to_setup_button"):
            self._back_to_setup_button.setEnabled(False)
        self._set_browser_panel_visible(False)
        self._update_workflow_indicators(0)

    def show_setup_stage(self) -> None:
        """레거시 진입점 — 이제 항상 test_case_stage로 리다이렉트.

        controller가 호환성을 위해 호출하지만, 신규 UX에서는 Step 2 = 테스트 케이스 선택.
        """
        self.show_test_case_stage()

    def show_setup_stage_legacy(self) -> None:
        """원래의 setup_page (drop area / mode buttons / chat). 보통 노출하지 않음."""
        self._workflow_stage = "setup"
        if self._workflow_stack.currentWidget() is not self._setup_page:
            self._workflow_stack.setCurrentWidget(self._setup_page)
        self._back_to_setup_button.setEnabled(False)
        self._set_browser_panel_visible(False)
        self._update_selected_site_summary()
        self._update_workflow_indicators(1)

    def show_benchmark_stage(self) -> None:
        self._workflow_stage = "benchmark"
        if self._workflow_stack.currentWidget() is not self._benchmark_page:
            self._workflow_stack.setCurrentWidget(self._benchmark_page)
        self._set_browser_panel_visible(False)
        self._update_workflow_indicators(1)

    def show_setup_stage_with_browser(self) -> None:
        """URL 로딩 후 호출되는 진입점 — 브라우저 미리보기와 함께 Step 2."""
        self.show_test_case_stage()
        self._set_browser_panel_visible(True)

    def show_review_stage(self) -> None:
        self._workflow_stage = "review"
        if self._workflow_stack.currentWidget() is not self._review_page:
            self._workflow_stack.setCurrentWidget(self._review_page)
        self._back_to_setup_button.setEnabled(not self._is_busy)
        # Step 3에서는 브라우저 미리보기 항상 표시 (WebEngine 가용 여부와 무관하게,
        # fallback widget도 의미있는 정보를 보여줌)
        self._set_browser_panel_visible(True)
        self._update_workflow_indicators(2)

    def show_exploration_results(self) -> None:
        """탐색 결과 뷰어 페이지 표시"""
        self._workflow_stage = "exploration"
        self._exploration_page.refresh_results()
        if self._workflow_stack.currentWidget() is not self._exploration_page:
            self._workflow_stack.setCurrentWidget(self._exploration_page)
        self._set_browser_panel_visible(False)
        self._update_workflow_indicators(3)

    def _update_selected_site_summary(self) -> None:
        """Step 2 페이지에서 'Step 1에서 선택한 사이트' 텍스트를 갱신."""
        if not hasattr(self, "_selected_site_summary"):
            return
        url = (self._selected_site_url or "").strip()
        if not url and hasattr(self, "_url_input") and self._url_input is not None:
            url = self._url_input.text().strip()
        if url:
            self._selected_site_summary.setText(url)
        else:
            self._selected_site_summary.setText("Step 1에서 사이트를 먼저 선택해 주세요.")

    def _set_browser_panel_visible(self, visible: bool) -> None:
        """레거시 호환 no-op. 브라우저 미리보기는 이제 Step 3 페이지 내부에서 관리됨."""
        return

    # ------------------------------------------------------------------
    # 컨트롤러에 노출되는 슬롯
    # ------------------------------------------------------------------
    def show_checklist(self, items: Iterable[str]) -> None:
        self._checklist_view.clear()
        for item in items:
            QListWidgetItem(item, self._checklist_view)

    def show_scenarios(self, scenarios: Sequence[object]) -> None:
        self._checklist_view.clear()
        # 시나리오 캐시 — 현재 실행 케이스 표시용
        self._scenarios_by_id = {}
        for scenario in scenarios:
            list_item = QListWidgetItem(self._checklist_view)
            list_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            card = ScenarioCard(scenario, self._checklist_view)
            list_item.setSizeHint(card.sizeHint())
            self._checklist_view.setItemWidget(list_item, card)
            scenario_id = str(getattr(scenario, "id", "") or "")
            if scenario_id:
                self._scenarios_by_id[scenario_id] = scenario

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
                        "QFrame#ScenarioCard { border: 2px solid #3182f6; background: #eff6ff; }"
                    )
                else:
                    # 나머지: 숨기기
                    item.setHidden(True)

        # Step 3 "현재 실행 중인 케이스" 카드 갱신
        scenario = self._scenarios_by_id.get(str(scenario_id))
        if scenario is None:
            return
        title = (
            getattr(scenario, "scenario", None)
            or getattr(scenario, "name", None)
            or str(scenario_id)
        )
        if hasattr(self, "_current_case_title"):
            self._current_case_title.setText(str(title))
        # 설명: assertion.description 또는 expected_result
        desc_text = ""
        assertion_source = getattr(scenario, "assertion", None)
        if assertion_source is not None:
            desc_text = str(getattr(assertion_source, "description", "") or "")
        if not desc_text:
            desc_text = str(getattr(scenario, "expected_result", "") or "")
        if not desc_text:
            steps = getattr(scenario, "steps", None) or []
            if steps:
                first = steps[0]
                if isinstance(first, dict):
                    desc_text = str(first.get("description", "") or "")
                else:
                    desc_text = str(getattr(first, "description", "") or "")
        if hasattr(self, "_current_case_desc"):
            self._current_case_desc.setText(desc_text or "-")

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

        # ExecRunCard의 "현재 단계" metric도 동기화
        if hasattr(self, "_metric_step_value") and completed is not None and total is not None:
            self._metric_step_value.setText(f"{completed} / {total}")

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

        # 기존 legacy 그리드 항목 제거 (호환용)
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

        # 통과 / 실패 카운트 계산 → Step 3 메트릭 갱신
        pass_count = 0
        fail_count = 0
        for title, percent, status in normalized_items:
            s = status.lower()
            if s == "failed":
                fail_count += 1
            elif s == "success" or percent >= 99.0:
                pass_count += 1
        if hasattr(self, "_metric_pass_value"):
            self._metric_pass_value.setText(str(pass_count))
        if hasattr(self, "_metric_fail_value"):
            self._metric_fail_value.setText(str(fail_count))

    def _detect_log_level(self, text: str) -> str:
        """메시지 텍스트에서 ERROR/WARN/INFO 레벨 추정 — append_log + 필터 공용."""
        lower = text.lower()
        if "❌" in text or "fail" in lower or "error" in lower or "오류" in text or "실패" in text:
            return "ERROR"
        if "⚠️" in text or "warn" in lower or "blocked" in lower or "차단" in text:
            return "WARN"
        return "INFO"

    def _format_log_line_html(self, message: str, ts: str | None = None) -> tuple[str, str]:
        """레벨 컬러 적용된 HTML 한 줄 + 추정된 레벨 반환."""
        import html as _html
        from datetime import datetime as _dt
        if ts is None:
            ts = _dt.now().strftime("%H:%M:%S.%f")[:-3]
        text = str(message or "")
        level = self._detect_log_level(text)
        if level == "ERROR":
            level_color, msg_color = "#ef4444", "#fca5a5"
        elif level == "WARN":
            level_color, msg_color = "#f59e0b", "#fde68a"
        elif "✅" in text or "success" in text.lower() or "pass" in text.lower() or "성공" in text or "달성" in text:
            level_color, msg_color = "#10b981", "#86efac"
        else:
            level_color, msg_color = "#10b981", "#e2e8f0"
        safe_text = _html.escape(text)
        html_line = (
            f'<span style="color:#94a3b8;">{ts}</span>  '
            f'<span style="color:{level_color}; font-weight:700;">{level}</span>  '
            f'<span style="color:{msg_color};">{safe_text}</span>'
        )
        return html_line, level

    def _log_level_filter_passes(self, line_level: str) -> bool:
        """현재 콤보 선택을 기준으로 해당 레벨을 표시할지 여부."""
        combo = getattr(self, "_terminal_level_combo", None)
        if combo is None:
            return True
        sel = combo.currentText().strip().upper()
        if sel in ("", "로그 레벨", "ALL"):
            return True
        return sel == line_level.upper()

    def _apply_log_level_filter(self, *_args) -> None:
        """콤보 변경 시 호출 — _full_execution_logs 전체를 필터링하여 _log_output 재렌더링."""
        log_output = getattr(self, "_log_output", None)
        if log_output is None:
            return
        try:
            log_output.clear()
            for line in getattr(self, "_full_execution_logs", []):
                html_line, level = self._format_log_line_html(str(line), ts="--:--:--.---")
                if self._log_level_filter_passes(level):
                    log_output.append(html_line)
            # 필터 변경 후 맨 아래로 스크롤 (자동 스크롤 옵션 ON인 경우)
            autoscroll = getattr(self, "_terminal_autoscroll", None)
            if autoscroll is None or autoscroll.isChecked():
                sb = log_output.verticalScrollBar()
                if sb:
                    sb.setValue(sb.maximum())
        except Exception:
            pass

    def append_log(self, message: str) -> None:
        """로그 메시지를 터미널에 출력 + KPI 지표 파싱 + 레벨 필터 적용."""
        # 항상 전체 로그를 저장 (필터 변경 시 재렌더링 위해)
        self._full_execution_logs.append(message)
        # KPI: 실행 로그 수 갱신
        if hasattr(self, "_metric_log_count") and self._metric_log_count is not None:
            try:
                self._metric_log_count.setText(str(len(self._full_execution_logs)))
            except Exception:
                pass
        # 벤치마크 진행 메시지에서 KPI 지표 직접 파싱 (tracker가 비어있는 benchmark 모드용)
        try:
            self._parse_progress_for_metrics(message)
        except Exception:
            pass

        # 터미널 스타일로 _log_output에 출력 (현재 레벨 필터에 부합하는 경우만)
        log_output = getattr(self, "_log_output", None)
        if log_output is None:
            return
        try:
            from PySide6.QtCore import QCoreApplication
            html_line, level = self._format_log_line_html(message)
            if self._log_level_filter_passes(level):
                log_output.append(html_line)
                # auto-scroll
                autoscroll = getattr(self, "_terminal_autoscroll", None)
                if autoscroll is None or autoscroll.isChecked():
                    sb = log_output.verticalScrollBar()
                    if sb:
                        sb.setValue(sb.maximum())
            QCoreApplication.processEvents()
        except Exception:
            pass

    def _parse_progress_for_metrics(self, message: str) -> None:
        """Worker progress 메시지에서 KPI 지표를 추출하여 Step 3 metric에 반영.

        Benchmark 출력 패턴:
          [A/B] C/D SCENARIO_ID ... → 시나리오 C/D번째 시작
          --- Step N/M ---          → 시나리오 내 step 진행
          🎯 목표 시작: ...           → 시나리오 시작
          ✅ 목표 달성!               → 시나리오 성공
          status=FAIL / SUCCESS      → 시나리오 종료
        """
        import re
        text = str(message or "")
        if not text:
            return

        # tracker가 비어있을 때만 직접 파싱 — controller의 _update_overall_progress_display가
        # 이미 동작 중이면 그 값을 덮어쓰지 않음
        # (현재 단계, 통과/실패 누적은 항상 갱신)

        state = getattr(self, "_benchmark_metric_state", None)
        if state is None:
            state = {"total": 0, "completed": 0, "pass": 0, "fail": 0, "current_step": ""}
            self._benchmark_metric_state = state

        # 패턴 1: [A/B] C/D SCENARIO_ID — C/D 형태로 전체 시나리오 진행 추적
        m = re.search(r"\[\d+/\d+\]\s+(\d+)/(\d+)\s+", text)
        if m:
            current = int(m.group(1))
            total = int(m.group(2))
            state["total"] = total
            # current가 N번째 시나리오 시작 → 완료된 건 N-1개
            state["completed"] = max(0, current - 1)
            self._apply_benchmark_metric_state()
            return

        # 패턴 2: --- Step N/M --- (시나리오 내 step 진행 — KPI "현재 단계"만 갱신)
        m = re.search(r"---\s*Step\s+(\d+)\s*/\s*(\d+)\s*---", text)
        if m:
            current = int(m.group(1))
            total = int(m.group(2))
            state["current_step"] = f"{current} / {total}"
            if hasattr(self, "_metric_step_value"):
                self._metric_step_value.setText(state["current_step"])
            # 성공률은 시나리오 완료(목표 달성/실패)에만 반영 — step 단위로 덮어쓰지 않음.
            return

        # 패턴 3: 성공/실패 마커 — 시나리오 종료
        success_markers = ("✅ 목표 달성", "status\": \"SUCCESS", "✅ PASS", "SUCCESS", "성공")
        fail_markers = ("❌", "status\": \"FAIL", "❌ FAIL", "FAIL", "실패")
        # 더 정확하게: "목표 달성" / "목표 실패" 같은 명확한 종료 마커만 카운트
        if "목표 달성" in text or "✅ PASS" in text:
            state["pass"] += 1
            state["completed"] = min(state["total"], state["completed"] + 1)
            self._apply_benchmark_metric_state()
            return
        if "목표 실패" in text or "❌ FAIL" in text or "scenario failed" in text.lower():
            state["fail"] += 1
            state["completed"] = min(state["total"], state["completed"] + 1)
            self._apply_benchmark_metric_state()
            return

    def _apply_benchmark_metric_state(self) -> None:
        """_benchmark_metric_state를 Step 3 metric 위젯에 반영.

        진행률(%)은 **완료율** 기준 — completed / total.
        (성공/실패 여부와 무관하게 진행 정도를 표시. 성공률은 별도의 통과/실패 카드.)
        """
        state = getattr(self, "_benchmark_metric_state", None)
        if state is None:
            return
        total = int(state.get("total", 0))
        completed = int(state.get("completed", 0))
        passed = int(state.get("pass", 0))
        failed = int(state.get("fail", 0))
        # 완료율 — 진행률은 완료된 시나리오의 비율 (성공/실패 무관)
        if total > 0:
            percent = min(100.0, completed / total * 100.0)
        else:
            percent = 0.0
        if hasattr(self, "_overall_progress_widget") and self._overall_progress_widget:
            self._overall_progress_widget.set_value(percent)
        if hasattr(self, "_overall_progress_detail") and self._overall_progress_detail:
            # 부제: "Y완료 / N 전체 · 성공 Z · 실패 W"
            sub_parts = [f"{completed} / {total} 완료"]
            if passed:
                sub_parts.append(f"성공 {passed}")
            if failed:
                sub_parts.append(f"실패 {failed}")
            self._overall_progress_detail.setText(" · ".join(sub_parts))
        if hasattr(self, "_metric_pass_value"):
            self._metric_pass_value.setText(str(passed))
        if hasattr(self, "_metric_fail_value"):
            self._metric_fail_value.setText(str(failed))
        if hasattr(self, "_metric_step_value") and total:
            # 시나리오 인덱스 표시 (전체 진행률과 다른 — "지금 몇번째 시나리오인지")
            self._metric_step_value.setText(f"{min(total, completed + 1)} / {total}")

    def _on_elapsed_tick(self) -> None:
        self._elapsed_seconds += 1
        if hasattr(self, "_metric_elapsed_value"):
            h = self._elapsed_seconds // 3600
            m = (self._elapsed_seconds % 3600) // 60
            s = self._elapsed_seconds % 60
            self._metric_elapsed_value.setText(f"{h:02d}:{m:02d}:{s:02d}")

    def _poll_live_preview_file(self) -> None:
        """agent가 GAIA_LIVE_PREVIEW_PATH로 dump한 screenshot 폴링하여 browser_view에 표시."""
        if self._live_preview_path is None:
            return
        try:
            if not self._live_preview_path.exists():
                return
            mtime = self._live_preview_path.stat().st_mtime
            if mtime <= self._live_preview_last_mtime:
                return
            is_first = self._live_preview_last_mtime == 0.0
            self._live_preview_last_mtime = mtime
            data = self._live_preview_path.read_bytes()
            if not data or len(data) < 100:  # 너무 작으면 skip (쓰는 중일 수 있음)
                return
            if is_first:
                print(f"[LivePreview] first frame received ({len(data)} bytes)")
            import base64
            b64 = base64.b64encode(data).decode("ascii")
            # 풀폭 + 위아래 letterbox로 가로 비율 유지
            html = f"""
            <html>
            <head>
                <style>
                    body {{ margin:0; padding:0; background:#0f172a;
                            display:flex; align-items:center; justify-content:center;
                            overflow:hidden; height:100vh; }}
                    img {{ max-width:100%; max-height:100vh; object-fit:contain;
                           border-radius:6px; box-shadow:0 0 30px rgba(49,130,246,0.18); }}
                </style>
            </head>
            <body>
                <img src="data:image/png;base64,{b64}" alt="Live preview">
            </body>
            </html>
            """
            if self._browser_view is not None:
                try:
                    self._browser_view.setHtml(html)
                except Exception:
                    pass
        except Exception:
            pass

    def _start_live_preview_watcher(self) -> None:
        """set_busy(True) 시점에 live preview 파일 폴링 시작."""
        try:
            workspace_root = Path(__file__).resolve().parents[3]
        except Exception:
            workspace_root = Path.cwd()
        self._live_preview_path = workspace_root / "artifacts" / "tmp" / "gui_live_preview" / "latest.png"
        self._live_preview_last_mtime = 0.0
        # 진단용 stdout — 사용자가 콘솔에서 폴링 시작 확인 가능
        print(f"[LivePreview] watcher started, polling: {self._live_preview_path}")
        # 이전 파일 삭제 — 이전 run의 잔재가 즉시 표시되지 않도록
        try:
            if self._live_preview_path.exists():
                self._live_preview_path.unlink()
                print("[LivePreview] stale file removed")
        except Exception:
            pass
        # 첫 프레임 도착 전 placeholder — 사용자가 라이브 프리뷰가 곧 도착함을 알 수 있도록
        if self._browser_view is not None:
            self._browser_view.setHtml("""
                <html><body style="margin:0; padding:0; height:100vh; background:#0f172a;
                                   display:flex; align-items:center; justify-content:center;
                                   color:#94a3b8; font-family:'Pretendard','Noto Sans KR',sans-serif;">
                    <div style="text-align:center; max-width:440px; padding:0 20px;">
                        <div style="display:inline-flex; gap:8px; margin-bottom:18px;">
                            <span style="width:12px; height:12px; background:#3182f6; border-radius:50%; opacity:0.4; animation:pulse 1.4s infinite ease-in-out;"></span>
                            <span style="width:12px; height:12px; background:#3182f6; border-radius:50%; opacity:0.7; animation:pulse 1.4s infinite ease-in-out 0.2s;"></span>
                            <span style="width:12px; height:12px; background:#3182f6; border-radius:50%; opacity:1.0; animation:pulse 1.4s infinite ease-in-out 0.4s;"></span>
                        </div>
                        <div style="font-size:16px; font-weight:700; color:#f3f4f6; margin-bottom:6px;">라이브 프리뷰 대기 중</div>
                        <div style="font-size:12px; color:#94a3b8; margin-bottom:16px;">첫 화면이 도착하면 자동으로 표시됩니다.</div>
                        <div style="margin-top:14px; padding:10px 14px; background:rgba(255,255,255,0.05); border-radius:8px; font-size:11px; color:#6b7280; line-height:1.5; text-align:left;">
                            <b style="color:#94a3b8;">참고:</b> "공개 영역 확인"처럼 클릭/입력 동작이 없는 시나리오는<br/>
                            AI가 DOM만 분석하고 끝나서 캡처가 발생하지 않을 수 있습니다.<br/>
                            검색·로그인 같은 인터랙티브 시나리오에서는 단계별 화면이 실시간 표시됩니다.
                        </div>
                    </div>
                    <style>
                        @keyframes pulse {
                            0%, 80%, 100% { transform: scale(0.6); opacity: 0.3; }
                            40% { transform: scale(1.0); opacity: 1.0; }
                        }
                    </style>
                </body></html>
            """)
        # 초기 폴링 빠르게 (500ms) — 첫 프레임 빠르게 잡기
        if not self._live_preview_timer.isActive():
            self._live_preview_timer.setInterval(700)
            self._live_preview_timer.start()

    def _stop_live_preview_watcher(self) -> None:
        if self._live_preview_timer.isActive():
            self._live_preview_timer.stop()

    def _reset_exec_metrics(self) -> None:
        """ExecRunCard의 모든 KPI metric 값을 초기화."""
        from datetime import datetime
        self._elapsed_seconds = 0
        # 벤치마크 진행 메트릭 상태 초기화
        self._benchmark_metric_state = {"total": 0, "completed": 0, "pass": 0, "fail": 0, "current_step": ""}
        if hasattr(self, "_metric_elapsed_value"):
            self._metric_elapsed_value.setText("00:00:00")
        if hasattr(self, "_metric_step_value"):
            self._metric_step_value.setText("- / -")
        if hasattr(self, "_metric_pass_value"):
            self._metric_pass_value.setText("0")
        if hasattr(self, "_metric_fail_value"):
            self._metric_fail_value.setText("0")
        if hasattr(self, "_overall_progress_widget") and self._overall_progress_widget:
            self._overall_progress_widget.set_value(0)
        if hasattr(self, "_overall_progress_detail") and self._overall_progress_detail:
            self._overall_progress_detail.setText("0 / 0 완료")
        if hasattr(self, "_current_case_title"):
            self._current_case_title.setText("-")
        if hasattr(self, "_current_case_desc"):
            self._current_case_desc.setText("실행이 시작되면 현재 케이스가 표시됩니다.")
        if hasattr(self, "_current_case_step_pill"):
            self._current_case_step_pill.setText("단계 -")
        if hasattr(self, "_review_url_label") and hasattr(self, "_selected_site_url"):
            self._review_url_label.setText(self._selected_site_url or "about:blank")
        # 신규 KPI: 시작 시간 표시 + 로그 카운트 리셋
        if hasattr(self, "_metric_start_time_label") and self._metric_start_time_label is not None:
            self._metric_start_time_label.setText(f"시작 {datetime.now().strftime('%H:%M:%S')}")
        if hasattr(self, "_metric_log_count") and self._metric_log_count is not None:
            self._metric_log_count.setText("0")
        # _selected_case_count는 Step 2에서 이미 설정 — 유지

    def _set_exec_status(self, state: str, label_text: str | None = None) -> None:
        """ExecRunCard 헤더의 상태 pill + 타이틀 업데이트.

        state: 'running' | 'done' | 'failed'
        """
        if hasattr(self, "_exec_status_pill"):
            self._exec_status_pill.setProperty("state", state)
            mapping = {"running": "실행 중", "done": "완료", "failed": "실패"}
            self._exec_status_pill.setText(label_text or mapping.get(state, state))
            style = self._exec_status_pill.style()
            if style is not None:
                style.unpolish(self._exec_status_pill)
                style.polish(self._exec_status_pill)
                self._exec_status_pill.update()
        if hasattr(self, "_exec_title"):
            mapping_title = {
                "running": "테스트 실행 중",
                "done": "테스트 완료",
                "failed": "테스트 실패",
            }
            self._exec_title.setText(mapping_title.get(state, "테스트 실행"))
        if hasattr(self, "_exec_subtitle"):
            mapping_subtitle = {
                "running": "AI가 테스트를 실행 중입니다.",
                "done": "모든 테스트 실행이 완료되었습니다.",
                "failed": "테스트 실행이 실패했습니다.",
            }
            self._exec_subtitle.setText(mapping_subtitle.get(state, ""))
        # status pill 옆 인라인 3-dot 로더 — running일 때만 애니메이션
        if hasattr(self, "_exec_status_dots") and self._exec_status_dots is not None:
            self._exec_status_dots.set_active(state == "running")
        # _current_case_spinner는 legacy 호환용 (보이지 않음) — start()는 호출하지 않음.

    def set_busy(self, busy: bool, *, message: str | None = None) -> None:
        self._is_busy = busy
        if busy:
            # 새 테스트 시작 — 이전 실행 결과 액션 바 숨김
            if hasattr(self, "_result_action_bar") and self._result_action_bar is not None:
                self._result_action_bar.setVisible(False)
            # 테스트 시작 시점에만 screencast 클라이언트 가동 (브라우저 없을 때 에러 스팸 방지)
            self._start_screencast_if_needed()
            self._reset_exec_metrics()
            self._elapsed_timer.start()
            # Live preview 파일 폴링 시작 — agent가 dump하는 screenshot을 1.2초마다 표시
            self._start_live_preview_watcher()
            self._set_exec_status("running")
            # 진행률 위젯 아래에 3-dot wave 로딩 모션 활성화
            if self._overall_progress_widget is not None:
                self._overall_progress_widget.set_dots_active(True)
            # 새 실행 — 로그 탭 자동 전환 트리거 재무장
            self._log_tab_auto_switched = False
            self.show_review_stage()
            # 실행 시작: 로그를 비우고 전체 모드 사용 (Step 3에서 모든 worker 출력을 사용자가 보도록)
            self._full_execution_logs = []
            self._log_mode = "full"
            if self._log_output:
                self._log_output.clear()
            if self._view_logs_button:
                self._view_logs_button.setEnabled(False)
            # 브라우저 뷰는 이미 사용자가 사이트 선택 시 load_url로 페이지를 로드했음.
            # set_busy(True) 시 그 페이지를 덮어쓰지 않음 — 사용자가 계속 페이지를 보면서
            # 진행 상황을 확인할 수 있도록 함. 백엔드 screencast 프레임이 도착하면 자동으로 갈아끼움.
        else:
            # 실행 완료: 상세 로그 보기 활성화, 타이머 정지, 상태 pill = 완료
            if self._view_logs_button:
                self._view_logs_button.setEnabled(True)
            if self._elapsed_timer.isActive():
                self._elapsed_timer.stop()
            self._stop_live_preview_watcher()
            # 도트 모션 비활성화
            if self._overall_progress_widget is not None:
                self._overall_progress_widget.set_dots_active(False)
            self._set_exec_status("done")

        self._start_button.setEnabled(not busy)
        self._cancel_button.setEnabled(busy)
        self._back_to_setup_button.setEnabled(
            self._workflow_stage == "review" and not busy
        )
        self._drop_area.setEnabled(not busy)
        self._url_input.setEnabled(not busy)
        if hasattr(self, "_benchmark_add_button"):
            self._benchmark_add_button.setEnabled(not busy)
        if hasattr(self, "_benchmark_view_button"):
            self._benchmark_view_button.setEnabled(not busy)
        if hasattr(self, "_source_none_button"):
            self._source_none_button.setEnabled(not busy)
        if hasattr(self, "_source_file_button"):
            self._source_file_button.setEnabled(not busy)
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
        if hasattr(self, "_review_url_label") and url:
            self._review_url_label.setText(url)

    def set_url_field(self, url: str) -> None:
        self._url_input.setText(url)
        self._selected_site_url = (url or "").strip()
        self._update_selected_site_summary()
        # 카드 selected 상태 업데이트
        for card in getattr(self, "_site_cards", []) or []:
            card.set_selected(card._url == self._selected_site_url)  # noqa: SLF001
        # Step 3 브라우저 chrome — 주소창 + 탭 타이틀 동기화
        if hasattr(self, "_review_url_label") and self._review_url_label is not None:
            self._review_url_label.setText(self._selected_site_url or "about:blank")
        if hasattr(self, "_browser_tab_title") and self._browser_tab_title is not None:
            try:
                from urllib.parse import urlparse
                host = urlparse(self._selected_site_url).netloc or "새 탭"
                self._browser_tab_title.setText(host)
            except Exception:
                pass

    def get_url_field_value(self) -> str:
        return self._url_input.text().strip()

    def set_feature_query(self, query: str) -> None:
        if not hasattr(self, "_feature_input"):
            return
        self._feature_input.setText(query)
        self._current_feature_query = query.strip()
        self._feature_input_container.setVisible(self._selected_run_mode == "quick")

    def set_benchmark_catalog(
        self,
        catalog: Sequence[Mapping[str, Any]],
        *,
        selected_site_key: str | None = None,
        selected_url: str | None = None,
    ) -> None:
        self._benchmark_catalog = [dict(item) for item in catalog]
        # Step 1 사이트 그리드를 실제 benchmark 카탈로그로 갱신
        # (시작 시점에 controller가 set_benchmark_catalog을 호출하므로 site grid의 카드가
        # 항상 등록된 벤치 사이트와 1:1로 매핑되어 site_key 해석이 항상 성공)
        if hasattr(self, "_site_grid_layout") and self._benchmark_catalog:
            grid_entries: list[dict[str, str]] = []
            for item in self._benchmark_catalog:
                url = str(item.get("default_url") or "").strip()
                if not url:
                    continue
                label = str(item.get("label") or item.get("key") or "?")
                grid_entries.append({
                    "label": label,
                    "url": url,
                    "initial": self._guess_site_initial(label),
                    "color": self._guess_brand_color(str(item.get("key") or label)),
                })
            if grid_entries:
                self._populate_site_grid(grid_entries)
        effective_site_key = str(selected_site_key or self._selected_benchmark_site_key or "").strip()
        selected_item: Mapping[str, Any] | None = None
        if effective_site_key:
            for item in self._benchmark_catalog:
                if str(item.get("key") or "").strip() == effective_site_key:
                    selected_item = item
                    break
        if selected_item is None and self._benchmark_catalog:
            selected_item = self._benchmark_catalog[0]
        self._selected_benchmark_site_key = str((selected_item or {}).get("key") or "").strip()
        self._selected_benchmark_url = str(
            selected_url
            or (selected_item or {}).get("default_url")
            or self._selected_benchmark_url
            or ""
        ).strip()

        if hasattr(self, "_benchmark_stage_summary_label"):
            if selected_item is None:
                self._benchmark_stage_summary_label.setText(
                    "아직 선택된 벤치가 없습니다. 벤치 관리에서 대상 사이트를 골라주세요."
                )
                if hasattr(self, "_benchmark_stage_detail_label"):
                    self._benchmark_stage_detail_label.setText(
                        "벤치 관리에서 대상 목록을 고르면 테스트 목록과 실행 버튼이 이어서 보입니다."
                    )
                if hasattr(self, "_benchmark_stage_site_metric"):
                    self._benchmark_stage_site_metric.setText("사이트 -")
                if hasattr(self, "_benchmark_stage_url_metric"):
                    self._benchmark_stage_url_metric.setText("링크 -")
            else:
                label = str(selected_item.get("label") or selected_item.get("key") or "-")
                status_text = str(selected_item.get("status_text") or "")
                self._benchmark_stage_summary_label.setText(
                    f"선택된 대상: {label}\n{self._selected_benchmark_url or '-'}"
                )
                if hasattr(self, "_benchmark_stage_detail_label"):
                    self._benchmark_stage_detail_label.setText(
                        status_text or "관리 화면에서 테스트 목록을 고르고 실행할 수 있습니다."
                    )
                if hasattr(self, "_benchmark_stage_site_metric"):
                    self._benchmark_stage_site_metric.setText(f"사이트 {label}")
                if hasattr(self, "_benchmark_stage_url_metric"):
                    self._benchmark_stage_url_metric.setText(f"링크 {self._selected_benchmark_url or '-'}")

    def get_selected_benchmark_site(self) -> str:
        return self._selected_benchmark_site_key

    def get_selected_benchmark_url(self) -> str:
        return self._selected_benchmark_url

    def show_html_in_browser(self, html_content: str) -> None:
        """브라우저 뷰에 HTML 콘텐츠를 표시합니다"""
        self._browser_view.setHtml(html_content)

    def _open_grafana_dashboard(self) -> None:
        """결과 액션 바의 Grafana 버튼 클릭 — 외부 기본 브라우저에서 대시보드 열기."""
        import os
        url = (
            getattr(self, "_result_grafana_url", "")
            or os.getenv("GAIA_GRAFANA_URL", "").strip()
            or "http://15.164.24.65:3000"
        )
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl as _QUrl
            QDesktopServices.openUrl(_QUrl(url))
        except Exception:
            # fallback — Python webbrowser
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass

    def _open_result_folder(self) -> None:
        """결과 액션 바의 폴더 열기 버튼 클릭 — OS 파일 탐색기에서 output_dir 열기."""
        import os
        from pathlib import Path
        folder = getattr(self, "_result_output_dir", "").strip()
        if not folder:
            return
        path = Path(folder)
        if not path.exists():
            return
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl as _QUrl
            QDesktopServices.openUrl(_QUrl.fromLocalFile(str(path)))
        except Exception:
            # fallback — os.startfile (Windows) or subprocess
            try:
                if os.name == "nt":
                    os.startfile(str(path))  # type: ignore[attr-defined]
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", str(path)])
            except Exception:
                pass

    def show_result_card(self, summary: Mapping[str, Any]) -> None:
        """Step 3 완료 시점에 호출 — visible Result Action Bar를 활성화.

        신규 단일 컬럼 레이아웃에서는 _browser_view가 invisible legacy stub이라
        HTML 렌더링이 사용자에게 보이지 않음. 대신 visible Result Action Bar에
        결과 요약 + Grafana 링크 + 결과 폴더 버튼을 표시한다.
        """
        try:
            import html as _html
            import os

            status = str(summary.get("status") or "unknown").lower()
            reason = str(summary.get("reason") or "").strip()
            mode = str(summary.get("mode") or "").strip()
            site_label = str(summary.get("site_label") or "").strip()
            target_url = str(summary.get("target_url") or "").strip()
            total_runs = int(summary.get("total_runs") or summary.get("total_goals") or 0)
            successful = int(summary.get("successful_runs") or summary.get("successful_goals") or 0)
            failed = int(summary.get("failed_runs") or summary.get("failed_goals") or 0)
            blocked = int(summary.get("blocked_runs") or 0)
            output_dir = str(summary.get("output_dir") or "").strip()
            push_metrics_enabled = bool(summary.get("push_metrics"))

            # ─── Visible Result Action Bar 활성화 (신규 GUI의 핵심 결과 표시) ──
            if hasattr(self, "_result_action_bar") and self._result_action_bar is not None:
                # 상태별 색상/아이콘
                if status == "success" and total_runs > 0 and successful == total_runs:
                    state_key, icon_text, title_text = "success", "✓", f"테스트 완료 — {successful}/{total_runs} 통과"
                elif failed > 0 or blocked > 0 or (total_runs > 0 and successful < total_runs):
                    state_key, icon_text, title_text = "failed", "!", f"테스트 실패 — {successful}/{total_runs} 통과"
                else:
                    state_key, icon_text, title_text = "warn", "i", "테스트 완료"

                self._result_action_bar.setProperty("state", state_key)
                self._result_action_status_icon.setProperty("state", state_key)
                self._result_action_status_icon.setText(icon_text)
                self._result_action_title.setText(title_text)

                # 이유 표시 (실패/차단 시)
                if reason and reason != "-" and state_key != "success":
                    self._result_action_reason.setText(reason)
                    self._result_action_reason.setVisible(True)
                else:
                    self._result_action_reason.setText("")
                    self._result_action_reason.setVisible(False)

                # Grafana URL 저장 + 버튼 활성화
                grafana_base = os.getenv("GAIA_GRAFANA_URL", "").strip() or "http://15.164.24.65:3000"
                self._result_grafana_url = grafana_base
                self._result_grafana_button.setToolTip(f"외부 브라우저에서 {grafana_base} 열기")

                # 결과 폴더 (output_dir이 존재하는 경우)
                self._result_output_dir = output_dir
                self._result_open_folder_button.setVisible(bool(output_dir))
                if output_dir:
                    self._result_open_folder_button.setToolTip(f"폴더 열기: {output_dir}")

                # 스타일 재적용
                self._restyle(self._result_action_bar)
                self._restyle(self._result_action_status_icon)
                self._result_action_bar.setVisible(True)

            # 정합성 보강 — successful + failed >= total 안 되면 차이를 failed로 흡수
            if total_runs > 0 and successful + failed < total_runs:
                failed = total_runs - successful

            # 상태별 헤더 색상/배지 텍스트 (성공률은 별도로 항상 초록)
            if status == "success" and successful == total_runs and total_runs > 0:
                badge_bg, badge_fg, badge_text = "#ecfdf5", "#047857", "성공"
            elif failed > 0 or blocked > 0 or successful < total_runs:
                badge_bg, badge_fg, badge_text = "#fef2f2", "#b91c1c", "실패"
            else:
                badge_bg, badge_fg, badge_text = "#fffbeb", "#b45309", "완료"

            success_rate = (successful / total_runs * 100.0) if total_runs else 0.0
            # 성공률은 항상 초록색 (사용자 요청)
            rate_color = "#10b981"

            # Grafana 링크 — 항상 표시. env 변수가 없으면 기본 로컬 인스턴스(http://localhost:3000) 사용.
            grafana_base = os.getenv("GAIA_GRAFANA_URL", "").strip() or "http://15.164.24.65:3000"
            grafana_link_html = (
                f'<a href="{_html.escape(grafana_base)}" target="_blank" '
                f'style="display:inline-flex; align-items:center; gap:10px; '
                f'padding:12px 22px; border-radius:10px; background:#3182f6; '
                f'color:#ffffff; text-decoration:none; font-weight:700; font-size:13px;">'
                f'<span style="font-size:14px;">↗</span> Grafana 대시보드 열기'
                f'</a>'
            )
            grafana_hint_html = (
                f'<div style="margin-top:8px; font-size:11.5px; color:#8b95a1;">'
                f'대시보드 주소: <code style="background:#f2f4f6; padding:2px 6px; border-radius:4px; '
                f'color:#4e5968;">{_html.escape(grafana_base)}</code> '
                f'(GAIA_GRAFANA_URL 환경변수로 변경 가능)</div>'
            )

            artifact_html = ""
            if output_dir:
                safe_dir = _html.escape(output_dir)
                artifact_html = (
                    f'<div style="margin-top:14px; padding:12px 14px; background:#f9fafb; '
                    f'border:1px solid #e5e8eb; border-radius:10px; '
                    f'font-family:Consolas,monospace; font-size:11.5px; color:#4e5968; word-break:break-all;">'
                    f'{safe_dir}</div>'
                )

            reason_html = ""
            if reason and reason != "-":
                # reason quote의 좌측 보더 색상은 badge fg 컬러 사용 (의미적 일관성)
                reason_html = (
                    f'<div style="margin-top:18px; padding:14px 18px; background:#f9fafb; '
                    f'border-left:3px solid {badge_fg}; border-radius:6px; '
                    f'color:#333d4b; font-size:13px; line-height:1.55;">{_html.escape(reason)}</div>'
                )

            site_url_row = ""
            if site_label or target_url:
                site_url_row = (
                    f'<div style="margin-top:6px; color:#6b7684; font-size:12.5px;">'
                    f'{_html.escape(site_label)} · {_html.escape(target_url)}</div>'
                )

            doc = f"""
            <!DOCTYPE html><html><head><meta charset="utf-8">
            <style>
                body {{ margin:0; padding:32px; background:#f9fafb;
                       font-family:'Pretendard','Noto Sans KR','Apple SD Gothic Neo','Segoe UI',sans-serif;
                       color:#191f28; }}
                .container {{ max-width:780px; margin:0 auto; }}
                .hero {{ background:#ffffff; border:1px solid #e5e8eb; border-radius:14px;
                        padding:32px; box-shadow:0 1px 0 rgba(0,0,0,0.02); }}
                .hero-head {{ display:flex; align-items:center; gap:12px; margin-bottom:18px; }}
                .hero-badge {{ background:{badge_bg}; color:{badge_fg};
                              padding:6px 14px; border-radius:999px;
                              font-size:12px; font-weight:800; }}
                .hero-title {{ font-size:26px; font-weight:800; color:#191f28; letter-spacing:-0.4px; }}
                .metrics {{ display:grid; grid-template-columns:repeat(4, 1fr); gap:12px;
                           margin-top:24px; padding:18px; background:#f9fafb;
                           border:1px solid #e5e8eb; border-radius:12px; }}
                .metric {{ text-align:center; }}
                .metric-label {{ font-size:11.5px; color:#6b7684; font-weight:600; margin-bottom:6px; }}
                .metric-value {{ font-size:24px; font-weight:800; color:#191f28; }}
                .metric-value.pass {{ color:#10b981; }}
                .metric-value.fail {{ color:#ef4444; }}
                .metric-value.rate {{ color:{rate_color}; }}
                .actions {{ margin-top:24px; display:flex; gap:10px; flex-wrap:wrap; }}
            </style></head><body>
            <div class="container">
                <div class="hero">
                    <div class="hero-head">
                        <span class="hero-badge">{badge_text}</span>
                        <span style="color:#8b95a1; font-size:12px; font-weight:600;">{_html.escape(mode.upper())}</span>
                    </div>
                    <div class="hero-title">테스트 실행이 완료되었습니다</div>
                    {site_url_row}
                    {reason_html}

                    <div class="metrics">
                        <div class="metric">
                            <div class="metric-label">전체 실행</div>
                            <div class="metric-value">{total_runs}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">성공</div>
                            <div class="metric-value pass">{successful}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">실패</div>
                            <div class="metric-value fail">{failed}</div>
                        </div>
                        <div class="metric">
                            <div class="metric-label">성공률</div>
                            <div class="metric-value rate">{success_rate:.1f}%</div>
                        </div>
                    </div>

                    {artifact_html}

                    <div class="actions">
                        {grafana_link_html}
                    </div>
                    {grafana_hint_html}
                </div>
            </div>
            </body></html>
            """
            if self._browser_view is not None:
                self._browser_view.setHtml(doc)
        except Exception:
            # 결과 카드 렌더링 실패 시 silent — controller가 legacy HTML로 fallback할 것임
            pass

    def _show_replay_html(self, html_content: str) -> None:
        if not html_content:
            self._browser_view.setHtml("""
                <html>
                <body style="margin:0; padding:0; background:#1f2937; display:flex; align-items:center; justify-content:center; color:#9ca3af; font-family:'Pretendard','Noto Sans KR','Apple SD Gothic Neo',sans-serif;">
                    <div style="text-align:center;">
                        <div style="font-size:36px; margin-bottom:10px;">🎞</div>
                        <div style="font-size:14px; font-weight:700; color:#f3f4f6;">재생할 이미지가 없습니다</div>
                    </div>
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
            <body style="margin:0; padding:0; background:#1f2937; display:flex; align-items:center; justify-content:center; position:relative;">
                <div style="position:relative; display:inline-block;">
                    <img src="data:image/png;base64,{screenshot_base64}"
                         style="max-width:100%; max-height:100%; object-fit:contain;
                                box-shadow: 0 0 20px rgba(49, 130, 246, 0.5);
                                border: 2px solid rgba(49, 130, 246, 0.3);
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
        if is_low_information_screenshot(shot):
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
        """로그 전체를 파일로 다운로드합니다 (버튼 라벨이 '전체 다운로드'이므로 실제 download 동작).

        QFileDialog로 저장 위치 선택 → .txt 파일로 저장.
        로그가 비어있으면 안내 메시지만 표시.
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from datetime import datetime

        # 다운로드할 로그 수집 — _full_execution_logs 우선, 없으면 현재 _log_output 텍스트
        logs: list[str] = []
        if hasattr(self, "_full_execution_logs") and self._full_execution_logs:
            logs = list(self._full_execution_logs)
        elif hasattr(self, "_log_output") and self._log_output is not None:
            current_text = self._log_output.toPlainText().strip()
            if current_text:
                logs = current_text.splitlines()

        if not logs:
            QMessageBox.information(
                self,
                "다운로드할 로그 없음",
                "아직 실행된 로그가 없습니다. 테스트를 먼저 실행해 주세요.",
            )
            return

        # 기본 파일명 — 타임스탬프 포함
        default_name = f"gaia_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "로그 다운로드",
            default_name,
            "Text Files (*.txt);;Log Files (*.log);;All Files (*)",
        )
        if not file_path:
            return  # 사용자가 취소

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("# GAIA Test Execution Logs\n")
                f.write(f"# Exported: {datetime.now().isoformat()}\n")
                f.write(f"# Total lines: {len(logs)}\n")
                f.write("# " + "=" * 60 + "\n\n")
                for line in logs:
                    f.write(line.rstrip() + "\n")
            QMessageBox.information(
                self,
                "다운로드 완료",
                f"로그가 저장되었습니다:\n{file_path}\n\n총 {len(logs)}줄",
            )
        except Exception as exc:
            QMessageBox.critical(
                self,
                "다운로드 실패",
                f"로그 저장 중 오류가 발생했습니다:\n{exc}",
            )

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
            if str(file_path).lower().endswith(".json"):
                self.set_selected_input_source("bundle")
            else:
                self.set_selected_input_source("file")
            self.fileDropped.emit(file_path)

    def _handle_file_drop(self, file_path: str) -> None:
        """파일이 드롭되었을 때 feature_query를 저장하고 시그널을 발생시킵니다."""
        feature_query = (
            self._feature_input.text().strip()
            if hasattr(self, "_feature_input")
            else ""
        )
        self._current_feature_query = feature_query
        if str(file_path).lower().endswith(".json"):
            self.set_selected_input_source("bundle")
        else:
            self.set_selected_input_source("file")
        self.fileDropped.emit(file_path)

    def _clear_input_source(self) -> None:
        self.inputSourceCleared.emit()

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

    def _activate_benchmark_mode(self) -> None:
        self.set_selected_run_mode("benchmark")
        self._emit_benchmark_manage()

    def set_selected_run_mode(self, mode: str) -> None:
        normalized = mode if mode in {"quick", "ai", "bundle", "benchmark"} else "quick"
        self._selected_run_mode = normalized
        mapping = {
            "quick": getattr(self, "_quick_mode_button", None),
            "ai": getattr(self, "_ai_mode_button", None),
            "bundle": getattr(self, "_bundle_mode_button", None),
            "benchmark": getattr(self, "_benchmark_mode_button", None),
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
        if hasattr(self, "_standard_action_container"):
            self._standard_action_container.setVisible(normalized != "benchmark")
        # 신규 3단계 UX: mode 변경만으로 stage를 자동 전환하지 않음.
        # Step 2 (test_case_stage)에서 선택 완료 시 _on_case_selection_complete가
        # 적절한 시그널을 발생시키고, controller가 set_busy(True)를 호출하여 Step 3로 전환.

    def set_selected_input_source(self, source: str) -> None:
        normalized = source if source in {"none", "file", "bundle"} else "none"
        self._selected_input_source = normalized
        mapping = {
            "none": getattr(self, "_source_none_button", None),
            "file": getattr(self, "_source_file_button", None),
            "bundle": getattr(self, "_load_plan_button", None),
        }
        for key, button in mapping.items():
            if button is None:
                continue
            selected = key == normalized
            button.setChecked(selected)
            button.setProperty("modeSelected", selected)
            button.style().unpolish(button)
            button.style().polish(button)

    def get_selected_run_mode(self) -> str:
        return self._selected_run_mode

    def _emit_benchmark_save(self) -> None:
        self.benchmarkSaveRequested.emit(self.get_selected_benchmark_site(), self.get_selected_benchmark_url())

    def _emit_benchmark_manage(self) -> None:
        self.benchmarkManageRequested.emit(self.get_selected_benchmark_site(), self.get_selected_benchmark_url())

    def _emit_benchmark_run(self) -> None:
        self.benchmarkRunRequested.emit(self.get_selected_benchmark_site(), self.get_selected_benchmark_url())

    def _emit_benchmark_view(self) -> None:
        self.benchmarkViewRequested.emit(
            self.get_selected_benchmark_site(),
            self.get_selected_benchmark_url(),
        )

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
        # Step 3 ExecRunCard 메트릭 동기화
        if step is not None and hasattr(self, "_current_case_step_pill"):
            step_text = str(step or "").strip() or "-"
            self._current_case_step_pill.setText(f"단계 {step_text}")

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
            self.set_selected_input_source("bundle")
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
        # 사이트 그리드 반응형 재배치 — 윈도우 너비 변화에 따라 컬럼 수 자동 조정
        if hasattr(self, "_site_cards") and self._site_cards:
            try:
                self._rearrange_site_grid()
            except Exception:
                pass

    def _emit_url_submitted(self) -> None:
        url = self._url_input.text().strip()
        if url:
            self.urlSubmitted.emit(url)

    def _setup_screencast(self) -> None:
        """CDP 스크린캐스트 WebSocket 클라이언트를 설정합니다.

        시작은 set_busy(True) 시점에 lazily 수행하여 brower가 없을 때
        무한 재시도/에러 로그가 발생하지 않도록 합니다.
        """
        self._screencast_client = ScreencastClient()
        self._screencast_client.frame_received.connect(self._update_screencast_frame)
        self._screencast_client.connection_status_changed.connect(
            self._on_screencast_connection_changed
        )
        self._screencast_client.error_occurred.connect(self._on_screencast_error)
        self._screencast_started = False

    def _start_screencast_if_needed(self) -> None:
        if self._screencast_client is None or self._screencast_started:
            return
        self._screencast_client.start()
        self._screencast_started = True
        # 조용히 시작 — 백엔드 없을 때 로그 스팸 방지

    def _stop_screencast(self) -> None:
        if self._screencast_client is None or not self._screencast_started:
            return
        try:
            self._screencast_client.stop()
        except Exception:
            pass
        self._screencast_started = False

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
                    background: #1f2937;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    overflow: hidden;
                }}
                img {{
                    max-width: 100%;
                    max-height: 100vh;
                    object-fit: contain;
                    box-shadow: 0 0 20px rgba(49, 130, 246, 0.3);
                    border: 1px solid rgba(49, 130, 246, 0.2);
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
        """스크린캐스트 연결 상태 변경 핸들러 — 로그 없이 조용히 처리."""
        if not connected:
            # 연결 끊김 시 안내 메시지만 표시 (로그 스팸 없음)
            if not self._is_busy:  # busy가 아닐 때만 메시지 표시
                self._browser_view.setHtml("""
                    <html>
                    <body style="margin:0; padding:0; background:#1f2937; display:flex; align-items:center; justify-content:center; color:#9ca3af; font-family:'Pretendard','Noto Sans KR','Apple SD Gothic Neo',sans-serif;">
                        <div style="text-align:center;">
                            <div style="font-size:42px; margin-bottom:12px;">🖥</div>
                            <div style="font-size:16px; font-weight:700; color:#f3f4f6; margin-bottom:6px;">브라우저 세션 없음</div>
                            <div style="font-size:12px; color:#9ca3af;">테스트를 시작하면 실시간 화면이 표시됩니다.</div>
                        </div>
                    </body>
                    </html>
                """)

    def _on_screencast_error(self, error_message: str) -> None:
        """스크린캐스트 에러 핸들러 — 조용히 무시 (백엔드 없는 정상 케이스)."""
        return

    def closeEvent(self, event) -> None:
        """창 닫기 이벤트 - 스크린캐스트 클라이언트 정리"""
        self._stop_screencast()
        super().closeEvent(event)
