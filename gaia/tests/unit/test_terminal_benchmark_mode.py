from __future__ import annotations

import json
from pathlib import Path

import pytest

from gaia.cli import DEFAULT_OPENAI_MODEL, _default_model, _run_terminal_benchmark_mode, main, run_launcher
from gaia.src.benchmark_suite_sharing import SharedSuiteNotFound
from gaia.src.benchmark_manager import BenchmarkPreset
from gaia.src.gui.benchmark_mode import find_preset
from gaia.src.terminal_benchmark_mode import (
    DEEP_QA_ALL_CASES_OPTION,
    DEEP_QA_BENCHMARK_MANIFEST_PATH,
    HUMAN_VS_GAIA_RUN_ALL_OPTION,
    _run_human_vs_gaia_all_sites,
    _main,
    _grafana_url_from_monitoring_config,
    append_scenario_to_suite,
    build_human_vs_gaia_catalog,
    build_single_scenario_suite_payload,
    build_deep_qa_benchmark_catalog,
    build_terminal_benchmark_catalog,
    build_url_history,
    create_custom_site_definition,
    delete_scenario_from_suite,
    delete_custom_benchmark_site,
    manage_benchmark_sites,
    open_scenario_form_gui,
    open_scenario_form_macos_dialogs,
    open_scenario_form_pyside,
    open_scenario_form_windows_dialogs,
    open_benchmark_report,
    prompt_scenario_fields,
    replace_scenario_in_suite,
    run_benchmark_suite,
    run_terminal_benchmark_mode,
    run_terminal_human_vs_gaia_mode,
    save_suite_payload,
    upsert_custom_benchmark_site,
    write_benchmark_report_html,
)


class _PromptScript:
    def __init__(
        self,
        *,
        selections: list[str] | None = None,
        texts: list[str] | None = None,
        non_empty: list[str] | None = None,
    ) -> None:
        self._selections = list(selections or [])
        self._texts = list(texts or [])
        self._non_empty = list(non_empty or [])
        self.select_calls: list[tuple[str, tuple[str, ...], str | None]] = []

    def select(self, prompt: str, options: tuple[str, ...] | list[str], default: str | None = None) -> str:
        normalized = tuple(options)
        self.select_calls.append((prompt, normalized, default))
        if self._selections:
            return self._selections.pop(0)
        return default or normalized[0]

    def text(self, prompt: str, default: str | None = None) -> str:
        del prompt
        if self._texts:
            return self._texts.pop(0)
        return default or ""

    def non_empty_prompt(self, prompt: str, default: str | None = None) -> str:
        del prompt
        if self._non_empty:
            return self._non_empty.pop(0)
        return default or ""


@pytest.fixture(autouse=True)
def _disable_shared_suite_network(monkeypatch) -> None:
    def _missing_shared_suite(*args, **kwargs):
        del args, kwargs
        raise SharedSuiteNotFound("disabled in unit tests")

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode.download_shared_suite",
        _missing_shared_suite,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_openai_default_model_is_gpt_55() -> None:
    assert DEFAULT_OPENAI_MODEL == "gpt-5.5"
    assert _default_model("openai") == "gpt-5.5"


def test_run_launcher_routes_terminal_benchmark_mode(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "gaia.cli._configure_session",
        lambda parsed, require_url: (
            "openai",
            "gpt-5.4",
            "reuse",
            None,
            "terminal",
            "workspace",
            "session-1",
            False,
        ),
    )
    monkeypatch.setattr("gaia.cli.load_session_state", lambda session_key: None)
    monkeypatch.setattr("gaia.cli._load_profile", lambda: {})
    monkeypatch.setattr("gaia.cli._resolve_terminal_launch_purpose", lambda *args, **kwargs: "benchmark")
    def _fake_benchmark_runner(*, workspace_root: Path, push_metrics: bool = False) -> int:
        called["workspace_root"] = workspace_root
        called["push_metrics"] = push_metrics
        return 37

    monkeypatch.setattr("gaia.cli._run_terminal_benchmark_mode", _fake_benchmark_runner)
    monkeypatch.setattr(
        "gaia.cli._resolve_control_channel",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("control prompt should be skipped")),
    )

    result = run_launcher([])

    assert result == 37
    assert called["workspace_root"] == _repo_root()
    assert called["push_metrics"] is False


def test_run_launcher_passes_terminal_benchmark_push_metrics_flag(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "gaia.cli._configure_session",
        lambda parsed, require_url: (
            "openai",
            "gpt-5.4",
            "reuse",
            None,
            "terminal",
            "workspace",
            "session-1",
            False,
        ),
    )
    monkeypatch.setattr("gaia.cli.load_session_state", lambda session_key: None)
    monkeypatch.setattr("gaia.cli._load_profile", lambda: {})
    monkeypatch.setattr("gaia.cli._resolve_terminal_launch_purpose", lambda *args, **kwargs: "benchmark")

    def _fake_benchmark_runner(*, workspace_root: Path, push_metrics: bool = False) -> int:
        called["workspace_root"] = workspace_root
        called["push_metrics"] = push_metrics
        return 0

    monkeypatch.setattr("gaia.cli._run_terminal_benchmark_mode", _fake_benchmark_runner)
    monkeypatch.setattr(
        "gaia.cli._resolve_control_channel",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("control prompt should be skipped")),
    )

    result = run_launcher(["--terminal", "--push-metrics"])

    assert result == 0
    assert called["workspace_root"] == _repo_root()
    assert called["push_metrics"] is True


def test_python_module_entry_routes_root_options_to_launcher(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "gaia.cli._configure_session",
        lambda parsed, require_url: (
            "openai",
            "gpt-5.4",
            "reuse",
            None,
            "terminal",
            "workspace",
            "session-1",
            False,
        ),
    )
    monkeypatch.setattr("gaia.cli.load_session_state", lambda session_key: None)
    monkeypatch.setattr("gaia.cli._load_profile", lambda: {})
    monkeypatch.setattr("gaia.cli._resolve_terminal_launch_purpose", lambda *args, **kwargs: "benchmark")

    def _fake_benchmark_runner(*, workspace_root: Path, push_metrics: bool = False) -> int:
        called["workspace_root"] = workspace_root
        called["push_metrics"] = push_metrics
        return 0

    monkeypatch.setattr("gaia.cli._run_terminal_benchmark_mode", _fake_benchmark_runner)
    monkeypatch.setattr(
        "gaia.cli._resolve_control_channel",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("control prompt should be skipped")),
    )

    result = main(["--terminal", "--push-metrics"])

    assert result == 0
    assert called["workspace_root"] == _repo_root()
    assert called["push_metrics"] is True


