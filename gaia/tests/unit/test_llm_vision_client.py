from __future__ import annotations

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
