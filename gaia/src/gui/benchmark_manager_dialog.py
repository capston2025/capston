"""Dedicated benchmark management dialog for GUI benchmark mode."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gaia.src.benchmark_manager import (
    append_scenario_to_suite,
    build_benchmark_site_catalog,
    build_scenario_payload,
    build_single_scenario_suite_payload,
    build_url_history,
    create_custom_site_definition,
    create_custom_suite_payload,
    default_scenario_name,
    delete_custom_benchmark_site,
    delete_scenario_from_suite,
    load_benchmark_registry,
    load_suite_payload,
    resolve_benchmark_site,
    save_benchmark_registry,
    save_suite_payload,
    upsert_benchmark_site_url,
    upsert_custom_benchmark_site,
    replace_scenario_in_suite,
)
from gaia.src.gui.asset_widgets import GuiAssetLabel


_BENCHMARK_DIALOG_STYLESHEET = """
QDialog {
    background: #f9fafb;
}

QWidget {
    color: #191f28;
    font-family: 'Apple SD Gothic Neo', 'Noto Sans KR', 'Segoe UI', sans-serif;
    font-size: 12px;
}

QLabel#DialogTitle {
    font-size: 26px;
    font-weight: 700;
    color: #191f28;
}

QLabel#DialogSubtitle {
    font-size: 13px;
    color: #6b7684;
}

QLabel#SectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #191f28;
}

QLabel#SiteCountBadge {
    color: #1b64da;
    background: #e8f3ff;
    border: 1px solid #d6e8ff;
    border-radius: 999px;
    padding: 5px 9px;
    font-size: 11px;
    font-weight: 800;
}

QLabel#FieldLabel {
    font-size: 12px;
    font-weight: 700;
    color: #4e5968;
}

QLabel#StatusLabel {
    color: #4e5968;
    background: #f9fafb;
    border: 1px solid #f2f4f6;
    border-radius: 12px;
    padding: 14px 16px;
}

QLabel#CompactStatusLabel {
    color: #4e5968;
    background: rgba(255, 255, 255, 0.72);
    border: 1px solid #e5edf7;
    border-radius: 12px;
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
}

QFrame#ScenarioVisualPanel {
    background: #f7fbff;
    border: 1px solid #dbeafe;
    border-radius: 16px;
}

QFrame#SelectedSitePanel {
    background: #f7fbff;
    border: 1px solid #dbeafe;
    border-radius: 18px;
}

QLabel#SelectedSiteKicker {
    color: #1b64da;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.4px;
}

QLabel#SelectedSiteTitle {
    color: #191f28;
    font-size: 18px;
    font-weight: 700;
}

QLabel#SelectedSiteMeta {
    color: #6b7684;
    font-size: 12px;
    font-weight: 600;
}

QLabel#ScenarioVisualImage {
    background: transparent;
}

QLabel#ScenarioVisualKicker {
    color: #1b64da;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.4px;
}

QLabel#ScenarioVisualTitle {
    color: #191f28;
    font-size: 17px;
    font-weight: 800;
}

QLabel#ScenarioVisualDescription {
    color: #6b7684;
    font-size: 12px;
}

QFrame#ScenarioEmptyPanel {
    background: #ffffff;
    border: 1px dashed #d6e8ff;
    border-radius: 16px;
}

QLabel#ScenarioEmptyTitle {
    color: #191f28;
    font-size: 15px;
    font-weight: 800;
}

QLabel#ScenarioEmptyDescription {
    color: #6b7684;
    font-size: 12px;
}

QFrame#ManagerCard {
    background: #ffffff;
    border: 1px solid #e5e8eb;
    border-radius: 14px;
}

QLineEdit,
QComboBox {
    background: #ffffff;
    color: #191f28;
    border: 1px solid #d1d6db;
    border-radius: 12px;
    padding: 8px 12px;
    min-height: 24px;
}

QTextEdit {
    background: #ffffff;
    color: #191f28;
    border: 1px solid #d1d6db;
    border-radius: 12px;
    padding: 12px 14px;
}

QLineEdit:focus,
QTextEdit:focus,
QComboBox:focus {
    border: 1px solid #3182f6;
}

QComboBox::drop-down {
    border: none;
    width: 28px;
}

QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #e5e8eb;
    selection-background-color: #e8f3ff;
    selection-color: #1b64da;
    color: #191f28;
}

QListWidget#SiteList,
QListWidget#ScenarioList {
    background: #ffffff;
    border: 1px solid #f2f4f6;
    border-radius: 12px;
    padding: 8px;
    outline: none;
}

QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 4px 2px 4px 2px;
}

