from __future__ import annotations

import asyncio
import json

from gaia import chat_hub
from gaia.chat_hub import HubContext
from gaia.telegram_bridge import TelegramConfig, _TelegramBridge
from gaia.src.phase4.memory.store import MemoryStore


class _ReplyFailsMessage:
    async def reply_text(self, _text: str) -> None:
        raise TimeoutError("telegram timeout")


def test_safe_reply_text_swallows_timeout() -> None:
    bridge = _TelegramBridge(
        hub_context=HubContext(
            provider="gemini",
            model="gemini-2.5-pro",
            auth_strategy="reuse",
            url="https://example.com",
            runtime="terminal",
            control_channel="telegram",
        ),
        config=TelegramConfig(),
        memory_store=MemoryStore(enabled=False),
    )

    result = asyncio.run(bridge._safe_reply_text(_ReplyFailsMessage(), "queued #1: test"))

    assert result is False


def test_parse_human_answer_intervention_accepts_login_key_values() -> None:
    response = _TelegramBridge._parse_intervention_response(
        "human_answer",
        "username=student01 password=secret email=student@example.test",
        fields=["proceed", "manual_done", "username", "password", "instruction"],
    )

    assert response["action"] == "continue"
    assert response["proceed"] == "true"
    assert response["username"] == "student01"
    assert response["password"] == "secret"
    assert response["email"] == "student@example.test"


def test_parse_human_answer_intervention_maps_single_raw_value_to_requested_field() -> None:
    response = _TelegramBridge._parse_intervention_response(
        "human_answer",
        "123456",
        fields=["proceed", "manual_done", "otp", "instruction"],
    )

    assert response == {
        "action": "continue",
        "proceed": "true",
        "otp": "123456",
    }


def test_compose_intervention_message_uses_llm_for_requested_fields(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class _FakeClient:
        def analyze_text(self, prompt: str, max_completion_tokens: int, temperature: float):
            seen["prompt"] = prompt
            seen["max_completion_tokens"] = str(max_completion_tokens)
            seen["temperature"] = str(temperature)
            return json.dumps(
                {
                    "message": (
                        "지금 OTP 확인이 필요합니다.\n"
                        "답장 예시: otp=<otp>\n"
                        "취소하려면 /cancel"
                    )
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(chat_hub, "_get_chat_router_client", lambda: _FakeClient())

    message = _TelegramBridge._compose_intervention_message(
        {
            "kind": "human_answer",
            "question": "현재 화면의 OTP를 입력해 주세요.",
            "goal_name": "로그인",
            "goal_description": "OTP 인증 후 로그인한다.",
        },
        ["proceed", "manual_done", "otp", "instruction"],
    )

    assert "otp=<otp>" in message
    assert "/cancel" in message
    assert '"otp"' in seen["prompt"]
    assert "현재 화면의 OTP" in seen["prompt"]
    assert "한 번에 하나씩" in seen["prompt"]


def test_compose_intervention_message_falls_back_when_llm_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_TELEGRAM_LLM_INTERVENTION_MESSAGE", "0")

    message = _TelegramBridge._compose_intervention_message(
        {
            "kind": "auth",
            "question": "로그인 정보가 필요합니다.",
        },
        ["username", "password", "manual_done"],
    )

    assert "아이디를 먼저 보내주세요" in message
    assert "/cancel" in message
    assert "proceed=true" not in message
    assert "username=" not in message
    assert "manual_done" not in message


def test_fallback_human_answer_login_message_hides_internal_proceed() -> None:
    message = _TelegramBridge._fallback_intervention_message(
        "human_answer",
        "인천대 사이버캠퍼스 로그인이 필요해요.",
        ["proceed", "manual_done", "username", "password", "instruction"],
    )

    assert "인천대 사이버캠퍼스 로그인이 필요해요." in message
    assert "아이디를 먼저 보내주세요" in message
    assert "proceed=true" not in message
    assert "action=" not in message
    assert "username=" not in message
    assert "manual_done" not in message
    assert "instruction=" not in message


def test_login_credentials_are_collected_sequentially() -> None:
    assert _TelegramBridge._should_collect_login_credentials(
        "human_answer",
        ["proceed", "manual_done", "username", "password", "instruction"],
    )
    assert _TelegramBridge._sequential_login_field(["email", "password"]) == "email"
    assert _TelegramBridge._sequential_login_field(["username", "password"]) == "username"
    assert not _TelegramBridge._should_collect_login_credentials("clarification", ["username", "password"])
