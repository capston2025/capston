from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.goal_achievement_runtime import (
    has_recent_transition_completion_proof,
    validate_goal_achievement_claim,
)
from gaia.src.phase4.goal_driven.goal_completion_helpers import (
    build_text_evidence_memory_block,
    detect_service_unavailable_state,
    evaluate_goal_completion_judge,
    evaluate_filter_result_surface_completion,
    evaluate_goal_target_completion,
    evaluate_payment_presubmit_completion,
    evaluate_reasoning_only_wait_completion,
    evaluate_repeated_stop_completion_judge,
    evaluate_wait_goal_completion,
    record_llm_requested_text_evidence,
)
from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent
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
        self._text_evidence_memory = []
        self._last_snapshot_evidence = {}
        self._active_snapshot_id = ""
        self._active_url = ""
        self._action_history = []
        self._action_feedback = []
        self._last_action_selected_element = None
        self._last_action_decision = None

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


def test_payment_presubmit_completion_accepts_checkout_page_with_final_payment_cta() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="결제 직전까지 검증",
        description="상품을 장바구니에 담고 결제하기를 누르기 전 화면까지 도달한다.",
        success_criteria=["주문/결제 화면에서 결제하기 버튼이 보이는지 확인"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="네이버페이 주문/결제",
            is_visible=True,
        ),
        DOMElement(
            id=2,
            tag="section",
            role="region",
            text="배송지 결제수단 총 결제금액 49,000원",
            context_text="주문상품 배송 정보 결제 수단",
            is_visible=True,
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="결제하기",
            context_text="최종 결제 실행",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_payment_presubmit_completion(agent, goal=goal, dom_elements=dom)

    assert reason is not None
    assert "결제 직전" in reason
    assert evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom) == reason


