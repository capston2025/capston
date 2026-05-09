from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.goal_achievement_runtime import validate_goal_achievement_claim
from gaia.src.phase4.goal_driven.goal_completion_helpers import (
    evaluate_reasoning_only_wait_completion,
    evaluate_wait_goal_completion,
)
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_constraints = {"mutation_direction": "increase"}
        self._persistent_state_memory = []
        self._recent_signal_history = []
        self._last_exec_result = None
        self._consecutive_wait_count = 2
        self._goal_state_cache = {}
        self._auth_completed_fields = set()
        self._judge_response = ""

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _goal_target_terms(goal: object) -> list[str]:
        return ["포용사회와문화탐방1"]

    @staticmethod
    def _goal_destination_terms(goal: object) -> list[str]:
        return ["시간표", "내 시간표"]

    @staticmethod
    def _goal_quoted_terms(goal: object) -> list[str]:
        return ["포용사회와문화탐방1"]

    @staticmethod
    def _goal_text_blob(goal: object) -> str:
        fields = [getattr(goal, "name", ""), getattr(goal, "description", "")]
        fields.extend(getattr(goal, "success_criteria", []) or [])
        return " ".join(str(field or "").strip() for field in fields if str(field or "").strip()).lower()

    @staticmethod
    def _constraint_failure_reason() -> None:
        return None

    @staticmethod
    def _run_goal_policy_closer(*, goal: object, dom_elements: list[DOMElement]) -> None:
        return None

    def _call_llm_text_only(self, prompt: str) -> str:
        self._last_judge_prompt = prompt
        return self._judge_response

    def _format_dom_for_llm(self, elements: list[DOMElement]) -> str:
        return "\n".join(
            str(getattr(item, "text", "") or "").strip()
            for item in elements
            if str(getattr(item, "text", "") or "").strip()
        )

    def _wait_completion_ready(self, dom_elements: list[DOMElement] | None = None) -> bool:
        from gaia.src.phase4.goal_driven.goal_achievement_runtime import wait_completion_ready

        return wait_completion_ready(self, dom_elements)


