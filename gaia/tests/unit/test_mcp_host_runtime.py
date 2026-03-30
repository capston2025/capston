from __future__ import annotations

from gaia.src.phase4.mcp_host_runtime import should_auto_start_mcp_host


def test_should_auto_start_mcp_host_defaults_to_false_for_openclaw(monkeypatch) -> None:
    monkeypatch.delenv("GAIA_BROWSER_BACKEND", raising=False)
    monkeypatch.delenv("GAIA_OPENCLAW_BASE_URL", raising=False)

    assert should_auto_start_mcp_host() is False


def test_should_auto_start_mcp_host_stays_false_for_explicit_openclaw(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "openclaw")

    assert should_auto_start_mcp_host() is False


def test_should_auto_start_mcp_host_allows_explicit_gaia(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_BROWSER_BACKEND", "gaia")

    assert should_auto_start_mcp_host() is True
