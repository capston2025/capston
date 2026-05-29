import io
import json
from types import SimpleNamespace

import pytest

from scripts.run_goal_benchmark import (
    COLD_PROCESS_RUNTIME,
    DEEP_ADAPTIVE_QA_MODE,
    WARM_PROCESS_COLD_STATE_RUNTIME,
    WARM_PROCESS_WARM_STATE_RUNTIME,
    _apply_qa_mode_env,
    _apply_max_steps_env,
    _apply_provider_model_env,
    _build_child_code,
    _battle_upload_config,
    _benchmark_mode_label,
    _build_battle_upload_payload,
    _compute_kpi_metrics,
    _compute_metrics,
    _infer_provider_from_model,
    _normalize_runtime_isolation,
    _normalize_qa_mode,
    _prepare_scenario_env,
    _provider_credential_error,
    _runtime_uses_cold_state,
    _runtime_uses_warm_process,
    _run_scenario_once,
    _resolve_codex_exec_timeout,
    _resolve_scenario_timeout_budget,
    _should_emit_live_trace_line,
    _should_publish_battle_board,
    _should_push_metrics,
    _try_upload_battle_record,
)
from scripts.runner_identity import resolve_runner_id, sanitize_runner_id
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
    assert "reset_browser_scenario_state" in code
    assert "close_mcp_session" in code
    assert "scenario tab cleanup" in code
    assert "closed_stale_tabs" in code


def test_build_child_code_forces_deep_qa_mode_for_benchmark_runs() -> None:
    scenario = {
        "id": "DEEP_001",
        "url": "https://example.com",
        "goal": "상품 필터 동작을 검증한다",
        "test_data": {"qa_mode": "adaptive"},
    }

    code = _build_child_code(scenario, "session-1", qa_mode="deep")

    assert '"qa_mode": "deep_adaptive_qa"' in code
    assert "goal_test_data['qa_mode'] = benchmark_qa_mode" in code
    assert "goal_test_data['deep_adaptive_qa'] = {'enabled': True}" in code


def test_build_child_code_applies_scenario_max_steps() -> None:
    scenario = {
        "id": "LONG_001",
        "url": "https://example.com",
        "goal": "긴 공개 탐색 테스트를 수행한다",
        "max_steps": 120,
    }

    code = _build_child_code(scenario, "session-1")

    assert '"max_steps": 120' in code
    assert "scenario_max_steps = int" in code
    assert "prepared_goal.max_steps = scenario_max_steps" in code


def test_runtime_isolation_helpers_normalize_warm_and_cold_modes() -> None:
    assert _normalize_runtime_isolation("warm") == WARM_PROCESS_COLD_STATE_RUNTIME
    assert _normalize_runtime_isolation("demo") == WARM_PROCESS_WARM_STATE_RUNTIME
    assert _normalize_runtime_isolation("legacy") == COLD_PROCESS_RUNTIME
    assert _runtime_uses_warm_process(WARM_PROCESS_COLD_STATE_RUNTIME) is True
    assert _runtime_uses_warm_process(COLD_PROCESS_RUNTIME) is False
    assert _runtime_uses_cold_state(WARM_PROCESS_COLD_STATE_RUNTIME) is True
    assert _runtime_uses_cold_state(WARM_PROCESS_WARM_STATE_RUNTIME) is False


def test_qa_mode_helpers_normalize_and_apply_env() -> None:
    env = {"GAIA_ADAPTIVE_QA": "1", "GAIA_DEEP_ADAPTIVE_QA": "1"}

    assert _normalize_qa_mode("deep") == DEEP_ADAPTIVE_QA_MODE
    assert _benchmark_mode_label(DEEP_ADAPTIVE_QA_MODE) == "deep_qa"

    _apply_qa_mode_env(env, "deep")

    assert "GAIA_ADAPTIVE_QA" not in env
    assert env["GAIA_DEEP_ADAPTIVE_QA"] == "1"

    _apply_qa_mode_env(env, "off")

    assert "GAIA_ADAPTIVE_QA" not in env
    assert "GAIA_DEEP_ADAPTIVE_QA" not in env


def test_apply_max_steps_env_sets_positive_override_only() -> None:
    env: dict[str, str] = {}

    assert _apply_max_steps_env(env, 120) == 120
    assert env["GAIA_GOAL_MAX_STEPS_OVERRIDE"] == "120"

    assert _apply_max_steps_env(env, 0) == 0
    assert env["GAIA_GOAL_MAX_STEPS_OVERRIDE"] == "120"


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


def test_provider_credential_error_accepts_gemini_vertex_credentials(tmp_path) -> None:
    credentials = tmp_path / "vertex-service-account.json"
    credentials.write_text("{}", encoding="utf-8")
    env = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "project-test",
        "GOOGLE_CLOUD_LOCATION": "global",
        "GOOGLE_APPLICATION_CREDENTIALS": str(credentials),
    }

    assert _provider_credential_error("gemini", env) == ""


def test_provider_credential_error_reports_incomplete_gemini_vertex_env() -> None:
    env = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_CLOUD_PROJECT": "project-test",
    }

    assert "Vertex AI requires" in _provider_credential_error("gemini", env)


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


