from scripts.run_goal_benchmark import (
    _build_child_code,
    _infer_provider_from_model,
    _prepare_scenario_env,
    _resolve_codex_exec_timeout,
    _resolve_scenario_timeout_budget,
    _should_emit_live_trace_line,
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


def test_infer_provider_from_model_handles_openai_and_gemini() -> None:
    assert _infer_provider_from_model("gpt-5.4") == "openai"
    assert _infer_provider_from_model("gpt-5.3-codex") == "openai"
    assert _infer_provider_from_model("gemini-2.5-pro") == "gemini"
    assert _infer_provider_from_model("unknown-model") == ""


def test_should_emit_live_trace_line_filters_to_step_level_messages() -> None:
    assert _should_emit_live_trace_line("🎯 목표 시작: 테스트")
    assert _should_emit_live_trace_line("--- Step 2/40 ---")
    assert _should_emit_live_trace_line("LLM 결정: click - 버튼을 누른다")
    assert _should_emit_live_trace_line("✅ 목표 달성! 이유: 확인됨")
    assert not _should_emit_live_trace_line("🧪 llm trace: {'used_llm': True}")
    assert not _should_emit_live_trace_line('{"schema_version":"gaia.benchmark.v1"}')
