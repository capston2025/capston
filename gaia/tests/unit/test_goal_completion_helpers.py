from gaia.src.phase4.goal_driven.goal_completion_helpers import evaluate_goal_target_completion
from gaia.src.phase4.goal_driven.agent import GoalDrivenAgent
from gaia.src.phase4.goal_driven.models import DOMElement, TestGoal


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
