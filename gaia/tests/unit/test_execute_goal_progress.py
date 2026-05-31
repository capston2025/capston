from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.execute_goal_progress import (
    evaluate_post_action_progress,
    _evaluate_deferred_action_goal_completion,
    _evaluate_inspect_action_goal_completion,
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


def test_deferred_action_goal_completion_requires_successful_action() -> None:
    class _Agent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._action_feedback: list[str] = []
            self.validation_calls = 0

        def _validate_goal_achievement_claim(self, *, goal, decision, dom_elements):
            self.validation_calls += 1
            return True, None

    agent = _Agent()
    goal = SimpleNamespace(
        name="강의 재생",
        description="플레이어의 재생 버튼을 눌러 강의를 튼다.",
        success_criteria=["재생 버튼 클릭"],
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e22",
        reasoning="재생 버튼을 클릭한다.",
        confidence=0.91,
        is_goal_achieved=True,
        goal_achievement_reason="재생 버튼 클릭이 마지막 단계입니다.",
    )
    dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="일시정지",
            context_text="Video Player",
            is_visible=True,
            is_enabled=True,
        )
    ]

    reason = _evaluate_deferred_action_goal_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        post_dom=dom,
    )
    failed_reason = _evaluate_deferred_action_goal_completion(
        agent=agent,
        goal=goal,
        decision=decision,
        success=False,
        post_dom=dom,
    )

    assert reason == "재생 버튼 클릭이 마지막 단계입니다."
    assert failed_reason is None
    assert agent.validation_calls == 1


def test_inspect_action_completion_uses_inspection_summary_without_next_step() -> None:
    class _Agent:
        _last_exec_result = SimpleNamespace(
            state_change={
                "inspection_summary": "title: 순위표 | body: 상위 3개 팀 순위와 승점이 표시됨",
                "inspection": {"title": "순위표", "bodyText": "상위 3개 팀 순위와 승점이 표시됨"},
            }
        )

        def __init__(self) -> None:
            self.reasoning_seen = ""

        def _evaluate_reasoning_only_wait_completion(self, *, goal, decision, dom_elements):
            self.reasoning_seen = str(decision.reasoning or "")
            if "상위 3개 팀" in self.reasoning_seen:
                return "inspect 결과에서 상위 3개 팀 순위가 확인되어 완료"
            return None

    agent = _Agent()
    reason = _evaluate_inspect_action_goal_completion(
        agent=agent,
        goal=SimpleNamespace(name="순위표 확인", description="상위 3개 팀 순위 확인"),
        decision=ActionDecision(
            action=ActionType.INSPECT,
            reasoning="순위표 영역을 확인한다.",
            confidence=0.7,
        ),
        success=True,
        post_dom=[
            DOMElement(id=1, tag="table", role="table", text="팀 순위", is_visible=True, is_enabled=True)
        ],
    )

    assert reason == "inspect 결과에서 상위 3개 팀 순위가 확인되어 완료"
    assert "inspect 결과" in agent.reasoning_seen


def test_inspect_action_completion_skips_empty_inspection_summary() -> None:
    class _Agent:
        _last_exec_result = SimpleNamespace(state_change={})

        @staticmethod
        def _evaluate_reasoning_only_wait_completion(*, goal, decision, dom_elements):
            raise AssertionError("empty inspection must not ask completion judge")

    reason = _evaluate_inspect_action_goal_completion(
        agent=_Agent(),
        goal=SimpleNamespace(name="상태 확인", description=""),
        decision=ActionDecision(action=ActionType.INSPECT, reasoning="상태 확인", confidence=0.7),
        success=True,
        post_dom=[],
    )

    assert reason is None