def test_cli_terminal_benchmark_mode_dispatches_general_mode(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr("gaia.cli._prompt_select", lambda *args, **kwargs: "일반 벤치마크")
    monkeypatch.setattr("gaia.cli._prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("gaia.cli._prompt_non_empty", lambda *args, **kwargs: "")

    def _fake_runner(**kwargs):
        called["runner"] = "general"
        called["kwargs"] = kwargs
        return 11

    def _fake_hvh_runner(**kwargs):
        called["runner"] = "human_vs_gaia"
        called["kwargs"] = kwargs
        return 22

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.run_terminal_benchmark_mode", _fake_runner)
    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.run_terminal_human_vs_gaia_mode", _fake_hvh_runner)

    result = _run_terminal_benchmark_mode(workspace_root=_repo_root(), push_metrics=True)

    assert result == 11
    assert called["runner"] == "general"
    assert called["kwargs"]["workspace_root"] == _repo_root()
    assert called["kwargs"]["push_metrics"] is True


def test_cli_terminal_benchmark_mode_dispatches_human_vs_gaia(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr("gaia.cli._prompt_select", lambda *args, **kwargs: "GAIA_VS_HUMAN")
    monkeypatch.setattr("gaia.cli._prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr("gaia.cli._prompt_non_empty", lambda *args, **kwargs: "")

    def _fake_runner(**kwargs):
        called["runner"] = "general"
        called["kwargs"] = kwargs
        return 11

    def _fake_hvh_runner(**kwargs):
        called["runner"] = "human_vs_gaia"
        called["kwargs"] = kwargs
        return 22

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.run_terminal_benchmark_mode", _fake_runner)
    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.run_terminal_human_vs_gaia_mode", _fake_hvh_runner)

    result = _run_terminal_benchmark_mode(workspace_root=_repo_root(), push_metrics=False)

    assert result == 22
    assert called["runner"] == "human_vs_gaia"
    assert called["kwargs"]["workspace_root"] == _repo_root()
    assert called["kwargs"]["push_metrics"] is False


def test_run_terminal_human_vs_gaia_mode_can_run_all_sites_except_lms(tmp_path: Path, monkeypatch) -> None:
    script = _PromptScript(selections=[HUMAN_VS_GAIA_RUN_ALL_OPTION, "이전으로"])
    calls: list[dict[str, object]] = []

    catalog = [
        {
            "key": "site_a",
            "label": "Site A",
            "default_url": "https://a.example",
            "urls": ["https://a.example"],
            "suite_path": "gaia/tests/scenarios/a.json",
            "suite_available": True,
            "status_text": "GAIA_VS_HUMAN 1개",
            "is_custom": True,
        },
        {
            "key": "inu_lms_hvh",
            "label": "LMS",
            "default_url": "https://lms.example",
            "urls": ["https://lms.example"],
            "suite_path": "gaia/tests/scenarios/lms.json",
            "suite_available": True,
            "status_text": "GAIA_VS_HUMAN 1개",
            "is_custom": True,
        },
        {
            "key": "site_b",
            "label": "Site B",
            "default_url": "https://b.example",
            "urls": ["https://b.example"],
            "suite_path": "gaia/tests/scenarios/b.json",
            "suite_available": True,
            "status_text": "GAIA_VS_HUMAN 1개",
            "is_custom": True,
        },
    ]
    preset_map = {
        "site_a": BenchmarkPreset(
            key="site_a",
            label="Site A",
            default_url="https://a.example",
            suite_path="gaia/tests/scenarios/a.json",
            host_aliases=("a.example",),
        ),
        "inu_lms_hvh": BenchmarkPreset(
            key="inu_lms_hvh",
            label="LMS",
            default_url="https://lms.example",
            suite_path="gaia/tests/scenarios/lms.json",
            host_aliases=("lms.example",),
        ),
        "site_b": BenchmarkPreset(
            key="site_b",
            label="Site B",
            default_url="https://b.example",
            suite_path="gaia/tests/scenarios/b.json",
            host_aliases=("b.example",),
        ),
    }
    scenario_filter_map = {
        "site_a": {"A_001"},
        "inu_lms_hvh": {"LMS_001"},
        "site_b": {"B_001"},
    }

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode.build_human_vs_gaia_catalog",
        lambda *args, **kwargs: (catalog, preset_map, scenario_filter_map),
    )
    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._resolve_push_metrics_for_run",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._load_terminal_suite_payload",
        lambda **kwargs: {
            "suite_id": "demo",
            "scenarios": [{"id": next(iter(scenario_filter_map[kwargs["preset"].key])), "goal": "ok"}],
        },
    )

    def fake_run_suite_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "summary": {}, "results": [], "output_dir": "/tmp/out"}

    result = run_terminal_human_vs_gaia_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=fake_run_suite_handler,
    )

    assert result == 130
    assert script.select_calls[0][1][0] == HUMAN_VS_GAIA_RUN_ALL_OPTION
    assert [call["preset"].key for call in calls] == ["site_a", "site_b"]
    assert [call["target_url"] for call in calls] == ["https://a.example", "https://b.example"]