def test_payment_presubmit_completion_rejects_cart_order_cta_before_checkout() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="결제 직전까지 검증",
        description="장바구니에서 결제하기 직전 화면까지 도달한다.",
        success_criteria=["결제하기 버튼이 보이는 주문/결제 화면"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="장바구니",
            context_text="총 주문 예상 금액 49,000원",
            is_visible=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="주문하기 1개의 상품",
            context_text="장바구니 하단 버튼",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    assert evaluate_payment_presubmit_completion(agent, goal=goal, dom_elements=dom) is None


def test_payment_presubmit_completion_rejects_payment_completion_goal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="결제 완료까지 진행",
        description="결제 버튼을 눌러 결제 완료 상태까지 진행한다.",
        success_criteria=["결제 완료"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="주문/결제",
            context_text="배송지 결제수단 총 결제금액",
            is_visible=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="결제하기",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    assert evaluate_payment_presubmit_completion(agent, goal=goal, dom_elements=dom) is None


def test_variant_price_image_completion_accepts_selected_product_surface() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._action_history = [
        "click 검색 결과 2등 상품 더단백 드링크 초코, 250ml, 36개 43,340원",
        "click 총 수량 옵션 18개",
    ]
    goal = SimpleNamespace(
        name="단백질 쉐이크 옵션 비교",
        description="단백질 쉐이크 검색한뒤 2등 상품 클릭해주고, 18개입 클릭했을때 대표이미지랑 가격이 2등 상품인 36개입짜리랑 달라지는지 확인해줘",
        success_criteria=["18개입 선택 후 대표이미지와 가격 확인"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="더단백 드링크 초코, 250ml, 18개",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="23,940원",
            context_text="상품 가격",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="img",
            role="img",
            text="Product image",
            aria_label="Product image",
            context_text="대표이미지",
            is_visible=True,
            is_enabled=True,
        ),
    ]
    agent._last_action_selected_element = DOMElement(
        id=9,
        tag="button",
        role="button",
        text="18개",
        is_visible=True,
        is_enabled=True,
    )

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is not None
    assert "선택 수량" in reason


def test_variant_price_image_completion_rejects_unselected_option_button_only() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._action_history = [
        "click 검색 결과 2등 상품 더단백 드링크 초코, 250ml, 36개 43,340원",
    ]
    goal = SimpleNamespace(
        name="단백질 쉐이크 옵션 비교",
        description="단백질 쉐이크 검색한뒤 2등 상품 클릭해주고, 18개입 클릭했을때 대표이미지랑 가격이 2등 상품인 36개입짜리랑 달라지는지 확인해줘",
        success_criteria=["18개입 선택 후 대표이미지와 가격 확인"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="더단백 드링크 초코, 250ml, 36개",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="43,340원",
            context_text="상품 가격",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="18개",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=4,
            tag="img",
            role="img",
            text="Product image",
            aria_label="Product image",
            context_text="대표이미지",
            is_visible=True,
            is_enabled=True,
        ),
    ]
    agent._last_action_selected_element = DOMElement(
        id=9,
        tag="a",
        role="link",
        text="더단백 드링크 초코, 250ml, 36개",
        is_visible=True,
        is_enabled=True,
    )

    assert evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom) is None


def test_variant_price_image_completion_accepts_recent_target_selection_after_inspect_loop() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._action_history = [
        "click 검색 결과 2등 상품 테이크핏 맥스 초코맛 프로틴, 250ml, 24개 32,900원",
        "click 모든 옵션 보기",
        "click 250ml × 18개",
        "inspect 대표이미지 ref=e173",
        "click 모든 옵션 보기",
    ]
    goal = SimpleNamespace(
        name="단백질 쉐이크 옵션 비교",
        description="단백질 쉐이크 검색한뒤 2등 상품 클릭해주고, 18개입 클릭했을때 대표이미지랑 가격이 2등 상품인 36개입짜리랑 달라지는지 확인해줘",
        success_criteria=["18개입 선택 후 대표이미지와 가격 확인"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="테이크핏 맥스 초코맛 + 바나나맛, 250ml, 18개",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="25,900원",
            context_text="상품 가격",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="img",
            role="img",
            text="Product image",
            aria_label="Product image",
            context_text="대표이미지",
            is_visible=True,
            is_enabled=True,
        ),
    ]
    agent._last_action_selected_element = DOMElement(
        id=9,
        tag="button",
        role="button",
        text="모든 옵션 보기",
        is_visible=True,
        is_enabled=True,
    )

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is not None
    assert "선택 수량" in reason


def test_sort_results_completion_accepts_checked_sorted_product_list() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._active_url = "https://www.coupang.com/np/search?q=제로콜라&sorter=saleCountDesc"
    agent._last_action_selected_element = DOMElement(
        id=9,
        tag="label",
        role="radio",
        text="판매량순",
        is_visible=True,
        is_enabled=True,
    )
    goal = SimpleNamespace(
        name="제로콜라 판매량순 정렬",
        description="제로콜라 검색 후에 판매량순 필터 클릭하고 순서대로 정렬되어 제품이 나타나는지 확인해줘",
        success_criteria=["판매량순 정렬된 제품 목록 확인"],
    )
    dom = [
        DOMElement(id=1, tag="h1", role="heading", text="제로콜라 검색결과", is_visible=True),
        DOMElement(id=2, tag="label", role="radio", text="판매량순 checked active", is_visible=True),
        DOMElement(
            id=3,
            tag="ul",
            role="list",
            text="product-list 1 코카콜라 제로 2 펩시 제로 3 탐스 제로",
            context_text="제품 목록 상품 무료배송 리뷰",
            is_visible=True,
        ),
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is not None
    assert "판매량순" in reason


def test_sort_results_completion_rejects_sort_option_before_click() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._active_url = "https://www.coupang.com/np/search?q=제로콜라"
    agent._last_action_selected_element = DOMElement(
        id=9,
        tag="button",
        role="button",
        text="검색",
        is_visible=True,
        is_enabled=True,
    )
    goal = SimpleNamespace(
        name="제로콜라 판매량순 정렬",
        description="제로콜라 검색 후에 판매량순 필터 클릭하고 순서대로 정렬되어 제품이 나타나는지 확인해줘",
        success_criteria=["판매량순 정렬된 제품 목록 확인"],
    )
    dom = [
        DOMElement(id=1, tag="h1", role="heading", text="제로콜라 검색결과", is_visible=True),
        DOMElement(id=2, tag="label", role="radio", text="판매량순", is_visible=True),
        DOMElement(
            id=3,
            tag="ul",
            role="list",
            text="product-list 1 코카콜라 제로 2 펩시 제로 3 탐스 제로",
            context_text="제품 목록 상품 무료배송 리뷰",
            is_visible=True,
        ),
    ]

    assert evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom) is None


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
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "검색 결과 목록이 변경되었고 디지털 관련 과목이 현재 화면에 표시됩니다.",
  "confidence": 0.9
}
""".strip()
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


def test_recent_transition_completion_rejects_uncertain_wait_rationale() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"require_state_change": True}
    goal = SimpleNamespace(
        name="과거 날짜 비활성 확인",
        description="과거 날짜 선택 불가를 확인한다.",
        success_criteria=["과거 날짜가 클릭되지 않는지 확인"],
        expected_signals=["날짜", "클릭되지 않음"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="DOM만으로는 숨겨져 있는지 추가 화면 컨텍스트 확인이 필요하므로 짧게 대기합니다.",
        confidence=0.6,
        is_goal_achieved=True,
        goal_achievement_reason="",
    )

    reason = has_recent_transition_completion_proof(
        agent,
        goal=goal,
        decision=decision,
        state_change={"dom_changed": True},
        achieved_signals=[],
    )

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


def test_wait_completion_defers_readonly_video_detail_claim_to_judge() -> None:
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

    assert evaluate_wait_goal_completion(agent, goal=goal, decision=decision, dom_elements=dom) is None

    reason = evaluate_reasoning_only_wait_completion(agent, goal=goal, decision=decision, dom_elements=dom)
    assert reason is not None
    assert "youtube" in reason.lower()


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


def test_repeated_screen_stop_runs_final_completion_judge() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 상품 의견 게시판에 최근 댓글 3개가 직접 표시되어 목표 증거가 충분합니다.",
  "confidence": 0.9
}
""".strip()
    goal = SimpleNamespace(
        name="상품 의견 게시판 댓글 확인",
        description="인기 로봇청소기 상세 페이지 하단의 상품 의견 게시판에서 최근 댓글 3개를 확인해줘.",
        success_criteria=["상품 의견 게시판과 최근 댓글 3개 확인"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.INSPECT,
        reasoning="같은 화면을 반복 확인하고 있어 더 진행하기 어렵습니다.",
        confidence=0.45,
    )
    dom = [
        DOMElement(id=1, tag="h3", role="heading", text="상품 의견", is_visible=True, is_enabled=True),
        DOMElement(id=2, tag="li", role="listitem", text="흡입력은 좋지만 소음이 조금 있습니다.", is_visible=True, is_enabled=True),
        DOMElement(id=3, tag="li", role="listitem", text="배송 빠르고 설치가 쉬웠어요.", is_visible=True, is_enabled=True),
        DOMElement(id=4, tag="li", role="listitem", text="앱 연결이 편하고 예약 청소가 됩니다.", is_visible=True, is_enabled=True),
    ]

    reason = evaluate_repeated_stop_completion_judge(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom,
        stop_reason="화면 상태가 반복되어 더 이상 진행이 어렵습니다.",
    )

    assert reason == "현재 상품 의견 게시판에 최근 댓글 3개가 직접 표시되어 목표 증거가 충분합니다."
    assert "반복 중단 직전 최종 판정 요청" in agent._last_judge_prompt
    assert "화면 상태가 반복" in agent._last_judge_prompt


def test_repeated_stop_judge_ignores_non_repeated_stop_reason() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "judge should not run",
  "confidence": 0.99
}
""".strip()
    goal = SimpleNamespace(
        name="로그인 필요 목표",
        description="로그인이 필요한 화면을 확인한다.",
        success_criteria=["로그인 후 화면 확인"],
        expected_signals=[],
    )
    dom = [DOMElement(id=1, tag="button", role="button", text="로그인", is_visible=True, is_enabled=True)]

    reason = evaluate_repeated_stop_completion_judge(
        agent,
        goal=goal,
        dom_elements=dom,
        stop_reason="로그인 모달 반복으로 목표를 진행할 수 없어 중단했습니다.",
    )

    assert reason is None
    assert not hasattr(agent, "_last_judge_prompt")


def test_llm_requested_text_evidence_records_dom_text_blocks_for_list_goal() -> None:
    agent = _FakeAgent()
    agent._active_snapshot_id = "openclaw:test:3"
    agent._active_url = "https://news.example.test/section/it"
    agent._last_snapshot_evidence = {
        "dom_text_blocks": [
            {
                "text": "AI 반도체 투자 확대 언론사A 12분전 기업들이 서버 투자를 늘리고 있다.",
                "section": "최신기사",
                "tag": "li",
            },
            {
                "text": "우주 발사체 시험 성공 언론사B 18분전 첫 시험 비행이 정상 종료됐다.",
                "section": "최신기사",
                "tag": "li",
            },
        ],
        "live_texts": ["AI 반도체 투자 확대 언론사A 12분전 기업들이 서버 투자를 늘리고 있다."],
    }
    goal = SimpleNamespace(
        id="TC_LIST",
        name="IT 기사 목록 2개 확인",
        description="IT 기사 목록에서 각 카드의 제목, 언론사, 시간, 요약을 확인한다.",
        success_criteria=["기사 목록 2개", "제목/언론사/시간/요약"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.INSPECT,
        reasoning="현재 화면에 기사 목록 카드가 보여 텍스트 evidence를 누적합니다.",
        confidence=0.8,
        collect_text_evidence=True,
        text_evidence_reason="기사 목록 2개 필드 수집",
        text_evidence_focus=["제목", "언론사", "시간", "요약"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="AI 반도체 투자 확대",
            context_text="언론사A | 12분전 | 기업들이 서버 투자를 늘리고 있다.",
            is_visible=True,
            is_enabled=True,
        )
    ]

    summary = record_llm_requested_text_evidence(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom,
    )

    assert summary == "텍스트 evidence 2개 블록 수집"
    block = build_text_evidence_memory_block(agent)
    assert "누적 텍스트 evidence" in block
    assert "AI 반도체 투자 확대" in block
    assert "우주 발사체 시험 성공" in block
    assert "focus=제목, 언론사, 시간, 요약" in block


def test_goal_completion_judge_receives_accumulated_text_evidence() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "누적 텍스트 evidence에 최신 댓글 3개 본문이 있어 목표 증거가 충분합니다.",
  "confidence": 0.88
}
""".strip()
    agent._text_evidence_memory = [
        {
            "snapshot_id": "openclaw:test:7",
            "url": "https://shop.example.test/opinion",
            "reason": "댓글 목록 수집",
            "focus": ["댓글 본문", "소음"],
            "lines": [
                "댓글1: 흡입력은 좋은데 소음이 조금 큽니다.",
                "댓글2: 설치가 쉽고 앱 연결이 빠릅니다.",
                "댓글3: 밤에는 시끄럽다 느낄 수 있습니다.",
            ],
        }
    ]
    goal = SimpleNamespace(
        name="최근 댓글 3개에서 소음 불만 개수 확인",
        description="상품 의견 게시판의 최근 댓글 3개를 읽고 소음/시끄럽다 불만을 센다.",
        success_criteria=["최근 댓글 3개 확인", "소음/시끄럽다 포함 불만 개수"],
        expected_signals=[],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning="최근 댓글 3개의 텍스트 evidence를 수집했습니다.",
        confidence=0.9,
        is_goal_achieved=True,
        goal_achievement_reason="최근 댓글 3개에서 소음 관련 불만 2건 확인",
    )
    dom = [DOMElement(id=1, tag="h3", role="heading", text="상품 의견", is_visible=True)]

    reason = evaluate_goal_completion_judge(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom,
    )

    assert reason == "누적 텍스트 evidence에 최신 댓글 3개 본문이 있어 목표 증거가 충분합니다."
    assert "누적 텍스트 evidence" in agent._last_judge_prompt
    assert "댓글1: 흡입력은 좋은데 소음이 조금 큽니다." in agent._last_judge_prompt


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
        DOMElement(
            id=3,
            tag="pre",
            role="generic",
            text='{"rCode":"RET9999","rMessage":"시스템 오류 발생"}',
            is_visible=True,
            is_enabled=True,
        ),
    ]

    assert evaluate_reasoning_only_wait_completion(agent, goal=goal, decision=decision, dom_elements=dom) is None
    assert not hasattr(agent, "_last_judge_prompt")


