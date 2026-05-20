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
    for key in (
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GAIA_GEMINI_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")

    token, source = gaia_auth.get_token_source("gemini")

    assert token == "AIza-reuse"
    assert source == f"envfile:{env_file}"


def test_get_token_source_accepts_gemini_vertex_env_file(tmp_path, monkeypatch) -> None:
    credentials = tmp_path / "vertex-service-account.json"
    credentials.write_text("{}", encoding="utf-8")
    env_file = tmp_path / ".env.gemini.local"
    env_file.write_text(
        "\n".join(
            [
                'GOOGLE_GENAI_USE_VERTEXAI="true"',
                'GOOGLE_CLOUD_PROJECT="project-test"',
                'GOOGLE_CLOUD_LOCATION="global"',
                f'GOOGLE_APPLICATION_CREDENTIALS="{credentials}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("GAIA_GEMINI_ENV_FILE", str(env_file))
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GAIA_GEMINI_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")

    token, source = gaia_auth.resolve_auth(provider="gemini", strategy="reuse")

    assert token == gaia_auth.GEMINI_VERTEX_TOKEN_SENTINEL
    assert source == f"vertex_ai:{env_file}"
    assert gaia_auth.os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert gaia_auth.os.environ["GOOGLE_CLOUD_PROJECT"] == "project-test"
    assert gaia_auth.os.environ["GOOGLE_CLOUD_LOCATION"] == "global"
    assert gaia_auth.os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(credentials)


def test_get_token_source_refreshes_expired_codex_oauth_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")
    monkeypatch.setattr(gaia_auth, "_now_ts", lambda: 1000)
    gaia_auth.AUTH_DIR.mkdir(parents=True)
    gaia_auth.AUTH_FILE.write_text(
        json.dumps(
            {
                "openai": {
                    "provider": "openai",
                    "token": "expired-token",
                    "source": "oauth_codex_cli",
                    "updated_at": "2026-05-18T00:00:00Z",
                    "metadata": {
                        "expires_at": 1001,
                        "refresh_token": "refresh-token",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        gaia_auth,
        "_post_oauth_token",
        lambda payload: {
            "access_token": "fresh-token",
            "expires_in": 3600,
            "refresh_token": "new-refresh-token",
            "token_type": "Bearer",
        },
    )

    token, source = gaia_auth.get_token_source("openai")

    assert token == "fresh-token"
    assert source == "oauth_codex_cli"
    saved = json.loads(gaia_auth.AUTH_FILE.read_text(encoding="utf-8"))
    assert saved["openai"]["token"] == "fresh-token"
    assert saved["openai"]["source"] == "oauth_codex_cli"
    assert saved["openai"]["metadata"]["refresh_token"] == "new-refresh-token"


def test_get_token_source_does_not_return_expired_codex_oauth_when_refresh_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")
    monkeypatch.setattr(gaia_auth, "_now_ts", lambda: 1000)
    gaia_auth.AUTH_DIR.mkdir(parents=True)
    gaia_auth.AUTH_FILE.write_text(
        json.dumps(
            {
                "openai": {
                    "provider": "openai",
                    "token": "expired-token",
                    "source": "oauth_codex_cli",
                    "updated_at": "2026-05-18T00:00:00Z",
                    "metadata": {
                        "expires_at": 1001,
                        "refresh_token": "refresh-token",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    def fail_refresh(_payload):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(gaia_auth, "_post_oauth_token", fail_refresh)

    token, source = gaia_auth.get_token_source("openai")

    assert token is None
    assert source is None


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
    for key in (
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GAIA_GEMINI_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
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


def test_resolve_auth_reuse_returns_local_ollama_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setattr(gaia_auth, "AUTH_DIR", tmp_path / "auth")
    monkeypatch.setattr(gaia_auth, "AUTH_FILE", tmp_path / "auth" / "profiles.json")

    token, source = gaia_auth.resolve_auth(provider="ollama", strategy="reuse")

    assert token == "ollama"
    assert source == "local:ollama"
    assert gaia_auth.os.environ["OLLAMA_API_KEY"] == "ollama"