def test_evaluate_post_action_progress_completes_deferred_action_after_execution() -> None:
    post_dom = [
        DOMElement(
            id=1,
            tag="button",
            role="button",
            text="일시정지",
            context_text="Video Player",
            is_visible=True,
            is_enabled=True,
        )
    ]

    class _Agent:
        session_id = "s1"
        _dom_cache_generation = 3
        _active_url = ""
        _active_snapshot_id = ""
        _active_dom_hash = ""
        _active_snapshot_epoch = 0
        _active_scoped_container_ref = ""
        _last_context_snapshot: dict[str, object] = {}
        _last_role_snapshot: dict[str, object] = {}
        _last_snapshot_elements_by_ref: dict[str, object] = {}
        _last_container_source_summary: dict[str, int] = {}
        _dom_analyze_cache: dict[str, object] = {}
        _goal_constraints: dict[str, object] = {}
        _goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
        _last_snapshot_evidence = {"text_digest": "재생"}
        _last_backend_post_action_snapshot = {
            "snapshot_id": "openclaw:s1:9",
            "url": "https://cyber.inu.ac.kr/mod/vod/viewer.php",
            "scope_applied": False,
            "dom_elements": [item.model_dump() for item in post_dom],
            "evidence": {"text_digest": "일시정지"},
        }
        _last_exec_result = SimpleNamespace(
            success=True,
            effective=True,
            reason_code="ok",
            state_change={
                "backend": "openclaw",
                "backend_progress": True,
                "text_digest_changed": True,
            },
        )

        def __init__(self) -> None:
            self.reason_codes: list[str] = []
            self.logs: list[str] = []
            self.summaries: list[dict[str, object]] = []

        @staticmethod
        def _estimate_goal_metric_from_dom(_dom: list[DOMElement]) -> None:
            return None

        @staticmethod
        def _dom_progress_signature(dom: list[DOMElement]) -> tuple[str, ...]:
            return tuple(str(getattr(item, "text", "") or "") for item in dom)

        @staticmethod
        def _evaluate_goal_target_completion(*, goal: object, dom_elements: list[DOMElement]) -> None:
            return None

        def _validate_goal_achievement_claim(self, *, goal, decision, dom_elements):
            return True, None

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

        def _log(self, message: str) -> None:
            self.logs.append(message)

        def _record_goal_summary(self, **payload: object) -> None:
            self.summaries.append(dict(payload))

    agent = _Agent()
    goal = SimpleNamespace(
        id="g-play",
        name="강의 재생",
        description="플레이어의 재생 버튼을 눌러 강의를 튼다.",
        success_criteria=["재생 버튼 클릭"],
        start_url="",
    )
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e22",
        reasoning="재생 버튼을 클릭한다.",
        confidence=0.91,
        is_goal_achieved=True,
        goal_achievement_reason="재생 버튼 클릭이 마지막 단계입니다.",
    )

    result = evaluate_post_action_progress(
        agent=agent,
        goal=goal,
        decision=decision,
        success=True,
        before_signature=("재생",),
        dom_elements=[
            DOMElement(id=1, tag="button", role="button", text="재생", is_visible=True, is_enabled=True)
        ],
        step_count=4,
        steps=[],
        start_time=0.0,
    )

    assert result["terminal_result"] is not None
    assert result["terminal_result"].success is True
    assert result["terminal_result"].steps_taken == []
    assert result["terminal_result"].total_steps == 4
    assert result["terminal_result"].final_reason == "재생 버튼 클릭이 마지막 단계입니다."
    assert "goal_achievement_after_action" in agent.reason_codes
    assert any("목표 달성" in message for message in agent.logs)


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