def test_detect_service_unavailable_state_marks_ret9999_as_hard() -> None:
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=1,
            tag="pre",
            role="generic",
            text='{"rCode":"RET9999","rMessage":"시스템 오류 발생"}',
            is_visible=True,
            is_enabled=True,
        )
    ]

    state = detect_service_unavailable_state(agent, dom)

    assert state is not None
    assert state["hard"] is True
    assert state["matched"] in {"ret9999", "시스템 오류 발생"}


def test_detect_service_unavailable_state_marks_delay_notice_as_soft() -> None:
    agent = _FakeAgent()
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

    state = detect_service_unavailable_state(agent, dom)

    assert state is not None
    assert state["hard"] is False
    assert state["matched"] == "서비스 지연 안내"


def test_detect_service_unavailable_state_marks_not_found_surface_as_hard() -> None:
    agent = _FakeAgent()
    dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text="페이지를 찾을 수 없습니다",
            is_visible=True,
            is_enabled=True,
        )
    ]

    state = detect_service_unavailable_state(agent, dom)

    assert state is not None
    assert state["hard"] is True
    assert state["matched"] == "페이지를 찾을 수 없습니다"


def test_detect_service_unavailable_state_uses_snapshot_text_when_dom_empty() -> None:
    agent = _FakeAgent()
    agent._last_snapshot_evidence = {
        "text_digest": "확인 중... CLOUDFLARE 개인 정보 도움말",
        "live_texts": [],
    }

    state = detect_service_unavailable_state(agent, [])

    assert state is not None
    assert state["hard"] is True
    assert state["matched"] == "cloudflare"


