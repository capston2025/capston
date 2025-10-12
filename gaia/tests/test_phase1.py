from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.pdf_loader import PDFLoader


def test_spec_analyzer_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GAIA_WORKFLOW_ID", raising=False)
    analyzer = SpecAnalyzer()
    plan = analyzer.generate_from_spec("Login feature description")
    assert plan
    assert plan[0].id.startswith("TC_")


def test_spec_analyzer_uses_agent_payload():
    class StubRunner:
        def run(self, document_text: str):  # pragma: no cover - simple stub
            return {
                "checklist": [
                    {
                        "id": "TC010",
                        "name": "로그인 시도",
                        "priority": "MUST",
                        "steps": ["페이지 접속", "자격 증명 입력", "로그인 클릭"],
                        "expected_result": "대시보드 진입",
                    }
                ],
                "summary": {"total": 1, "must": 1, "should": 0, "may": 0},
            }

    analyzer = SpecAnalyzer(agent_runner=StubRunner())
    scenarios = analyzer.generate_from_spec("임의 문서")

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.id == "TC010"
    assert scenario.priority == "High"
    assert len(scenario.steps) == 3


def test_pdf_loader_requires_existing_file(tmp_path):
    loader = PDFLoader()
    pdf_path = tmp_path / "missing.pdf"
    try:
        loader.extract(pdf_path)
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - guard
        raise AssertionError("Expected FileNotFoundError")