def test_build_human_vs_gaia_catalog_prefers_manifest_default_url(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "sites": [
                    {
                        "site_key": "site_a",
                        "label": "Site A",
                        "default_url": "https://manifest.example",
                        "suite_path": "gaia/tests/scenarios/a.json",
                        "allowed_scenarios": ["A_001"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    catalog, preset_map, _ = build_human_vs_gaia_catalog(
        {
            "sites": {
                "site_a": {
                    "default_url": "https://stale.example",
                    "urls": ["https://older.example"],
                }
            }
        },
        workspace_root=tmp_path,
        manifest_path=manifest_path,
    )

    assert catalog[0]["default_url"] == "https://manifest.example"
    assert catalog[0]["urls"][0] == "https://manifest.example"
    assert "https://stale.example" in catalog[0]["urls"]
    assert preset_map["site_a"].default_url == "https://manifest.example"


def test_human_vs_gaia_all_sites_advances_case_progress_for_skipped_sites(tmp_path: Path, monkeypatch) -> None:
    catalog = [
        {"key": "site_a", "label": "Site A", "default_url": "https://a.example"},
        {"key": "site_b", "label": "Site B", "default_url": "https://b.example"},
    ]
    preset_map = {
        "site_a": BenchmarkPreset(
            key="site_a",
            label="Site A",
            default_url="https://a.example",
            suite_path="gaia/tests/scenarios/a.json",
            host_aliases=("a.example",),
        ),
        "site_b": BenchmarkPreset(
            key="site_b",
            label="Site B",
            default_url="https://b.example",
            suite_path="gaia/tests/scenarios/b.json",
            host_aliases=("b.example",),
        ),
    }
    scenario_filter_map = {"site_a": {"A_001", "A_002"}, "site_b": {"B_001"}}
    emitted: list[str] = []
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._resolve_push_metrics_for_run",
        lambda **kwargs: False,
    )

    def fake_load_suite_payload(**kwargs):
        if kwargs["preset"].key == "site_a":
            return {"suite_id": "a", "scenarios": []}
        return {"suite_id": "b", "scenarios": [{"id": "B_001", "goal": "ok"}]}

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._load_terminal_suite_payload",
        fake_load_suite_payload,
    )

    _run_human_vs_gaia_all_sites(
        workspace_root=tmp_path,
        catalog=catalog,
        preset_map=preset_map,
        scenario_filter_map=scenario_filter_map,
        prompt_select=lambda *args, **kwargs: "",
        prompt_non_empty=lambda *args, **kwargs: "",
        emit=emitted.append,
        run_suite_handler=lambda **kwargs: calls.append(kwargs) or {"status": "success"},
        push_metrics=False,
        monitoring_config_path=None,
        runner_id="runner",
    )

    assert [call["preset"].key for call in calls] == ["site_b"]
    assert any("3/3" in message for message in emitted)


def test_run_terminal_benchmark_mode_site_menu_lists_all_presets(tmp_path: Path) -> None:
    script = _PromptScript(selections=["종료"])

    result = run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
    )

    assert result == 0
    first_prompt = script.select_calls[0]
    assert first_prompt[1][0] == "전체사이트 전체케이스 실행"
    assert first_prompt[2] == "전체사이트 전체케이스 실행"
    assert "INU TIMETABLE" in first_prompt[1]
    assert "머니터링" in first_prompt[1]
    assert "잡코리아" in first_prompt[1]
    assert "서울문화포털" in first_prompt[1]
    assert "디시인사이드" in first_prompt[1]
    assert "사이트 추가" in first_prompt[1]
    assert "사이트 수정" in first_prompt[1]
    assert "사이트 삭제" in first_prompt[1]


def test_deep_qa_benchmark_catalog_uses_dedicated_manifest() -> None:
    catalog, preset_map = build_deep_qa_benchmark_catalog(
        workspace_root=_repo_root(),
        manifest_path=DEEP_QA_BENCHMARK_MANIFEST_PATH,
    )

    labels = {item["label"] for item in catalog}
    assert "Deep QA Kakao Map" in labels
    assert "INU TIMETABLE" not in labels
    assert preset_map["deep_qa_kakao_map"].suite_path == "gaia/tests/scenarios/deep_qa_kakao_map_suite.json"


def test_run_terminal_deep_qa_benchmark_mode_uses_dedicated_manifest(tmp_path: Path) -> None:
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091", "token": "team-token"}, ensure_ascii=False),
        encoding="utf-8",
    )
    script = _PromptScript(selections=[DEEP_QA_ALL_CASES_OPTION, "종료"])
    calls: list[dict[str, object]] = []
    emitted: list[str] = []

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        run_pack_handler=lambda **kwargs: calls.append(kwargs) or {},
        monitoring_config_path=monitoring_config_path,
        dedicated_deep_qa=True,
    )

    assert len(calls) == 1
    assert calls[0]["manifest_path"] == DEEP_QA_BENCHMARK_MANIFEST_PATH
    assert calls[0]["qa_mode"] == "deep_adaptive_qa"
    assert calls[0]["session_prefix"] == "terminal-deep-qa"
    first_prompt = script.select_calls[0]
    assert first_prompt[1][0] == DEEP_QA_ALL_CASES_OPTION
    assert "INU TIMETABLE" not in first_prompt[1]
    assert "사이트 추가" not in first_prompt[1]
    assert any("기존 benchmark catalog" in message for message in emitted)


def test_run_terminal_benchmark_mode_dispatches_all_sites_all_cases_with_grafana_config(tmp_path: Path) -> None:
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091", "token": "team-token"}, ensure_ascii=False),
        encoding="utf-8",
    )
    script = _PromptScript(selections=["전체사이트 전체케이스 실행", "종료"])
    calls: list[dict[str, object]] = []

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_pack_handler=lambda **kwargs: calls.append(kwargs) or {},
        monitoring_config_path=monitoring_config_path,
    )

    assert len(calls) == 1
    assert calls[0]["manifest_path"] == Path("gaia/tests/scenarios/external_public_manifest.json")
    assert calls[0]["push_metrics"] is True
    assert calls[0]["runner_id"]
    assert not any(call[0] == "모니터링 서버 연결" for call in script.select_calls)


def test_run_terminal_benchmark_mode_connects_before_all_sites_all_cases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monitoring_config_path = tmp_path / "monitoring.json"
    captured_cmd: list[str] = []
    script = _PromptScript(
        selections=["전체사이트 전체케이스 실행", "지금 연결하기", "종료"],
        non_empty=["http://monitor.example:9091", "secret-token"],
    )
    calls: list[dict[str, object]] = []

    class _Result:
        returncode = 0

    def fake_connect_run(cmd, **kwargs):
        del kwargs
        captured_cmd[:] = list(cmd)
        monitoring_config_path.write_text(
            json.dumps({"server": "http://monitor.example:9091", "token": "secret-token"}, ensure_ascii=False),
            encoding="utf-8",
        )
        return _Result()

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.subprocess.run", fake_connect_run)

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_pack_handler=lambda **kwargs: calls.append(kwargs) or {},
        monitoring_config_path=monitoring_config_path,
    )

    assert Path(captured_cmd[1]).as_posix().endswith("scripts/gaia_monitor_connect.py")
    assert captured_cmd[-2:] == ["--token", "secret-token"]
    assert len(calls) == 1
    assert calls[0]["push_metrics"] is True


def test_build_url_history_prioritizes_latest_default() -> None:
    urls = build_url_history(
        {
            "default_url": "https://latest.example",
            "urls": ["https://older.example", "https://latest.example", "https://backup.example"],
        }
    )

    assert urls[0] == "https://latest.example"
    assert urls[1:] == ["https://older.example", "https://backup.example"]


def test_run_terminal_benchmark_mode_dispatches_full_suite(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "기존 테스트 전체 실행",
            "로컬만 저장",
            "이전으로",
            "종료",
        ]
    )
    calls: list[dict[str, object]] = []

    def fake_run_suite_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "summary": {}, "results": [], "output_dir": "/tmp/out"}

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=fake_run_suite_handler,
    )

    assert len(calls) == 1
    assert calls[0]["preset"].key == "inu_timetable"
    assert calls[0]["run_tag"] == "full_suite"
    assert calls[0]["push_metrics"] is False
    assert len(list(calls[0]["suite_payload"]["scenarios"])) > 1


