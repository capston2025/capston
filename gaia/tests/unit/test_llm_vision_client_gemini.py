from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4 import llm_vision_client_gemini as gemini_module


def test_gemini_client_can_initialize_with_vertex_ai(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeGenaiClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(gemini_module.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(gemini_module, "_load_local_env_vars", lambda: {})
    for key in (
        "GEMINI_API_KEY",
        "GAIA_GEMINI_BACKEND",
        "GAIA_GEMINI_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project-test")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GAIA_LLM_MODEL", "gemini-3.5-flash")

    client = gemini_module.GeminiVisionClient()

    assert client.model == "gemini-3.5-flash"
    assert client.auth_backend == "vertex_ai"
    assert captured["vertexai"] is True
    assert captured["project"] == "project-test"
    assert captured["location"] == "global"
    assert captured["http_options"] == {"api_version": "v1"}
    assert "api_key" not in captured


def test_gemini_client_keeps_developer_api_path(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeGenaiClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(gemini_module.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(gemini_module, "_load_local_env_vars", lambda: {})
    for key in (
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GAIA_GEMINI_BACKEND",
        "GAIA_GEMINI_API_VERSION",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    monkeypatch.setenv("GAIA_LLM_MODEL", "gemini-2.5-flash")

    client = gemini_module.GeminiVisionClient()

    assert client.model == "gemini-2.5-flash"
    assert client.auth_backend == "developer_api"
    assert captured["api_key"] == "AIza-test"
    assert captured["http_options"] == {"api_version": "v1alpha"}
    assert "vertexai" not in captured


def test_gemini_text_call_sets_user_role(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="OK")

    class FakeGenaiClient:
        def __init__(self, **_kwargs) -> None:
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeGenaiClient)
    monkeypatch.setattr(gemini_module, "_load_local_env_vars", lambda: {})
    for key in (
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GAIA_GEMINI_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")

    client = gemini_module.GeminiVisionClient()
    result = client.analyze_text("hello", max_completion_tokens=8)

    assert result == "OK"
    content = captured["contents"][0]  # type: ignore[index]
    assert content.role == "user"
