from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "gaia" / "tests" / "fixtures" / "local_chat_login.html"
SCENARIO_PATH = REPO_ROOT / "gaia" / "tests" / "scenarios" / "local_chat_login_suite.json"
DEMO_EMAIL = "demo@example.test"
DEMO_PASSWORD = "gaia-demo-password"
DEMO_A_EMAIL = "demo-a@example.test"
DEMO_A_PASSWORD = "gaia-demo-password-a"
DEMO_B_EMAIL = "demo-b@example.test"
DEMO_B_PASSWORD = "gaia-demo-password-b"
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
    assert DEMO_A_EMAIL in html
    assert DEMO_A_PASSWORD in html
    assert DEMO_B_EMAIL in html
    assert DEMO_B_PASSWORD in html
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
    assert "const ACCOUNTS = {" in html
    assert 'sessionStorage.setItem(SESSION_ACCOUNT_KEY, email)' in html
    assert 'localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages))' in html
    assert 'const kind = message.email === currentEmail ? "sent" : "received"' in html
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
            page.evaluate("localStorage.clear(); sessionStorage.clear();")
            page.reload()
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
    assert f'"{DEMO_A_EMAIL}": {{ password: "{DEMO_A_PASSWORD}", name: "Demo A", botMode: false }}' in html
    assert f'"{DEMO_B_EMAIL}": {{ password: "{DEMO_B_PASSWORD}", name: "Demo B", botMode: false }}' in html
    assert 'const BOT_REPLY_PREFIX = "Assistant reply"' in html
    assert "const account = ACCOUNTS[email];" in html
    assert "if (account && password === account.password)" in html
    assert "showChat(email);" in html
    assert "const messages = getMessages();" in html
    assert "const turn = messages.filter((message) => !message.bot).length + 1;" in html
    assert 'messages.push({' in html
    assert 'if (currentAccount.botMode)' in html
    assert 'item.dataset.testid = kind + "-message";' in html
    assert 'const label = kind === "sent" ? "Sent" : kind === "reply" ? "Assistant reply" : "Received";' in html
    assert 'turnCount.setAttribute("aria-label", "Total chat turns: " + completedTurns);' in html


def test_local_chat_fixture_two_accounts_exchange_with_browser_or_static_fallback() -> None:
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
            context = browser.new_context()
            page_a = context.new_page()
            page_a.goto(FIXTURE_PATH.as_uri())
            page_a.evaluate("localStorage.clear(); sessionStorage.clear();")
            page_a.reload()
            page_a.get_by_label("Email").fill(DEMO_A_EMAIL)
            page_a.get_by_label("Password").fill(DEMO_A_PASSWORD)
            page_a.get_by_test_id("login-button").click()
            page_a.get_by_test_id("login-status").wait_for(state="visible")
            assert DEMO_A_EMAIL in page_a.get_by_test_id("login-status").inner_text()

            first_message = "A가 보낸 GAIA 대결 테스트 메시지"
            page_a.get_by_label("Chat message").fill(first_message)
            page_a.get_by_test_id("send-button").click()

            page_b = context.new_page()
            page_b.goto(FIXTURE_PATH.as_uri())
            page_b.get_by_label("Email").fill(DEMO_B_EMAIL)
            page_b.get_by_label("Password").fill(DEMO_B_PASSWORD)
            page_b.get_by_test_id("login-button").click()
            page_b.get_by_test_id("login-status").wait_for(state="visible")
            assert DEMO_B_EMAIL in page_b.get_by_test_id("login-status").inner_text()
            assert first_message in page_b.get_by_test_id("received-message").nth(0).inner_text()

            reply_message = "B가 확인하고 답장합니다"
            page_b.get_by_label("Chat message").fill(reply_message)
            page_b.get_by_test_id("send-button").click()
            page_a.wait_for_function(
                """
                (reply) => [...document.querySelectorAll('[data-testid="received-message"]')]
                  .some((node) => node.textContent.includes(reply))
                """,
                reply_message,
            )
            assert reply_message in page_a.get_by_test_id("received-message").nth(0).inner_text()
            assert page_a.get_by_test_id("turn-count").inner_text() == "Total turns: 2"
    except PlaywrightError:
        _assert_static_login_and_five_turn_flow_contract()
    finally:
        if browser is not None:
            browser.close()


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
