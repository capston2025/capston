from gaia.src.phase4.goal_driven.goal_completion_helpers import evaluate_goal_target_completion
from gaia.src.phase4.goal_driven.deterministic_goal_preplan import build_deterministic_goal_preplan
from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent
from gaia.src.phase4.goal_driven.models import ActionType, DOMElement, TestGoal


class _CompletionAgent:
    def __init__(self) -> None:
        self._goal_constraints = {
            "mutation_direction": "increase",
            "require_no_navigation": True,
            "current_view_only": True,
        }
        self._goal_semantics = None
        self._last_snapshot_evidence = {}

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _run_goal_policy_closer(*, goal, dom_elements):
        return None

    @staticmethod
    def _goal_destination_terms(goal) -> list[str]:
        return []

    @staticmethod
    def _goal_target_terms(goal) -> list[str]:
        return ["Apple", "Store"]

    @staticmethod
    def _goal_quoted_terms(goal) -> list[str]:
        return []

    @staticmethod
    def _goal_text_blob(goal) -> str:
        return " ".join(
            [
                str(getattr(goal, "name", "") or ""),
                str(getattr(goal, "description", "") or ""),
                " ".join(str(item or "") for item in (getattr(goal, "success_criteria", None) or [])),
            ]
        )

    @staticmethod
    def _estimate_goal_metric_from_dom(dom_elements):
        return None


