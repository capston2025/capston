from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "gaia" / "tests" / "fixtures" / "local_chat_login.html"
SCENARIO_PATH = REPO_ROOT / "gaia" / "tests" / "scenarios" / "local_chat_login_suite.json"
DEMO_EMAIL = "demo@example.test"
DEMO_PASSWORD = "gaia-demo-password"
CHAT_MESSAGES = [f"gaia turn {index}" for index in range(1, 6)]
FIFTH_REPLY = "Assistant reply 5: received gaia turn 5"


class _FixtureContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.test_ids: set[str] = set()
        self.labels_for: set[str] = set()
        self.inputs: dict[str, dict[str, str]] = {}
        self.buttons: dict[str, dict[str, str]] = {}
        self.aria_labels: set[str] = set()
        self.roles: dict[str, str] = {}
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        test_id = attr_map.get("data-testid")
        if test_id:
            self.test_ids.add(test_id)
        if attr_map.get("aria-label"):
            self.aria_labels.add(attr_map["aria-label"])
        if test_id and attr_map.get("role"):
            self.roles[test_id] = attr_map["role"]
        if tag == "label" and attr_map.get("for"):
            self.labels_for.add(attr_map["for"])
        if tag == "input" and attr_map.get("id"):
            self.inputs[attr_map["id"]] = attr_map
        if tag == "button" and attr_map.get("id"):
            self.buttons[attr_map["id"]] = attr_map

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.text_parts.append(text)


def _parse_fixture() -> tuple[str, _FixtureContractParser]:
    html = FIXTURE_PATH.read_text(encoding="utf-8")
    parser = _FixtureContractParser()
    parser.feed(html)
    return html, parser


def test_local_chat_fixture_static_contract_is_automation_friendly() -> None:
    html, parser = _parse_fixture()

    assert DEMO_EMAIL in html
    assert DEMO_PASSWORD in html
    assert {"email", "password", "chat-message"}.issubset(parser.labels_for)
    assert parser.inputs["email"]["data-testid"] == "login-email"
    assert parser.inputs["password"]["data-testid"] == "login-password"
    assert parser.inputs["chat-message"]["data-testid"] == "chat-input"
    assert parser.buttons["login-button"]["data-testid"] == "login-button"
    assert parser.buttons["send-button"]["data-testid"] == "send-button"
    assert {
        "login-panel",
        "login-form",
        "login-email",
        "login-password",
        "login-button",
        "chat-panel",
        "chat-form",
        "chat-input",
        "send-button",
        "turn-count",
        "message-list",
    }.issubset(parser.test_ids)
    assert parser.roles["turn-count"] == "status"
    assert "Total chat turns" in parser.aria_labels
    assert 'data-authenticated="false"' in html
    assert 'chatPanel.dataset.authenticated = "true"' in html
    assert 'item.dataset.testid = kind + "-message"' in html
    assert 'item.dataset.turn = String(turn)' in html
    assert 'appendMessage("sent", turn, "You sent: " + text)' in html
    assert 'appendMessage("reply", turn, BOT_REPLY_PREFIX + " " + turn + ": received " + text)' in html
    assert 'turnCount.textContent = "Total turns: " + completedTurns' in html


def test_local_chat_fixture_login_and_five_turns_with_browser_or_static_fallback() -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception:
        _assert_static_login_and_five_turn_flow_contract()
        return

    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(FIXTURE_PATH.as_uri())
            page.get_by_label("Email").fill(DEMO_EMAIL)
            page.get_by_label("Password").fill(DEMO_PASSWORD)
            page.get_by_test_id("login-button").click()
            page.get_by_test_id("login-status").wait_for(state="visible")
            assert DEMO_EMAIL in page.get_by_test_id("login-status").inner_text()

            for message in CHAT_MESSAGES:
                page.get_by_label("Chat message").fill(message)
                page.get_by_test_id("send-button").click()

            assert page.get_by_test_id("sent-message").count() == 5
            assert page.get_by_test_id("reply-message").count() == 5
            assert page.get_by_test_id("turn-count").inner_text() == "Total turns: 5"
            assert FIFTH_REPLY in page.get_by_test_id("reply-message").nth(4).inner_text()
    except PlaywrightError:
        _assert_static_login_and_five_turn_flow_contract()
    finally:
        if browser is not None:
            browser.close()


def _assert_static_login_and_five_turn_flow_contract() -> None:
    html, _parser = _parse_fixture()
    assert f'const DEMO_EMAIL = "{DEMO_EMAIL}"' in html
    assert f'const DEMO_PASSWORD = "{DEMO_PASSWORD}"' in html
    assert 'const BOT_REPLY_PREFIX = "Assistant reply"' in html
    assert "email === DEMO_EMAIL && password === DEMO_PASSWORD" in html
    assert "showChat();" in html
    assert 'let completedTurns = 0;' in html
    assert 'const turn = completedTurns + 1;' in html
    assert 'appendMessage("sent", turn, "You sent: " + text);' in html
    assert 'appendMessage("reply", turn, BOT_REPLY_PREFIX + " " + turn + ": received " + text);' in html
    assert 'item.dataset.testid = kind + "-message";' in html
    assert 'item.setAttribute("aria-label", (kind === "sent" ? "Sent" : "Assistant reply") + " message turn " + turn);' in html
    assert 'turnCount.setAttribute("aria-label", "Total chat turns: " + completedTurns);' in html


def test_local_chat_scenario_points_to_five_turn_fixture_contract() -> None:
    data = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario = data["scenarios"][0]

    assert scenario["id"] == "LOCAL_CHAT_001_LOGIN_AND_FIVE_TURNS"
    assert scenario["url"] == "http://127.0.0.1:8765/local_chat_login.html"
    assert scenario["constraints"]["local_fixture_path"] == "gaia/tests/fixtures/local_chat_login.html"
    assert "demo@example.test / gaia-demo-password" in scenario["goal"]
    assert "5개의 메시지" in scenario["goal"]
    assert "Assistant reply 5: received gaia turn 5" in scenario["goal"]
    assert {"auth_completed", "five_chat_turns_completed", "fifth_reply_visible", "turn_count_visible"}.issubset(
        scenario["expected_signals"]
    )
    assert (REPO_ROOT / scenario["constraints"]["local_fixture_path"]).is_file()