def test_detect_service_unavailable_state_uses_inspection_text_when_dom_empty() -> None:
    agent = _FakeAgent()
    agent._last_exec_result = SimpleNamespace(
        state_change={
            "inspection": {
                "title": "Just a moment...",
                "bodyText": "확인 중... Cloudflare 개인 정보 도움말",
                "frames": [],
            },
            "inspection_summary": "title: Just a moment... text: 확인 중... Cloudflare",
        }
    )

    state = detect_service_unavailable_state(agent, [])

    assert state is not None
    assert state["hard"] is True
    assert state["matched"] == "cloudflare"


def test_dead_navigation_recovery_rewinds_to_start_url_and_records_feedback() -> None:
    agent = object.__new__(GoalDrivenAgent)
    agent._active_url = "https://example.test/404"
    agent._action_feedback = []
    agent._dead_navigation_recovery_count = 0
    agent._last_action_decision = ActionDecision(
        action=ActionType.CLICK,
        element_id=7,
        reasoning="open listing section",
    )
    agent._last_action_selected_element = DOMElement(
        id=7,
        tag="a",
        text="매물",
        href="https://broken.example.test/complexes",
    )
    calls: list[tuple[str, str | None, str | None]] = []
    reason_codes: list[str] = []

    def _execute_action(action: str, url: str | None = None, value: str | None = None) -> SimpleNamespace:
        calls.append((action, url, value))
        return SimpleNamespace(success=True, state_change={"evaluate_result": {"wentBack": True}})

    agent._execute_action = _execute_action  # type: ignore[method-assign]
    agent._record_reason_code = lambda code: reason_codes.append(str(code or ""))  # type: ignore[method-assign]
    agent._log = lambda message: None  # type: ignore[method-assign]
    goal = SimpleNamespace(start_url="https://example.test/start")

    recovered = agent._maybe_recover_dead_navigation(
        goal=goal,  # type: ignore[arg-type]
        service_state={
            "matched": "페이지를 찾을 수 없습니다",
            "reason": "외부 서비스 오류/차단 화면이 표시되었습니다: 페이지를 찾을 수 없습니다",
        },
    )

    assert recovered is True
    assert len(calls) == 1
    assert calls[0][0] == "evaluate"
    assert reason_codes == ["dead_navigation_recovered"]
    assert agent._dead_navigation_recovery_count == 1
    assert any("dead_target=매물" in item for item in agent._action_feedback)