def test_validate_goal_achievement_claim_accepts_wait_when_destination_row_is_visible():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목을 바로 추가",
        description="이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인",
        success_criteria=["내 시간표에 포용사회와문화탐방1이 다시 보이는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        value='{"text":"(HUSS국립부경대)포용사회와문화탐방1"}',
        reasoning=(
            "현재 열린 내 시간표 surface 안에 포용사회와문화탐방1의 직접 행이 보이고 "
            "삭제 후 다시 바로 추가까지 수행했으므로 목표를 달성했습니다."
        ),
        confidence=0.98,
        is_goal_achieved=True,
        goal_achievement_reason="내 시간표에 다시 반영됨",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            context_text="내 시간표 | 총 9개 과목 • 25학점 | 시간표에서 제거",
            group_action_labels=["시간표에서 제거"],
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_when_destination_anchor_and_row_action_are_separate():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목을 바로 추가",
        description="이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인",
        success_criteria=["내 시간표에 포용사회와문화탐방1이 다시 보이는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 내 시간표 화면에서 포용사회와문화탐방1 행과 같은 줄의 제거 CTA가 확인되어 목표를 달성했습니다.",
        confidence=0.95,
        is_goal_achieved=True,
        goal_achievement_reason="내 시간표에 다시 반영됨",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="내 시간표",
            aria_label="내 시간표",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            container_ref_id="row-1",
            tag="div",
            role="generic",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            context_text="온라인 / 시간외 과목",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            container_ref_id="row-1",
            tag="button",
            role="button",
            text="제거",
            aria_label="제거",
            context_text="온라인 / 시간외 과목",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_keeps_wait_rejected_for_source_add_row_even_with_page_destination_anchor():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목을 바로 추가",
        description="이미 추가되어 있던 경우 삭제 후 다시 추가되는지 확인",
        success_criteria=["내 시간표에 포용사회와문화탐방1이 다시 보이는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 페이지에 내 시간표 앵커가 있고 포용사회와문화탐방1 행이 보여 목표를 달성했다고 판단합니다.",
        confidence=0.72,
        is_goal_achieved=True,
        goal_achievement_reason="반영됨",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="내 시간표",
            aria_label="내 시간표",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            container_ref_id="row-1",
            tag="div",
            role="generic",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            context_text="검색 결과 | 미배정",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            container_ref_id="row-1",
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            context_text="검색 결과 | 미배정",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다."


def test_validate_goal_achievement_claim_accepts_wait_for_generic_search_change_proof():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="검색 결과 변경 검증",
        description="과목 검색창에 키워드를 입력해 검색 결과 목록이 실제로 바뀌는지 검증",
        success_criteria=["검색 결과 목록이 실제로 바뀌는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        value='{"timeMs":1000}',
        reasoning=(
            "현재 화면에서 검색 결과가 변경되어 검색 결과(총 35개 중 20개 표시)와 "
            "디지털 관련 과목들이 표시되고 있으므로 결과 목록 변화가 반영되었다고 판단합니다."
        ),
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="검색 결과 변경 반영",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="검색 결과(총 35개 중 20개 표시)",
            aria_label="검색 결과(총 35개 중 20개 표시)",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="(HUSS)디지털포용과스타트업창업실무",
            aria_label="(HUSS)디지털포용과스타트업창업실무",
            context_text="검색 결과(총 35개 중 20개 표시)",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_for_readonly_visibility_absence_observation():
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": ""}
    goal = SimpleNamespace(
        name="현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인",
        description="추가 조작 없이 종료해줘.",
        success_criteria=["현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인"],
        expected_signals=["text_visible", "cta_visible"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "현재 메인 화면에는 과목 검색과 바로 추가 버튼만 보이고 "
            "로그인 버튼이나 로그인 유도 CTA는 보이지 않습니다. "
            "가시 여부 확인이 끝났으므로 추가 조작 없이 종료합니다."
        ),
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="현재 화면에서 로그인 CTA 미노출 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="과목 검색",
            aria_label="과목 검색",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_rejects_generic_search_wait_without_specific_evidence():
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="검색 결과 변경 검증",
        description="과목 검색창에 키워드를 입력해 검색 결과 목록이 실제로 바뀌는지 검증",
        success_criteria=["검색 결과 목록이 실제로 바뀌는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        value='{"timeMs":1000}',
        reasoning="현재 검색 결과 목록이 표시되고 있으므로 변화가 반영된 것으로 판단합니다.",
        confidence=0.7,
        is_goal_achieved=True,
        goal_achievement_reason="검색 결과 표시",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="검색 결과(총 35개 중 20개 표시)",
            aria_label="검색 결과(총 35개 중 20개 표시)",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다."


def test_validate_goal_achievement_claim_accepts_wait_when_expected_signals_are_met() -> None:
    agent = _FakeAgent()
    agent._persistent_state_memory = [
        {
            "kind": "select",
            "expected_value": "전핵",
            "previous_selected_value": "전체",
            "ref_id": "e33",
            "role_ref_name": "전체",
            "container_name": "검색",
            "context_text": "검색 | 전체 | &service",
        }
    ]
    agent._last_exec_result = SimpleNamespace(state_change={"text_digest_changed": True})
    goal = SimpleNamespace(
        name="구분 필터 결과 변경",
        description="구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        success_criteria=["결과 목록이 실제로 바뀌는지 확인"],
        expected_signals=["target_value_changed", "dom_changed"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="필터 선택값과 결과 목록 변화가 모두 확인되어 목표를 달성했습니다.",
        confidence=0.92,
        is_goal_achieved=True,
        goal_achievement_reason="필터 결과 변경 확인",
    )
    dom = [
        DOMElement(
            id=33,
            ref_id="e33",
            tag="select",
            role="combobox",
            text="구분",
            selected_value="전핵",
            role_ref_name="전체",
            container_name="검색",
            context_text="검색 | 전체 | &service",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=716,
            tag="div",
            role="generic",
            text="전핵 | 과목 A",
            context_text="검색 결과",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_multi_user_blackboard_evidence() -> None:
    agent = _FakeAgent()
    agent._last_snapshot_evidence = {
        "text_digest": "receiver-user: round 6: receiver closes full e2e loop",
        "live_texts": [],
    }
    agent._participant_registry = SimpleNamespace(
        is_multi=lambda: True,
        blackboard=SimpleNamespace(
            all_entries=lambda: [
                SimpleNamespace(
                    key="sender_message_round_5",
                    value={
                        "sender": "sender-user",
                        "text": "round 5: sender sends final challenge",
                    },
                ),
                SimpleNamespace(
                    key="receiver_message_round_6",
                    value={
                        "sender": "receiver-user",
                        "text": "round 6: receiver closes full e2e loop",
                    },
                ),
            ]
        ),
    )
    goal = SimpleNamespace(
        name="multi user chat",
        description="sender와 receiver가 왕복 채팅한다",
        success_criteria=[
            "sender-user: round 5: sender sends final challenge",
            "receiver-user: round 6: receiver closes full e2e loop",
        ],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="전체 왕복 메시지 확인 완료",
        is_goal_achieved=True,
        goal_achievement_reason="sender/receiver transcript가 모두 표시되어 완료",
    )

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, [])

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "multi_user_evidence"


def test_validate_goal_achievement_claim_rejects_signup_goal_without_completion_signal() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="회원가입 완료 확인",
        description="회원가입이 정상적으로 끝났는지 확인",
        success_criteria=["회원가입 완료 여부 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        reasoning="회원가입 화면이 보이므로 목표를 달성했다고 판단합니다.",
        confidence=0.8,
        is_goal_achieved=True,
        goal_achievement_reason="회원가입 화면 진입",
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="회원가입",
            aria_label="회원가입",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "회원가입 목표는 화면 진입만으로 성공으로 보지 않습니다. 회원가입 제출 및 완료 신호가 필요합니다."


def test_validate_goal_achievement_claim_accepts_multi_user_signup_context_evidence() -> None:
    agent = _FakeAgent()
    agent._last_snapshot_evidence = {
        "text_digest": "sender-signup-user: signup alpha final challenge receiver-signup-user: signup beta final closes loop",
        "live_texts": [],
    }
    agent._participant_registry = SimpleNamespace(
        is_multi=lambda: True,
        blackboard=SimpleNamespace(
            all_entries=lambda: [
                SimpleNamespace(
                    key="sender_message_round_5",
                    value={
                        "sender": "sender-signup-user",
                        "text": "signup alpha final challenge",
                    },
                ),
                SimpleNamespace(
                    key="receiver_message_round_6",
                    value={
                        "sender": "receiver-signup-user",
                        "text": "signup beta final closes loop",
                    },
                ),
            ]
        ),
    )
    goal = SimpleNamespace(
        name="회원가입 후 multi user chat",
        description="두 개의 새 계정을 회원가입한 뒤 서로 채팅한다",
        success_criteria=[
            "signup alpha final challenge",
            "signup beta final closes loop",
        ],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="양쪽 transcript와 가입 후 로그인 상태를 모두 확인했습니다.",
        confidence=1.0,
        is_goal_achieved=True,
        goal_achievement_reason="두 새 계정의 채팅 검증 완료",
    )
    dom = [
        DOMElement(
            id=1,
            tag="input",
            role="textbox",
            text="",
            aria_label="Message",
            context_text=(
                "Signed up and logged in as receiver-signup-user | Message | "
                "sender-signup-user: signup alpha final challenge | "
                "receiver-signup-user: signup beta final closes loop"
            ),
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "multi_user_evidence"


def test_validate_goal_achievement_claim_accepts_wait_on_recent_transition_even_when_expected_signals_are_missing() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": "clear"}
    agent._last_exec_result = SimpleNamespace(
        state_change={
            "dom_changed": True,
            "text_digest_changed": True,
        }
    )
    goal = SimpleNamespace(
        name="캡스톤디자인 과목을 추가 후 다시 삭제",
        description="추가한 뒤 삭제까지 끝났는지 확인",
        success_criteria=["추가 후 삭제가 완료되었는지 확인"],
        expected_signals=["post_action_verified", "ui_transition_recorded"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="방금 추가 후 삭제 전환이 반영되었고 현재는 삭제 완료 상태이므로 종료합니다.",
        confidence=0.91,
        is_goal_achieved=True,
        goal_achievement_reason="추가 후 삭제 전환 완료",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="status",
            text="'캡스톤디자인' 삭제 완료",
            context_text="상태 토스트",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_for_readonly_visibility_goal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {
        "require_no_navigation": True,
        "current_view_only": True,
    }
    goal = SimpleNamespace(
        name="메인 화면 로그인 CTA 확인",
        description="현재 메인 화면에서 로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인하고 추가 조작 없이 종료",
        success_criteria=["로그인 버튼 또는 로그인 유도 CTA가 이미 보이는지 확인"],
        expected_signals=["text_visible", "cta_visible"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 메인 화면 상단에 로그인 버튼이 직접 보이므로 추가 조작 없이 종료합니다.",
        confidence=0.95,
        is_goal_achieved=True,
        goal_achievement_reason="로그인 CTA 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="로그인",
            aria_label="로그인",
            context_text="상단 배너 | 인증",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_accepts_wait_via_generic_judge_for_late_response_goal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._judge_response = """
```json
{
  "success": true,
  "blocked": false,
  "reason": "입력한 문장이 전송되었고 그에 대한 응답 본문이 현재 화면에 직접 보여 목표가 완료되었습니다.",
  "confidence": 0.96
}
```
""".strip()
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "입력한 문장은 이미 전송되었고, 현재 화면에 assistant 응답인 "
            "'안녕! 그냥 너랑 대화하려고 기다리고 있었지'가 직접 표시됩니다."
        ),
        confidence=0.93,
        is_goal_achieved=True,
        goal_achievement_reason="응답 본문 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="대화 입력",
            is_visible=True,
            is_enabled=True,
        )
    ]
    for idx in range(2, 47):
        dom.append(
            DOMElement(
                id=idx,
                tag="div",
                role="generic",
                text=f"filler-{idx}",
                context_text="sidebar",
                is_visible=True,
                is_enabled=True,
            )
        )
    dom.append(
        DOMElement(
            id=47,
            ref_id="e319",
            tag="div",
            role="article",
            text="안녕! 그냥 너랑 대화하려고 기다리고 있었지 🙂 너는 지금 뭐 하고 있어?",
            context_text="assistant response",
            is_visible=True,
            is_enabled=True,
        )
    )

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_wait_completion_accepts_readonly_video_detail_information_without_contract_signal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name="검색 결과에서 공개 영상을 하나 열어 제목, 채널명, 조회 정보 또는 설명 일부 확인",
        description="YouTube 검색 결과에서 공개 영상 상세 화면의 정보가 보이는지 확인해줘.",
        success_criteria=["공개 영상 상세 화면에서 제목, 채널명, 조회 정보 또는 설명 일부가 보이는지 확인"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "현재 화면은 YouTube watch 페이지이며 상단에 영상 제목, 채널명(한국관광공사TV), "
            "조회 정보와 설명 일부가 이미 보입니다. 목표는 공개 영상을 하나 열어 해당 정보가 "
            "보이는지 확인하는 것이므로 추가 조작 없이 완료 상태입니다."
        ),
        confidence=0.78,
        is_goal_achieved=False,
        goal_achievement_reason=None,
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="외국인들한테 보여줬더니 감탄사 연발한 영상",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="a",
            role="link",
            text="한국관광공사TV",
            context_text="채널",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="span",
            role="generic",
            text="조회수 12만회 3개월 전",
            context_text="영상 설명 일부",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_wait_goal_completion(agent, goal=goal, decision=decision, dom_elements=dom)

    assert reason is not None
    assert "한국관광공사tv" in reason.lower()


def test_reasoning_only_wait_completion_uses_judge_for_readonly_video_detail_claim() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 YouTube 영상 상세 화면에 제목, 채널명, 조회 정보가 직접 보입니다.",
  "confidence": 0.91
}
""".strip()
    goal = SimpleNamespace(
        name="검색 결과에서 공개 영상을 하나 열어 제목, 채널명, 조회 정보 또는 설명 일부 확인",
        description="YouTube 검색 결과에서 공개 영상 상세 화면의 정보가 보이는지 확인해줘.",
        success_criteria=["공개 영상 상세 화면에서 제목, 채널명, 조회 정보 또는 설명 일부가 보이는지 확인"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="영상 제목, 채널명, 조회/업로드 정보와 설명 일부가 이미 보입니다. 목표 조건을 충족합니다.",
        confidence=0.78,
        is_goal_achieved=False,
        goal_achievement_reason=None,
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="서울 여행에서 꼭 가봐야 할 명소",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="a",
            role="link",
            text="한국관광공사TV",
            context_text="채널",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="span",
            role="generic",
            text="조회수 12만회 3개월 전",
            context_text="설명",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_reasoning_only_wait_completion(agent, goal=goal, decision=decision, dom_elements=dom)

    assert reason == "현재 YouTube 영상 상세 화면에 제목, 채널명, 조회 정보가 직접 보입니다."
    assert "WAIT reasoning이 현재 화면 기준 목표 완료를 주장했습니다." not in agent._last_judge_prompt
    assert "영상 제목, 채널명, 조회/업로드 정보" in agent._last_judge_prompt


def test_wait_completion_rejects_service_unavailable_false_positive() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name="관광지 상세 정보 확인",
        description="대한민국 구석구석에서 관광지 상세 화면의 주소와 설명 일부를 확인해줘.",
        success_criteria=["상세 정보 화면에서 주소 또는 설명 일부 확인"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="대한민국 구석구석 서비스 지연 안내가 보이고 서비스 정보가 표시되어 목표 조건을 충족합니다.",
        confidence=0.8,
        is_goal_achieved=False,
        goal_achievement_reason=None,
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="대한민국 구석구석 서비스 지연 안내",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="p",
            role="generic",
            text="잠시 후 다시 접속해 주세요.",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    assert evaluate_wait_goal_completion(agent, goal=goal, decision=decision, dom_elements=dom) is None


def test_reasoning_only_wait_completion_skips_judge_on_service_unavailable_page() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    agent._judge_response = '{"success": true, "reason": "오판"}'
    goal = SimpleNamespace(
        name="법령 상세 정보 확인",
        description="국가법령정보센터에서 법령 상세 화면의 시행일과 조문 일부를 확인해줘.",
        success_criteria=["상세 정보 화면에서 시행일 또는 조문 확인"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="상세 정보와 서비스 문구가 이미 보입니다. 목표 조건을 충족합니다.",
        confidence=0.8,
        is_goal_achieved=False,
        goal_achievement_reason=None,
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="서비스 이용에 불편을 드려서 죄송합니다",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="p",
            role="generic",
            text="현재 사용자가 많아 요청하신 페이지를 정상적으로 제공할 수 없습니다.",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    assert evaluate_reasoning_only_wait_completion(agent, goal=goal, decision=decision, dom_elements=dom) is None
    assert not hasattr(agent, "_last_judge_prompt")


def test_wait_completion_accepts_readonly_map_route_panel_information_without_contract_signal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name="카카오맵 길찾기 패널 확인",
        description="카카오맵에서 길찾기 패널을 열어 출발지와 도착지 입력 영역이 보이는지 확인해줘.",
        success_criteria=["길찾기 패널의 출발지와 도착지 입력 영역 확인"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "현재 카카오맵 길찾기 패널에 출발지 서울역과 도착지 경복궁이 이미 표시되고, "
            "대중교통 경로 탭도 보입니다. 목표가 요구한 경로 정보 확인 조건을 충족합니다."
        ),
        confidence=0.82,
        is_goal_achieved=False,
        goal_achievement_reason=None,
    )
    dom = [
        DOMElement(
            id=1,
            tag="input",
            role="textbox",
            text="서울역",
            placeholder="출발지를 입력하세요",
            context_text="길찾기 출발지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="input",
            role="textbox",
            text="경복궁",
            placeholder="도착지를 입력하세요",
            context_text="길찾기 도착지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="button",
            role="tab",
            text="대중교통",
            context_text="자동차 대중교통 도보 자전거",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_wait_goal_completion(agent, goal=goal, decision=decision, dom_elements=dom)

    assert reason is not None
    assert "길찾기" in reason


def test_validate_goal_achievement_claim_includes_new_page_evidence_in_judge_prompt() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "새 창 viewer evidence와 현재 DOM 증거를 함께 확인했습니다.",
  "confidence": 0.94
}
""".strip()
    agent._last_exec_result = SimpleNamespace(
        state_change={
            "new_page_detected": True,
            "new_page_count": 1,
            "new_page_same_origin_detected": True,
            "new_page_urls": ["https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868"],
            "new_page_titles": ["대중_6주차_1차시_동물복제"],
            "new_page_kinds": ["viewer_like"],
        }
    )
    goal = SimpleNamespace(
        name="6주차 1차시 수강 버튼 누르기",
        description="동영상 보기 클릭 후 실제 관련 viewer 창이 뜨는지 확인",
        success_criteria=["관련 viewer 창 또는 수강 surface가 열리는지 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="방금 동영상 보기 클릭 이후 별도 viewer 창이 열린 것으로 보입니다.",
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="viewer 창 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="동영상 보기",
            context_text="6주차 1차시 상세",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "judge"
    assert '"new_page_detected": true' in agent._last_judge_prompt
    assert "viewer.php?id=1346868" in agent._last_judge_prompt
    assert "viewer_like" in agent._last_judge_prompt


def test_validate_goal_achievement_claim_rejects_wait_when_play_control_is_still_visible() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="6주차 1차시 동영상을 재생한다",
        description="viewer 창에서 재생 버튼을 눌러 동영상을 재생해줘.",
        success_criteria=["재생 버튼을 눌러 동영상을 재생한다"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="viewer 창과 play 버튼이 보이므로 목표를 달성했다고 판단합니다.",
        confidence=0.88,
        is_goal_achieved=True,
        goal_achievement_reason="viewer surface 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="application",
            text="Video Player",
            aria_label="Video Player",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            ref_id="e15",
            tag="button",
            role="button",
            text="재생",
            aria_label="재생",
            title="재생",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "재생 목표는 현재 player surface에 play/start control이 남아 있으면 완료로 보지 않습니다. 먼저 재생 버튼을 누르세요."


def test_validate_goal_achievement_claim_accepts_wait_via_reasoning_result_quote_without_judge() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "이전 단계에서 메시지를 보냈고, 현재 화면에 응답인 "
            "'안녕! 😊 지금 너랑 대화하고 있지 😊 뭐 도와줄까?'가 표시되어 목표가 달성되었습니다."
        ),
        confidence=0.94,
        is_goal_achieved=True,
        goal_achievement_reason="응답 본문 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="내 메시지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="article",
            text="안녕! 😊 지금 너랑 대화하고 있지 😊 뭐 도와줄까?",
            context_text="assistant response",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None


def test_validate_goal_achievement_claim_defers_first_wait_for_transient_loading_surface() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._consecutive_wait_count = 1
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 화면 증거상 목표가 완료되었습니다.",
  "confidence": 0.95
}
""".strip()
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="응답이 보이기 시작했으니 목표가 끝난 것 같습니다.",
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="응답 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="status",
            role="status",
            text="생각 중",
            context_text="loading surface",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="진행률 16%",
            context_text="progress overlay",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "첫 WAIT는 완료 판정을 내리지 않고 한 번 더 상태 변화를 관찰합니다."
    assert agent._last_goal_completion_source == ""


def test_validate_goal_achievement_claim_allows_first_wait_for_stable_zero_state_surface() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": "clear"}
    agent._consecutive_wait_count = 1
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "삭제 이후 stable zero-state가 직접 확인되어 목표가 완료되었습니다.",
  "confidence": 0.95
}
""".strip()
    goal = SimpleNamespace(
        name="위시리스트 비우기",
        description="로그인 후 위시리스트를 모두 비우고 총 0학점 상태인지 확인해줘.",
        success_criteria=["총 0학점과 empty-state 문구 확인"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="현재 화면에 총 0학점과 빈 위시리스트 상태가 직접 보여 목표가 완료되었습니다.",
        confidence=0.92,
        is_goal_achieved=True,
        goal_achievement_reason="zero-state 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="총 0학점",
            context_text="위시리스트 요약",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="status",
            text="담은 과목이 없어요.",
            context_text="empty state",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "judge"


def test_validate_goal_achievement_claim_rejects_loading_quote_as_result() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning='현재 화면에는 "생각 중" 상태가 표시되어 결과를 생성하고 있습니다.',
        confidence=0.8,
        is_goal_achieved=True,
        goal_achievement_reason="로딩 중",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="내 메시지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="status",
            role="status",
            text="생각 중",
            context_text="loading",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is False
    assert reason == "WAIT 기반 성공 판정은 현재 DOM의 강한 목표 증거나 contract signal이 필요합니다."


def test_validate_goal_achievement_claim_allows_judge_to_bypass_missing_expected_signals() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 화면 증거상 목표가 완료되었습니다.",
  "confidence": 0.93
}
""".strip()
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    goal = SimpleNamespace(
        name='이 사이트 들어가서 "안녕 뭐해?"라고 입력하고 결과물 알려줘봐',
        description='입력 후 나온 결과를 확인해줘.',
        success_criteria=['"안녕 뭐해?" 입력 후 결과 응답이 화면에 나타나는지 확인'],
        expected_signals=["response_visible"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="사용자 입력과 응답이 모두 화면에 보여 목표가 달성되었습니다.",
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="응답 확인",
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="안녕 뭐해?",
            context_text="내 메시지",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="article",
            text="안녕! 반가워요.",
            context_text="assistant response",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "judge"


def test_validate_goal_achievement_claim_allows_direct_click_without_expected_signal_gate() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="공개 문서 열기",
        description="문서를 여는 버튼을 누르면 완료",
        success_criteria=["문서 열기 버튼 클릭"],
        expected_signals=["url_changed"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e9",
        reasoning="현재 화면의 직접 CTA를 눌렀고, 이 클릭 자체가 목표의 마지막 단계입니다.",
        confidence=0.91,
        is_goal_achieved=True,
        goal_achievement_reason="문서 열기 버튼 클릭 완료",
    )
    dom = [
        DOMElement(
            id=1,
            ref_id="e9",
            tag="button",
            role="button",
            text="문서 열기",
            context_text="상세 페이지",
            is_visible=True,
            is_enabled=True,
        )
    ]

    ok, reason = validate_goal_achievement_claim(agent, goal, decision, dom)

    assert ok is True
    assert reason is None
    assert agent._last_goal_completion_source == "direct"