def test_run_terminal_benchmark_mode_passes_push_metrics_opt_in(tmp_path: Path) -> None:
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text("{}", encoding="utf-8")
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "기존 테스트 전체 실행",
            "업로드하기",
            "이전으로",
            "종료",
        ],
    )
    calls: list[dict[str, object]] = []

    def fake_run_suite_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "summary": {}, "results": [], "output_dir": "/tmp/out"}

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=fake_run_suite_handler,
        monitoring_config_path=monitoring_config_path,
    )

    assert len(calls) == 1
    assert calls[0]["push_metrics"] is True


def test_run_terminal_benchmark_mode_shows_connection_menu_when_upload_without_config(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "기존 테스트 전체 실행",
            "업로드하기",
            "연결 명령 보기",
            "이전으로",
            "종료",
        ],
    )
    calls: list[dict[str, object]] = []
    emitted: list[str] = []

    def fake_run_suite_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "summary": {}, "results": [], "output_dir": "/tmp/out"}

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=fake_run_suite_handler,
        monitoring_config_path=tmp_path / "missing-monitoring.json",
    )

    assert len(calls) == 1
    assert calls[0]["push_metrics"] is False
    assert any("gaia_monitor_connect.py" in message for message in emitted)


def test_run_terminal_benchmark_mode_can_connect_before_push_metrics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monitoring_config_path = tmp_path / "monitoring.json"
    captured_cmd: list[str] = []
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "기존 테스트 전체 실행",
            "업로드하기",
            "지금 연결하기",
            "이전으로",
            "종료",
        ],
        non_empty=["http://monitor.example:9091", "secret-token"],
    )
    calls: list[dict[str, object]] = []

    class _Result:
        returncode = 0

    def fake_connect_run(cmd, **kwargs):
        del kwargs
        captured_cmd[:] = list(cmd)
        monitoring_config_path.write_text("{}", encoding="utf-8")
        return _Result()

    def fake_run_suite_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "summary": {}, "results": [], "output_dir": "/tmp/out"}

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.subprocess.run", fake_connect_run)

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=fake_run_suite_handler,
        monitoring_config_path=monitoring_config_path,
    )

    assert Path(captured_cmd[1]).as_posix().endswith("scripts/gaia_monitor_connect.py")
    assert captured_cmd[-2:] == ["--token", "secret-token"]
    assert len(calls) == 1
    assert calls[0]["push_metrics"] is True


def test_grafana_url_is_derived_from_pushgateway_config(tmp_path: Path) -> None:
    config_path = tmp_path / "monitoring.json"
    config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert _grafana_url_from_monitoring_config(config_path) == (
        "http://monitor.example:3000/d/gaia-kpi-v1/gaia-benchmark-results"
    )


def test_run_benchmark_suite_appends_push_metrics_flag_when_enabled(tmp_path: Path) -> None:
    preset = find_preset("inu_timetable")
    assert preset is not None
    captured_cmd: list[str] = []

    class _FakeProcess:
        def __init__(self, cmd, **kwargs):
            del kwargs
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
                json.dumps([{"scenario_id": "INUU_001_HOME_LOGIN_VISIBLE", "status": "SUCCESS"}], ensure_ascii=False),
                encoding="utf-8",
            )
            self.stdout = iter([])

        def wait(self):
            return 0

    run_benchmark_suite(
        workspace_root=tmp_path,
        preset=preset,
        target_url="https://inuu-timetable.vercel.app/",
        suite_payload={
            "suite_id": "inu_timetable_public_v1",
            "site": {"name": "INU TIMETABLE", "base_url": "https://inuu-timetable.vercel.app/"},
            "scenarios": [
                {
                    "id": "INUU_001_HOME_LOGIN_VISIBLE",
                    "url": "https://inuu-timetable.vercel.app/",
                    "goal": "홈 화면 확인",
                }
            ],
        },
        emit=lambda message: None,
        run_tag="full_suite",
        process_factory=_FakeProcess,
        push_metrics=True,
    )

    assert "--push-metrics" in captured_cmd


def test_run_benchmark_suite_forwards_deep_qa_mode_and_tags_artifact(tmp_path: Path) -> None:
    preset = find_preset("inu_timetable")
    assert preset is not None
    captured_cmd: list[str] = []
    emitted: list[str] = []

    class _FakeProcess:
        def __init__(self, cmd, **kwargs):
            del kwargs
            captured_cmd[:] = list(cmd)
            output_dir = Path(cmd[cmd.index("--output-dir") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "scenario_count": 1,
                        "status_counts": {"SUCCESS": 1},
                        "qa_mode": "deep_adaptive_qa",
                        "benchmark_mode": "deep_qa",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (output_dir / "results.json").write_text(
                json.dumps(
                    [
                        {
                            "scenario_id": "INUU_001_HOME_LOGIN_VISIBLE",
                            "status": "SUCCESS",
                            "qa_mode": "deep_adaptive_qa",
                            "benchmark_mode": "deep_qa",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self.stdout = iter([])

        def wait(self):
            return 0

    result = run_benchmark_suite(
        workspace_root=tmp_path,
        preset=preset,
        target_url="https://inuu-timetable.vercel.app/",
        suite_payload={
            "suite_id": "inu_timetable_public_v1",
            "site": {"name": "INU TIMETABLE", "base_url": "https://inuu-timetable.vercel.app/"},
            "scenarios": [
                {
                    "id": "INUU_001_HOME_LOGIN_VISIBLE",
                    "url": "https://inuu-timetable.vercel.app/",
                    "goal": "홈 화면 확인",
                }
            ],
        },
        emit=emitted.append,
        run_tag="full_suite",
        process_factory=_FakeProcess,
        qa_mode="deep",
    )

    assert captured_cmd[captured_cmd.index("--qa-mode") + 1] == "deep_adaptive_qa"
    output_dir = Path(captured_cmd[captured_cmd.index("--output-dir") + 1])
    assert "deep_qa_full_suite" in output_dir.name
    assert result["qa_mode"] == "deep_adaptive_qa"
    assert any("qa_mode: Deep QA" in message for message in emitted)


def test_run_terminal_benchmark_mode_dispatches_single_scenario(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "개별 실행",
            "INUU_001_HOME_LOGIN_VISIBLE | 현재 메인 화면에서 로그인 버튼 또는 로그아웃 버튼 중 하나가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
            "로컬만 저장",
            "이전으로",
            "종료",
        ]
    )
    calls: list[dict[str, object]] = []

    def fake_run_suite_handler(**kwargs):
        calls.append(kwargs)
        return {"status": "success", "summary": {}, "results": [], "output_dir": "/tmp/out"}

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=fake_run_suite_handler,
    )

    assert len(calls) == 1
    assert calls[0]["run_tag"] == "INUU_001_HOME_LOGIN_VISIBLE"
    assert [row["id"] for row in calls[0]["suite_payload"]["scenarios"]] == ["INUU_001_HOME_LOGIN_VISIBLE"]


def test_run_terminal_benchmark_mode_forwards_deep_qa_to_suite_runs(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "기존 테스트 전체 실행",
            "로컬만 저장",
            "이전으로",
            "종료",
        ]
    )
    calls: list[dict[str, object]] = []
    emitted: list[str] = []

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        run_suite_handler=lambda **kwargs: calls.append(kwargs) or {},
        qa_mode="deep",
    )

    assert len(calls) == 1
    assert calls[0]["qa_mode"] == "deep_adaptive_qa"
    assert any("Deep QA 벤치마크 프로필" in message for message in emitted)


def test_run_terminal_benchmark_mode_recovers_missing_custom_suite_before_run(tmp_path: Path) -> None:
    registry_path = tmp_path / "benchmark_registry.json"
    suite_path = tmp_path / "gaia/tests/scenarios/custom_story_docs_suite.json"
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="story_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "기존 테스트 실행",
            "이전으로",
            "종료",
        ]
    )
    calls: list[dict[str, object]] = []
    emitted: list[str] = []

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=registry_path,
        run_suite_handler=lambda **kwargs: calls.append(kwargs) or {},
    )

    assert calls == []
    assert suite_path.exists()
    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    assert payload["site"]["name"] == "Storybook Docs"
    assert payload["site"]["base_url"] == "https://storybook.js.org/"
    assert payload["scenarios"] == []
    assert any("suite를 새로 만들었습니다" in message for message in emitted)
    assert any("등록된 테스트가 없습니다" in message for message in emitted)