def test_dead_navigation_recovery_ignores_initial_start_url_failure_without_action() -> None:
    agent = object.__new__(GoalDrivenAgent)
    agent._active_url = "https://example.test/404"
    agent._action_feedback = []
    agent._dead_navigation_recovery_count = 0
    agent._last_action_decision = None
    agent._last_action_selected_element = None
    agent._execute_action = lambda action, url=None: (_ for _ in ()).throw(AssertionError("unexpected recovery"))  # type: ignore[method-assign]
    goal = SimpleNamespace(start_url="https://example.test/start")

    recovered = agent._maybe_recover_dead_navigation(
        goal=goal,  # type: ignore[arg-type]
        service_state={
            "matched": "페이지를 찾을 수 없습니다",
            "reason": "외부 서비스 오류/차단 화면이 표시되었습니다: 페이지를 찾을 수 없습니다",
        },
    )

    assert recovered is False


def test_dead_navigation_recovery_marks_search_submit_path_as_dead() -> None:
    agent = object.__new__(GoalDrivenAgent)
    agent._active_url = "https://example.test/404"
    agent._action_feedback = []
    agent._dead_navigation_recovery_count = 0
    agent._last_action_decision = ActionDecision(
        action=ActionType.PRESS,
        element_id=3,
        value="Enter",
        reasoning="submit search",
    )
    agent._last_action_selected_element = DOMElement(
        id=3,
        tag="input",
        text="",
        placeholder="검색",
    )
    agent._execute_action = (  # type: ignore[method-assign]
        lambda action, url=None, value=None: SimpleNamespace(
            success=True,
            state_change={"evaluate_result": {"wentBack": True}},
        )
    )
    agent._record_reason_code = lambda code: None  # type: ignore[method-assign]
    agent._log = lambda message: None  # type: ignore[method-assign]
    goal = SimpleNamespace(start_url="https://example.test/start")

    recovered = agent._maybe_recover_dead_navigation(
        goal=goal,  # type: ignore[arg-type]
        service_state={
            "matched": "page not found",
            "reason": "external page not found",
        },
    )

    assert recovered is True
    assert any("검색 입력/검색 버튼/Enter submit 경로도 dead navigation" in item for item in agent._action_feedback)


