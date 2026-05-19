from __future__ import annotations

import json

from gaia.src.phase4.goal_driven.adaptive_qa_runtime import (
    ADAPTIVE_QA_MODE,
    DEEP_ADAPTIVE_QA_MODE,
    adaptive_qa_enabled,
    adaptive_qa_mode,
    build_edge_goal,
    generate_adaptive_qa_plan,
    summarize_adaptive_qa_report,
)
from gaia.src.phase4.goal_driven.models import DOMElement, GoalResult, TestGoal as GoalModel


class _AdaptiveAgent:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self._last_prompt = ""
        self._action_history = ["click 스포츠 필터", "click 축구 카테고리"]
        self._action_feedback = ["축구 뉴스 목록이 표시됨"]

    def _call_llm_text_only(self, prompt: str) -> str:
        self._last_prompt = prompt
        return "```json\n" + json.dumps(self._payload, ensure_ascii=False) + "\n```"

    @staticmethod
    def _format_dom_for_llm(elements: list[DOMElement]) -> str:
        return "\n".join(str(item.text or item.aria_label or "") for item in elements)


def _goal(test_data: dict | None = None) -> GoalModel:
    return GoalModel(
        id="G1",
        name="뉴스 축구 순위 확인",
        description="네이버 뉴스에서 스포츠 필터와 축구 카테고리를 선택한 뒤 상위 3개 팀 순위를 확인한다.",
        priority="MUST",
        test_data=test_data or {},
        success_criteria=["상위 3개 팀 순위가 보인다."],
        max_steps=20,
        start_url="https://news.naver.com/",
    )


def _result(success: bool = True) -> GoalResult:
    return GoalResult(
        goal_id="G1",
        goal_name="뉴스 축구 순위 확인",
        success=success,
        total_steps=4,
        final_reason="상위 3개 팀 순위가 화면에 표시됨" if success else "축구 카테고리 진입 실패",
    )


def test_adaptive_qa_enabled_from_explicit_config_and_mode_alias() -> None:
    assert adaptive_qa_enabled(_goal({ADAPTIVE_QA_MODE: {"enabled": True}})) is True
    assert adaptive_qa_enabled(_goal({"qa_mode": "progressive_qa"})) is True
    assert adaptive_qa_enabled(_goal({ADAPTIVE_QA_MODE: {"enabled": False}})) is False
    assert adaptive_qa_mode(_goal({DEEP_ADAPTIVE_QA_MODE: {"enabled": True}})) == DEEP_ADAPTIVE_QA_MODE
    assert adaptive_qa_mode(_goal({"qa_mode": "deep_qa"})) == DEEP_ADAPTIVE_QA_MODE


def test_generate_plan_parses_checks_and_filters_risky_edges() -> None:
    agent = _AdaptiveAgent(
        {
            "checks": [
                {
                    "id": "top_three_visible",
                    "title": "상위 3개 팀 노출",
                    "rationale": "사용자 목표의 핵심 검증이다.",
                    "evidence_hint": "순위표 첫 3행",
                }
            ],
            "edge_cases": [
                {
                    "id": "switch_sort",
                    "name": "정렬 전환 반영",
                    "description": "현재 순위 영역에서 다른 정렬 옵션을 선택해 목록이 갱신되는지 확인한다.",
                    "reason": "관찰 가능한 필터 확장",
                    "success_criteria": ["정렬 선택값과 목록 표시가 일치한다."],
                },
                {
                    "id": "paid_checkout",
                    "name": "결제 버튼 확인",
                    "description": "유료 결제 버튼을 눌러 결제 완료 상태까지 진행한다.",
                    "reason": "결제는 비용 발생 위험이 있다.",
                    "success_criteria": ["결제 완료"],
                },
                {
                    "id": "category_back",
                    "name": "카테고리 되돌림",
                    "description": "다른 축구 하위 카테고리를 선택했다가 원래 카테고리로 돌아와 순위표가 유지되는지 확인한다.",
                    "success_criteria": ["순위표가 다시 표시된다."],
                },
            ],
        }
    )
    dom = [DOMElement(id=1, tag="section", text="축구 순위표 1위 2위 3위")]

    plan = generate_adaptive_qa_plan(
        agent,
        goal=_goal({ADAPTIVE_QA_MODE: {"enabled": True, "max_edge_cases": 2}}),
        primary_result=_result(True),
        dom_elements=dom,
    )

    assert plan["status"] == "generated"
    assert [item["id"] for item in plan["checks"]] == ["top_three_visible"]
    assert [item["id"] for item in plan["edge_cases"]] == ["switch_sort", "category_back"]
    assert "현재 DOM" in agent._last_prompt