def test_apply_provider_model_env_overrides_stale_shell_provider() -> None:
    env = {
        "GAIA_LLM_PROVIDER": "gemini",
        "GAIA_LLM_MODEL": "gemini-3.5-flash",
        "VISION_PROVIDER": "gemini",
    }

    _apply_provider_model_env(env, "openai", "gpt-5.5")

    assert env["GAIA_LLM_PROVIDER"] == "openai"
    assert env["GAIA_LLM_MODEL"] == "gpt-5.5"
    assert env["VISION_PROVIDER"] == "gemini"


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


def test_battle_board_publish_is_explicit_but_can_use_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAIA_BATTLE_BOARD", raising=False)

    assert _should_publish_battle_board(SimpleNamespace(battle_board=False)) is False
    assert _should_publish_battle_board(SimpleNamespace(battle_board=True)) is True

    monkeypatch.setenv("GAIA_BATTLE_BOARD", "1")

    assert _should_publish_battle_board(SimpleNamespace()) is True


def test_battle_upload_config_requires_url_and_session() -> None:
    assert _battle_upload_config(SimpleNamespace(), {}) == {}
    assert _battle_upload_config(
        SimpleNamespace(battle_upload_url="https://board.example/api/records", battle_session_id="battle-1"),
        {},
    )["session_id"] == "battle-1"
    assert _battle_upload_config(
        SimpleNamespace(battle_upload_url="", battle_session_id=""),
        {
            "GAIA_BATTLE_UPLOAD_URL": "https://board.example/api/records",
            "GAIA_BATTLE_SESSION_ID": "battle-2",
            "GAIA_BATTLE_UPLOAD_TOKEN": "secret",
        },
    )["token"] == "secret"


def test_build_battle_upload_payload_maps_gaia_result() -> None:
    payload = _build_battle_upload_payload(
        config={
            "session_id": "battle-live",
            "participant_id": "gaia",
            "participant_name": "GAIA",
            "scenario_label": "현장 QA 미션",
        },
        row={
            "scenario_id": "DEMO_001",
            "status": "SUCCESS",
            "duration_seconds": 12.3,
            "runner_id": "runner",
            "provider": "openai",
            "model": "gpt-5.5",
            "artifact_url": "https://evidence.example/gaia-run",
            "expected_signals": ["order-visible"],
            "summary": {
                "attachments": [
                    {
                        "kind": "image_base64",
                        "mime": "image/png",
                        "data": "ZmFrZQ==",
                        "label": "최종 증거 화면",
                        "path": "/tmp/final-proof.png",
                        "current_url": "https://service.example/result",
                    }
                ]
            },
        },
        scenario={"id": "DEMO_001", "goal": "demo"},
        summary={"battle_board": {"url": "file:///tmp/battle_board.html"}, "suite_id": "suite-demo"},
    )

    assert payload["sessionId"] == "battle-live"
    assert payload["participantType"] == "gaia"
    assert payload["scenarioLabel"] == "현장 QA 미션"
    assert payload["artifactUrl"] == "https://evidence.example/gaia-run"
    assert payload["metadata"]["evidenceSource"] == "gaia-runner"
    assert payload["metadata"]["suiteId"] == "suite-demo"
    assert payload["metadata"]["goal"] == "demo"
    assert payload["metadata"]["model"] == "gpt-5.5"
    assert payload["metadata"]["expectedSignals"] == ["order-visible"]
    assert payload["metadata"]["screenshotDataUrl"] == "data:image/png;base64,ZmFrZQ=="
    assert payload["metadata"]["screenshotLabel"] == "최종 증거 화면"
    assert payload["metadata"]["currentUrl"] == "https://service.example/result"


def test_build_battle_upload_payload_skips_oversized_screenshot() -> None:
    payload = _build_battle_upload_payload(
        config={
            "session_id": "battle-live",
            "participant_id": "gaia",
            "participant_name": "GAIA",
            "scenario_label": "운영 미션",
            "screenshot_max_bytes": "2",
        },
        row={
            "scenario_id": "CASE_001",
            "status": "SUCCESS",
            "summary": {
                "attachments": [
                    {
                        "kind": "image_base64",
                        "mime": "image/png",
                        "data": "ZmFrZQ==",
                        "path": "/tmp/too-large.png",
                    }
                ]
            },
        },
        scenario={"id": "CASE_001", "goal": "run"},
        summary={"suite_id": "suite-live"},
    )

    assert "screenshotDataUrl" not in payload["metadata"]
    assert payload["metadata"]["screenshotSkippedReason"].startswith("image_base64_too_large")


def test_try_upload_battle_record_posts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_urlopen(request, timeout):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = request.data
        captured["authorization"] = request.headers.get("Authorization")
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("scripts.run_goal_benchmark.urllib.request.urlopen", fake_urlopen)

    ok = _try_upload_battle_record(
        {"upload_url": "https://board.example/api/records", "token": "secret"},
        {"sessionId": "demo", "participantType": "gaia"},
    )

    assert ok is True
    assert captured["url"] == "https://board.example/api/records"
    assert captured["method"] == "POST"
    assert captured["authorization"] == "Bearer secret"
    assert b'"sessionId": "demo"' in captured["body"]


def test_runner_id_prefers_explicit_value_and_sanitizes_label() -> None:
    assert sanitize_runner_id("맥미니 runner 01") == "맥미니-runner-01"
    assert resolve_runner_id("team-a/mac mini", {"GAIA_RUNNER_ID": "ignored"}) == "team-a-mac-mini"
    assert resolve_runner_id("", {"GAIA_RUNNER_ID": "minihost@desk"}) == "minihost@desk"


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