def test_filter_result_surface_completion_accepts_visible_region_filter_and_count() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="부동산 필터 확인",
        description=(
            "검색결과에서 인천시 연수구 송도동을 클릭해줘. "
            "이후 빌라주택을 눌렀을 때 빌라, 주택 필터의 결과만 나타나는지 확인해줘."
        ),
        expected_signals=[
            "부동산",
            "매물",
            "인천시 연수구 송도동",
            "빌라주택",
            "빌라",
            "주택",
        ],
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            text="인천시 > 연수구 > 송도동",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="a",
            text="빌라·주택",
            class_name="selected",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="p",
            text="‘송도동’ 매물건수 매매 0 | 전세 0 | 월세 0",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_filter_result_surface_completion(agent, goal=goal, dom_elements=dom)  # type: ignore[arg-type]

    assert reason is not None
    assert "필터 결과 확인 목표" in reason


def test_filter_result_surface_completion_rejects_region_option_before_committed_summary() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="부동산 필터 확인",
        description=(
            "검색결과에서 인천시 연수구 송도동을 클릭해줘. "
            "이후 빌라주택을 눌렀을 때 빌라, 주택 필터의 결과만 나타나는지 확인해줘."
        ),
        expected_signals=[
            "부동산",
            "매물",
            "인천시 연수구 송도동",
            "빌라주택",
            "빌라",
            "주택",
        ],
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            text="인천시 > 연수구 > 읍/면/동 동춘동 선학동 송도동 연수동",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="a",
            text="빌라·주택",
            class_name="selected",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="p",
            text="송도동 (0) 연수동 (0) 옥련동 (0) ‘연수구’ 매물건수 매매 0 | 전세 0 | 월세 0",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    assert evaluate_filter_result_surface_completion(agent, goal=goal, dom_elements=dom) is None  # type: ignore[arg-type]