def test_generate_plan_allows_user_authorized_message_send_edge() -> None:
    agent = _AdaptiveAgent(
        {
            "checks": [{"id": "mail_sent_visible", "title": "메일 전송 결과 확인"}],
            "edge_cases": [
                {
                    "id": "send_test_mail",
                    "name": "메일 전송 확인",
                    "description": "사용자가 지정한 수신자에게 지정한 테스트 문구를 메일로 전송하고 전송 완료 문구가 보이는지 확인한다.",
                    "reason": "사용자가 메일 전송을 명시적으로 허용했다.",
                    "safety": "user_authorized_reversible_enough",
                    "success_criteria": ["전송 완료 문구가 보인다."],
                }
            ],
        }
    )

    plan = generate_adaptive_qa_plan(
        agent,
        goal=_goal({ADAPTIVE_QA_MODE: {"enabled": True, "max_edge_cases": 2}}),
        primary_result=_result(True),
        dom_elements=[],
    )

    assert [item["id"] for item in plan["edge_cases"]] == ["send_test_mail"]


def test_deep_adaptive_qa_generates_more_edge_cases_with_aggressive_prompt() -> None:
    agent = _AdaptiveAgent(
        {
            "checks": [{"id": "rank_table", "title": "순위표 표시"}],
            "edge_cases": [
                {
                    "id": f"safe_edge_{idx}",
                    "name": f"안전 엣지 {idx}",
                    "description": f"현재 화면에서 안전한 필터 전환 {idx}을 확인한다.",
                    "success_criteria": ["화면 증거가 보인다."],
                }
                for idx in range(1, 7)
            ],
        }
    )

    plan = generate_adaptive_qa_plan(
        agent,
        goal=_goal({DEEP_ADAPTIVE_QA_MODE: {"enabled": True}}),
        primary_result=_result(True),
        dom_elements=[],
    )

    assert plan["mode"] == DEEP_ADAPTIVE_QA_MODE
    assert len(plan["edge_cases"]) == 6
    assert "공격적 Deep QA 모드" in agent._last_prompt


def test_generate_plan_does_not_execute_edges_when_primary_failed() -> None:
    agent = _AdaptiveAgent(
        {
            "checks": [{"id": "sports_filter", "title": "스포츠 필터 진입"}],
            "edge_cases": [
                {
                    "id": "safe_filter",
                    "name": "필터 변경",
                    "description": "다른 필터를 선택해 상태 변화를 확인한다.",
                }
            ],
        }
    )

    plan = generate_adaptive_qa_plan(
        agent,
        goal=_goal({ADAPTIVE_QA_MODE: {"enabled": True}}),
        primary_result=_result(False),
        dom_elements=[],
    )

    assert plan["checks"][0]["id"] == "sports_filter"
    assert plan["edge_cases"] == []


def test_build_edge_goal_continues_current_page_and_disables_recursive_expansion() -> None:
    parent = _goal(
        {
            ADAPTIVE_QA_MODE: {"enabled": True},
            DEEP_ADAPTIVE_QA_MODE: {"enabled": True},
            "qa_mode": DEEP_ADAPTIVE_QA_MODE,
            "keep": "value",
        }
    )
    edge_goal = build_edge_goal(
        parent,
        {
            "name": "정렬 전환 반영",
            "description": "현재 순위 영역에서 다른 정렬 옵션을 선택해 목록이 갱신되는지 확인한다.",
            "success_criteria": ["목록이 갱신된다."],
        },
        index=1,
    )

    assert edge_goal.id == "G1_EDGE_1"
    assert edge_goal.start_url is None
    assert edge_goal.max_steps == 8
    assert edge_goal.test_data["keep"] == "value"
    assert ADAPTIVE_QA_MODE not in edge_goal.test_data
    assert DEEP_ADAPTIVE_QA_MODE not in edge_goal.test_data
    assert "qa_mode" not in edge_goal.test_data
    assert edge_goal.test_data["adaptive_qa_edge_case"] is True


def test_summary_scores_primary_and_edge_results() -> None:
    report = summarize_adaptive_qa_report(
        primary_goal=_goal(),
        primary_result=_result(True),
        plan={"checks": [{"id": "extra", "title": "추가 체크"}], "edge_cases": [{"id": "edge"}]},
        edge_results=[{"status": "PASS"}, {"status": "FAIL"}],
    )

    assert report["mode"] == ADAPTIVE_QA_MODE
    assert report["summary"]["generated_check_count"] == 1
    assert report["summary"]["executed_edge_case_count"] == 2
    assert report["summary"]["score"] == 0.667
