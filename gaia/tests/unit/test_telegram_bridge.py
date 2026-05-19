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


def test_login_prompt_fallback_is_contextual_and_hides_internal_fields(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_TELEGRAM_LLM_INTERVENTION_MESSAGE", "0")

    first = _TelegramBridge._compose_login_credentials_message(
        payload={
            "kind": "human_answer",
            "goal_description": "대중매체속바이오테크놀로지 강의 12주차의 첫번째 강의를 누르고 재생 확인",
        },
        question="로그인 정보가 필요합니다.",
        username_label="아이디",
        stage="username",
    )
    second = _TelegramBridge._compose_login_credentials_message(
        payload={"kind": "human_answer", "goal_description": "강의 재생 확인"},
        question="로그인 정보가 필요합니다.",
        username_label="아이디",
        stage="password",
    )

    assert "대중매체속바이오테크놀로지" in first
    assert "아이디만 먼저" in first
    assert "비밀번호는 다음 메시지" in first
    assert "username" not in first
    assert "proceed" not in first
    assert "/cancel" in first
    assert "비밀번호만" in second
    assert "아이디 받았어요" in second


def test_login_prompt_can_use_llm_for_more_chatbot_like_message(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class _FakeClient:
        def analyze_text(self, prompt: str, max_completion_tokens: int, temperature: float):
            seen["prompt"] = prompt
            seen["max_completion_tokens"] = str(max_completion_tokens)
            seen["temperature"] = str(temperature)
            return json.dumps(
                {
                    "message": (
                        "좋아요, 로그인만 도와주시면 제가 바로 이어서 확인할게요.\n"
                        "아이디만 먼저 보내주세요.\n"
                        "중단하려면 /cancel"
                    )
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(chat_hub, "_get_chat_router_client", lambda: _FakeClient())

    message = _TelegramBridge._compose_login_credentials_message(
        payload={"kind": "human_answer", "goal_description": "강의 12주차 첫 번째 영상 재생 확인"},
        question="로그인 정보가 필요합니다.",
        username_label="아이디",
        stage="username",
    )

    assert "아이디만 먼저" in message
    assert "stage" in seen["prompt"]
    assert "내부 필드명" in seen["prompt"]


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


def test_freeform_intent_fallback_defaults_to_goal_without_heuristics(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_TELEGRAM_LLM_MESSAGE_INTENT", "0")

    assert _TelegramBridge._classify_freeform_message_intent("보이루") == "goal"
    assert _TelegramBridge._classify_freeform_message_intent("로그인해줘") == "goal"
    assert _TelegramBridge._classify_freeform_message_intent("재생해줘") == "goal"


def test_freeform_intent_can_use_llm_to_avoid_false_goal_queue(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class _FakeClient:
        def analyze_text(self, prompt: str, max_completion_tokens: int, temperature: float):
            seen["prompt"] = prompt
            seen["max_completion_tokens"] = str(max_completion_tokens)
            seen["temperature"] = str(temperature)
            return json.dumps({"intent": "casual", "reason": "농담성 인사"}, ensure_ascii=False)

    monkeypatch.setattr(chat_hub, "_get_chat_router_client", lambda: _FakeClient())

    assert _TelegramBridge._classify_freeform_message_intent("부스에서 가볍게 인사 한번 해줘") == "casual"
    assert "run_gaia_goal" in seen["prompt"]


def test_freeform_intent_llm_keeps_search_goal_even_with_usage_word(monkeypatch) -> None:
    class _FakeClient:
        def analyze_text(self, prompt: str, max_completion_tokens: int, temperature: float):
            assert "네이버에서 사용법 검색해줘" in prompt
            return json.dumps(
                {
                    "intent": "goal",
                    "skill": "run_gaia_goal",
                    "confidence": 0.91,
                    "goal_text": "네이버에서 챗GPT 사용법 검색해줘",
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(chat_hub, "_get_chat_router_client", lambda: _FakeClient())

    assert _TelegramBridge._classify_freeform_message_intent("네이버에서 챗GPT 사용법 검색해줘") == "goal"


def test_help_request_is_answered_without_queue_language() -> None:
    bridge = _TelegramBridge(
        hub_context=HubContext(
            provider="openai",
            model="gpt-5.5",
            auth_strategy="reuse",
            url="https://example.com",
            runtime="terminal",
            control_channel="telegram",
        ),
        config=TelegramConfig(),
        memory_store=MemoryStore(enabled=False),
    )

    message = bridge._format_help_message(123)

    assert "테스트 목표를 한 문장" in message
    assert "현재 상태" in message
    assert "대기열" not in message
    assert "queued" not in message.lower()


def test_current_screen_request_is_routed_by_llm_to_status(monkeypatch) -> None:
    class _FakeClient:
        def analyze_text(self, prompt: str, max_completion_tokens: int, temperature: float):
            assert "현재 화면" in prompt
            return json.dumps(
                {"intent": "status", "skill": "show_status", "confidence": 0.95},
                ensure_ascii=False,
            )

    monkeypatch.setattr(chat_hub, "_get_chat_router_client", lambda: _FakeClient())

    assert _TelegramBridge._classify_freeform_message_intent("현재 화면") == "status"


def test_help_request_during_pending_input_explains_needed_field() -> None:
    context = HubContext(
        provider="openai",
        model="gpt-5.5",
        auth_strategy="reuse",
        url="https://example.com",
        runtime="terminal",
        control_channel="telegram",
        pending_user_input={
            "kind": "human_answer",
            "question": "사이버캠퍼스 로그인이 필요합니다.",
            "fields": ["proceed", "manual_done", "username", "password", "instruction"],
        },
    )
    bridge = _TelegramBridge(
        hub_context=context,
        config=TelegramConfig(),
        memory_store=MemoryStore(enabled=False),
    )

    message = bridge._format_help_message(123)

    assert "사용자 입력을 기다리는 중" in message
    assert "아이디" in message
    assert "password" not in message
    assert "proceed" not in message


def test_command_received_message_hides_internal_queue_number() -> None:
    message = _TelegramBridge._format_command_received_message(
        "12주차 첫 번째 강의를 재생 확인해줘",
        queued_ahead=0,
    )

    assert "실행해볼게요" in message
    assert "queue" not in message.lower()
    assert "대기열" not in message
    assert "#1" not in message