def test_run_terminal_benchmark_mode_metrics_view_does_not_require_custom_suite(tmp_path: Path) -> None:
    registry_path = tmp_path / "benchmark_registry.json"
    suite_path = tmp_path / "gaia/tests/scenarios/custom_story_docs_suite.json"
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="story_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = tmp_path / "report.html"
    opened: list[Path] = []
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "지표 확인",
            "로컬 결과 보기",
            "이전으로",
            "종료",
        ]
    )

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=registry_path,
        report_writer=lambda **kwargs: report_path,
        report_opener=lambda path: opened.append(path) or True,
    )

    assert opened == [report_path]
    assert not suite_path.exists()


def test_run_terminal_benchmark_mode_can_pull_team_shared_tests(tmp_path: Path, monkeypatch) -> None:
    registry_path = tmp_path / "benchmark_registry.json"
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091", "token": "team-token"}, ensure_ascii=False),
        encoding="utf-8",
    )
    suite_path = tmp_path / "gaia/tests/scenarios/custom_story_docs_suite.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "scenarios": [{"id": "LOCAL_ONLY", "goal": "local"}],
        },
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="story_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "팀 테스트 공유",
            "팀 테스트 가져오기",
            "이전으로",
            "종료",
        ]
    )
    emitted: list[str] = []

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode.download_shared_suite",
        lambda **kwargs: {
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "scenarios": [{"id": "REMOTE", "goal": "remote"}],
        },
    )

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=registry_path,
        monitoring_config_path=monitoring_config_path,
    )

    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in payload["scenarios"]] == ["REMOTE", "LOCAL_ONLY"]
    assert any("팀 테스트 가져오기 완료" in message for message in emitted)


def test_run_terminal_benchmark_mode_auto_pulls_shared_tests_before_run(tmp_path: Path, monkeypatch) -> None:
    registry_path = tmp_path / "benchmark_registry.json"
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091", "token": "team-token"}, ensure_ascii=False),
        encoding="utf-8",
    )
    suite_path = tmp_path / "gaia/tests/scenarios/custom_story_docs_suite.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "scenarios": [{"id": "LOCAL_ONLY", "goal": "local"}],
        },
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="story_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "기존 테스트 실행",
            "기존 테스트 전체 실행",
            "로컬만 저장",
            "이전으로",
            "종료",
        ]
    )
    calls: list[dict[str, object]] = []
    emitted: list[str] = []

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode.download_shared_suite",
        lambda **kwargs: {
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "scenarios": [{"id": "REMOTE", "goal": "remote"}],
        },
    )

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=registry_path,
        run_suite_handler=lambda **kwargs: calls.append(kwargs) or {},
        monitoring_config_path=monitoring_config_path,
    )

    assert [row["id"] for row in calls[0]["suite_payload"]["scenarios"]] == ["REMOTE", "LOCAL_ONLY"]
    assert any("자동 가져오기 완료" in message for message in emitted)


def test_run_terminal_benchmark_mode_can_upload_team_shared_tests(tmp_path: Path, monkeypatch) -> None:
    registry_path = tmp_path / "benchmark_registry.json"
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091", "token": "team-token"}, ensure_ascii=False),
        encoding="utf-8",
    )
    suite_path = tmp_path / "gaia/tests/scenarios/custom_story_docs_suite.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "story_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "scenarios": [{"id": "REMOTE", "goal": "remote"}],
        },
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="story_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": "gaia/tests/scenarios/custom_story_docs_suite.json",
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "팀 테스트 공유",
            "내 테스트 올리기",
            "이전으로",
            "종료",
        ]
    )
    captured: dict[str, object] = {}

    def fake_upload(**kwargs):
        captured.update(kwargs)
        return "http://monitor.example:9091/shared/suites/story_docs.json"

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.upload_shared_suite", fake_upload)

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=registry_path,
        monitoring_config_path=monitoring_config_path,
        auto_pull_shared_tests=False,
    )

    assert captured["server"] == "http://monitor.example:9091"
    assert captured["token"] == "team-token"
    assert captured["suite_key"] == "story_docs"
    assert captured["suite_payload"]["scenarios"] == [{"id": "REMOTE", "goal": "remote"}]


def test_run_terminal_benchmark_mode_add_flow_uses_gui_form_when_available(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    registry_path = tmp_path / "benchmark_registry.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "demo_suite",
            "site": {"name": "Demo", "base_url": "https://example.com"},
            "grader_configs": {},
            "scenarios": [],
        },
    )
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "새로운 테스트 추가",
            "이전으로",
            "종료",
        ]
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="storybook_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": str(suite_path.relative_to(tmp_path)),
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    gui_calls: list[dict[str, object]] = []

    def fake_form(**kwargs):
        gui_calls.append(kwargs)
        return {
            "id": "STORYBOOK_001_HOME_READY",
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

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=registry_path,
        scenario_form_opener=fake_form,
    )

    reloaded = json.loads(suite_path.read_text(encoding="utf-8"))
    assert gui_calls
    assert reloaded["scenarios"][0]["id"] == "STORYBOOK_001_HOME_READY"


