import io
import json
from types import SimpleNamespace

import pytest

from scripts.run_goal_benchmark import (
    _build_child_code,
    _compute_kpi_metrics,
    _compute_metrics,
    _infer_provider_from_model,
    _prepare_scenario_env,
    _provider_credential_error,
    _run_scenario_once,
    _resolve_codex_exec_timeout,
    _resolve_scenario_timeout_budget,
    _should_emit_live_trace_line,
    _should_push_metrics,
)
from scripts.benchmark_blocking import (
    BLOCKED_CAPTCHA_REASON_CODE,
    BLOCKED_USER_ACTION_STATUS,
    is_blocked_user_action,
    normalize_blocked_user_action_row,
)


def test_build_child_code_propagates_expected_signals_without_mcp_host_guard() -> None:
    scenario = {
        "id": "INUU_001_HOME_LOGIN_VISIBLE",
        "url": "https://inuu-timetable.vercel.app/",
        "goal": "현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
        "test_data": {
            "filter_control_hint": {
                "include_terms": ["검색 결과"],
                "exclude_terms": ["위시리스트"],
            },
        },
        "expected_signals": ["text_visible", "cta_visible"],
        "constraints": {
            "allow_navigation": False,
            "require_ref_only": True,
            "require_state_change": False,
        },
    }

    code = _build_child_code(scenario, "session-1")

    assert "should_auto_start_mcp_host()" not in code
    assert "prepared_goal.expected_signals" in code
    assert "harness_expected_signals" in code
    assert "scenario_test_data" in code
    assert "filter_control_hint" in code
    assert "text_visible" in code
    assert "cta_visible" in code
    assert "_TeeWriter" in code
    assert "sys.__stdout__" in code


def test_timeout_floor_applies_by_default() -> None:
    budget = _resolve_scenario_timeout_budget(
        scenario_budget=180,
        timeout_cap=600,
        timeout_floor=600,
    )
    assert budget == 600


def test_timeout_cap_is_clamped_to_minimum_budget() -> None:
    budget = _resolve_scenario_timeout_budget(
        scenario_budget=180,
        timeout_cap=180,
        timeout_floor=600,
    )
    assert budget == 600


def test_codex_exec_timeout_scales_with_benchmark_budget() -> None:
    assert _resolve_codex_exec_timeout(600) == 300
    assert _resolve_codex_exec_timeout(900) == 300


def test_prepare_scenario_env_sets_codex_runtime_guards() -> None:
    env = _prepare_scenario_env({"FOO": "bar"}, 600)

    assert env["FOO"] == "bar"
    assert env["GAIA_CODEX_EXEC_TIMEOUT_SEC"] == "300"
    assert env["GAIA_CODEX_REASONING_EFFORT"] == "low"


def test_infer_provider_from_model_handles_openai_gemini_and_ollama() -> None:
    assert _infer_provider_from_model("gpt-5.5") == "openai"
    assert _infer_provider_from_model("gpt-5.4") == "openai"
    assert _infer_provider_from_model("gpt-5.3-codex") == "openai"
    assert _infer_provider_from_model("gemini-2.5-pro") == "gemini"
    assert _infer_provider_from_model("gemma4:26b") == "ollama"
    assert _infer_provider_from_model("unknown-model") == ""


def test_provider_credential_error_fails_fast_for_missing_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.run_goal_benchmark._has_codex_cli_auth", lambda: False)

    assert "provider=openai" in _provider_credential_error("openai", {})
    assert _provider_credential_error("openai", {"OPENAI_API_KEY": "sk-test"}) == ""
    assert _provider_credential_error("openai", {"OPENAI_ADMIN_KEY": "sk-admin-test"}) == ""
    assert _provider_credential_error("ollama", {}) == ""