QScrollBar::handle:vertical {
    background: #d1d6db;
    border-radius: 4px;
    min-height: 28px;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
}

QListWidget#SiteList::item {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 2px;
    margin: 2px 0px;
}

QListWidget#SiteList::item:selected {
    background: transparent;
    border: 1px solid transparent;
    color: #1b64da;
}

QFrame#SiteItem {
    background: #ffffff;
    border: 1px solid transparent;
    border-radius: 10px;
}

QFrame#SiteItem[selected="true"] {
    background: #f2f8ff;
    border: 1px solid #b2d4ff;
}

QLabel#SiteItemName {
    color: #191f28;
    font-size: 12px;
    font-weight: 700;
}

QLabel#SiteItemMeta {
    color: #8b95a1;
    font-size: 10.5px;
    font-weight: 600;
}

QListWidget#ScenarioList::item {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 2px;
    margin: 3px 0px;
}

QListWidget#ScenarioList::item:selected {
    background: transparent;
    border: 1px solid transparent;
    color: #1b64da;
}

QFrame#ScenarioItem {
    background: #ffffff;
    border: 1px solid #edf0f3;
    border-radius: 12px;
}

QFrame#ScenarioItem[selected="true"] {
    background: #f2f8ff;
    border: 1px solid #b2d4ff;
}

QLabel#ScenarioItemId {
    color: #1b64da;
    font-size: 12px;
    font-weight: 700;
}

QLabel#ScenarioItemName {
    color: #191f28;
    font-size: 13.5px;
    font-weight: 700;
}

QLabel#ScenarioItemGoal {
    color: #6b7684;
    font-size: 12px;
}

QLabel#ScenarioItemMeta {
    color: #8b95a1;
    font-size: 11px;
    font-weight: 600;
}

QPushButton {
    min-height: 18px;
    border-radius: 12px;
    padding: 9px 14px;
    background: #3182f6;
    color: #ffffff;
    border: 1px solid #3182f6;
    font-weight: 600;
}

QPushButton:hover {
    background: #1b64da;
    border: 1px solid #1b64da;
}

QPushButton[tone="neutral"] {
    background: #f2f4f6;
    color: #4e5968;
    border: 1px solid #e5e8eb;
}

QPushButton[tone="neutral"]:hover {
    background: #e5e8eb;
    color: #333d4b;
    border: 1px solid #d1d6db;
}

QPushButton[tone="danger"] {
    background: #f04452;
    border: 1px solid #f04452;
}

QPushButton[tone="danger"]:hover {
    background: #d92d20;
    border: 1px solid #d92d20;
}