def test_run_terminal_benchmark_mode_add_flow_falls_back_to_terminal_when_gui_returns_none(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    registry_path = tmp_path / "benchmark_registry.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "demo_suite",
            "site": {"name": "Demo", "base_url": "https://example.com"},
            "grader_configs": {},
            "scenarios": [],
        },
    )
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "새로운 테스트 추가",
            "이전으로",
            "종료",
        ],
        non_empty=["스토리북 홈 확인", "https://storybook.js.org/", "홈이 보이는지 확인", "300"],
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="storybook_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": str(suite_path.relative_to(tmp_path)),
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=registry_path,
        scenario_form_opener=lambda **kwargs: None,
    )

    reloaded = json.loads(suite_path.read_text(encoding="utf-8"))
    assert reloaded["scenarios"][0]["name"] == "스토리북 홈 확인"
    assert reloaded["scenarios"][0]["time_budget_sec"] == 300


def test_prompt_scenario_fields_auto_generates_id_and_defaults_for_new_scenario() -> None:
    script = _PromptScript(
        non_empty=["새 갤러리 진입", "https://ko.wikipedia.org/wiki/Test", "새 시나리오 목표", "90"],
    )

    scenario = prompt_scenario_fields(
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        existing=None,
        existing_ids={"WIKI_001_HOME_SEARCH_READY"},
        default_url="https://ko.wikipedia.org/",
    )

    assert scenario["id"] == "WIKI_002_BENCHMARK"
    assert scenario["name"] == "새 갤러리 진입"
    assert scenario["constraints"]["allow_navigation"] is True
    assert scenario["constraints"]["require_ref_only"] is True
    assert scenario["constraints"]["require_state_change"] is False
    assert scenario["expected_signals"] == []
    assert scenario["time_budget_sec"] == 90


def test_add_flow_appends_valid_scenario_and_validates_saved_json(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    base_payload = {
        "suite_id": "wiki_suite",
        "site": {"name": "Wikipedia", "base_url": "https://ko.wikipedia.org/"},
        "grader_configs": {},
        "scenarios": [],
    }
    scenario = {
        "id": "WIKI_900_TEST",
        "url": "https://ko.wikipedia.org/wiki/Test",
        "goal": "테스트 목표",
        "constraints": {
            "allow_navigation": True,
            "require_ref_only": True,
            "require_state_change": False,
        },
        "expected_signals": ["heading_visible"],
        "time_budget_sec": 120,
    }

    updated = append_scenario_to_suite(base_payload, scenario)
    save_suite_payload(suite_path, updated)
    reloaded = json.loads(suite_path.read_text(encoding="utf-8"))

    assert len(reloaded["scenarios"]) == 1
    assert reloaded["scenarios"][0]["id"] == "WIKI_900_TEST"


def test_edit_flow_preserves_unchanged_fields_on_enter() -> None:
    existing = {
        "id": "MDN_001_HOME_DOCS_READY",
        "url": "https://developer.mozilla.org/ko/",
        "goal": "홈 화면 확인",
        "constraints": {
            "allow_navigation": False,
            "require_ref_only": True,
            "require_state_change": False,
            "requires_test_credentials": True,
        },
        "expected_signals": ["searchbox_visible", "link_visible"],
        "difficulty": "easy",
        "time_budget_sec": 60,
        "grader_configs": {"custom": True},
    }
    script = _PromptScript()

    scenario = prompt_scenario_fields(
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        existing=existing,
        existing_ids={existing["id"]},
        default_url="https://developer.mozilla.org/ko/",
    )

    assert scenario == existing


def test_prompt_scenario_fields_uses_five_minute_default_timeout() -> None:
    script = _PromptScript(
        non_empty=["새 테스트", "https://example.com", "예시 목표", "300"],
    )

    scenario = prompt_scenario_fields(
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        existing=None,
        existing_ids=set(),
        default_url="https://example.com",
    )

    assert scenario["time_budget_sec"] == 300


def test_open_scenario_form_macos_dialogs_builds_payload(monkeypatch) -> None:
    answers = iter(["스토리북 홈 확인", "https://storybook.js.org/", "홈이 보이는지 확인", "300"])

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._prompt_macos_dialog",
        lambda **kwargs: next(answers),
    )

    scenario = open_scenario_form_macos_dialogs(
        emit=lambda message: None,
        existing=None,
        existing_ids={"STORYBOOK_001_OLD"},
        default_url="https://storybook.js.org/",
        title="Storybook 테스트 추가",
    )

    assert scenario is not None
    assert scenario["name"] == "스토리북 홈 확인"
    assert scenario["url"] == "https://storybook.js.org/"
    assert scenario["goal"] == "홈이 보이는지 확인"
    assert scenario["time_budget_sec"] == 300


def test_open_scenario_form_gui_uses_macos_dialog_fallback_when_pyside_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.sys.platform", "darwin")

    fallback_calls: list[dict[str, object]] = []

    def fake_macos_form(**kwargs):
        fallback_calls.append(kwargs)
        return {
            "id": "STORYBOOK_001_HOME_READY",
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

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode.open_scenario_form_pyside",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("PySide6 unavailable")),
    )
    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.open_scenario_form_macos_dialogs", fake_macos_form)

    scenario = open_scenario_form_gui(
        emit=lambda message: None,
        existing=None,
        existing_ids=set(),
        default_url="https://storybook.js.org/",
        title="Storybook 테스트 추가",
    )

    assert fallback_calls
    assert scenario is not None
    assert scenario["id"] == "STORYBOOK_001_HOME_READY"


def test_open_scenario_form_windows_dialogs_builds_payload(monkeypatch) -> None:
    answers = iter(["스토리북 홈 확인", "https://storybook.js.org/", "홈이 보이는지 확인", "300"])

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._prompt_windows_dialog",
        lambda **kwargs: next(answers),
    )

    scenario = open_scenario_form_windows_dialogs(
        emit=lambda message: None,
        existing=None,
        existing_ids={"STORYBOOK_001_OLD"},
        default_url="https://storybook.js.org/",
        title="Storybook 테스트 추가",
    )

    assert scenario is not None
    assert scenario["name"] == "스토리북 홈 확인"
    assert scenario["url"] == "https://storybook.js.org/"
    assert scenario["goal"] == "홈이 보이는지 확인"
    assert scenario["time_budget_sec"] == 300


