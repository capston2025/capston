from __future__ import annotations

import signal

from gaia.src.phase4 import embedded_openclaw_runtime as runtime


def test_build_embedded_openclaw_config_defaults_to_local_unauthenticated_browser() -> None:
    config = runtime.build_embedded_openclaw_config(
        gateway_port=18789,
        cdp_port=18800,
        browser_executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    assert config["gateway"]["mode"] == "local"
    assert config["gateway"]["auth"]["mode"] == "none"
    assert config["browser"]["enabled"] is True
    assert config["browser"]["headless"] is True
    assert config["browser"]["defaultProfile"] == "openclaw"
    assert config["browser"]["profiles"]["openclaw"]["cdpPort"] == 18800
    assert config["browser"]["executablePath"].endswith("Google Chrome")


def test_build_embedded_openclaw_config_respects_visible_override(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_OPENCLAW_HEADLESS", "0")

    config = runtime.build_embedded_openclaw_config(
        gateway_port=18789,
        cdp_port=18800,
        browser_executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    assert config["browser"]["headless"] is False


def test_detect_browser_executable_prefers_env_override(monkeypatch, tmp_path) -> None:
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    monkeypatch.setenv("GAIA_OPENCLAW_BROWSER_EXECUTABLE", str(browser))

    assert runtime.detect_browser_executable() == str(browser)


def test_probe_existing_browser_server_returns_ready_control_port(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_PORT_CANDIDATES", ((18789, 18791, 18800), (19001, 19003, 19012)))
    monkeypatch.setattr(
        runtime,
        "_browser_server_ready",
        lambda base_url: base_url == "http://127.0.0.1:18791",
    )

    assert runtime._probe_existing_browser_server() == ("http://127.0.0.1:18791", 18789, 18791, 18800)


def test_cleanup_stale_browser_profile_removes_singleton_lock(monkeypatch, tmp_path) -> None:
    state_dir = tmp_path / "state"
    user_data_dir = state_dir / "browser" / "openclaw" / "user-data"
    user_data_dir.mkdir(parents=True)
    singleton_lock = user_data_dir / "SingletonLock"
    singleton_lock.write_text("", encoding="utf-8")
    calls: list[tuple[int, int]] = []

    monkeypatch.setattr(runtime, "_state_dir", lambda: state_dir)
    monkeypatch.setattr(runtime.subprocess, "check_output", lambda *args, **kwargs: "111\n")

    def _fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:
            return

    monkeypatch.setattr(runtime.os, "kill", _fake_kill)

    runtime._cleanup_stale_browser_profile()

    assert (111, signal.SIGTERM) in calls
    assert not singleton_lock.exists()