QPushButton[tone="neutral"]:disabled,
QPushButton[tone="danger"]:disabled {
    background: #f2f4f6;
    border: 1px solid #e5e8eb;
    color: #b0b8c1;
}
"""


def _apply_dialog_styles(dialog: QDialog) -> None:
    dialog.setStyleSheet(_BENCHMARK_DIALOG_STYLESHEET)


class _SiteEditorDialog(QDialog):
    def __init__(
        self,
        *,
        existing: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        current = dict(existing or {})
        self.setWindowTitle("벤치 사이트 설정")
        self.resize(420, 200)
        _apply_dialog_styles(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        title = QLabel("벤치 사이트 설정", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setSpacing(12)
        layout.addLayout(form)

        self._label_input = QLineEdit(str(current.get("label") or "").strip(), self)
        self._url_input = QLineEdit(str(current.get("default_url") or "").strip(), self)
        self._site_key_input = QLineEdit(str(current.get("site_key") or "").strip(), self)
        form.addRow("사이트 이름", self._label_input)
        form.addRow("기본 링크", self._url_input)
        form.addRow("site_key (옵션)", self._site_key_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText("저장")
        if cancel_button is not None:
            cancel_button.setText("취소")
            cancel_button.setProperty("tone", "neutral")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self._label_input.text().strip():
            QMessageBox.warning(self, "입력 오류", "사이트 이름을 입력해주세요.")
            self._label_input.setFocus()
            return
        if not self._url_input.text().strip():
            QMessageBox.warning(self, "입력 오류", "기본 링크를 입력해주세요.")
            self._url_input.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "label": self._label_input.text().strip(),
            "default_url": self._url_input.text().strip(),
            "site_key": self._site_key_input.text().strip(),
        }


class _ScenarioEditorDialog(QDialog):
    def __init__(
        self,
        *,
        existing: Mapping[str, Any] | None = None,
        default_url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        current = dict(existing or {})
        self.setWindowTitle("벤치 테스트 설정")
        self.resize(640, 320)
        _apply_dialog_styles(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)

        title = QLabel("벤치 테스트 설정", self)
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setSpacing(12)
        layout.addLayout(form)

        self._name_input = QLineEdit(default_scenario_name(current), self)
        self._url_input = QLineEdit(str(current.get("url") or default_url or "").strip(), self)
        self._goal_input = QTextEdit(self)
        self._goal_input.setPlainText(str(current.get("goal") or "").strip())
        self._time_budget_input = QLineEdit(str(current.get("time_budget_sec") or 300), self)

        form.addRow("테스트 이름", self._name_input)
        form.addRow("url", self._url_input)
        form.addRow("goal", self._goal_input)
        form.addRow("time_budget_sec", self._time_budget_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            ok_button.setText("저장")
        if cancel_button is not None:
            cancel_button.setText("취소")
            cancel_button.setProperty("tone", "neutral")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self._name_input.text().strip():
            QMessageBox.warning(self, "입력 오류", "테스트 이름을 입력해주세요.")
            self._name_input.setFocus()
            return
        if not self._url_input.text().strip():
            QMessageBox.warning(self, "입력 오류", "url을 입력해주세요.")
            self._url_input.setFocus()
            return
        if not self._goal_input.toPlainText().strip():
            QMessageBox.warning(self, "입력 오류", "goal을 입력해주세요.")
            self._goal_input.setFocus()
            return
        try:
            max(1, int(self._time_budget_input.text().strip()))
        except Exception:
            QMessageBox.warning(self, "입력 오류", "time_budget_sec는 1 이상의 정수여야 합니다.")
            self._time_budget_input.setFocus()
            return
        self.accept()

    def values(self) -> dict[str, Any]:
        return {
            "test_name": self._name_input.text().strip(),
            "url": self._url_input.text().strip(),
            "goal": self._goal_input.toPlainText().strip(),
            "time_budget_sec": max(1, int(self._time_budget_input.text().strip())),
        }


def _slugify_site_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


class BenchmarkManagerDialog(QDialog):
    catalogMutated = Signal(str, str)
    runRequested = Signal(str, str, str)
    viewRequested = Signal(str, str)

    def __init__(
        self,
        *,
        workspace_root: Path,
        registry_path: Path | None = None,
        selected_site_key: str = "",
        selected_url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace_root = Path(workspace_root)
        self._registry_path = registry_path
        self._selected_site_key = str(selected_site_key or "").strip()
        self._selected_url = str(selected_url or "").strip()
        self._registry: dict[str, Any] = {}
        self._catalog: list[dict[str, Any]] = []
        self._suite_payload: dict[str, Any] = {"scenarios": []}

        self.setWindowTitle("벤치 관리")
        self.resize(920, 700)
        self.setObjectName("BenchmarkManagerDialog")
        _apply_dialog_styles(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        title = QLabel("벤치 관리", self)
        title.setObjectName("DialogTitle")
        header_row.addWidget(title)
        header_row.addStretch()
        close_button = QPushButton("닫기", self)
        close_button.setProperty("tone", "neutral")
        close_button.clicked.connect(self.accept)
        header_row.addWidget(close_button)
        layout.addLayout(header_row)

        subtitle = QLabel(
            "사이트를 먼저 고르면 해당 테스트 목록과 실행 동작만 이어서 보입니다.",
            self,
        )
        subtitle.setObjectName("DialogSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        body_layout = QHBoxLayout()
        body_layout.setSpacing(16)
        layout.addLayout(body_layout, stretch=1)

        site_card = QFrame(self)
        site_card.setObjectName("ManagerCard")
        site_layout = QVBoxLayout(site_card)
        site_layout.setContentsMargins(18, 18, 18, 18)
        site_layout.setSpacing(14)
        site_card.setMinimumWidth(300)
        site_card.setMaximumWidth(340)

        site_header = QHBoxLayout()
        site_header.setSpacing(10)
        site_title = QLabel("벤치마킹 대상", site_card)
        site_title.setObjectName("SectionTitle")
        site_header.addWidget(site_title)
        site_header.addStretch()
        self._site_count_label = QLabel("0", site_card)
        self._site_count_label.setObjectName("SiteCountBadge")
        site_header.addWidget(self._site_count_label)
        site_layout.addLayout(site_header)

        self._site_list = QListWidget(self)
        self._site_list.setObjectName("SiteList")
        self._site_list.setMinimumHeight(360)
        self._site_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._site_list.currentRowChanged.connect(self._on_site_changed)
        site_layout.addWidget(self._site_list, stretch=1)

        self._add_site_button = QPushButton("사이트 추가", self)
        self._add_site_button.setProperty("tone", "neutral")
        self._add_site_button.clicked.connect(self._add_site)
        site_layout.addWidget(self._add_site_button)
        body_layout.addWidget(site_card)

        scenario_card = QFrame(self)
        scenario_card.setObjectName("ManagerCard")
        scenario_layout = QVBoxLayout(scenario_card)
        scenario_layout.setContentsMargins(18, 18, 18, 18)
        scenario_layout.setSpacing(14)

        selected_panel = QFrame(scenario_card)
        selected_panel.setObjectName("SelectedSitePanel")
        selected_panel.setMaximumHeight(178)
        selected_layout = QHBoxLayout(selected_panel)
        selected_layout.setContentsMargins(16, 12, 16, 12)
        selected_layout.setSpacing(12)
        selected_copy = QVBoxLayout()
        selected_copy.setSpacing(5)
        selected_kicker = QLabel("SELECTED TARGET", selected_panel)
        selected_kicker.setObjectName("SelectedSiteKicker")
        selected_copy.addWidget(selected_kicker)
        self._selected_site_title_label = QLabel("대상을 선택하세요", selected_panel)
        self._selected_site_title_label.setObjectName("SelectedSiteTitle")
        selected_copy.addWidget(self._selected_site_title_label)
        self._selected_site_meta_label = QLabel("왼쪽 목록에서 벤치마킹 대상을 고르면 테스트 목록이 열립니다.", selected_panel)
        self._selected_site_meta_label.setObjectName("SelectedSiteMeta")
        self._selected_site_meta_label.setWordWrap(True)
        selected_copy.addWidget(self._selected_site_meta_label)

        target_label = QLabel("대상 링크", selected_panel)
        target_label.setObjectName("FieldLabel")
        selected_copy.addWidget(target_label)
        self._url_combo = QComboBox(self)
        self._url_combo.setEditable(True)
        self._url_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        selected_copy.addWidget(self._url_combo)

        site_manage_row = QHBoxLayout()
        site_manage_row.setSpacing(10)
        self._save_url_button = QPushButton("링크 저장", self)
        self._save_url_button.setProperty("tone", "neutral")
        self._save_url_button.clicked.connect(self._save_current_url)
        site_manage_row.addWidget(self._save_url_button)
        self._edit_site_button = QPushButton("사이트 수정", self)
        self._edit_site_button.setProperty("tone", "neutral")
        self._edit_site_button.clicked.connect(self._edit_site)
        site_manage_row.addWidget(self._edit_site_button)
        self._delete_site_button = QPushButton("사이트 삭제", self)
        self._delete_site_button.setProperty("tone", "danger")
        self._delete_site_button.clicked.connect(self._delete_site)
        site_manage_row.addWidget(self._delete_site_button)
        site_manage_row.addStretch()
        selected_copy.addLayout(site_manage_row)

        self._status_label = QLabel("사이트를 선택하면 suite와 시나리오를 편집할 수 있습니다.", self)
        self._status_label.setObjectName("CompactStatusLabel")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        selected_layout.addLayout(selected_copy, stretch=1)
        scenario_layout.addWidget(selected_panel)

        scenario_header = QHBoxLayout()
        scenario_header.setSpacing(12)
        scenario_title = QLabel("테스트 목록", scenario_card)
        scenario_title.setObjectName("SectionTitle")
        scenario_header.addWidget(scenario_title)
        scenario_header.addStretch()
        self._scenario_count_label = QLabel("0개", scenario_card)
        self._scenario_count_label.setObjectName("ScenarioItemMeta")
        scenario_header.addWidget(self._scenario_count_label)
        scenario_layout.addLayout(scenario_header)
        self._scenario_list = QListWidget(self)
        self._scenario_list.setObjectName("ScenarioList")
        self._scenario_list.setMinimumHeight(340)
        self._scenario_list.setWordWrap(True)
        self._scenario_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scenario_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._scenario_list.currentRowChanged.connect(self._refresh_scenario_item_selection)
        scenario_layout.addWidget(self._scenario_list, stretch=1)

        self._scenario_empty_panel = QFrame(scenario_card)
        self._scenario_empty_panel.setObjectName("ScenarioEmptyPanel")
        empty_layout = QHBoxLayout(self._scenario_empty_panel)
        empty_layout.setContentsMargins(18, 16, 18, 16)
        empty_layout.setSpacing(14)
        empty_image = GuiAssetLabel(
            "benchmark_empty_state.png",
            parent=self._scenario_empty_panel,
            min_height=112,
            max_height=130,
            fit="contain",
        )
        empty_image.setMinimumWidth(140)
        empty_layout.addWidget(empty_image)
        empty_copy = QVBoxLayout()
        empty_copy.setSpacing(6)
        empty_title = QLabel("아직 테스트가 없습니다.", self._scenario_empty_panel)
        empty_title.setObjectName("ScenarioEmptyTitle")
        empty_copy.addWidget(empty_title)
        empty_description = QLabel(
            "테스트 추가를 눌러 이름, url, goal, timeout만 입력하면 suite에 바로 저장됩니다.",
            self._scenario_empty_panel,
        )
        empty_description.setObjectName("ScenarioEmptyDescription")
        empty_description.setWordWrap(True)
        empty_copy.addWidget(empty_description)
        empty_copy.addStretch()
        empty_layout.addLayout(empty_copy, stretch=1)
        scenario_layout.addWidget(self._scenario_empty_panel)

        scenario_button_row = QHBoxLayout()
        scenario_button_row.setSpacing(10)
        self._add_scenario_button = QPushButton("테스트 추가", self)
        self._add_scenario_button.setProperty("tone", "neutral")
        self._add_scenario_button.clicked.connect(self._add_scenario)
        scenario_button_row.addWidget(self._add_scenario_button)
        self._edit_scenario_button = QPushButton("테스트 수정", self)
        self._edit_scenario_button.setProperty("tone", "neutral")
        self._edit_scenario_button.clicked.connect(self._edit_scenario)
        scenario_button_row.addWidget(self._edit_scenario_button)
        self._delete_scenario_button = QPushButton("테스트 삭제", self)
        self._delete_scenario_button.setProperty("tone", "danger")
        self._delete_scenario_button.clicked.connect(self._delete_scenario)
        scenario_button_row.addWidget(self._delete_scenario_button)
        scenario_button_row.addStretch()
        self._run_full_button = QPushButton("전체 실행", self)
        self._run_full_button.clicked.connect(self._run_full_suite)
        scenario_button_row.addWidget(self._run_full_button)
        self._run_single_button = QPushButton("선택 실행", self)
        self._run_single_button.setProperty("tone", "neutral")
        self._run_single_button.clicked.connect(self._run_selected_scenario)
        scenario_button_row.addWidget(self._run_single_button)
        self._view_reports_button = QPushButton("결과 보기", self)
        self._view_reports_button.setProperty("tone", "neutral")
        self._view_reports_button.clicked.connect(self._view_reports)
        scenario_button_row.addWidget(self._view_reports_button)
        scenario_layout.addLayout(scenario_button_row)
        body_layout.addWidget(scenario_card, stretch=1)

        self._reload_catalog(selected_site_key=self._selected_site_key, selected_url=self._selected_url)

    def _load_registry(self) -> None:
        self._registry = load_benchmark_registry(self._registry_path)

    def _reload_catalog(
        self,
        *,
        selected_site_key: str | None = None,
        selected_url: str | None = None,
        selected_scenario_id: str | None = None,
    ) -> None:
        self._load_registry()
        catalog, _ = build_benchmark_site_catalog(self._registry)
        self._catalog = catalog
        if hasattr(self, "_site_count_label"):
            self._site_count_label.setText(f"{len(self._catalog)}")
        effective_site_key = str(selected_site_key or self._current_site_key() or "").strip()
        self._site_list.blockSignals(True)
        self._site_list.clear()
        for item in self._catalog:
            site_key = str(item.get("key") or "").strip()
            list_item = QListWidgetItem(self._site_list)
            list_item.setData(0x0100, site_key)
            list_item.setSizeHint(QSize(0, 52))
            self._site_list.setItemWidget(list_item, self._build_site_item_widget(item))
        selected_index = 0
        if effective_site_key:
            for index, item in enumerate(self._catalog):
                if str(item.get("key") or "") == effective_site_key:
                    selected_index = index
                    break
        self._site_list.setCurrentRow(selected_index if self._catalog else -1)
        self._site_list.blockSignals(False)
        self._refresh_current_site(selected_url=selected_url, selected_scenario_id=selected_scenario_id)
        self._refresh_site_item_selection()

    def _current_site_key(self) -> str:
        item = self._site_list.currentItem()
        if item is None:
            return ""
        return str(item.data(0x0100) or "").strip()

    def _current_site_entry(self) -> dict[str, Any] | None:
        site_key = self._current_site_key()
        for item in self._catalog:
            if str(item.get("key") or "").strip() == site_key:
                return dict(item)
        return None

    def _current_target_url(self) -> str:
        return str(self._url_combo.currentText() or "").strip()

    def _build_site_item_widget(self, site: Mapping[str, Any]) -> QWidget:
        frame = QFrame(self._site_list)
        frame.setObjectName("SiteItem")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        label = str(site.get("label") or site.get("key") or "-").strip()
        label_widget = QLabel(label, frame)
        label_widget.setObjectName("SiteItemName")
        layout.addWidget(label_widget)

        suffix = "직접 추가" if bool(site.get("is_custom")) else "기본 사이트"
        meta_widget = QLabel(suffix, frame)
        meta_widget.setObjectName("SiteItemMeta")
        layout.addWidget(meta_widget)
        return frame

    def _refresh_site_item_selection(self) -> None:
        current_row = self._site_list.currentRow()
        for index in range(self._site_list.count()):
            item = self._site_list.item(index)
            widget = self._site_list.itemWidget(item)
            if widget is None:
                continue
            widget.setProperty("selected", "true" if index == current_row else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _current_suite_path(self) -> Path | None:
        site = self._current_site_entry()
        suite_path = str((site or {}).get("suite_path") or "").strip()
        if not suite_path:
            return None
        return (self._workspace_root / suite_path).resolve()

    def _current_scenario_id(self) -> str:
        item = self._scenario_list.currentItem()
        if item is None:
            return ""
        return str(item.data(0x0100) or "").strip()

    def _refresh_current_site(
        self,
        *,
        selected_url: str | None = None,
        selected_scenario_id: str | None = None,
    ) -> None:
        site = self._current_site_entry()
        if site is None:
            self._url_combo.clear()
            self._scenario_list.clear()
            if hasattr(self, "_scenario_empty_panel"):
                self._scenario_empty_panel.setVisible(True)
            if hasattr(self, "_selected_site_title_label"):
                self._selected_site_title_label.setText("대상을 선택하세요")
            if hasattr(self, "_selected_site_meta_label"):
                self._selected_site_meta_label.setText("왼쪽 목록에서 벤치마킹 대상을 고르면 테스트 목록이 열립니다.")
            self._status_label.setText("사이트를 먼저 선택해주세요.")
            return
        if hasattr(self, "_selected_site_title_label"):
            self._selected_site_title_label.setText(str(site.get("label") or site.get("key") or "-").strip())
        if hasattr(self, "_selected_site_meta_label"):
            source = "직접 추가 대상" if bool(site.get("is_custom")) else "기본 제공 대상"
            self._selected_site_meta_label.setText(source)
        urls = build_url_history(site)
        effective_url = str(selected_url or "").strip() or str(site.get("default_url") or "").strip()
        self._url_combo.blockSignals(True)
        self._url_combo.clear()
        for url in urls:
            self._url_combo.addItem(url)
        if effective_url and effective_url not in urls:
            self._url_combo.addItem(effective_url)
        self._url_combo.setCurrentText(effective_url)
        self._url_combo.blockSignals(False)

        suite_path = self._current_suite_path()
        if suite_path and suite_path.exists():
            self._suite_payload = load_suite_payload(self._workspace_root, str(site.get("suite_path") or ""))
        else:
            self._suite_payload = {"scenarios": []}
        self._refresh_scenario_list(selected_scenario_id=selected_scenario_id)

        is_custom = bool(site.get("is_custom"))
        self._edit_site_button.setEnabled(is_custom)
        self._delete_site_button.setEnabled(is_custom)
        scenario_count = len([row for row in list(self._suite_payload.get("scenarios") or []) if isinstance(row, Mapping)])
        suite_text = str(site.get("suite_path") or "-")
        status_text = str(site.get("status_text") or "")
        self._status_label.setToolTip(f"suite={suite_text}")
        self._status_label.setText(
            f"{status_text} · 시나리오 {scenario_count}개"
        )

    def _refresh_scenario_list(self, *, selected_scenario_id: str | None = None) -> None:
        self._scenario_list.clear()
        target_id = str(selected_scenario_id or "").strip()
        selected_row = -1
        scenarios = [dict(row) for row in list(self._suite_payload.get("scenarios") or []) if isinstance(row, Mapping)]
        for index, scenario in enumerate(scenarios):
            scenario_id = str(scenario.get("id") or "").strip()
            item = QListWidgetItem(self._scenario_list)
            item.setData(0x0100, scenario_id)
            item.setSizeHint(QSize(0, 124))
            self._scenario_list.setItemWidget(item, self._build_scenario_item_widget(scenario))
            if str(scenario.get("id") or "").strip() == target_id:
                selected_row = index
        if hasattr(self, "_scenario_count_label"):
            self._scenario_count_label.setText(f"{len(scenarios)}개")
        if hasattr(self, "_scenario_empty_panel"):
            has_scenarios = bool(scenarios)
            self._scenario_list.setVisible(has_scenarios)
            self._scenario_empty_panel.setVisible(not has_scenarios)
        if selected_row >= 0:
            self._scenario_list.setCurrentRow(selected_row)
        elif self._scenario_list.count():
            self._scenario_list.setCurrentRow(0)
        self._refresh_scenario_item_selection()

    def _build_scenario_item_widget(self, scenario: Mapping[str, Any]) -> QWidget:
        frame = QFrame(self._scenario_list)
        frame.setObjectName("ScenarioItem")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(7)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        scenario_id = str(scenario.get("id") or "-").strip()
        id_label = QLabel(scenario_id, frame)
        id_label.setObjectName("ScenarioItemId")
        top_row.addWidget(id_label)
        top_row.addStretch()
        timeout = str(scenario.get("time_budget_sec") or 300).strip()
        meta = QLabel(f"{timeout}s", frame)
        meta.setObjectName("ScenarioItemMeta")
        top_row.addWidget(meta)
        layout.addLayout(top_row)

        name = default_scenario_name(scenario)
        name_label = QLabel(name, frame)
        name_label.setObjectName("ScenarioItemName")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        goal = str(scenario.get("goal") or "").strip()
        goal_label = QLabel(goal[:190] + ("..." if len(goal) > 190 else ""), frame)
        goal_label.setObjectName("ScenarioItemGoal")
        goal_label.setWordWrap(True)
        layout.addWidget(goal_label)
        return frame

    def _refresh_scenario_item_selection(self) -> None:
        current_row = self._scenario_list.currentRow()
        for index in range(self._scenario_list.count()):
            item = self._scenario_list.item(index)
            widget = self._scenario_list.itemWidget(item)
            if widget is None:
                continue
            widget.setProperty("selected", "true" if index == current_row else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _persist_registry(self, *, selected_url: str | None = None) -> None:
        save_benchmark_registry(self._registry, self._registry_path)
        selected_site_key = self._current_site_key()
        effective_url = str(selected_url or self._current_target_url() or "").strip()
        self._reload_catalog(selected_site_key=selected_site_key, selected_url=effective_url)
        self.catalogMutated.emit(selected_site_key, effective_url)

    def _persist_suite(self, payload: Mapping[str, Any], *, selected_scenario_id: str | None = None) -> None:
        suite_path = self._current_suite_path()
        if suite_path is None:
            raise FileNotFoundError("suite_path가 없습니다.")
        save_suite_payload(suite_path, payload)
        self._reload_catalog(
            selected_site_key=self._current_site_key(),
            selected_url=self._current_target_url(),
            selected_scenario_id=selected_scenario_id,
        )
        self.catalogMutated.emit(self._current_site_key(), self._current_target_url())

    def _save_current_url(self) -> None:
        site_key = self._current_site_key()
        target_url = self._current_target_url()
        if not site_key or not target_url:
            QMessageBox.warning(self, "저장 오류", "저장할 사이트와 링크를 확인해주세요.")
            return
        self._registry = upsert_benchmark_site_url(self._registry, site_key, target_url)
        self._persist_registry(selected_url=target_url)

    def _add_site(self) -> None:
        dialog = _SiteEditorDialog(parent=self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        values = dialog.values()
        site_key = _slugify_site_key(values["site_key"] or values["label"])
        if not site_key:
            QMessageBox.warning(self, "입력 오류", "생성 가능한 site_key가 없습니다.")
            return
        if resolve_benchmark_site(self._registry, site_key) is not None:
            QMessageBox.warning(self, "입력 오류", f"이미 존재하는 site_key입니다: {site_key}")
            return
        site_definition = create_custom_site_definition(
            site_key=site_key,
            label=values["label"],
            default_url=values["default_url"],
        )
        suite_path = (self._workspace_root / str(site_definition["suite_path"])).resolve()
        save_suite_payload(
            suite_path,
            create_custom_suite_payload(
                site_key=site_key,
                label=values["label"],
                default_url=values["default_url"],
            ),
        )
        self._registry = upsert_custom_benchmark_site(
            self._registry,
            site_key=site_key,
            site_definition=site_definition,
        )
        self._persist_registry(selected_url=values["default_url"])

    def _edit_site(self) -> None:
        site = self._current_site_entry()
        if not site or not bool(site.get("is_custom")):
            return
        dialog = _SiteEditorDialog(
            existing={
                "label": str(site.get("label") or "").strip(),
                "default_url": str(site.get("default_url") or "").strip(),
                "site_key": str(site.get("key") or "").strip(),
            },
            parent=self,
        )
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        values = dialog.values()
        site_key = str(site.get("key") or "").strip()
        site_definition = create_custom_site_definition(
            site_key=site_key,
            label=values["label"],
            default_url=values["default_url"],
        )
        suite_payload = load_suite_payload(self._workspace_root, str(site_definition["suite_path"] or ""))
        suite_payload["site"] = {
            **dict(suite_payload.get("site") or {}),
            "name": values["label"],
            "base_url": values["default_url"],
        }
        save_suite_payload((self._workspace_root / str(site_definition["suite_path"])).resolve(), suite_payload)
        self._registry = upsert_custom_benchmark_site(
            self._registry,
            site_key=site_key,
            site_definition=site_definition,
        )
        self._persist_registry(selected_url=values["default_url"])

    def _delete_site(self) -> None:
        site = self._current_site_entry()
        if not site or not bool(site.get("is_custom")):
            return
        suite_path = self._current_suite_path()
        if suite_path and suite_path.exists():
            suite_path.unlink()
        self._registry = delete_custom_benchmark_site(self._registry, str(site.get("key") or "").strip())
        next_key = ""
        if self._catalog:
            for item in self._catalog:
                if str(item.get("key") or "").strip() != str(site.get("key") or "").strip():
                    next_key = str(item.get("key") or "").strip()
                    break
        save_benchmark_registry(self._registry, self._registry_path)
        self._reload_catalog(selected_site_key=next_key)
        self.catalogMutated.emit(self._current_site_key(), self._current_target_url())

    def _find_current_scenario(self) -> dict[str, Any] | None:
        scenario_id = self._current_scenario_id()
        for raw in list(self._suite_payload.get("scenarios") or []):
            if isinstance(raw, Mapping) and str(raw.get("id") or "").strip() == scenario_id:
                return dict(raw)
        return None

    def _add_scenario(self) -> None:
        site = self._current_site_entry()
        if not site:
            return
        dialog = _ScenarioEditorDialog(default_url=self._current_target_url() or str(site.get("default_url") or ""), parent=self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        values = dialog.values()
        existing_ids = {
            str(row.get("id") or "").strip()
            for row in list(self._suite_payload.get("scenarios") or [])
            if isinstance(row, Mapping)
        }
        scenario = build_scenario_payload(
            current={},
            test_name=values["test_name"],
            url=values["url"],
            goal=values["goal"],
            time_budget_sec=values["time_budget_sec"],
            existing_ids=existing_ids,
        )
        updated = append_scenario_to_suite(self._suite_payload, scenario)
        self._persist_suite(updated, selected_scenario_id=str(scenario.get("id") or "").strip())

    def _edit_scenario(self) -> None:
        existing = self._find_current_scenario()
        if existing is None:
            return
        dialog = _ScenarioEditorDialog(existing=existing, default_url=self._current_target_url(), parent=self)
        if dialog.exec() != int(QDialog.DialogCode.Accepted):
            return
        values = dialog.values()
        existing_ids = {
            str(row.get("id") or "").strip()
            for row in list(self._suite_payload.get("scenarios") or [])
            if isinstance(row, Mapping)
        }
        updated_scenario = build_scenario_payload(
            current=existing,
            test_name=values["test_name"],
            url=values["url"],
            goal=values["goal"],
            time_budget_sec=values["time_budget_sec"],
            existing_ids=existing_ids,
        )
        updated = replace_scenario_in_suite(self._suite_payload, str(existing.get("id") or "").strip(), updated_scenario)
        self._persist_suite(updated, selected_scenario_id=str(updated_scenario.get("id") or "").strip())

    def _delete_scenario(self) -> None:
        scenario_id = self._current_scenario_id()
        if not scenario_id:
            return
        updated = delete_scenario_from_suite(self._suite_payload, scenario_id)
        self._persist_suite(updated)

    def _run_full_suite(self) -> None:
        site_key = self._current_site_key()
        if not site_key:
            return
        self.runRequested.emit(site_key, self._current_target_url(), "")

    def _run_selected_scenario(self) -> None:
        site_key = self._current_site_key()
        scenario_id = self._current_scenario_id()
        if not site_key or not scenario_id:
            return
        self.runRequested.emit(site_key, self._current_target_url(), scenario_id)

    def _view_reports(self) -> None:
        site_key = self._current_site_key()
        if not site_key:
            return
        self.viewRequested.emit(site_key, self._current_target_url())

    def _on_site_changed(self) -> None:
        self._refresh_current_site()
        self._refresh_site_item_selection()


__all__ = ["BenchmarkManagerDialog"]
