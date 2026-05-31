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
    benchmarkBattleCatalogRequested = Signal()

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
    emitted: list[tuple[str, str, str, bool]] = []
    dialog.runRequested.connect(
        lambda site_key, url, scenario_id, push_metrics: emitted.append((site_key, url, scenario_id, push_metrics))
    )

    dialog._scenario_list.setCurrentRow(0)
    dialog._run_selected_scenario()

    assert emitted == [("story_docs", "https://storybook.js.org/", "STORY_001_HOME", False)]


def test_benchmark_manager_dialog_emits_push_metrics_when_checked(tmp_path: Path) -> None:
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
    emitted: list[tuple[str, str, str, bool]] = []
    dialog.runRequested.connect(
        lambda site_key, url, scenario_id, push_metrics: emitted.append((site_key, url, scenario_id, push_metrics))
    )

    dialog._push_metrics_checkbox.setChecked(True)
    dialog._run_full_suite()

    assert emitted == [("story_docs", "https://storybook.js.org/", "", True)]


def test_controller_passes_push_metrics_to_benchmark_worker(tmp_path: Path) -> None:
    _app()
    window = _FakeWindow()
    controller = AppController(window)
    _, registry_path = _build_custom_site(
        tmp_path,
        scenarios=[
            {
                "id": "STORY_001_HOME",
                "name": "스토리북 홈 확인",
                "url": "https://storybook.js.org/",
                "goal": "홈이 보이는지 확인",
            }
        ],
    )
    controller._benchmark_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    captured: dict[str, object] = {}

    def fake_start_benchmark_worker(
        preset,
        target_url,
        *,
        suite_payload=None,
        run_tag="full_suite",
        push_metrics=False,
        run_options=None,
    ):
        captured["preset"] = preset
        captured["target_url"] = target_url
        captured["suite_payload"] = suite_payload
        captured["run_tag"] = run_tag
        captured["push_metrics"] = push_metrics
        captured["run_options"] = run_options

    controller._start_benchmark_worker = fake_start_benchmark_worker

    controller._run_benchmark_request(
        site_key="story_docs",
        url="https://storybook.js.org/",
        push_metrics=True,
    )

    assert captured["push_metrics"] is True
    assert captured["run_tag"] == "full_suite"