def test_goal_target_completion_skips_readonly_visibility_goal() -> None:
    agent = _CompletionAgent()
    goal = TestGoal(
        id="readonly-1",
        name="현재 Apple Store 홈 화면 확인",
        description="현재 Apple Store 홈 화면에서 iPhone 링크가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
        expected_signals=["text_visible", "cta_visible"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="iPhone",
            aria_label="iPhone",
            context_text="Apple Store 홈",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None


def test_goal_target_completion_skips_multi_user_shortcut() -> None:
    agent = _CompletionAgent()
    agent._participant_registry = type("Registry", (), {"is_multi": lambda self: True})()
    goal = TestGoal(
        id="multi-user-1",
        name="두 사용자가 채팅 왕복",
        description="sender와 receiver가 같은 방에서 메시지를 주고받는지 확인",
        success_criteria=["Apple", "Store"],
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="Apple Store",
            context_text="채팅 transcript",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None


def test_goal_constraints_do_not_infer_numeric_collect_contract_from_goal_text() -> None:
    query = (
        "네이버 메일 받은메일함 화면에서 새 메일을 실제로 전송한다. "
        "메일 작성 버튼을 눌러 받는 사람 jangboss02@gmail.com, 제목 테스트, 본문 '테스트다 이눔아'를 입력한 뒤 발송 버튼을 누른다. "
        "전송 완료 안내가 보이거나 보낸메일함에서 같은 수신자와 제목의 메일이 확인될 때만 성공으로 판정한다. "
        "추가 인증이 뜨면 우회하지 말고 실패 상태와 화면 근거를 기록한다."
    )
    goal = TestGoal(
        id="mail-send-constraints",
        name="네이버 메일 실제 발송",
        description=query,
        success_criteria=[
            "전송 완료 안내가 보인다.",
            "또는 보낸메일함에서 수신자 jangboss02@gmail.com, 제목 테스트의 발송 메일이 보인다.",
        ],
    )

    constraints = GoalDrivenAgent._derive_goal_constraints(goal)

    assert constraints.get("collect_min") is None
    assert constraints.get("apply_target") is None
    assert constraints.get("metric_label") != "jangboss"
    assert constraints.get("mutation_direction") != "increase"


def test_goal_constraints_do_not_promote_ranking_counts_to_collect_min() -> None:
    goal = TestGoal(
        id="ranking-count",
        name="상위 팀 순위 확인",
        description="순위표 영역으로 이동하고 상위 3개 팀의 순위 정보가 정상적으로 표시되는지 확인한다.",
        success_criteria=["상위 3개 팀의 순위 정보가 표시된다."],
    )

    constraints = GoalDrivenAgent._derive_goal_constraints(goal)

    assert constraints.get("collect_min") is None
    assert constraints.get("metric_label") is None


def test_goal_constraints_do_not_treat_today_phrase_as_increase_mutation() -> None:
    goal = TestGoal(
        id="wiki-portal-readonly",
        name="사용자 모임 경유 K-pop 포털 정보 확인",
        description=(
            "위키백과에서 사용자 모임을 클릭한 뒤, 포털 영역에서 K-pop을 선택했을 때 "
            "오늘의 아티스트나 오늘의 그림 같은 정보가 화면에 나타나는지 확인해줘."
        ),
        success_criteria=[
            "오늘의 아티스트나 오늘의 그림 같은 정보가 화면에 나타난다.",
        ],
    )

    constraints = GoalDrivenAgent._derive_goal_constraints(goal)

    assert constraints.get("mutation_direction") is None


def test_goal_constraints_do_not_infer_mutation_direction_from_free_text() -> None:
    goal = TestGoal(
        id="free-text-mutation",
        name="위시리스트 담기 확인",
        description="첫 번째 상품을 위시리스트에 담아 정상적으로 추가되는지 확인해줘.",
        success_criteria=["위시리스트에 상품이 추가된다."],
    )

    constraints = GoalDrivenAgent._derive_goal_constraints(goal)

    assert constraints.get("mutation_direction") is None
    assert constraints.get("mutate_required") is None


def test_explicit_goal_constraints_are_preserved_from_test_data() -> None:
    goal = TestGoal(
        id="explicit-mutation-contract",
        name="명시 계약 기반 위시리스트 담기",
        description="첫 번째 상품을 위시리스트에 담는다.",
        success_criteria=["위시리스트에 상품이 추가된다."],
        test_data={
            "goal_constraints": {
                "mutation_direction": "increase",
                "mutate_required": True,
                "destination_terms": ["위시리스트"],
            }
        },
    )

    constraints = GoalDrivenAgent._derive_goal_constraints(goal)

    assert constraints["mutation_direction"] == "increase"
    assert constraints["mutate_required"] is True
    assert constraints["destination_terms"] == ["위시리스트"]


def test_goal_target_completion_does_not_shortcut_readonly_portal_link_visibility() -> None:
    agent = _CompletionAgent()
    goal = TestGoal(
        id="wiki-portal-readonly",
        name="사용자 모임 경유 K-pop 포털 정보 확인",
        description=(
            "위키백과에서 사용자 모임을 클릭한 뒤, 포털 영역에서 K-pop을 선택했을 때 "
            "오늘의 아티스트나 오늘의 그림 같은 정보가 화면에 나타나는지 확인해줘."
        ),
        success_criteria=[
            "오늘의 아티스트나 오늘의 그림 같은 정보가 화면에 나타난다.",
        ],
    )
    agent._goal_constraints = GoalDrivenAgent._derive_goal_constraints(goal)
    agent._goal_target_terms = lambda goal: ["K-pop"]  # type: ignore[method-assign]
    dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="K-pop",
            aria_label="K-pop",
            context_text="포털 목록",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None


def test_goal_target_completion_does_not_shortcut_explicit_mutation_contract() -> None:
    agent = _CompletionAgent()
    agent._goal_constraints = {
        "mutation_direction": "increase",
        "mutate_required": True,
        "target_terms": ["첫 번째 상품"],
    }
    agent._goal_target_terms = lambda goal: ["첫 번째 상품"]  # type: ignore[method-assign]
    goal = TestGoal(
        id="explicit-mutation-no-shortcut",
        name="첫 번째 상품 위시리스트 담기",
        description="첫 번째 상품을 위시리스트에 담고 반영 여부를 확인한다.",
        success_criteria=["위시리스트에 첫 번째 상품이 추가된다."],
    )
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="첫 번째 상품",
            context_text="상품 목록",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None


def test_deterministic_goal_preplan_requires_explicit_opt_in() -> None:
    class _PreplanAgent:
        _goal_constraints = {}
        _element_full_selectors = {}
        _element_selectors = {}
        _locate_target_search_consumed = False

        @staticmethod
        def _normalize_text(value: object) -> str:
            return str(value or "").strip().lower()

        @staticmethod
        def _extract_goal_query_tokens(goal: TestGoal) -> list[str]:
            return ["챗GPT 사용법"]

    dom = [
        DOMElement(
            id=1,
            tag="input",
            role="textbox",
            type="search",
            placeholder="검색어를 입력하세요",
            is_visible=True,
            is_enabled=True,
        )
    ]
    goal = TestGoal(
        id="search-no-preplan",
        name="네이버에서 챗GPT 사용법 검색",
        description="네이버에서 챗GPT 사용법을 검색해줘.",
        success_criteria=["검색 결과가 보인다."],
    )

    assert build_deterministic_goal_preplan(_PreplanAgent(), goal=goal, dom_elements=dom) is None

    opt_in_goal = goal.model_copy(update={"test_data": {"allow_deterministic_preplan": True}})
    decision = build_deterministic_goal_preplan(_PreplanAgent(), goal=opt_in_goal, dom_elements=dom)

    assert decision is not None
    assert decision.action == ActionType.FILL
    assert decision.value == "챗GPT 사용법"


def test_goal_constraints_ignore_forbidden_purchase_or_cart_actions() -> None:
    goal = TestGoal(
        id="shopping-readonly",
        name="네이버 쇼핑 검색 결과 검증",
        description=(
            "네이버에 로그인한 상태에서 네이버 쇼핑으로 이동해 '노트북 파우치 13인치'를 검색한다. "
            "검색 결과에서 필터 또는 정렬 affordance를 활용해 결과가 바뀌는지 확인하고, "
            "상위 3개 상품 카드의 상품명, 가격, 판매처 또는 배송 정보가 정상 표시되는지 검증한다. "
            "장바구니 담기, 구매, 결제, 유료 예약, 개인정보 변경은 절대 하지 마."
        ),
        success_criteria=[
            "상위 3개 상품 카드의 상품명, 가격, 판매처 또는 배송 정보가 표시된다.",
        ],
    )

    constraints = GoalDrivenAgent._derive_goal_constraints(goal)

    assert constraints.get("mutation_direction") is None
    assert "네이버에" not in constraints.get("target_terms", [])


def test_goal_target_completion_skips_filter_control_visibility_shortcut() -> None:
    agent = _CompletionAgent()
    agent._goal_target_terms = lambda goal: ["N배송 빠르게 받기", "빠른배송 전체"]  # type: ignore[method-assign]
    goal = TestGoal(
        id="shipping-filter-edge",
        name="N배송/빠른배송 필터 검증",
        description=(
            "화면에 보이는 'N배송 빠르게 받기' 또는 '빠른배송 전체' 버튼을 선택해 "
            "결과가 배송 유형 기준으로 바뀌는지 확인하고, 상위 상품 카드에 배송 정보가 표시되는지 검증한다."
        ),
        success_criteria=[
            "배송유형 필터 선택 상태가 보인다.",
            "상위 카드에 빠른배송/N배송/오늘출발/도착 예정 중 하나가 표시된다.",
        ],
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="N배송 빠르게 받기",
            context_text="배송 필터",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None


def test_goal_target_completion_skips_explicit_mail_send_submission_shortcut() -> None:
    agent = _CompletionAgent()
    agent._goal_constraints = {
        "mutation_direction": "increase",
        "collect_min": 2,
        "metric_label": "jangboss",
    }
    agent._goal_target_terms = lambda goal: ["받은메일함"]  # type: ignore[method-assign]
    agent._estimate_goal_metric_from_dom = lambda dom_elements: 2  # type: ignore[method-assign]
    goal = TestGoal(
        id="mail-send-shortcut",
        name="네이버 메일 실제 발송",
        description=(
            "네이버 메일 받은메일함 화면에서 새 메일을 실제로 전송한다. "
            "메일 작성 버튼을 눌러 받는 사람 jangboss02@gmail.com, 제목 테스트, 본문 '테스트다 이눔아'를 입력한 뒤 발송 버튼을 누른다."
        ),
        success_criteria=[
            "전송 완료 안내가 보인다.",
            "또는 보낸메일함에서 수신자 jangboss02@gmail.com, 제목 테스트의 발송 메일이 보인다.",
        ],
    )
    dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="받은메일함",
            aria_label="받은메일함",
            context_text="jangboss02@gmail.com 제목 테스트",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = evaluate_goal_target_completion(agent, goal=goal, dom_elements=dom)

    assert reason is None
