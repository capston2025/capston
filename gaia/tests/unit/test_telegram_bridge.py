from __future__ import annotations

import asyncio
import json

from gaia import chat_hub
from gaia.chat_hub import CommandResult, HubContext, build_command_payload
from gaia.telegram_bridge import TelegramConfig, _ActiveRun, _TelegramBridge
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


def test_freeform_intent_fallback_asks_for_clarification_when_chatbot_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("GAIA_TELEGRAM_LLM_MESSAGE_INTENT", "0")

    assert _TelegramBridge._classify_freeform_message_intent("보이루") == "clarify"

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

    route = bridge._route_telegram_message("암뇽", chat_id=123)

    assert route["intent"] == "clarify"
    assert route["skill"] == "ask_clarification"


def test_telegram_route_uses_context_provider_model_chatbot(monkeypatch) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setenv("GAIA_LLM_PROVIDER", "openai")
    monkeypatch.setenv("GAIA_LLM_MODEL", "gpt-5.5")

    class _FakeGeminiClient:
        def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
            seen["provider_at_create"] = "gemini"
            seen["model_at_create"] = model or ""

        def analyze_text(self, prompt: str, max_completion_tokens: int, temperature: float):
            seen["prompt"] = prompt
            seen["provider_during_call"] = __import__("os").environ.get("GAIA_LLM_PROVIDER", "")
            seen["model_during_call"] = __import__("os").environ.get("GAIA_LLM_MODEL", "")
            return json.dumps(
                {
                    "intent": "casual",
                    "skill": "casual_chat",
                    "confidence": 0.94,
                    "reply": "안녕! 테스트 목표를 보내주면 바로 준비할게요.",
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr("gaia.src.phase4.llm_vision_client_gemini.GeminiVisionClient", _FakeGeminiClient)

    bridge = _TelegramBridge(
        hub_context=HubContext(
            provider="gemini",
            model="gemini-3.5-flash",
            auth_strategy="reuse",
            url="https://cyber.inu.ac.kr/login.php",
            runtime="terminal",
            control_channel="telegram",
            qa_mode="deep_adaptive_qa",
        ),
        config=TelegramConfig(),
        memory_store=MemoryStore(enabled=False),
    )

    route = bridge._route_telegram_message("암뇽", chat_id=123)

    assert route["intent"] == "casual"
    assert route["reply"].startswith("안녕")
    assert seen["provider_at_create"] == "gemini"
    assert seen["model_at_create"] == "gemini-3.5-flash"
    assert seen["provider_during_call"] == "openai"
    assert seen["model_during_call"] == "gpt-5.5"
    assert "https://cyber.inu.ac.kr/login.php" in seen["prompt"]
    assert "deep_adaptive_qa" in seen["prompt"]


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


def test_tracking_command_enables_and_live_status_includes_progress() -> None:
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

    reply = bridge._handle_tracking_command("/상태 추적", 123)

    assert reply is not None
    assert "상태 추적을 켰어요" in reply
    assert bridge._tracking_enabled_for(123)

    with bridge._state_lock:
        bridge._active_runs[123] = bridge._active_runs.get(123) or _ActiveRun(
            chat_id=123,
            raw_command="사물인터넷 과제 확인",
            started_at=0,
            current="과제 상세 페이지 진입 및 상세 정보 정합성 검증",
            next_action="이 케이스 실행",
            completed=["기본 목표 (SUCCESS)"],
            last_user_note="로그인 후 과목부터 봐줘",
        )

    status = bridge._format_live_status(123)

    assert "현재 테스트" in status
    assert "과제 상세 페이지" in status
    assert "최근 사용자 개입" in status


def test_tracking_progress_messages_are_test_case_level() -> None:
    message = _TelegramBridge._format_tracking_progress_message(
        {
            "kind": "edge_finished",
            "name": "13주차 섹션 아코디언 접기/펴기 토글 동작 검증",
            "status": "SKIP",
            "reason": "실제 화면에서 접기 버튼이 관찰되지 않음",
        }
    )

    assert "상태 추적" in message
    assert "완료" in message
    assert "SKIP" in message
    assert "Step" not in message


def test_live_intervention_is_available_to_running_agent() -> None:
    context = HubContext(
        provider="openai",
        model="gpt-5.5",
        auth_strategy="reuse",
        url="https://example.com",
        runtime="terminal",
        control_channel="telegram",
    )
    bridge = _TelegramBridge(
        hub_context=context,
        config=TelegramConfig(),
        memory_store=MemoryStore(enabled=False),
    )

    reply = bridge._record_live_intervention(123, "검색은 하지 말고 현재 화면의 두 번째 항목을 확인해줘")
    provider = bridge._build_live_intervention_provider(123)
    payload = provider()

    assert "반영" in reply
    assert payload is not None
    assert payload["instruction"].startswith("검색은 하지 말고")
    assert context.steering_policy["raw_text"].startswith("검색은 하지 말고")


def test_payload_text_includes_deep_qa_edge_case_summary() -> None:
    payload = {
        "status": "success",
        "final_status": "SUCCESS",
        "goal": "로그인하고 사물인터넷 두번째 과제가 올라왔는지 확인해봐",
        "steps": 6,
        "duration": 62.71,
        "reason": "사물인터넷 과목 페이지에서 과제2를 확인했습니다.",
        "adaptive_qa_report": {
            "mode": "deep_adaptive_qa",
            "summary": {
                "generated_edge_case_count": 10,
                "executed_edge_case_count": 10,
                "passed_edge_case_count": 7,
                "failed_edge_case_count": 3,
                "score": 0.727,
            },
            "edge_results": [
                {"name": "과제 상세 페이지 진입 및 상세 정보 정합성 검증", "status": "PASS", "reason": "상세 페이지 정상"},
                {"name": "13주차 섹션 아코디언 접기/펴기 토글 동작 검증", "status": "FAIL", "reason": "화면 상태 반복"},
                {"name": "브라우저 뒤로 가기 시 이전 페이지 상태 보존 검증", "status": "FAIL", "reason": "뒤로 가기 반응 없음"},
                {"name": "화면 확대 및 축소 시 과제 목록 레이아웃 깨짐 검증", "status": "FAIL", "reason": "inspect 반복"},
            ],
        },
    }

    text = _TelegramBridge._format_payload_text(payload)

    assert "Deep QA 확장 결과" in text
    assert "생성 10건 / 실행 10건" in text
    assert "성공 7건 / 실패 3건" in text
    assert "점수 72.7%" in text
    assert "13주차 섹션 아코디언" in text
    assert "브라우저 뒤로 가기" in text
    assert "화면 확대 및 축소" in text


def test_compact_report_payload_includes_deep_qa_failures() -> None:
    payload = {
        "status": "success",
        "final_status": "SUCCESS",
        "goal": "과제 확인",
        "adaptive_qa_report": {
            "mode": "deep_adaptive_qa",
            "summary": {
                "executed_edge_case_count": 2,
                "passed_edge_case_count": 1,
                "failed_edge_case_count": 1,
            },
            "edge_results": [
                {"id": "edge-1", "name": "정상 케이스", "status": "PASS", "reason": "성공", "steps": 1},
                {"id": "edge-2", "name": "실패 케이스", "status": "FAIL", "reason": "화면 반복", "steps": 12},
            ],
        },
    }

    report = _TelegramBridge._build_compact_report_payload(payload)

    adaptive = report["adaptive_qa"]
    assert adaptive["mode"] == "deep_adaptive_qa"
    assert adaptive["summary"]["executed_edge_case_count"] == 2
    assert adaptive["edge_results"][1]["name"] == "실패 케이스"
    assert adaptive["failed_edge_results"] == [adaptive["edge_results"][1]]


def test_build_command_payload_preserves_adaptive_qa_report() -> None:
    context = HubContext(
        provider="openai",
        model="gpt-5.5",
        auth_strategy="reuse",
        url="https://example.com",
        runtime="terminal",
        control_channel="telegram",
    )
    adaptive_report = {
        "mode": "deep_adaptive_qa",
        "summary": {"executed_edge_case_count": 10, "failed_edge_case_count": 3},
        "edge_results": [{"name": "아코디언", "status": "FAIL", "reason": "화면 반복"}],
    }

    payload = build_command_payload(
        context,
        "/test 과제 확인",
        CommandResult(
            code=0,
            data={
                "status": "success",
                "final_status": "SUCCESS",
                "goal": "과제 확인",
                "adaptive_qa_report": adaptive_report,
            },
        ),
    )

    assert payload["adaptive_qa_report"] is adaptive_report
