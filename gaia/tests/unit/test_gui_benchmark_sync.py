from __future__ import annotations

import json
import os
import subprocess
from io import BytesIO
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from gaia.src.benchmark_manager import (
    create_custom_suite_payload,
    save_benchmark_registry,
    save_suite_payload,
    upsert_custom_benchmark_site,
)
from gaia.src.gui.benchmark_manager_dialog import BenchmarkManagerDialog
from gaia.src.gui.controller import AppController
from gaia.src.gui.goal_worker import BenchmarkWorker
from gaia.src.gui.main_window import MainWindow




def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeWindow(QObject):
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

    def __init__(self) -> None:
        super().__init__()
        self.catalog: list[dict[str, object]] = []
        self.logs: list[str] = []

    def set_benchmark_catalog(self, catalog, *, selected_site_key=None, selected_url=None):
        self.catalog = [dict(item) for item in catalog]
        self.selected_site_key = selected_site_key
        self.selected_url = selected_url

    def append_log(self, message: str) -> None:
        self.logs.append(message)

    def set_url_field(self, value: str) -> None:
        self.url = value

    def get_url_field_value(self) -> str:
        return str(getattr(self, "url", "") or "")

    def set_feature_query(self, value: str) -> None:
        self.feature_query = value

    def set_selected_run_mode(self, mode: str) -> None:
        self.mode = mode

    def set_control_channel(self, channel: str) -> None:
        self.control_channel = channel

    def set_execution_status(self, **kwargs) -> None:
        self.execution_status = kwargs

    def reset_result_summary(self) -> None:
        self.reset_called = True

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = (busy, message)

    def show_result_summary(self, summary) -> None:
        self.summary = summary

    def show_html_in_browser(self, html_content: str) -> None:
        self.html = html_content

    def append_chat_message(self, sender: str, message: str) -> None:
        self.logs.append(f"{sender}:{message}")


def _build_custom_site(tmp_path: Path, *, scenarios: list[dict[str, object]]) -> tuple[Path, Path]:
    suite_path = tmp_path / "gaia/tests/scenarios/custom_story_docs_suite.json"
    registry_path = tmp_path / "benchmark_registry.json"
    save_suite_payload(
        suite_path,
        {
            **create_custom_suite_payload(
                site_key="story_docs",
                label="Storybook Docs",
                default_url="https://storybook.js.org/",
            ),
            "scenarios": scenarios,
        },
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}, "custom_sites": {}},
        site_key="story_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
            "host_aliases": ["storybook.js.org"],
        },
    )
    save_benchmark_registry(registry, registry_path)
    return suite_path, registry_path


def test_controller_refresh_benchmark_catalog_includes_custom_sites(tmp_path: Path) -> None:
    _app()
    window = _FakeWindow()
    controller = AppController(window)
    _, registry_path = _build_custom_site(tmp_path, scenarios=[])
    controller._benchmark_registry = json.loads(registry_path.read_text(encoding="utf-8"))

    controller._refresh_benchmark_catalog(selected_site_key="story_docs")

    keys = {str(item.get("key") or "") for item in window.catalog}
    assert "story_docs" in keys


def test_controller_resolve_analysis_base_url_uses_window_field_when_current_url_missing() -> None:
    _app()
    window = _FakeWindow()
    window.set_url_field("https://service.example/")
    controller = AppController(window)

    assert controller._current_url is None
    assert controller._resolve_analysis_base_url() == "https://service.example/"
    assert controller._current_url == "https://service.example/"


def test_benchmark_manager_dialog_adds_and_deletes_scenarios_without_confirmation(monkeypatch, tmp_path: Path) -> None:
    _app()
    suite_path, registry_path = _build_custom_site(
        tmp_path,
        scenarios=[
            {
                "id": "STORY_001_HOME",
                "name": "스토리북 홈 확인",
                "url": "https://storybook.js.org/",
                "goal": "홈이 보이는지 확인",
                "constraints": {
                    "allow_navigation": True,
                    "require_ref_only": True,
                    "require_state_change": False,
                },
                "expected_signals": [],
                "time_budget_sec": 300,
            }
        ],
    )

    class _FakeScenarioDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return int(QDialog.DialogCode.Accepted)

        def values(self):
            return {
                "test_name": "스토리북 문서 진입",
                "url": "https://storybook.js.org/docs",
                "goal": "문서 페이지로 진입",
                "time_budget_sec": 300,
            }

    monkeypatch.setattr(
        "gaia.src.gui.benchmark_manager_dialog._ScenarioEditorDialog",
        _FakeScenarioDialog,
    )

    dialog = BenchmarkManagerDialog(
        workspace_root=tmp_path,
        registry_path=registry_path,
        selected_site_key="story_docs",
    )
    mutations: list[tuple[str, str]] = []
    dialog.catalogMutated.connect(lambda site_key, url: mutations.append((site_key, url)))

    dialog._add_scenario()
    reloaded = json.loads(suite_path.read_text(encoding="utf-8"))
    assert len(reloaded["scenarios"]) == 2
    assert any(row["name"] == "스토리북 문서 진입" for row in reloaded["scenarios"])

    dialog._scenario_list.setCurrentRow(0)
    dialog._delete_scenario()
    reloaded = json.loads(suite_path.read_text(encoding="utf-8"))
    assert len(reloaded["scenarios"]) == 1
    assert mutations


