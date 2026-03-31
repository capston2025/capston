from __future__ import annotations

import json
from pathlib import Path

from gaia import auth as gaia_auth


class _DummyTTY:
    def isatty(self) -> bool:
        return True


def test_get_token_source_reads_gemini_env_file(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env.gemini.local"
    env_file.write_text('GEMINI_API_KEY="AIza-reuse"\n', encoding="utf-8")

    monkeypatch.setenv("GAIA_GEMINI_ENV_FILE", str(env_file))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")

    token, source = gaia_auth.get_token_source("gemini")

    assert token == "AIza-reuse"
    assert source == f"envfile:{env_file}"


def test_interactive_login_gemini_writes_env_file_and_profile(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env.gemini.local"
    auth_dir = tmp_path / "auth"
    auth_file = auth_dir / "profiles.json"

    monkeypatch.setenv("GAIA_GEMINI_ENV_FILE", str(env_file))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", auth_dir)
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", auth_file)
    monkeypatch.setattr(gaia_auth.sys, "stdin", _DummyTTY())
    monkeypatch.setattr(gaia_auth.getpass, "getpass", lambda _: "AIza-fresh")

    token = gaia_auth.interactive_login(
        provider="gemini",
        open_browser=False,
        use_oauth=False,
    )

    assert token == "AIza-fresh"
    assert env_file.exists()
    env_text = env_file.read_text(encoding="utf-8")
    assert 'GEMINI_API_KEY="AIza-fresh"' in env_text
    assert 'GAIA_LLM_PROVIDER="gemini"' in env_text
    assert auth_file.exists()
    profiles = json.loads(auth_file.read_text(encoding="utf-8"))
    assert profiles["gemini"]["token"] == "AIza-fresh"
    assert profiles["gemini"]["source"] == "manual"


def test_resolve_auth_reuse_uses_gemini_env_file_and_exports_env(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env.gemini.local"
    env_file.write_text('GEMINI_API_KEY="AIza-existing"\n', encoding="utf-8")

    monkeypatch.setenv("GAIA_GEMINI_ENV_FILE", str(env_file))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")

    token, source = gaia_auth.resolve_auth(provider="gemini", strategy="reuse")

    assert token == "AIza-existing"
    assert source == f"envfile:{env_file}"
    assert gaia_auth.os.environ["GEMINI_API_KEY"] == "AIza-existing"


def test_interactive_login_gemini_with_explicit_token_still_updates_env_file(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env.gemini.local"
    monkeypatch.setenv("GAIA_GEMINI_ENV_FILE", str(env_file))
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")

    token = gaia_auth.interactive_login(provider="gemini", token="AIza-direct")

    assert token == "AIza-direct"
    assert env_file.exists()
    assert 'GEMINI_API_KEY="AIza-direct"' in env_file.read_text(encoding="utf-8")