def test_wait_completion_defers_readonly_map_route_panel_claim_to_judge() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: []  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 카카오맵 길찾기 패널의 출발지와 도착지 입력 영역이 직접 보입니다.",
  "confidence": 0.91
}
""".strip()
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

    assert evaluate_wait_goal_completion(agent, goal=goal, decision=decision, dom_elements=dom) is None

    reason = evaluate_reasoning_only_wait_completion(agent, goal=goal, decision=decision, dom_elements=dom)
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


def test_validate_goal_achievement_claim_accepts_wait_via_judge_for_result_quote() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_quoted_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_target_terms = lambda goal: ["안녕 뭐해?"]  # type: ignore[method-assign]
    agent._goal_destination_terms = lambda goal: []  # type: ignore[method-assign]
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "입력과 구분되는 응답 본문이 현재 화면에 표시됩니다.",
  "confidence": 0.9
}
""".strip()
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


def test_reasoning_only_wait_completion_accepts_past_showtime_unavailable_evidence() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_semantics = SimpleNamespace(mutate_required=True)
    agent._judge_response = ""
    goal = SimpleNamespace(
        name="메가박스 송도 지난 상영시간 비활성 확인",
        description="예매 화면에서 5월 19일 과거 날짜의 지난 상영시간이 클릭되지 않는지 확인한다.",
        success_criteria=["과거 날짜 또는 지난 상영시간이 선택 불가/disabled임을 확인"],
        expected_signals=["메가박스 송도", "예매", "5월 19일", "상영시간", "클릭되지 않음"],
    )
    decision = ActionDecision(
        action=ActionType.WAIT,
        reasoning=(
            "현재 화면은 메가박스 송도 예매 탭이며 5.30(오늘)부터 날짜가 시작되고 "
            "이전 버튼은 disabled라 5월 19일로 이동할 수 없습니다. "
            "지난 상영시간도 비활성화되어 클릭되지 않음이 확인됩니다."
        ),
        confidence=0.75,
        is_goal_achieved=False,
    )
    dom = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="메가박스 송도",
            context_text="장소 상세",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="tab",
            text="메가박스 송도 예매",
            context_text="예매 탭 선택됨",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="이전",
            context_text="날짜 이전 disabled",
            is_visible=True,
            is_enabled=False,
        ),
        DOMElement(
            id=4,
            tag="button",
            role="button",
            text="11:00",
            context_text="상영시간 disabled",
            is_visible=True,
            is_enabled=False,
        ),
    ]

    reason = evaluate_reasoning_only_wait_completion(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom,
    )

    assert reason == "현재 화면에서 목표 조건의 시간/옵션이 disabled 상태로 표시되어 클릭되지 않음을 확인했습니다."


def test_reasoning_only_inspect_completion_accepts_past_showtime_unavailable_evidence() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._goal_semantics = SimpleNamespace(mutate_required=True)
    agent._judge_response = ""
    goal = SimpleNamespace(
        name="메가박스 송도 지난 상영시간 비활성 확인",
        description="예매 화면에서 5월 19일 과거 날짜의 지난 상영시간이 클릭되지 않는지 확인한다.",
        success_criteria=["과거 날짜 또는 지난 상영시간이 선택 불가/disabled임을 확인"],
        expected_signals=["메가박스 송도", "예매", "5월 19일", "상영시간", "클릭되지 않음"],
    )
    decision = ActionDecision(
        action=ActionType.INSPECT,
        reasoning=(
            "현재 화면에서 목표 검증에 필요한 증거가 모두 확인됩니다. "
            "이전 버튼은 disabled이고 지난 상영시간도 비활성화되어 클릭되지 않습니다."
        ),
        confidence=0.75,
        is_goal_achieved=False,
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="tab",
            text="메가박스 송도 예매",
            context_text="예매 탭 선택됨",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="이전",
            context_text="날짜 이전 disabled",
            is_visible=True,
            is_enabled=False,
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="11:00",
            context_text="상영시간 disabled",
            is_visible=True,
            is_enabled=False,
        ),
    ]

    reason = evaluate_reasoning_only_wait_completion(
        agent,
        goal=goal,
        decision=decision,
        dom_elements=dom,
    )

    assert reason == "현재 화면에서 목표 조건의 시간/옵션이 disabled 상태로 표시되어 클릭되지 않음을 확인했습니다."


