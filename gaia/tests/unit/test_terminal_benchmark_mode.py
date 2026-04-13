from __future__ import annotations

import json
from pathlib import Path

from gaia.cli import run_launcher
from gaia.src.gui.benchmark_mode import find_preset
from gaia.src.terminal_benchmark_mode import (
    append_scenario_to_suite,
    build_single_scenario_suite_payload,
    build_terminal_benchmark_catalog,
    build_url_history,
    create_custom_site_definition,
    delete_scenario_from_suite,
    delete_custom_benchmark_site,
    manage_benchmark_sites,
    open_benchmark_report,
    prompt_scenario_fields,
    replace_scenario_in_suite,
    run_terminal_benchmark_mode,
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


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
    def _fake_benchmark_runner(*, workspace_root: Path) -> int:
        called["workspace_root"] = workspace_root
        return 37

    monkeypatch.setattr("gaia.cli._run_terminal_benchmark_mode", _fake_benchmark_runner)
    monkeypatch.setattr(
        "gaia.cli._resolve_control_channel",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("control prompt should be skipped")),
    )

    result = run_launcher([])

    assert result == 37
    assert called["workspace_root"] == _repo_root()

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
    assert "INU TIMETABLE" in first_prompt[1]
    assert "맞춤법 검사기" in first_prompt[1]
    assert "디시인사이드" in first_prompt[1]
    assert "사이트 추가" in first_prompt[1]
    assert "사이트 수정" in first_prompt[1]
    assert "사이트 삭제" in first_prompt[1]


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
    assert len(list(calls[0]["suite_payload"]["scenarios"])) > 1


def test_run_terminal_benchmark_mode_dispatches_single_scenario(tmp_path: Path) -> None:
    script = _PromptScript(
        selections=[
            "INU TIMETABLE",
            "https://inuu-timetable.vercel.app/",
            "기존 테스트 실행",
            "개별 실행",
            "INUU_001_HOME_LOGIN_VISIBLE | 현재 메인 화면에서 로그인 버튼 또는 로그아웃 버튼 중 하나가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
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


def test_delete_flow_removes_exactly_one_scenario() -> None:
    payload = {
        "scenarios": [
            {"id": "A", "goal": "alpha"},
            {"id": "B", "goal": "beta"},
        ]
    }

    updated = delete_scenario_from_suite(payload, "A")

    assert [row["id"] for row in updated["scenarios"]] == ["B"]


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


def test_spell_checker_preset_stays_visible_even_without_existing_tests() -> None:
    preset = find_preset("spell_checker")

    assert preset is not None
    assert preset.label == "맞춤법 검사기"
    assert preset.suite_path == "gaia/tests/scenarios/spell_checker_public_suite.json"


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