def test_benchmark_manager_dialog_battle_mode_uses_manifest_shortlist(tmp_path: Path) -> None:
    _app()
    scenario_dir = tmp_path / "gaia/tests/scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    save_suite_payload(
        scenario_dir / "custom_story_docs_suite.json",
        {
            **create_custom_suite_payload(
                site_key="story_docs",
                label="Storybook Docs",
                default_url="https://storybook.js.org/",
            ),
            "scenarios": [
                {"id": "STORY_001_HOME", "url": "https://storybook.js.org/", "goal": "홈 확인"},
                {"id": "STORY_002_DOCS", "url": "https://storybook.js.org/docs", "goal": "문서 확인"},
            ],
        },
    )
    save_suite_payload(
        scenario_dir / "custom_lms_suite.json",
        {
            **create_custom_suite_payload(
                site_key="inu_lms_hvh",
                label="인천대학교 LMS",
                default_url="https://cyber.inu.ac.kr/",
            ),
            "scenarios": [{"id": "CYBER_001_LSM_LOGIN_AND_CHAT", "goal": "LMS 확인"}],
        },
    )
    (scenario_dir / "gaia_vs_human_manifest.json").write_text(
        json.dumps(
            {
                "sites": [
                    {
                        "site_key": "story_docs",
                        "label": "Storybook Docs",
                        "default_url": "https://storybook.js.org/",
                        "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
                        "host_aliases": ["storybook.js.org"],
                        "allowed_scenarios": ["STORY_002_DOCS"],
                    },
                    {
                        "site_key": "inu_lms_hvh",
                        "label": "인천대학교 LMS",
                        "default_url": "https://cyber.inu.ac.kr/",
                        "suite_path": "gaia/tests/scenarios/custom_lms_suite.json",
                        "host_aliases": ["cyber.inu.ac.kr"],
                        "allowed_scenarios": ["CYBER_001_LSM_LOGIN_AND_CHAT"],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "benchmark_registry.json"
    save_benchmark_registry({"sites": {}, "custom_sites": {}}, registry_path)

    dialog = BenchmarkManagerDialog(
        workspace_root=tmp_path,
        registry_path=registry_path,
    )

    dialog._battle_mode_checkbox.setChecked(True)

    assert [str(item.get("key") or "") for item in dialog._catalog] == ["story_docs"]
    assert dialog._scenario_list.count() == 1
    assert dialog._current_scenario_id() == "STORY_002_DOCS"
    assert dialog._add_site_button.isEnabled() is False
    assert dialog._add_scenario_button.isEnabled() is False
    assert dialog.benchmark_run_options()["battle_mode"] is True


def test_controller_battle_mode_filters_suite_and_forwards_options(tmp_path: Path, monkeypatch) -> None:
    _app()
    window = _FakeWindow()
    controller = AppController(window)
    scenario_dir = tmp_path / "gaia/tests/scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    save_suite_payload(
        scenario_dir / "custom_story_docs_suite.json",
        {
            **create_custom_suite_payload(
                site_key="story_docs",
                label="Storybook Docs",
                default_url="https://storybook.js.org/",
            ),
            "scenarios": [
                {"id": "STORY_001_HOME", "url": "https://storybook.js.org/", "goal": "홈 확인"},
                {"id": "STORY_002_DOCS", "url": "https://storybook.js.org/docs", "goal": "문서 확인"},
            ],
        },
    )
    (scenario_dir / "gaia_vs_human_manifest.json").write_text(
        json.dumps(
            {
                "sites": [
                    {
                        "site_key": "story_docs",
                        "label": "Storybook Docs",
                        "default_url": "https://storybook.js.org/",
                        "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
                        "host_aliases": ["storybook.js.org"],
                        "allowed_scenarios": ["STORY_002_DOCS"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    controller._workspace_root = lambda: tmp_path
    controller._benchmark_registry = {"sites": {}, "custom_sites": {}}
    monkeypatch.setattr(
        "gaia.src.gui.controller.save_benchmark_registry",
        lambda _payload: tmp_path / "benchmark_registry.json",
    )
    captured: dict[str, object] = {}

    def fake_start_benchmark_worker(
        preset,
        target_url,
        *,
        suite_payload=None,
        run_tag="full_suite",
        push_metrics=False,
        run_options=None,
    ):
        captured["preset"] = preset
        captured["target_url"] = target_url
        captured["suite_payload"] = suite_payload
        captured["run_tag"] = run_tag
        captured["push_metrics"] = push_metrics
        captured["run_options"] = run_options

    controller._start_benchmark_worker = fake_start_benchmark_worker

    controller._run_benchmark_request(
        site_key="story_docs",
        url="https://storybook.js.org/",
        push_metrics=True,
        run_options={"battle_mode": True, "fast_mode": True},
    )

    assert captured["target_url"] == "https://storybook.js.org/"
    assert captured["run_options"] == {"battle_mode": True, "fast_mode": True}
    assert [str(item.get("key") or "") for item in window.catalog] == ["story_docs"]
    assert window.catalog[0]["allowed_scenarios"] == ["STORY_002_DOCS"]
    suite_payload = captured["suite_payload"]
    assert isinstance(suite_payload, dict)
    assert [row["id"] for row in suite_payload["scenarios"]] == ["STORY_002_DOCS"]


def test_controller_battle_catalog_signal_uses_human_vs_gaia_shortlist(tmp_path: Path) -> None:
    _app()
    window = _FakeWindow()
    controller = AppController(window)
    scenario_dir = tmp_path / "gaia/tests/scenarios"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / "gaia_vs_human_manifest.json").write_text(
        json.dumps(
            {
                "sites": [
                    {
                        "site_key": "story_docs",
                        "label": "Storybook Docs",
                        "default_url": "https://storybook.js.org/",
                        "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
                        "host_aliases": ["storybook.js.org"],
                        "allowed_scenarios": ["STORY_002_DOCS"],
                    },
                    {
                        "site_key": "inu_lms_hvh",
                        "label": "인천대학교 LMS",
                        "default_url": "https://cyber.inu.ac.kr/",
                        "suite_path": "gaia/tests/scenarios/custom_lms_suite.json",
                        "host_aliases": ["cyber.inu.ac.kr"],
                        "allowed_scenarios": ["CYBER_001_LSM_LOGIN_AND_CHAT"],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    controller._workspace_root = lambda: tmp_path
    controller._benchmark_registry = {"sites": {}, "custom_sites": {}}

    window.benchmarkBattleCatalogRequested.emit()

    assert [str(item.get("key") or "") for item in window.catalog] == ["story_docs"]
    assert window.catalog[0]["allowed_scenarios"] == ["STORY_002_DOCS"]


def test_benchmark_worker_supports_in_memory_suite_payload(tmp_path: Path, monkeypatch) -> None:
    _app()
    results: list[dict[str, object]] = []
    progress: list[str] = []

    class _FakeProcess:
        def __init__(self, cmd, cwd=None, stdout=None, stderr=None, text=None, bufsize=None, env=None, **kwargs):
            del cwd, stdout, stderr, text, bufsize, env, kwargs
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


def test_benchmark_worker_appends_push_metrics_flag(tmp_path: Path, monkeypatch) -> None:
    _app()
    captured_cmd: list[str] = []

    class _FakeProcess:
        def __init__(self, cmd, cwd=None, stdout=None, stderr=None, text=None, bufsize=None, env=None, **kwargs):
            del cwd, stdout, stderr, text, bufsize, env, kwargs
            captured_cmd[:] = list(cmd)
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
            self.stdout = iter([])

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
        push_metrics=True,
    )

    worker.start()

    assert "--push-metrics" in captured_cmd


def test_benchmark_worker_appends_battle_and_fast_options(tmp_path: Path, monkeypatch) -> None:
    _app()
    captured_cmd: list[str] = []
    captured_env: dict[str, str] = {}
    captured_session_body: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=0):
        del timeout
        captured_session_body.update(json.loads(request.data.decode("utf-8")))
        return _FakeResponse()

    class _FakeProcess:
        def __init__(self, cmd, cwd=None, stdout=None, stderr=None, text=None, bufsize=None, env=None, **kwargs):
            del cwd, stdout, stderr, text, bufsize, kwargs
            captured_cmd[:] = list(cmd)
            captured_env.update(dict(env or {}))
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
            self.stdout = iter([])

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            return None

    monkeypatch.setattr("gaia.src.gui.goal_worker.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(subprocess, "Popen", _FakeProcess)

    worker = BenchmarkWorker(
        site_key="story_docs",
        site_label="Storybook Docs",
        suite_path="gaia/tests/scenarios/custom_story_docs_suite.json",
        suite_payload={
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "grader_configs": {},
            "scenarios": [
                {
                    "id": "STORY_001_HOME",
                    "name": "스토리북 홈",
                    "url": "https://storybook.js.org/",
                    "goal": "홈 확인",
                }
            ],
        },
        target_url="https://storybook.js.org/",
        run_tag="STORY_001_HOME",
        workspace_root=tmp_path,
        run_options={"battle_mode": True, "fast_mode": True},
    )

    worker.start()

    assert "--battle-board" in captured_cmd
    assert captured_cmd[captured_cmd.index("--battle-upload-url") + 1] == "https://gaia-battle-web.vercel.app/api/records"
    assert captured_cmd[captured_cmd.index("--battle-session-id") + 1] == "battle-live"
    assert captured_env["GAIA_CODEX_APP_SERVER_ARGS"] == '-c service_tier="priority"'
    assert captured_env["GAIA_BATTLE_SCENARIO_LABEL"] == "스토리북 홈"
    assert captured_session_body["sessionId"] == "battle-live"
    assert captured_session_body["scenarioLabel"] == "스토리북 홈"


def test_benchmark_worker_disables_inherited_fast_mode_when_toggle_off(tmp_path: Path, monkeypatch) -> None:
    _app()
    captured_env: dict[str, str] = {}

    class _FakeProcess:
        def __init__(self, cmd, cwd=None, stdout=None, stderr=None, text=None, bufsize=None, env=None, **kwargs):
            del cwd, stdout, stderr, text, bufsize, kwargs
            captured_env.update(dict(env or {}))
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
            self.stdout = iter([])

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            return None

    monkeypatch.setenv("GAIA_CODEX_APP_SERVER_ARGS", '-c service_tier="priority"')
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
        run_options={"fast_mode": False},
    )

    worker.start()

    assert "GAIA_CODEX_APP_SERVER_ARGS" not in captured_env


def test_main_window_benchmark_mode_emits_manager_open_without_auto_stage_jump(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(MainWindow, "_setup_screencast", lambda self: None)

    window = MainWindow()
    emitted: list[tuple[str, str]] = []
    window.benchmarkManageRequested.connect(lambda site_key, url: emitted.append((site_key, url)))

    window._benchmark_mode_button.click()

    assert window.get_selected_run_mode() == "benchmark"
    assert window._standard_action_container.isVisible() is False
    assert emitted == [("", "")]


def test_main_window_battle_demo_mode_enables_web_and_fast_path(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(MainWindow, "_setup_screencast", lambda self: None)

    window = MainWindow()
    emitted: list[bool] = []
    window.benchmarkBattleCatalogRequested.connect(lambda: emitted.append(True))

    window._battle_demo_mode_button.click()

    assert window.get_selected_run_mode() == "battle_demo"
    assert window._workflow_stage == "site_selection"
    assert window._site_battle_demo_button.text() == "Human vs GAIA 시연 모드"
    assert window._site_battle_demo_button.isChecked() is True
    assert window._standard_action_container.isVisible() is False
    assert window.get_benchmark_run_options()["battle_mode"] is True
    assert window.get_benchmark_run_options()["fast_mode"] is True
    assert window._benchmark_battle_checkbox.isChecked() is True
    assert window._benchmark_fast_checkbox.isChecked() is True
    assert emitted == [True]


def test_main_window_site_selection_battle_demo_button_is_direct_entry(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(MainWindow, "_setup_screencast", lambda self: None)

    window = MainWindow()
    emitted: list[bool] = []
    window.benchmarkBattleCatalogRequested.connect(lambda: emitted.append(True))

    window.show_site_selection_stage()
    window._site_battle_demo_button.click()

    assert window.get_selected_run_mode() == "battle_demo"
    assert window.get_benchmark_run_options()["battle_mode"] is True
    assert window.get_benchmark_run_options()["fast_mode"] is True
    assert window._site_battle_demo_button.isChecked() is True
    assert emitted == [True]


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
