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
    assert config["browser"]["headless"] is False
    assert config["browser"]["defaultProfile"] == "openclaw"
    assert config["browser"]["profiles"]["openclaw"]["cdpPort"] == 18800
    assert config["browser"]["executablePath"].endswith("Google Chrome")


def test_build_embedded_openclaw_config_respects_headless_override(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_OPENCLAW_HEADLESS", "1")

    config = runtime.build_embedded_openclaw_config(
        gateway_port=18789,
        cdp_port=18800,
        browser_executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )

    assert config["browser"]["headless"] is True


def test_detect_browser_executable_prefers_env_override(monkeypatch, tmp_path) -> None:
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")
    monkeypatch.setenv("GAIA_OPENCLAW_BROWSER_EXECUTABLE", str(browser))

    assert runtime.detect_browser_executable() == str(browser)


def test_detect_browser_executable_prefers_playwright_chromium(monkeypatch, tmp_path) -> None:
    cache_dir = tmp_path / "ms-playwright"
    old_browser = cache_dir / "chromium-1187" / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS"
    old_browser.mkdir(parents=True)
    (old_browser / "Chromium").write_text("", encoding="utf-8")
    new_browser = cache_dir / "chromium-1208" / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS"
    new_browser.mkdir(parents=True)
    (new_browser / "Chromium").write_text("", encoding="utf-8")

    monkeypatch.delenv("GAIA_OPENCLAW_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.setattr(runtime, "_PLAYWRIGHT_CACHE_DIR_CANDIDATES", (cache_dir,))
    monkeypatch.setattr(
        runtime,
        "_CHROME_EXECUTABLE_CANDIDATES",
        ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
    )

    assert runtime.detect_browser_executable() == str(new_browser / "Chromium")


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


def test_bootstrap_env_sets_openclaw_config_dir(monkeypatch, tmp_path) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setattr(runtime, "_state_dir", lambda: state_dir)

    env = runtime._bootstrap_env(gateway_port=18789, config_path=tmp_path / "openclaw.json")

    assert env["OPENCLAW_CONFIG_DIR"] == str(state_dir)
    assert env["OPENCLAW_STATE_DIR"] == str(state_dir)
    assert env["OPENCLAW_BUNDLED_PLUGINS_DIR"] == str(runtime.vendor_root() / "extensions")


def test_ensure_browser_profile_started_raises_on_error(monkeypatch) -> None:
    class _Response:
        status_code = 500
        text = 'Error: Failed to start Chrome CDP on port 18800 for profile "openclaw".'

        def json(self) -> dict[str, str]:
            return {"error": self.text}

    monkeypatch.setattr(runtime.requests, "post", lambda *args, **kwargs: _Response())

    try:
        runtime._ensure_browser_profile_started("http://127.0.0.1:18791")
    except RuntimeError as exc:
        assert "Failed to start Chrome CDP" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when /start returns failure")


def test_ensure_embedded_openclaw_base_url_requires_profile_start(monkeypatch, tmp_path) -> None:
    base_url = "http://127.0.0.1:18791"
    calls: list[str] = []

    monkeypatch.setattr(runtime, "_browser_server_ready", lambda candidate: candidate == base_url)
    monkeypatch.setattr(runtime, "_probe_existing_browser_server", lambda: (base_url, 18789, 18791, 18800))

    def _fake_start(candidate: str) -> None:
        calls.append(candidate)
        raise RuntimeError('Failed to start Chrome CDP on port 18800 for profile "openclaw".')

    monkeypatch.setattr(runtime, "_ensure_browser_profile_started", _fake_start)
    runtime.stop_embedded_openclaw_server()

    try:
        runtime.ensure_embedded_openclaw_base_url()
    except RuntimeError as exc:
        assert "Failed to start Chrome CDP" in str(exc)
    else:
        raise AssertionError("expected ensure_embedded_openclaw_base_url to propagate /start failure")

    assert calls == [base_url]
