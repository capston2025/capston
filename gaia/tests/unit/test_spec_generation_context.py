from __future__ import annotations

from gaia.src.gui.analysis_worker import AnalysisWorker
from gaia.src.phase1.agent_client import AgentServiceClient
from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.utils import models as gaia_models


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_agent_service_client_sends_base_url_in_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url, *, json, headers, timeout):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = dict(json)
        captured["headers"] = dict(headers)
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "success": True,
                "data": {
                    "output_text": '{"test_scenarios":[{"id":"TC_001","priority":"MUST","scenario":"홈 진입","steps":[],"assertion":{"description":"보임","selector":"","condition":"expectVisible","params":[]}}]}'
                },
            }
        )

    monkeypatch.setattr("gaia.src.phase1.agent_client.requests.post", fake_post)

    client = AgentServiceClient(base_url="http://localhost:3000")
    result = client.analyze_document(
        "명세 텍스트",
        feature_query="로그인",
        base_url="https://example.com/",
        timeout=123,
    )

    assert "test_scenarios" in result
    assert captured["url"] == "http://localhost:3000/api/analyze"
    assert captured["json"] == {
        "input_as_text": "명세 텍스트",
        "feature_query": "로그인",
        "base_url": "https://example.com/",
    }
    assert captured["timeout"] == (10, 123)


def test_spec_analyzer_passes_base_url_to_agent_client() -> None:
    captured: dict[str, str] = {}

    class _FakeClient:
        def analyze_document(self, text: str, feature_query: str = "", base_url: str = "", timeout: int = 1500):
            captured["text"] = text
            captured["feature_query"] = feature_query
            captured["base_url"] = base_url
            captured["timeout"] = str(timeout)
            return {
                "test_scenarios": [
                    {
                        "id": "SPEC_001",
                        "priority": "MUST",
                        "scenario": "메인 진입",
                        "steps": [],
                        "assertion": {
                            "description": "메인 화면이 보인다",
                            "selector": "",
                            "condition": "expectVisible",
                            "params": [],
                        },
                    }
                ]
            }

    analyzer = SpecAnalyzer(agent_client=_FakeClient())
    scenarios = analyzer.generate_from_spec(
        "서비스 기획서",
        feature_query="탭 이동",
        base_url="https://service.example/",
    )

    assert len(scenarios) == 1
    assert scenarios[0].id == "SPEC_001"
    assert captured["feature_query"] == "탭 이동"
    assert captured["base_url"] == "https://service.example/"


def test_analysis_worker_passes_base_url_to_analyzer_and_emits_progress() -> None:
    captured: dict[str, str] = {}
    progress: list[str] = []
    finished: list[object] = []

    class _FakeAnalyzer:
        def generate_from_spec(self, document_text: str, feature_query: str = "", base_url: str = ""):
            captured["document_text"] = document_text
            captured["feature_query"] = feature_query
            captured["base_url"] = base_url
            return [
                gaia_models.TestScenario(
                    id="GUI_001",
                    priority="MUST",
                    scenario="홈 확인",
                    steps=[gaia_models.TestStep(description="홈으로 이동", action="goto", selector="", params=["https://service.example/"])],
                    assertion=gaia_models.Assertion(
                        description="홈이 보인다",
                        selector="",
                        condition="expectVisible",
                        params=[],
                    ),
                )
            ]

    worker = AnalysisWorker(
        "기획서 원문",
        analyzer=_FakeAnalyzer(),
        feature_query="기업정보 탭",
        base_url="https://service.example/",
    )
    worker.progress.connect(progress.append)
    worker.finished.connect(finished.append)

    worker.run()

    assert captured["feature_query"] == "기업정보 탭"
    assert captured["base_url"] == "https://service.example/"
    assert any("참조 사이트 링크" in line for line in progress)
    assert finished