def test_evaluate_post_action_progress_defers_observation_without_immediate_snapshot() -> None:
    before_dom = [
        DOMElement(id=1, tag="button", role="button", text="호텔/리조트", is_visible=True, is_enabled=True)
    ]
    evidence = {
        "text_digest": "홈 호텔/리조트",
        "live_texts": ["홈", "호텔/리조트"],
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
    reason_codes: list[str] = []

    class _DeferredAgent:
        session_id = "s1"
        _dom_cache_generation = 1
        _dom_analyze_cache: dict[str, object] = {}
        _goal_constraints: dict[str, object] = {}
        _goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
        _goal_policy_phase = ""
        _goal_phase_intent = ""
        _last_snapshot_evidence = dict(evidence)
        _last_backend_post_action_snapshot: dict[str, object] = {}
        _last_exec_result = SimpleNamespace(
            success=True,
            effective=True,
            reason_code="ok",
            state_change={
                "backend": "openclaw",
                "effective": True,
                "backend_progress": False,
                "post_action_observation_deferred": True,
                "backend_pending_observation": True,
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
        def _record_reason_code(code: str) -> None:
            reason_codes.append(code)

        @staticmethod
        def _log(_message: str) -> None:
            return None

        @staticmethod
        def _analyze_dom(scope_container_ref_id: str = "") -> list[DOMElement]:
            raise AssertionError("deferred post-action observation should wait for the next collect")

    result = evaluate_post_action_progress(
        agent=_DeferredAgent(),
        goal=SimpleNamespace(id="g1", name="호텔/리조트", description="", success_criteria=[], start_url=""),
        decision=ActionDecision(action=ActionType.CLICK, reasoning="호텔/리조트 클릭", confidence=0.8),
        success=True,
        before_signature=("호텔/리조트",),
        dom_elements=before_dom,
        step_count=1,
        steps=[],
        start_time=0.0,
    )

    assert result["changed"] is False
    assert result["post_dom"] == []
    assert "post_action_observation_deferred" in reason_codes


def test_evaluate_post_action_progress_rejects_unreflected_commit_even_when_modal_closed(monkeypatch) -> None:
    monkeypatch.setattr("gaia.src.phase4.goal_driven.execute_goal_progress.time.sleep", lambda _seconds: None)
    before_dom = [
        DOMElement(id=1, tag="button", role="button", text="적용하기", ref_id="eApply", is_visible=True, is_enabled=True)
    ]
    before_evidence = {
        "text_digest": "날짜, 인원 선택 2026.06 06.02(화) • 1박 적용하기",
        "live_texts": ["날짜, 인원 선택", "2026.06", "06.02(화) • 1박", "적용하기"],
        "list_count": 1,
        "interactive_count": 1,
        "modal_count": 1,
        "backdrop_count": 0,
        "dialog_count": 1,
        "modal_open": True,
        "auth_prompt_visible": False,
        "login_visible": False,
        "logout_visible": False,
    }
    after_evidence = {
        "text_digest": "송도/소래포구 05.30~05.31 · 2명 정렬",
        "live_texts": ["송도/소래포구", "05.30~05.31 · 2명", "정렬"],
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
    after_dom = [
        DOMElement(id=2, tag="button", role="button", text="05.30~05.31 · 2명", is_visible=True, is_enabled=True),
        DOMElement(id=3, tag="button", role="button", text="정렬", is_visible=True, is_enabled=True),
    ]
    reason_codes: list[str] = []

    class _CommitAgent:
        session_id = "s1"
        _dom_cache_generation = 3
        _dom_analyze_cache: dict[str, object] = {}
        _goal_constraints: dict[str, object] = {}
        _goal_semantics = SimpleNamespace(goal_kind="", mutate_required=False)
        _goal_policy_phase = ""
        _goal_phase_intent = ""
        _last_snapshot_evidence = dict(before_evidence)
        _last_backend_post_action_snapshot = {
            "snapshot_id": "openclaw:s1:5",
            "url": "https://nol.yanolja.com/discovery/s/local/list",
            "scope_applied": False,
            "dom_elements": after_dom,
            "evidence": dict(after_evidence),
        }
        _last_exec_result = SimpleNamespace(
            success=True,
            effective=True,
            reason_code="ok",
            state_change={
                "backend": "openclaw",
                "backend_progress": False,
                "backend_effective_only": True,
                "effective": True,
                "modal_state_changed": True,
                "text_digest_changed": True,
                "commit_verification_failed": True,
                "commit_pending": True,
                "commit_verification": {
                    "kind": "date_range",
                    "expected_range": "06.02~06.03",
                    "observed_ranges": ["05.30~05.31"],
                    "reflected": False,
                },
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
        def _log(_message: str) -> None:
            return None

        @staticmethod
        def _analyze_dom(scope_container_ref_id: str = "") -> list[DOMElement]:
            raise AssertionError("commit failure should not use a weak settle probe as progress")

        @staticmethod
        def _record_reason_code(code: str) -> None:
            reason_codes.append(code)

    agent = _CommitAgent()

    result = evaluate_post_action_progress(
        agent=agent,
        goal=SimpleNamespace(id="g1", name="날짜 적용", description="", success_criteria=[], start_url=""),
        decision=ActionDecision(action=ActionType.CLICK, ref_id="eApply", reasoning="적용하기 클릭", confidence=0.8),
        success=True,
        before_signature=("적용하기",),
        dom_elements=before_dom,
        step_count=7,
        steps=[],
        start_time=0.0,
    )

    assert result["changed"] is False
    assert "weak_effective_ignored" in reason_codes