def test_open_scenario_form_gui_uses_windows_dialog_fallback_when_pyside_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.sys.platform", "win32")

    fallback_calls: list[dict[str, object]] = []

    def fake_windows_form(**kwargs):
        fallback_calls.append(kwargs)
        return {
            "id": "STORYBOOK_001_HOME_READY",
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

    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode.open_scenario_form_pyside",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("PySide6 unavailable")),
    )
    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.open_scenario_form_windows_dialogs", fake_windows_form)

    scenario = open_scenario_form_gui(
        emit=lambda message: None,
        existing=None,
        existing_ids=set(),
        default_url="https://storybook.js.org/",
        title="Storybook 테스트 추가",
    )

    assert fallback_calls
    assert scenario is not None
    assert scenario["id"] == "STORYBOOK_001_HOME_READY"


def test_open_scenario_form_gui_prefers_pyside_when_available(monkeypatch) -> None:
    pyside_calls: list[dict[str, object]] = []

    def fake_pyside(**kwargs):
        pyside_calls.append(kwargs)
        return {
            "id": "STORYBOOK_001_HOME_READY",
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

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.open_scenario_form_pyside", fake_pyside)

    scenario = open_scenario_form_gui(
        emit=lambda message: None,
        existing=None,
        existing_ids=set(),
        default_url="https://storybook.js.org/",
        title="Storybook 테스트 추가",
    )

    assert pyside_calls
    assert scenario is not None
    assert scenario["id"] == "STORYBOOK_001_HOME_READY"


def test_open_scenario_form_pyside_reads_result_from_worker_subprocess(monkeypatch) -> None:
    class _Result:
        returncode = 0
        stdout = json.dumps(
            {
                "id": "STORYBOOK_001_HOME_READY",
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
            },
            ensure_ascii=False,
        )
        stderr = ""

    monkeypatch.setattr("gaia.src.terminal_benchmark_mode.subprocess.run", lambda *args, **kwargs: _Result())

    scenario = open_scenario_form_pyside(
        emit=lambda message: None,
        existing=None,
        existing_ids=set(),
        default_url="https://storybook.js.org/",
        title="Storybook 테스트 추가",
    )

    assert scenario is not None
    assert scenario["id"] == "STORYBOOK_001_HOME_READY"


def test_scenario_form_worker_main_returns_json(monkeypatch, tmp_path: Path, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "existing": {},
                "existing_ids": [],
                "default_url": "https://storybook.js.org/",
                "title": "Storybook 테스트 추가",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "gaia.src.terminal_benchmark_mode._open_scenario_form_pyside_inline",
        lambda **kwargs: {
            "id": "STORYBOOK_001_HOME_READY",
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
        },
    )

    code = _main(["--scenario-form-worker", str(request_path)])
    captured = capsys.readouterr()

    assert code == 0
    assert "STORYBOOK_001_HOME_READY" in captured.out


def test_delete_flow_removes_exactly_one_scenario() -> None:
    payload = {
        "scenarios": [
            {"id": "A", "goal": "alpha"},
            {"id": "B", "goal": "beta"},
        ]
    }

    updated = delete_scenario_from_suite(payload, "A")

    assert [row["id"] for row in updated["scenarios"]] == ["B"]


def test_run_terminal_benchmark_mode_deletes_scenario_without_confirmation(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    registry_path = tmp_path / "benchmark_registry.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "demo_suite",
            "site": {"name": "Demo", "base_url": "https://example.com"},
            "grader_configs": {},
            "scenarios": [
                {"id": "A", "goal": "alpha", "url": "https://example.com/a", "constraints": {}, "time_budget_sec": 300},
                {"id": "B", "goal": "beta", "url": "https://example.com/b", "constraints": {}, "time_budget_sec": 300},
            ],
        },
    )
    script = _PromptScript(
        selections=[
            "Storybook Docs",
            "https://storybook.js.org/",
            "테스트 편집",
            "A | alpha",
            "삭제",
            "이전으로",
            "종료",
        ]
    )
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="storybook_docs",
        site_definition={
            "label": "Storybook Docs",
            "default_url": "https://storybook.js.org/",
            "suite_path": str(suite_path.relative_to(tmp_path)),
            "host_aliases": ["storybook.js.org"],
        },
    )
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

    run_terminal_benchmark_mode(
        workspace_root=tmp_path,
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
        registry_path=registry_path,
    )

    reloaded = json.loads(suite_path.read_text(encoding="utf-8"))
    assert [row["id"] for row in reloaded["scenarios"]] == ["B"]


def test_metrics_view_writes_html_board_and_opens_report(tmp_path: Path) -> None:
    bench_root = tmp_path / "artifacts" / "benchmarks" / "run_1"
    bench_root.mkdir(parents=True)
    (bench_root / "summary.json").write_text(
        json.dumps(
            {
                "started_at": "2026-04-12 09:00:00",
                "site": {"base_url": "https://inuu-timetable.vercel.app/"},
                "status_counts": {"SUCCESS": 1, "FAIL": 0},
                "metrics": {"success_rate": 1.0, "avg_time_seconds": 12.3},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (bench_root / "results.json").write_text(
        json.dumps([{"scenario_id": "INUU_001", "status": "SUCCESS", "reason": "ok"}], ensure_ascii=False),
        encoding="utf-8",
    )
    preset = find_preset("inu_timetable")
    assert preset is not None
    opened: list[str] = []

    report_path = write_benchmark_report_html(
        workspace_root=tmp_path,
        preset=preset,
        selected_url="https://inuu-timetable.vercel.app/",
    )
    opened_ok = open_benchmark_report(report_path, opener=lambda uri: opened.append(uri) or True)

    assert report_path.exists()
    assert "INUU_001" in report_path.read_text(encoding="utf-8")
    assert opened_ok is True
    assert opened == [report_path.resolve().as_uri()]


def test_terminal_metrics_view_can_open_local_report(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "지표 확인",
            "로컬 결과 보기",
            "이전으로",
            "종료",
        ]
    )
    preset = find_preset("inu_timetable")
    assert preset is not None
    report_path = tmp_path / "report.html"
    opened: list[Path] = []
    emitted: list[str] = []

    def fake_report_writer(**kwargs):
        assert kwargs["preset"].key == preset.key
        report_path.write_text("<html>ok</html>", encoding="utf-8")
        return report_path

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        report_writer=fake_report_writer,
        report_opener=lambda path: opened.append(path) or True,
    )

    assert opened == [report_path]
    assert any("로컬 결과 보드" in message for message in emitted)


def test_terminal_metrics_view_can_delete_failed_records(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "지표 확인",
            "실패 기록 삭제",
            "삭제하기",
            "이전으로",
            "종료",
        ]
    )
    calls: list[dict[str, object]] = []
    emitted: list[str] = []

    def fake_pruner(**kwargs):
        calls.append(dict(kwargs))
        return {"deleted": 2 if kwargs.get("dry_run") else 2, "deleted_dirs": ["/tmp/a", "/tmp/b"]}

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        record_pruner=fake_pruner,
    )

    assert [call["dry_run"] for call in calls] == [True, False]
    assert all(call["failed_only"] is True for call in calls)
    assert any("실패 기록 삭제 완료: 2개" in message for message in emitted)


