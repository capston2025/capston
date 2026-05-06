from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.execute_goal_progress import (
    evaluate_post_action_progress,
    _evaluate_post_action_judge_completion,
    _prime_dom_cache_from_backend_snapshot,
)
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_constraints = {"mutation_direction": "clear"}
        self._goal_semantics = SimpleNamespace(goal_kind="clear_list", mutate_required=True)
        self._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "현재 화면의 zero-state가 직접 보여 목표가 완료되었습니다.",
  "confidence": 0.95
}
""".strip()
        self._persistent_state_memory = []
        self._action_history = []
        self._action_feedback = []
        self._last_exec_result = None

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _goal_quoted_terms(_goal: object) -> list[str]:
        return []

    @staticmethod
    def _goal_target_terms(_goal: object) -> list[str]:
        return ["위시리스트"]

    @staticmethod
    def _goal_destination_terms(_goal: object) -> list[str]:
        return []

    def _call_llm_text_only(self, _prompt: str) -> str:
        return self._judge_response

    def _format_dom_for_llm(self, elements: list[DOMElement]) -> str:
        return "\n".join(str(getattr(item, "text", "") or "") for item in elements)


def test_post_action_judge_completion_uses_judge_for_changed_clear_flow() -> None:
    agent = _FakeAgent()
    goal = SimpleNamespace(
        name="위시리스트 비우기",
        description="모든 담은 과목을 비운 뒤 빈 상태를 확인",
        success_criteria=["빈 상태 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e949",
        reasoning="마지막 삭제 버튼을 눌렀습니다.",
        confidence=0.88,
        is_goal_achieved=False,
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="div",
            role="status",
            text="담은 과목이 없어요.",
            context_text="empty state",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = _evaluate_post_action_judge_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        post_dom=post_dom,
    )

    assert reason == "현재 화면의 zero-state가 직접 보여 목표가 완료되었습니다."


def test_post_action_judge_completion_runs_for_readonly_navigation_flow() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": ""}
    agent._goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "newest 페이지의 최신 글 목록이 현재 DOM에 직접 보여 목표가 완료되었습니다.",
  "confidence": 0.93
}
""".strip()
    goal = SimpleNamespace(
        name="Hacker News newest 이동",
        description="Hacker News 홈 화면에서 newest 페이지로 이동하고 최신 글 목록이 보이는지 확인해줘.",
        success_criteria=["newest 페이지의 최신 글 목록 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e17",
        reasoning="상단 new 링크를 클릭했습니다.",
        confidence=0.86,
        is_goal_achieved=False,
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="a",
            role="link",
            text="new",
            context_text="Hacker News | new | past | comments",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="tr",
            role="row",
            text="1. Example story 0 minutes ago",
            context_text="newest stories list",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = _evaluate_post_action_judge_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        post_dom=post_dom,
    )

    assert reason == "newest 페이지의 최신 글 목록이 현재 DOM에 직접 보여 목표가 완료되었습니다."


def test_post_action_judge_completion_skips_high_risk_login_navigation_flow() -> None:
    class _NoJudgeAgent(_FakeAgent):
        def _call_llm_text_only(self, _prompt: str) -> str:
            raise AssertionError("login navigation should not use post-action completion judge")

    agent = _NoJudgeAgent()
    agent._goal_constraints = {"mutation_direction": ""}
    agent._goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
    goal = SimpleNamespace(
        name="로그인 페이지 이동",
        description="로그인 페이지로 이동하고 로그인 폼이 보이는지 확인해줘.",
        success_criteria=["로그인 폼 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="login-link",
        reasoning="로그인 링크를 클릭했습니다.",
        confidence=0.84,
        is_goal_achieved=False,
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="input",
            role="textbox",
            text="",
            aria_label="username",
            context_text="로그인 폼",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = _evaluate_post_action_judge_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        post_dom=post_dom,
    )

    assert reason is None


def test_post_action_judge_completion_runs_for_readonly_search_result_flow() -> None:
    agent = _FakeAgent()
    agent._goal_constraints = {"mutation_direction": ""}
    agent._goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
    agent._judge_response = """
{
  "success": true,
  "blocked": false,
  "reason": "requests 검색 결과 목록이 현재 DOM에 직접 보여 목표가 완료되었습니다.",
  "confidence": 0.94
}
""".strip()
    goal = SimpleNamespace(
        name="PyPI requests 검색",
        description="PyPI 검색에서 requests를 찾아 검색 결과 목록이 보이는지 확인해줘.",
        success_criteria=["requests 검색 결과 목록 확인"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="search-submit",
        reasoning="검색 버튼을 클릭했습니다.",
        confidence=0.87,
        is_goal_achieved=False,
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="h1",
            role="heading",
            text='프로젝트 "requests"의 경우',
            context_text="검색결과",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="a",
            role="link",
            text="requests Python HTTP for Humans.",
            context_text="검색 결과 목록",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    reason = _evaluate_post_action_judge_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        post_dom=post_dom,
    )

    assert reason == "requests 검색 결과 목록이 현재 DOM에 직접 보여 목표가 완료되었습니다."


def test_post_action_judge_completion_skips_search_fill_before_submit() -> None:
    class _NoJudgeAgent(_FakeAgent):
        def _call_llm_text_only(self, _prompt: str) -> str:
            raise AssertionError("search fill should not use post-action completion judge")

    agent = _NoJudgeAgent()
    agent._goal_constraints = {"mutation_direction": ""}
    agent._goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
    goal = SimpleNamespace(
        name="PyPI requests 검색",
        description="PyPI 검색에서 requests를 찾아 검색 결과 목록이 보이는지 확인해줘.",
        success_criteria=["requests 검색 결과 목록 확인"],
    )
    decision = ActionDecision(
        action=ActionType.FILL,
        ref_id="search-input",
        value="requests",
        reasoning="검색어를 입력했습니다.",
        confidence=0.87,
        is_goal_achieved=False,
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="input",
            role="textbox",
            text="",
            value="requests",
            context_text="PyPI 검색",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = _evaluate_post_action_judge_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        changed=True,
        post_dom=post_dom,
    )

    assert reason is None


def test_prime_dom_cache_from_backend_snapshot_seeds_next_default_analyze() -> None:
    agent = SimpleNamespace(
        session_id="s1",
        _dom_cache_generation=3,
        _active_url="",
        _active_snapshot_id="",
        _active_dom_hash="",
        _active_snapshot_epoch=0,
        _active_scoped_container_ref="ctx-old",
        _last_context_snapshot={},
        _last_role_snapshot={},
        _last_snapshot_elements_by_ref={},
        _last_snapshot_evidence={},
        _last_container_source_summary={},
        _dom_analyze_cache={},
    )
    post_dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="적용",
            ref_id="e1",
            container_source="role_row",
            is_visible=True,
            is_enabled=True,
        )
    ]
    backend_snapshot = {
        "snapshot_id": "openclaw:s1:4",
        "dom_hash": "hash-4",
        "epoch": 4,
        "url": "https://example.com/app",
        "scope_applied": False,
        "context_snapshot": {"nodes": [{"ref_id": "e1"}]},
        "role_snapshot": {"snapshot": '- button "적용" [ref=e1]'},
        "elements_by_ref": {"e1": {"ref_id": "e1", "text": "적용"}},
        "evidence": {"text_digest": "적용 완료", "live_texts": ["적용 완료"]},
    }

    _prime_dom_cache_from_backend_snapshot(
        agent=agent,
        backend_snapshot=backend_snapshot,
        post_dom=post_dom,
    )

    assert agent._active_snapshot_id == "openclaw:s1:4"
    assert agent._active_url == "https://example.com/app"
    assert agent._last_snapshot_evidence["text_digest"] == "적용 완료"
    assert agent._last_container_source_summary == {"role_row": 1}
    assert agent._dom_analyze_cache["key"] == (3, "s1", "", "")
    assert agent._dom_analyze_cache["elements"] == post_dom


def test_prime_dom_cache_from_backend_snapshot_skips_scoped_payload() -> None:
    agent = SimpleNamespace(
        session_id="s1",
        _dom_cache_generation=3,
        _dom_analyze_cache={"key": (3, "s1", "", "")},
    )
    post_dom = [
        DOMElement(id=1, tag="button", role="button", text="스코프", is_visible=True, is_enabled=True)
    ]

    _prime_dom_cache_from_backend_snapshot(
        agent=agent,
        backend_snapshot={"snapshot_id": "openclaw:s1:4", "scope_applied": True},
        post_dom=post_dom,
    )

    assert agent._dom_analyze_cache == {"key": (3, "s1", "", "")}


def test_evaluate_post_action_progress_keeps_fresh_settle_probe_when_backend_snapshot_has_no_change(monkeypatch) -> None:
    monkeypatch.setattr("gaia.src.phase4.goal_driven.execute_goal_progress.time.sleep", lambda _seconds: None)
    before_dom = [
        DOMElement(id=1, tag="button", role="button", text="열기", is_visible=True, is_enabled=True)
    ]
    evidence = {
        "text_digest": "열기",
        "live_texts": ["열기"],
        "list_count": 1,
        "interactive_count": 1,
        "modal_count": 0,
        "backdrop_count": 0,
        "dialog_count": 0,
        "modal_open": False,
        "auth_prompt_visible": False,
        "login_visible": False,
        "logout_visible": False,
    }
    analyze_calls: list[str] = []

    class _ProgressAgent:
        session_id = "s1"
        _dom_cache_generation = 3
        _dom_analyze_cache: dict[str, object] = {}
        _goal_constraints: dict[str, object] = {}
        _goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
        _goal_policy_phase = ""
        _goal_phase_intent = ""
        _last_snapshot_evidence = dict(evidence)
        _last_backend_post_action_snapshot = {
            "snapshot_id": "openclaw:s1:4",
            "url": "https://example.com/app",
            "scope_applied": False,
            "dom_elements": [before_dom[0]],
            "evidence": dict(evidence),
        }
        _last_exec_result = SimpleNamespace(
            success=True,
            effective=True,
            reason_code="ok",
            state_change={
                "backend": "openclaw",
                "backend_progress": False,
                "effective": True,
            },
        )

        @staticmethod
        def _estimate_goal_metric_from_dom(_dom: list[DOMElement]) -> None:
            return None

        @staticmethod
        def _dom_progress_signature(dom: list[DOMElement]) -> tuple[str, ...]:
            return tuple(str(getattr(item, "text", "") or "") for item in dom)

        @staticmethod
        def _evaluate_goal_target_completion(*, goal: object, dom_elements: list[DOMElement]) -> None:
            return None

        @staticmethod
        def _record_reason_code(_code: str) -> None:
            return None

        @staticmethod
        def _log(_message: str) -> None:
            return None

        def _analyze_dom(self, scope_container_ref_id: str = "") -> list[DOMElement]:
            analyze_calls.append(scope_container_ref_id)
            self._last_snapshot_evidence = dict(evidence)
            return list(before_dom)

    agent = _ProgressAgent()

    result = evaluate_post_action_progress(
        agent=agent,
        goal=SimpleNamespace(id="g1", name="열기", description="", success_criteria=[], start_url=""),
        decision=ActionDecision(action=ActionType.CLICK, reasoning="열기 버튼 클릭", confidence=0.8),
        success=True,
        before_signature=("열기",),
        dom_elements=before_dom,
        step_count=1,
        steps=[],
        start_time=0.0,
    )

    assert result["changed"] is False
    assert analyze_calls == [""]
    assert agent._dom_analyze_cache == {}
