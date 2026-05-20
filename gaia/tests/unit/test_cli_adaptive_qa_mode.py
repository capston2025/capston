from __future__ import annotations

from gaia.cli import (
    DEEP_ADAPTIVE_QA_MODE,
    TERMINAL_DEEP_QA_BENCHMARK_PURPOSE_LABEL,
    TERMINAL_PURPOSE_CHOICES,
    QUICK_DEEP_QA_LABEL,
    QUICK_RUN_MODE_CHOICES,
    _dispatch_chat,
    _resolve_terminal_launch_purpose,
    run_launcher,
)


class _TTY:
    def isatty(self) -> bool:
        return True


def _stub_configured_terminal() -> tuple[str, str, str, str, str, str, str, bool]:
    return (
        "openai",
        "gpt-5.5",
        "reuse",
        "https://example.com",
        "terminal",
        "workspace",
        "session-1",
        False,
    )


def test_launcher_interactive_menu_can_select_deep_qa(monkeypatch) -> None:
    profile: dict[str, str] = {}
    captured: dict[str, object] = {}

    monkeypatch.setattr("gaia.cli._configure_session", lambda parsed, require_url: _stub_configured_terminal())
    monkeypatch.setattr("gaia.cli.load_session_state", lambda session_key: None)
    monkeypatch.setattr("gaia.cli._load_profile", lambda: profile)
    monkeypatch.setattr("gaia.cli._resolve_terminal_launch_purpose", lambda *args, **kwargs: "actual")
    monkeypatch.setattr("gaia.cli._resolve_url", lambda *args, **kwargs: "https://example.com")
    monkeypatch.setattr("gaia.cli._persist_session_state", lambda **kwargs: None)
    monkeypatch.setattr("gaia.cli._persist_profile", lambda profile, **kwargs: None)
    monkeypatch.setattr("gaia.cli._resolve_control_channel", lambda *args, **kwargs: "local")
    monkeypatch.setattr("gaia.cli.sys.stdin", _TTY())

    def fake_select(prompt: str, options, default=None):
        captured["prompt"] = prompt
        captured["options"] = tuple(options)
        captured["default"] = default
        return QUICK_DEEP_QA_LABEL

    def fake_dispatch_chat(runtime, url, feature_query, repl, *, session_id, qa_mode=None):
        captured["runtime"] = runtime
        captured["url"] = url
        captured["feature_query"] = feature_query
        captured["repl"] = repl
        captured["session_id"] = session_id
        captured["qa_mode"] = qa_mode
        return 0

    monkeypatch.setattr("gaia.cli._prompt_select", fake_select)
    monkeypatch.setattr("gaia.cli._prompt_non_empty", lambda prompt: "네이버 쇼핑에서 배송 필터 검증")
    monkeypatch.setattr("gaia.cli._dispatch_chat", fake_dispatch_chat)

    assert run_launcher(["--terminal"]) == 0

    assert captured["prompt"] == "실행 방식을 선택하세요"
    assert captured["options"] == QUICK_RUN_MODE_CHOICES
    assert captured["qa_mode"] == DEEP_ADAPTIVE_QA_MODE
    assert captured["feature_query"] == "네이버 쇼핑에서 배송 필터 검증"
    assert captured["repl"] is False
    assert profile["last_quick_mode"] == DEEP_ADAPTIVE_QA_MODE


def test_terminal_purpose_menu_can_select_deep_qa_benchmark(monkeypatch) -> None:
    profile: dict[str, str] = {}
    captured: dict[str, object] = {}

    monkeypatch.setattr("gaia.cli.sys.stdin", _TTY())
    monkeypatch.setattr("gaia.cli._save_profile", lambda payload: captured.setdefault("profile", dict(payload)))

    def fake_select(prompt: str, options, default=None):
        captured["prompt"] = prompt
        captured["options"] = tuple(options)
        captured["default"] = default
        return TERMINAL_DEEP_QA_BENCHMARK_PURPOSE_LABEL

    monkeypatch.setattr("gaia.cli._prompt_select", fake_select)

    selected = _resolve_terminal_launch_purpose(object(), profile, runtime="terminal")

    assert selected == "deep_qa_benchmark"
    assert captured["prompt"] == "테스트 용도 인가요?"
    assert captured["options"] == TERMINAL_PURPOSE_CHOICES
    assert profile["last_terminal_purpose"] == "deep_qa_benchmark"


def test_launcher_routes_deep_qa_benchmark_to_benchmark_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr("gaia.cli._configure_session", lambda parsed, require_url: _stub_configured_terminal())
    monkeypatch.setattr("gaia.cli.load_session_state", lambda session_key: None)
    monkeypatch.setattr("gaia.cli._load_profile", lambda: {})
    monkeypatch.setattr("gaia.cli._resolve_terminal_launch_purpose", lambda *args, **kwargs: "deep_qa_benchmark")

    def fake_run_terminal_deep_qa_benchmark_mode(*, workspace_root, push_metrics=False, qa_mode=None, dedicated_deep_qa=False):
        captured["workspace_root"] = workspace_root
        captured["push_metrics"] = push_metrics
        captured["qa_mode"] = qa_mode
        captured["dedicated_deep_qa"] = dedicated_deep_qa
        return 0

    monkeypatch.setattr("gaia.cli._run_terminal_benchmark_mode", fake_run_terminal_deep_qa_benchmark_mode)

    assert run_launcher(["--terminal"]) == 0

    assert captured["qa_mode"] == DEEP_ADAPTIVE_QA_MODE
    assert captured["dedicated_deep_qa"] is True
    assert captured["push_metrics"] is False


def test_dispatch_chat_terminal_applies_deep_qa_env_for_run(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("GAIA_ADAPTIVE_QA", "old-adaptive")
    monkeypatch.delenv("GAIA_DEEP_ADAPTIVE_QA", raising=False)

    def fake_terminal_runner(*, url, initial_query, repl, session_id):
        import os

        captured["url"] = url
        captured["initial_query"] = initial_query
        captured["repl"] = repl
        captured["session_id"] = session_id
        captured["adaptive_env"] = os.environ.get("GAIA_ADAPTIVE_QA")
        captured["deep_env"] = os.environ.get("GAIA_DEEP_ADAPTIVE_QA")
        return 17

    monkeypatch.setattr("gaia.terminal.run_chat_terminal", fake_terminal_runner)

    result = _dispatch_chat(
        "terminal",
        "https://example.com",
        "목표 실행",
        False,
        session_id="session-1",
        qa_mode=DEEP_ADAPTIVE_QA_MODE,
    )

    assert result == 17
    assert captured["adaptive_env"] is None
    assert captured["deep_env"] == "1"
    import os

    assert os.environ.get("GAIA_ADAPTIVE_QA") == "old-adaptive"
    assert os.environ.get("GAIA_DEEP_ADAPTIVE_QA") is None


def test_dispatch_chat_gui_forwards_deep_qa_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_gui(argv):
        captured["argv"] = list(argv)
        return 0

    monkeypatch.setattr("gaia.cli.run_gui", fake_run_gui)

    assert (
        _dispatch_chat(
            "gui",
            "https://example.com",
            "목표 실행",
            False,
            session_id="session-1",
            qa_mode=DEEP_ADAPTIVE_QA_MODE,
        )
        == 0
    )

    assert captured["argv"] == [
        "--mode",
        DEEP_ADAPTIVE_QA_MODE,
        "--url",
        "https://example.com",
        "--feature-query",
        "목표 실행",
    ]