def test_terminal_metrics_view_can_open_grafana(tmp_path: Path) -> None:
    monitoring_config_path = tmp_path / "monitoring.json"
    monitoring_config_path.write_text(
        json.dumps({"server": "http://monitor.example:9091"}, ensure_ascii=False),
        encoding="utf-8",
    )
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "지표 확인",
            "Grafana 열기",
            "이전으로",
            "종료",
        ]
    )
    opened: list[str] = []
    emitted: list[str] = []

    def fail_report_writer(**kwargs):
        del kwargs
        raise AssertionError("local report should not be generated")

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        report_writer=fail_report_writer,
        grafana_opener=lambda url: opened.append(url) or True,
        monitoring_config_path=monitoring_config_path,
        auto_pull_shared_tests=False,
    )

    assert opened == ["http://monitor.example:3000/d/gaia-kpi-v1/gaia-benchmark-results"]
    assert any("Grafana 대시보드" in message for message in emitted)


def test_terminal_metrics_view_shows_connect_menu_when_grafana_not_connected(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "지표 확인",
            "Grafana 열기",
            "연결 명령 보기",
            "이전으로",
            "종료",
        ]
    )
    opened: list[str] = []
    emitted: list[str] = []

    run_terminal_benchmark_mode(
        workspace_root=_repo_root(),
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=emitted.append,
        registry_path=tmp_path / "benchmark_registry.json",
        grafana_opener=lambda url: opened.append(url) or True,
        monitoring_config_path=tmp_path / "missing-monitoring.json",
    )

    assert opened == []
    assert any("gaia_monitor_connect.py" in message for message in emitted)


def test_jobkorea_preset_stays_visible_even_without_existing_tests() -> None:
    preset = find_preset("jobkorea")

    assert preset is not None
    assert preset.label == "잡코리아"
    assert preset.suite_path == "gaia/tests/scenarios/jobkorea_public_suite.json"


def test_replace_scenario_in_suite_keeps_order_and_updates_selected_row() -> None:
    payload = {
        "scenarios": [
            {"id": "A", "goal": "alpha"},
            {"id": "B", "goal": "beta"},
        ]
    }

    updated = replace_scenario_in_suite(payload, "B", {"id": "B2", "goal": "beta updated"})

    assert [row["id"] for row in updated["scenarios"]] == ["A", "B2"]


def test_build_single_scenario_suite_payload_keeps_selected_case_only() -> None:
    payload = {
        "suite_id": "demo",
        "scenarios": [
            {"id": "A", "goal": "alpha"},
            {"id": "B", "goal": "beta"},
        ],
    }

    single = build_single_scenario_suite_payload(payload, "B")

    assert [row["id"] for row in single["scenarios"]] == ["B"]


def test_manage_benchmark_sites_can_add_custom_site(tmp_path: Path) -> None:
    script = _PromptScript(
        texts=["storybook_docs"],
        non_empty=["Storybook Docs", "https://storybook.js.org/"],
    )

    updated = manage_benchmark_sites(
        workspace_root=tmp_path,
        registry={"sites": {}},
        action="사이트 추가",
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
    )

    catalog, preset_map = build_terminal_benchmark_catalog(updated)
    assert "storybook_docs" in preset_map
    assert any(item["label"] == "Storybook Docs" and item["is_custom"] for item in catalog)
    suite_path = tmp_path / "gaia/tests/scenarios/custom_storybook_docs_suite.json"
    assert suite_path.exists()
    suite_payload = json.loads(suite_path.read_text(encoding="utf-8"))
    assert suite_payload["site"]["name"] == "Storybook Docs"
    assert suite_payload["site"]["base_url"] == "https://storybook.js.org/"


def test_manage_benchmark_sites_can_edit_custom_site(tmp_path: Path) -> None:
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="storybook_docs",
        site_definition=create_custom_site_definition(
            site_key="storybook_docs",
            label="Storybook Docs",
            default_url="https://storybook.js.org/",
        ),
    )
    suite_path = tmp_path / "gaia/tests/scenarios/custom_storybook_docs_suite.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "storybook_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "grader_configs": {},
            "scenarios": [],
        },
    )
    script = _PromptScript(
        selections=["Storybook Docs"],
        texts=["Storybook Docs Korea"],
        non_empty=["https://storybook.js.org/tutorials/"],
    )

    updated = manage_benchmark_sites(
        workspace_root=tmp_path,
        registry=registry,
        action="사이트 수정",
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
    )

    catalog, preset_map = build_terminal_benchmark_catalog(updated)
    assert preset_map["storybook_docs"].label == "Storybook Docs Korea"
    assert any(item["label"] == "Storybook Docs Korea" for item in catalog)
    suite_payload = json.loads(suite_path.read_text(encoding="utf-8"))
    assert suite_payload["site"]["name"] == "Storybook Docs Korea"
    assert suite_payload["site"]["base_url"] == "https://storybook.js.org/tutorials/"


def test_manage_benchmark_sites_can_delete_custom_site(tmp_path: Path) -> None:
    registry = upsert_custom_benchmark_site(
        {"sites": {}},
        site_key="storybook_docs",
        site_definition=create_custom_site_definition(
            site_key="storybook_docs",
            label="Storybook Docs",
            default_url="https://storybook.js.org/",
        ),
    )
    suite_path = tmp_path / "gaia/tests/scenarios/custom_storybook_docs_suite.json"
    save_suite_payload(
        suite_path,
        {
            "suite_id": "storybook_docs_public_v1",
            "site": {"name": "Storybook Docs", "base_url": "https://storybook.js.org/"},
            "grader_configs": {},
            "scenarios": [],
        },
    )
    script = _PromptScript(
        selections=["Storybook Docs"],
        non_empty=["storybook_docs"],
    )

    updated = manage_benchmark_sites(
        workspace_root=tmp_path,
        registry=registry,
        action="사이트 삭제",
        prompt_select=script.select,
        prompt=script.text,
        prompt_non_empty=script.non_empty_prompt,
        emit=lambda message: None,
    )

    assert updated == delete_custom_benchmark_site(registry, "storybook_docs")
    assert not suite_path.exists()
