from __future__ import annotations

import json

from gaia.src.phase4.llm_vision_client import LLMVisionClient, get_vision_client


def test_llm_vision_client_uses_ollama_openai_compatible_settings(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("gaia.src.phase4.llm_vision_client.openai.OpenAI", _FakeOpenAI)
    monkeypatch.setenv("GAIA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GAIA_LLM_MODEL", "gemma4:26b")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

    client = LLMVisionClient(provider="ollama")

    assert client.provider == "ollama"
    assert client.model == "gemma4:26b"
    assert captured["api_key"] == "ollama"
    assert captured["base_url"] == "http://127.0.0.1:11434/v1"


def test_get_vision_client_returns_openai_compatible_client_for_ollama(monkeypatch) -> None:
    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr("gaia.src.phase4.llm_vision_client.openai.OpenAI", _FakeOpenAI)
    monkeypatch.setenv("GAIA_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("GAIA_LLM_MODEL", "gemma4:26b")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")

    client = get_vision_client()

    assert isinstance(client, LLMVisionClient)
    assert client.provider == "ollama"


def test_openai_client_uses_codex_cli_auth_without_api_key(monkeypatch, tmp_path) -> None:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "redacted"}}),
        encoding="utf-8",
    )

    def fail_openai_init(**_kwargs):
        raise AssertionError("OpenAI client should not be initialized for Codex CLI auth")

    monkeypatch.setattr("gaia.src.phase4.llm_vision_client.Path.home", lambda: tmp_path)
    monkeypatch.setattr("gaia.src.phase4.llm_vision_client.shutil.which", lambda name: "/opt/homebrew/bin/codex" if name == "codex" else None)
    monkeypatch.setattr("gaia.src.phase4.llm_vision_client.openai.OpenAI", fail_openai_init)
    monkeypatch.setattr("gaia.src.phase4.llm_vision_client.LLMVisionClient._read_local_env_file_assignments", staticmethod(lambda: {}))
    monkeypatch.setenv("GAIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("GAIA_LLM_MODEL", "gpt-5.5")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)

    client = LLMVisionClient(provider="openai")

    assert client.provider == "openai"
    assert client._prefer_codex_cli is True
    assert client.client is None