def test_target_completion_accepts_recent_not_actionable_for_disabled_verification_goal() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    agent._action_feedback = [
        '[not_actionable] Error: Element "f9e212" not found or not visible. Run a new snapshot.'
    ]
    goal = SimpleNamespace(
        name="메가박스 송도 지난 상영시간 비활성 확인",
        description="예매 화면에서 5월 19일 과거 날짜의 지난 상영시간이 클릭되지 않는지 확인한다.",
        success_criteria=["과거 날짜 또는 지난 상영시간이 선택 불가/disabled임을 확인"],
        expected_signals=["메가박스 송도", "예매", "5월 19일", "상영시간", "클릭되지 않음"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="tab",
            text="예매",
            context_text="예매 탭 선택됨",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="날짜 오늘",
            context_text="상영시간 11:00 13:40 16:15",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason == "최근 목표 조건의 비활성/선택 불가 대상 클릭이 not_actionable으로 실패했고 현재 검증 화면이 유지되어 클릭되지 않음을 확인했습니다."


def test_target_completion_accepts_past_date_outside_visible_booking_range() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="메가박스 송도 지난 상영시간 비활성 확인",
        description="예매 화면에서 5월 19일 과거 날짜의 지난 상영시간이 클릭되지 않는지 확인한다.",
        success_criteria=["과거 날짜 또는 지난 상영시간이 선택 불가/disabled임을 확인"],
        expected_signals=["메가박스 송도", "예매", "5월 19일", "상영시간", "클릭되지 않음"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="tab",
            text="메가박스 송도 예매",
            context_text="예매 탭 선택됨",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="5.30 오늘 5.31 6.1",
            context_text="날짜 선택 상영시간",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason == "현재 날짜 선택 UI가 목표 과거 날짜 이후 범위만 제공해 목표 날짜 선택지가 클릭되지 않음을 확인했습니다."


def test_target_completion_rejects_past_date_range_without_goal_anchor() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="메가박스 송도 지난 상영시간 비활성 확인",
        description="예매 화면에서 5월 19일 과거 날짜의 지난 상영시간이 클릭되지 않는지 확인한다.",
        success_criteria=["과거 날짜 또는 지난 상영시간이 선택 불가/disabled임을 확인"],
        expected_signals=["메가박스 송도", "예매", "5월 19일", "상영시간", "클릭되지 않음"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="네이버지도 검색",
            context_text="5.30 오늘 날짜",
            is_visible=True,
            is_enabled=True,
        )
    ]

    assert evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom) is None


def test_target_completion_rejects_past_date_range_before_booking_surface() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {}
    goal = SimpleNamespace(
        name="메가박스 송도 지난 상영시간 비활성 확인",
        description="예매 화면에서 5월 19일 과거 날짜의 지난 상영시간이 클릭되지 않는지 확인한다.",
        success_criteria=["과거 날짜 또는 지난 상영시간이 선택 불가/disabled임을 확인"],
        expected_signals=["메가박스 송도", "예매", "5월 19일", "상영시간", "클릭되지 않음"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="input",
            role="combobox",
            text="메가박스 송도",
            context_text="지도 검색창 5.30 오늘",
            is_visible=True,
            is_enabled=True,
        )
    ]

    assert evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom) is None