def test_provider_credential_error_accepts_codex_cli_auth(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "redacted"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr("scripts.run_goal_benchmark.Path.home", lambda: tmp_path)
    monkeypatch.setattr("scripts.run_goal_benchmark.shutil.which", lambda name: "/opt/homebrew/bin/codex" if name == "codex" else None)

    assert _provider_credential_error("openai", {}) == ""


def test_should_emit_live_trace_line_filters_to_step_level_messages() -> None:
    assert _should_emit_live_trace_line("🎯 목표 시작: 테스트")
    assert _should_emit_live_trace_line("--- Step 2/40 ---")
    assert _should_emit_live_trace_line("LLM 결정: click - 버튼을 누른다")
    assert _should_emit_live_trace_line("✅ 목표 달성! 이유: 확인됨")
    assert not _should_emit_live_trace_line("🧪 llm trace: {'used_llm': True}")
    assert not _should_emit_live_trace_line('{"schema_version":"gaia.benchmark.v1"}')


def test_monitoring_push_is_explicit_opt_in() -> None:
    assert _should_push_metrics(SimpleNamespace(push_metrics=False)) is False
    assert _should_push_metrics(SimpleNamespace(push_metrics=True)) is True
    assert _should_push_metrics(SimpleNamespace()) is False


def test_run_scenario_once_preserves_child_traceback_when_json_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeStdout(io.StringIO):
        def __init__(self) -> None:
            self.text = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'openai'\n"
            super().__init__(self.text)

    class _FakeProcess:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.stdout = _FakeStdout()
            self._returncode: int | None = None

        def poll(self) -> int | None:
            if self.stdout.tell() >= len(self.stdout.text):
                self._returncode = 1
            return self._returncode

        def wait(self, timeout: int | None = None) -> int:
            self._returncode = 1
            return 1

        def kill(self) -> None:
            self._returncode = -9

    monkeypatch.setattr("scripts.run_goal_benchmark.subprocess.Popen", _FakeProcess)

    row = _run_scenario_once(
        {"id": "BROKEN_001", "url": "https://example.com", "goal": "check page"},
        python_executable="python",
        session_id="broken-session",
        timeout_sec=600,
        env={},
    )

    assert row["status"] == "FAIL"
    assert row["exit_code"] == 1
    assert "child_process_failed(exit_code=1)" in row["reason"]
    assert "ModuleNotFoundError: No module named 'openai'" in row["reason"]
    assert "ModuleNotFoundError: No module named 'openai'" in row["captured_log"]


def test_korean_captcha_gate_is_normalized_to_blocked_user_action() -> None:
    row = normalize_blocked_user_action_row(
        {
            "scenario_id": "NAVERSHOP_002_SEARCH_PRODUCT",
            "status": "FAIL",
            "reason": "현재 화면은 NAVER 보안 확인 캡차이며 보안문자 정답이 필요합니다.",
            "summary": {"final_status": "FAIL", "reason_code_summary": {"wait_repeated": 2}},
            "captured_log": "human_answer requested: 보안문자 입력 필요",
        }
    )

    assert row["status"] == BLOCKED_USER_ACTION_STATUS
    assert row["summary"]["final_status"] == BLOCKED_USER_ACTION_STATUS
    assert row["summary"]["blocked_reason_code"] == BLOCKED_CAPTCHA_REASON_CODE
    assert row["summary"]["reason_code_summary"][BLOCKED_CAPTCHA_REASON_CODE] == 1
    assert is_blocked_user_action(row)


def test_blocked_captcha_is_excluded_from_primary_success_rate() -> None:
    rows = [
        {"scenario_id": "OK_001", "status": "SUCCESS", "duration_seconds": 5.0, "summary": {}},
        normalize_blocked_user_action_row(
            {
                "scenario_id": "NAVERSHOP_002_SEARCH_PRODUCT",
                "status": "FAIL",
                "reason": "NAVER 보안 확인 캡차로 보안문자 입력이 필요합니다.",
                "duration_seconds": 10.0,
                "summary": {"final_status": "FAIL", "reason_code_summary": {}},
            }
        ),
        {
            "scenario_id": "TIMEOUT_001",
            "status": "FAIL",
            "reason": "benchmark_timeout(600s)",
            "duration_seconds": 600.0,
            "summary": {},
        },
    ]

    metrics = _compute_metrics(rows, repeats=1)
    kpis = _compute_kpi_metrics(rows, repeats=1)

    assert metrics["success_rate"] == 0.3333
    assert metrics["primary_success_rate"] == 0.5
    assert metrics["blocked_runs_total"] == 1
    assert kpis["scenario_success_rate"] == 0.3333
    assert kpis["primary_success_rate"] == 0.5
    assert kpis["counts"]["blocked"] == 1
    assert kpis["counts"]["primary_runs"] == 2