def test_benchmark_manager_dialog_emits_single_run_request(tmp_path: Path) -> None:
    _app()
    _, registry_path = _build_custom_site(
        tmp_path,
        scenarios=[
            {
                "id": "STORY_001_HOME",
                "name": "스토리북 홈 확인",
                "url": "https://storybook.js.org/",
                "goal": "홈이 보이는지 확인",
                "constraints": {
                    "allow_navigation": True,
                    "require_ref_only": True,
                    "require_state_change": False,
                },
                "expected_signals": [],
                "time_budget_sec": 300,
            }
        ],
    )
    dialog = BenchmarkManagerDialog(
        workspace_root=tmp_path,
        registry_path=registry_path,
        selected_site_key="story_docs",
    )
    emitted: list[tuple[str, str, str]] = []
    dialog.runRequested.connect(lambda site_key, url, scenario_id: emitted.append((site_key, url, scenario_id)))

    dialog._scenario_list.setCurrentRow(0)
    dialog._run_selected_scenario()

    assert emitted == [("story_docs", "https://storybook.js.org/", "STORY_001_HOME")]


def test_benchmark_worker_supports_in_memory_suite_payload(tmp_path: Path, monkeypatch) -> None:
    _app()
    results: list[dict[str, object]] = []
    progress: list[str] = []

    class _FakeProcess:
        def __init__(self, cmd, cwd=None, stdout=None, stderr=None, text=None, bufsize=None, env=None):
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "scenario_count": 1,
                        "status_counts": {"SUCCESS": 1, "FAIL": 0},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (output_dir / "results.json").write_text(
                json.dumps([{"scenario_id": "STORY_001_HOME", "status": "SUCCESS"}], ensure_ascii=False),
                encoding="utf-8",
            )
            self.stdout = iter(["--- Step 1/40 ---\n", "LLM 결정: click\n"])

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _FakeProcess)

    worker = BenchmarkWorker(
        site_key="story_docs",
        site_label="Storybook Docs",
        suite_path="gaia/tests/scenarios/custom_story_docs_suite.json",
        suite_payload={
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "grader_configs": {},
            "scenarios": [{"id": "STORY_001_HOME", "url": "https://storybook.js.org/", "goal": "홈 확인"}],
        },
        target_url="https://storybook.js.org/",
        run_tag="STORY_001_HOME",
        workspace_root=tmp_path,
    )
    worker.progress.connect(progress.append)
    worker.result_ready.connect(results.append)

    worker.start()

    assert any("LLM 결정" in line for line in progress)
    assert results
    assert results[0]["successful_runs"] == 1
    assert "STORY_001_HOME" in str(results[0]["output_dir"])


def test_main_window_benchmark_mode_uses_dedicated_stage_and_emits_manager_open(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(MainWindow, "_setup_screencast", lambda self: None)

    window = MainWindow()
    emitted: list[tuple[str, str]] = []
    window.benchmarkManageRequested.connect(lambda site_key, url: emitted.append((site_key, url)))

    window._benchmark_mode_button.click()

    assert window.get_selected_run_mode() == "benchmark"
    assert window._workflow_stack.currentWidget() is window._benchmark_page
    assert emitted == [("", "")]


def test_main_window_benchmark_stage_summary_tracks_selected_catalog(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(MainWindow, "_setup_screencast", lambda self: None)

    window = MainWindow()
    window.set_benchmark_catalog(
        [
            {
                "key": "story_docs",
                "label": "Storybook Docs",
                "default_url": "https://storybook.js.org/",
                "status_text": "준비됨",
            }
        ],
        selected_site_key="story_docs",
        selected_url="https://storybook.js.org/docs",
    )

    summary = window._benchmark_stage_summary_label.text()
    assert "Storybook Docs" in summary
    assert "https://storybook.js.org/docs" in summary


def test_main_window_result_screenshot_history_ignores_blank_capture(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(MainWindow, "_setup_screencast", lambda self: None)

    def _png_base64(color: tuple[int, int, int]) -> str:
        image = Image.new("RGB", (32, 32), color)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    import base64

    window = MainWindow()
    window._record_result_screenshot(_png_base64((255, 255, 255)))
    assert window._result_screenshot_history == []

    valid_shot = _png_base64((49, 130, 246))
    window._record_result_screenshot(valid_shot)
    assert window._result_screenshot_history == [valid_shot]
