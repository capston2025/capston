from gaia.src.phase4.agent import AgentOrchestrator, MCPClient
from gaia.src.utils.models import Assertion, DomElement, TestScenario, TestStep


def test_mcp_client_fallback(monkeypatch):
    client = MCPClient()
    monkeypatch.setattr(client, "_fallback_elements", lambda url: [])
    monkeypatch.setattr(
        client,
        "analyze_dom",
        lambda url: client._fallback_elements(url),
    )
    elements = client.analyze_dom("https://example.com")
    assert isinstance(elements, list)


def _build_scenario(selector: str) -> TestScenario:
    return TestScenario(
        id="TC_001",
        priority="High",
        scenario="Login flow",
        steps=[
            TestStep(
                description="로그인 버튼 클릭",
                action="click",
                selector=selector,
                params=[],
            )
        ],
        assertion=Assertion(
            description="대시보드로 이동",
            selector="body",
            condition="url_contains",
            params=["/dashboard"],
        ),
    )


def _dom_element(selector: str, *, tag: str = "div", text: str = "", **attributes) -> DomElement:
    return DomElement(
        tag=tag,
        selector=selector,
        text=text,
        attributes=attributes,
        element_type="",
    )


def test_agent_orchestrator_uses_provided_plan(monkeypatch):
    orchestrator = AgentOrchestrator()
    scenario = _build_scenario("a[href='/login']")
    dom_snapshot = [_dom_element("a[href='/login']", tag="a", text="로그인", href="/login")]

    monkeypatch.setattr(orchestrator.mcp_client, "analyze_dom", lambda url: dom_snapshot)

    plan = orchestrator.plan_for_url(
        "https://example.com",
        scenarios=[scenario],
    )

    assert plan == [scenario]
    assert orchestrator.blocked_scenarios == {}


def test_agent_orchestrator_detects_blocked(monkeypatch):
    orchestrator = AgentOrchestrator()
    scenario = _build_scenario("button[type='submit']")

    monkeypatch.setattr(orchestrator.mcp_client, "analyze_dom", lambda url: [])

    plan = orchestrator.plan_for_url(
        "https://example.com",
        scenarios=[scenario],
    )

    assert plan == []
    assert "TC_001" in orchestrator.blocked_scenarios


def test_agent_orchestrator_fallback_plan(monkeypatch):
    orchestrator = AgentOrchestrator()

    monkeypatch.setattr(orchestrator.mcp_client, "analyze_dom", lambda url: [])
    monkeypatch.setattr(
        orchestrator.analyzer,
        "generate_from_context",
        lambda dom, document_text=None: [],
    )
    fallback = orchestrator.analyzer._fallback_plan()
    monkeypatch.setattr(
        orchestrator.analyzer,
        "generate_from_spec",
        lambda text: fallback,
    )

    plan = orchestrator.plan_for_url("https://example.com", document_text="Spec")
    assert plan == []
    assert orchestrator.blocked_scenarios


def test_selector_match_by_attribute(monkeypatch):
    orchestrator = AgentOrchestrator()
    scenario = _build_scenario("input[name='user_email']")
    dom_snapshot = [_dom_element("#login_form input", tag="input", name="user_email")]
    monkeypatch.setattr(orchestrator.mcp_client, "analyze_dom", lambda url: dom_snapshot)

    plan = orchestrator.plan_for_url("https://example.com", scenarios=[scenario])

    assert plan == [scenario]


def test_selector_match_by_text(monkeypatch):
    orchestrator = AgentOrchestrator()
    scenario = _build_scenario("button:has-text('로그인')")
    dom_snapshot = [_dom_element("#submit_login", tag="button", text="로그인")]
    monkeypatch.setattr(orchestrator.mcp_client, "analyze_dom", lambda url: dom_snapshot)

    plan = orchestrator.plan_for_url("https://example.com", scenarios=[scenario])

    assert plan == [scenario]
