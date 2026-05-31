from gaia.src.phase4.goal_driven import action_execution_runtime as runtime
from gaia.src.phase4.goal_driven.action_execution_runtime import (
    _coordinate_click_script,
    _find_rebound_element,
    _find_visible_text_ref_candidate,
    _is_stale_like_timeout,
    _visual_find_label_candidates,
    _visual_find_label_is_safe,
)
from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, DOMElement
from gaia.src.phase4.goal_driven.runtime import ActionExecResult


def test_is_stale_like_timeout_detects_snapshot_visibility_error() -> None:
    result = ActionExecResult(
        success=False,
        effective=False,
        reason_code="action_timeout",
        reason='"t0-f0-e28"를 찾을 수 없거나 표시되지 않습니다. 최신 snapshot을 기반으로 요소를 다시 확인하세요.',
    )

    assert _is_stale_like_timeout(result) is True


def test_is_stale_like_timeout_ignores_generic_timeout() -> None:
    result = ActionExecResult(
        success=False,
        effective=False,
        reason_code="action_timeout",
        reason="action budget exceeded (45.0s)",
    )

    assert _is_stale_like_timeout(result) is False


class _FakeAgent:
    def __init__(self) -> None:
        self.session_id = "goal-session"
        self.mcp_host_url = "http://localhost:8000"
        self._browser_backend_name = ""
        self._goal_policy_phase = ""
        self._goal_phase_intent = ""
        self._active_snapshot_id = "snap-1"
        self._element_selectors = {23: "button[data-course='target']"}
        self._element_full_selectors = {23: "main button[data-course='target']"}
        self._element_ref_ids = {23: "old-ref"}
        self._element_ref_meta_by_id = {
            23: {
                "ref": "old-ref",
                "selector": "button[data-course='target']",
                "full_selector": "main button[data-course='target']",
                "text": "바로 추가",
                "tag": "button",
                "role_ref_role": "button",
                "role_ref_name": "바로 추가",
                "role_ref_nth": 5,
                "container_name": "(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
            }
        }
        self._last_snapshot_elements_by_ref = {
            "old-ref": dict(self._element_ref_meta_by_id[23]),
        }
        self._last_context_snapshot = {}
        self._selector_to_ref_id = {"main button[data-course='target']": "new-ref"}
        self.logs: list[str] = []
        self._last_exec_result = None
        self._recent_signal_history = []
        self._persistent_state_memory = []
        self.reason_codes: list[str] = []

    def _normalize_text(self, value: object) -> str:
        return str(value or "").strip().lower()

    def _contains_logout_hint(self, _field: object) -> bool:
        return False

    def _goal_allows_logout(self) -> bool:
        return False

    def _contains_login_hint(self, _field: object) -> bool:
        return False

    def _is_ref_temporarily_blocked(self, _ref_id: object) -> bool:
        return False

    def _analyze_dom(self):
        self._active_snapshot_id = "snap-2"
        self._element_selectors = {17: "button[data-course='target']"}
        self._element_full_selectors = {17: "main button[data-course='target']"}
        self._element_ref_ids = {17: "new-ref"}
        self._element_ref_meta_by_id = {
            17: {
                "ref": "new-ref",
                "selector": "button[data-course='target']",
                "full_selector": "main button[data-course='target']",
                "text": "바로 추가",
                "tag": "button",
                "role_ref_role": "button",
                "role_ref_name": "바로 추가",
                "role_ref_nth": 5,
                "container_name": "(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
            }
        }
        self._last_snapshot_elements_by_ref = {
            "new-ref": dict(self._element_ref_meta_by_id[17]),
        }
        self._selector_to_ref_id = {"main button[data-course='target']": "wrong-ref"}
        return [
            DOMElement(
                id=17,
                tag="button",
                text="바로 추가",
                ref_id="new-ref",
                container_name="(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
                context_text="강의평 | (HUSS국립부경대)포용사회와문화탐방1 | 미배정",
                role_ref_role="button",
                role_ref_name="바로 추가",
                role_ref_nth=5,
            )
        ]

    def _log(self, message: str) -> None:
        self.logs.append(message)

    def _record_reason_code(self, reason_code: str) -> None:
        self.reason_codes.append(reason_code)


def test_find_rebound_element_matches_by_role_ref_and_container() -> None:
    agent = _FakeAgent()
    prior = DOMElement(
        id=23,
        tag="button",
        text="바로 추가",
        ref_id="old-ref",
        container_name="(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
        context_text="강의평 | (HUSS국립부경대)포용사회와문화탐방1 | 미배정",
        role_ref_role="button",
        role_ref_name="바로 추가",
        role_ref_nth=5,
    )
    live_dom = agent._analyze_dom()

    rebound = _find_rebound_element(agent, prior, live_dom)

    assert rebound is not None
    assert rebound.id == 17
    assert rebound.ref_id == "new-ref"


def test_execute_decision_retries_stale_timeout_with_rebound_ref(monkeypatch) -> None:
    agent = _FakeAgent()
    dom_elements = [
        DOMElement(
            id=23,
            tag="button",
            text="바로 추가",
            ref_id="old-ref",
            container_name="(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
            context_text="강의평 | (HUSS국립부경대)포용사회와문화탐방1 | 미배정",
            role_ref_role="button",
            role_ref_name="바로 추가",
            role_ref_nth=5,
        )
    ]
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-ref", reasoning="click target")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(
            {
                "action": action_name,
                "selector": selector,
                "full_selector": full_selector,
                "ref_id": ref_id,
                "snapshot": agent_obj._active_snapshot_id,
            }
        )
        if len(calls) == 1:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="action_timeout",
                reason='"old-ref"를 찾을 수 없거나 표시되지 않습니다. 최신 snapshot을 기반으로 요소를 다시 확인하세요.',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok")

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert len(calls) == 2
    assert calls[0]["ref_id"] == "old-ref"
    assert calls[1]["ref_id"] == "new-ref"
    assert calls[1]["snapshot"] == "snap-2"
    assert any("ref 재바인딩" in log for log in agent.logs)


def test_execute_decision_blocks_temporarily_ineffective_ref_in_openclaw_mode() -> None:
    agent = _FakeAgent()
    agent._browser_backend_name = "openclaw"
    agent._is_ref_temporarily_blocked = lambda ref_id: ref_id == "old-ref"  # type: ignore[method-assign]
    dom_elements = [
        DOMElement(
            id=23,
            tag="button",
            text="바로 추가",
            ref_id="old-ref",
            is_visible=True,
            is_enabled=True,
        )
    ]
    decision = ActionDecision(
        action=ActionType.CLICK,
        element_id=23,
        reasoning="같은 버튼을 다시 눌러본다.",
    )

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is False
    assert "blocked_ref_no_progress" in str(err)
    assert agent._last_exec_result.reason_code == "blocked_ref_no_progress"


def test_execute_action_preserves_requested_ref_on_openclaw_failure(monkeypatch) -> None:
    agent = _FakeAgent()

    def fake_execute_mcp_action_with_recovery(**_kwargs):
        return type(
            "Response",
            (),
            {
                "status_code": 200,
                "payload": {
                    "success": False,
                    "effective": False,
                    "reason_code": "not_actionable",
                    "reason": 'Element "e20" not found or not visible.',
                    "state_change": {"effective": False, "backend": "openclaw"},
                },
                "text": "",
            },
        )()

    monkeypatch.setattr(runtime, "execute_mcp_action_with_recovery", fake_execute_mcp_action_with_recovery)

    result = runtime.execute_action(agent, "click", ref_id="e20")

    assert result.success is False
    assert result.reason_code == "not_actionable"
    assert result.ref_id_used == "e20"


def test_execute_decision_retries_ref_stale_with_rebound_ref(monkeypatch) -> None:
    agent = _FakeAgent()
    dom_elements = [
        DOMElement(
            id=23,
            tag="button",
            text="바로 추가",
            ref_id="old-ref",
            container_name="(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
            context_text="강의평 | (HUSS국립부경대)포용사회와문화탐방1 | 미배정",
            role_ref_role="button",
            role_ref_name="바로 추가",
            role_ref_nth=5,
        )
    ]
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-ref", reasoning="click target")
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        if len(calls) == 1:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="ref_stale",
                reason='Error: Unknown ref "old-ref". Run a new snapshot and use a ref from that snapshot.',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok")

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == ["old-ref", "new-ref"]


def test_execute_decision_force_refreshes_fill_ref_recovery(monkeypatch) -> None:
    class _SearchAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._active_snapshot_id = "snap-search-1"
            self._element_selectors = {1: "input[type='search']"}
            self._element_full_selectors = {1: "header input[type='search']"}
            self._element_ref_ids = {1: "old-search-ref"}
            self._element_ref_meta_by_id = {
                1: {
                    "ref": "old-search-ref",
                    "selector": "input[type='search']",
                    "full_selector": "header input[type='search']",
                    "tag": "input",
                    "type": "search",
                    "role_ref_role": "searchbox",
                    "role_ref_name": "검색",
                    "placeholder": "검색",
                }
            }
            self._last_snapshot_elements_by_ref = {
                "old-search-ref": dict(self._element_ref_meta_by_id[1]),
            }
            self._selector_to_ref_id = {"header input[type='search']": "new-search-ref"}
            self.force_reasons: list[str] = []
            self.analyze_force_flags: list[object] = []

        def _force_next_dom_resnapshot(self, *, reason: str = "") -> None:
            self.force_reasons.append(reason)

        def _analyze_dom(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.analyze_force_flags.append(kwargs.get("force_refresh"))
            self._active_snapshot_id = "snap-search-2"
            self._element_selectors = {9: "input[type='search']"}
            self._element_full_selectors = {9: "header input[type='search']"}
            self._element_ref_ids = {9: "new-search-ref"}
            self._element_ref_meta_by_id = {
                9: {
                    "ref": "new-search-ref",
                    "selector": "input[type='search']",
                    "full_selector": "header input[type='search']",
                    "tag": "input",
                    "type": "search",
                    "role_ref_role": "searchbox",
                    "role_ref_name": "뉴스 검색",
                    "placeholder": "뉴스 검색",
                }
            }
            self._last_snapshot_elements_by_ref = {
                "new-search-ref": dict(self._element_ref_meta_by_id[9]),
            }
            self._selector_to_ref_id = {"header input[type='search']": "new-search-ref"}
            return [
                DOMElement(
                    id=9,
                    tag="input",
                    type="search",
                    role="searchbox",
                    placeholder="뉴스 검색",
                    aria_label="뉴스 검색",
                    ref_id="new-search-ref",
                    is_visible=True,
                    is_enabled=True,
                )
            ]

    agent = _SearchAgent()
    dom_elements = [
        DOMElement(
            id=1,
            tag="input",
            type="search",
            role="searchbox",
            placeholder="검색",
            aria_label="검색",
            ref_id="old-search-ref",
        )
    ]
    decision = ActionDecision(
        action=ActionType.FILL,
        ref_id="old-search-ref",
        value="손흥민",
        reasoning="뉴스 검색 입력 필드에 손흥민 입력",
    )
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        if len(calls) == 1:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="not_found",
                reason="old ref not found",
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == ["old-search-ref", "new-search-ref"]
    assert "ref_recovery" in agent.force_reasons
    assert True in agent.analyze_force_flags


def test_execute_decision_repairs_non_fillable_search_target_before_fill(monkeypatch) -> None:
    class _SearchAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._active_snapshot_id = "snap-link-1"
            self._element_selectors = {10: "a.logo"}
            self._element_full_selectors = {10: "header a.logo"}
            self._element_ref_ids = {10: "naver-link-ref"}
            self._element_ref_meta_by_id = {}
            self._last_snapshot_elements_by_ref = {}
            self._selector_to_ref_id = {}
            self.force_reasons: list[str] = []

        def _force_next_dom_resnapshot(self, *, reason: str = "") -> None:
            self.force_reasons.append(reason)

        def _analyze_dom(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self._active_snapshot_id = "snap-link-2"
            self._element_selectors = {42: "input[placeholder='뉴스 검색']"}
            self._element_full_selectors = {42: "header input[placeholder='뉴스 검색']"}
            self._element_ref_ids = {42: "news-search-ref"}
            self._element_ref_meta_by_id = {}
            self._last_snapshot_elements_by_ref = {}
            self._selector_to_ref_id = {}
            return [
                DOMElement(
                    id=42,
                    tag="input",
                    type="search",
                    role="searchbox",
                    placeholder="뉴스 검색",
                    aria_label="뉴스 검색",
                    ref_id="news-search-ref",
                )
            ]

    agent = _SearchAgent()
    dom_elements = [DOMElement(id=10, tag="a", text="NAVER", ref_id="naver-link-ref")]
    decision = ActionDecision(
        action=ActionType.FILL,
        ref_id="naver-link-ref",
        value="손흥민",
        reasoning="뉴스 검색 입력 필드에 손흥민을 입력한다.",
    )
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == ["news-search-ref"]
    assert "fill_target_recovery" in agent.force_reasons
    assert any("fill 대상 재바인딩" in log for log in agent.logs)


def test_execute_decision_keeps_rich_text_body_target_over_searchbox(monkeypatch) -> None:
    agent = _FakeAgent()
    agent._active_snapshot_id = "snap-mail-1"
    agent._element_selectors = {
        7: "openclaw-ref:mail-body-ref",
        8: "openclaw-ref:mail-search-ref",
    }
    agent._element_full_selectors = dict(agent._element_selectors)
    agent._element_ref_ids = {
        7: "mail-body-ref",
        8: "mail-search-ref",
    }
    agent._element_ref_meta_by_id = {}
    agent._last_snapshot_elements_by_ref = {}
    agent._selector_to_ref_id = {}
    dom_elements = [
        DOMElement(
            id=7,
            tag="body",
            role="document",
            aria_label="본문 내용",
            context_text="메일쓰기 본문 내용",
            ref_id="mail-body-ref",
            is_focused=True,
        ),
        DOMElement(
            id=8,
            tag="input",
            type="search",
            role="searchbox",
            placeholder="메일 검색",
            aria_label="메일 검색",
            ref_id="mail-search-ref",
            is_focused=True,
        ),
    ]
    decision = ActionDecision(
        action=ActionType.FILL,
        ref_id="mail-body-ref",
        value="테스트다 이눔아",
        reasoning=(
            "검색창에 잘못 들어간 문구는 성공 증거가 아니므로, "
            "본문 편집 영역에 직접 목표 본문을 입력합니다."
        ),
    )
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == ["mail-body-ref"]
    assert not any("fill 대상 재바인딩" in log for log in agent.logs)


def test_execute_decision_passes_iframe_scope_for_body_fill(monkeypatch) -> None:
    agent = _FakeAgent()
    agent._active_snapshot_id = "snap-mail-iframe"
    agent._element_selectors = {7: "openclaw-ref:mail-body-ref"}
    agent._element_full_selectors = dict(agent._element_selectors)
    agent._element_ref_ids = {7: "mail-body-ref"}
    frame_selector = 'iframe >> nth=4 >> internal:control=enter-frame >> [aria-label="본문 내용"]'
    dom_elements = [
        DOMElement(
            id=7,
            tag="div",
            role="generic",
            aria_label="본문 내용",
            ref_id="mail-body-ref",
            frame_scoped_selector=frame_selector,
        )
    ]
    decision = ActionDecision(
        action=ActionType.FILL,
        ref_id="mail-body-ref",
        value="테스트다 이눔아",
        reasoning="메일 본문 iframe 편집 영역에 본문을 입력한다.",
    )
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **kwargs):
        calls.append(
            {
                "action": action_name,
                "ref_id": ref_id,
                "value": value,
                "frame_scoped_selector": kwargs.get("frame_scoped_selector"),
            }
        )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == [
        {
            "action": "fill",
            "ref_id": "mail-body-ref",
            "value": "테스트다 이눔아",
            "frame_scoped_selector": frame_selector,
        }
    ]


def test_find_visible_text_ref_candidate_prefers_safe_interactive_match() -> None:
    agent = _FakeAgent()
    dom = [
        DOMElement(id=1, tag="div", text="낮은 가격순 안내", ref_id="e1"),
        DOMElement(id=2, tag="button", role="option", text="낮은 가격순", ref_id="e2"),
    ]

    match = _find_visible_text_ref_candidate(agent, ["낮은 가격순"], dom)

    assert match is not None
    assert match.ref_id == "e2"


def test_visual_find_label_safety_blocks_destructive_targets() -> None:
    agent = _FakeAgent()

    assert _visual_find_label_is_safe(agent, "낮은 가격순") is True
    assert _visual_find_label_is_safe(agent, "결제하기") is False


def test_visual_find_label_candidates_extracts_unquoted_year() -> None:
    agent = _FakeAgent()
    decision = ActionDecision(
        action=ActionType.CLICK,
        reasoning="스카이뷰가 활성화되어 있고 촬영 연도 목록에서 2008년 옵션을 클릭한다.",
    )

    assert "2008" in _visual_find_label_candidates(agent, decision, None)


def test_coordinate_click_script_prioritizes_radio_left_offset_targets() -> None:
    script = _coordinate_click_script(796, 999)

    assert "[x - 24, y]" in script
    assert "[role=\"radio\"]" in script
    assert "closest('label')" in script
    assert "scoreTarget" in script
    assert "targetScore" in script
    assert "PointerEvent('pointerdown'" in script


def test_execute_decision_skips_same_ref_timeout_retry(monkeypatch) -> None:
    class _SameRefAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._element_selectors = {88: "span.year-2008"}
            self._element_full_selectors = {88: "div.skyview-years span.year-2008"}
            self._element_ref_ids = {88: "e389"}
            self._element_ref_meta_by_id = {
                88: {
                    "ref": "e389",
                    "selector": "span.year-2008",
                    "full_selector": "div.skyview-years span.year-2008",
                    "text": "2008",
                    "tag": "span",
                    "role_ref_role": "radio",
                    "role_ref_name": "2008",
                    "role_ref_nth": 8,
                    "container_name": "스카이뷰 촬영연도 2008",
                }
            }
            self._last_snapshot_elements_by_ref = {"e389": dict(self._element_ref_meta_by_id[88])}

        def _analyze_dom(self):
            self._active_snapshot_id = "snap-1"
            self._element_selectors = {88: "span.year-2008"}
            self._element_full_selectors = {88: "div.skyview-years span.year-2008"}
            self._element_ref_ids = {88: "e389"}
            self._element_ref_meta_by_id = {
                88: {
                    "ref": "e389",
                    "selector": "span.year-2008",
                    "full_selector": "div.skyview-years span.year-2008",
                    "text": "2008",
                    "tag": "span",
                    "role_ref_role": "radio",
                    "role_ref_name": "2008",
                    "role_ref_nth": 8,
                    "container_name": "스카이뷰 촬영연도 2008",
                }
            }
            self._last_snapshot_elements_by_ref = {"e389": dict(self._element_ref_meta_by_id[88])}
            return [
                DOMElement(
                    id=88,
                    tag="span",
                    text="2008",
                    ref_id="e389",
                    role_ref_role="radio",
                    role_ref_name="2008",
                    role_ref_nth=8,
                    container_name="스카이뷰 촬영연도 2008",
                )
            ]

    agent = _SameRefAgent()
    dom_elements = [
        DOMElement(
            id=88,
            tag="span",
            text="2008",
            ref_id="e389",
            role_ref_role="radio",
            role_ref_name="2008",
            role_ref_nth=8,
            container_name="스카이뷰 촬영연도 2008",
        )
    ]
    decision = ActionDecision(action=ActionType.CLICK, ref_id="e389", reasoning="2008년 옵션을 클릭한다.")
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        return ActionExecResult(
            success=False,
            effective=False,
            reason_code="action_timeout",
            reason='TimeoutError: locator.click: Timeout 8000ms exceeded. Call log: waiting for locator("aria-ref=e389")',
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is False
    assert err is not None
    assert calls == ["e389"]
    assert "visible_ref_timeout_no_retry" in agent.reason_codes


def test_execute_decision_disables_visual_fallback_by_default_after_same_ref_timeout(monkeypatch) -> None:
    class _VisionLLM:
        def find_element_coordinates(self, screenshot: str, description: str) -> dict[str, object]:
            raise AssertionError("visual coordinate fallback should be opt-in")

    class _SameRefCoordinateAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._browser_backend_name = "openclaw"
            self.llm = _VisionLLM()
            self._active_snapshot_id = "snap-year"
            self._dom_cache_generation = 3
            self._dom_analyze_cache = {"key": "stale"}
            self._prev_raw_snapshot_text = "stale snapshot"
            self._element_selectors = {88: "span.year-2008"}
            self._element_full_selectors = {88: "div.skyview-years span.year-2008"}
            self._element_ref_ids = {88: "e381"}
            self._element_ref_meta_by_id = {
                88: {
                    "ref": "e381",
                    "selector": "span.year-2008",
                    "full_selector": "div.skyview-years span.year-2008",
                    "text": "2008",
                    "tag": "span",
                    "role_ref_role": "radio",
                    "role_ref_name": "2008",
                    "role_ref_nth": 8,
                    "container_name": "스카이뷰 촬영연도 2008",
                }
            }
            self._last_snapshot_elements_by_ref = {"e381": dict(self._element_ref_meta_by_id[88])}

        def _analyze_dom(self):
            self._active_snapshot_id = "snap-year"
            self._element_selectors = {88: "span.year-2008"}
            self._element_full_selectors = {88: "div.skyview-years span.year-2008"}
            self._element_ref_ids = {88: "e381"}
            self._last_snapshot_elements_by_ref = {"e381": dict(self._element_ref_meta_by_id[88])}
            return [
                DOMElement(
                    id=88,
                    tag="span",
                    text="2008",
                    ref_id="e381",
                    role_ref_role="radio",
                    role_ref_name="2008",
                    role_ref_nth=8,
                    container_name="스카이뷰 촬영연도 2008",
                )
            ]

        def _capture_screenshot(self) -> str:
            raise AssertionError("screenshot should not be captured when fallback is disabled")

        def _contains_next_pagination_hint(self, _field: object) -> bool:
            return False

        def _is_numeric_page_label(self, _field: object) -> bool:
            return False

    monkeypatch.delenv("GAIA_VISUAL_COORDINATE_FALLBACK", raising=False)
    agent = _SameRefCoordinateAgent()
    decision = ActionDecision(action=ActionType.CLICK, ref_id="e381", reasoning="2008년 옵션을 클릭한다.")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "ref_id": ref_id, "value": value})
        return ActionExecResult(
            success=False,
            effective=False,
            reason_code="action_timeout",
            reason='TimeoutError: locator.click: Timeout 8000ms exceeded. Call log: waiting for locator("aria-ref=e381")',
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [])

    assert ok is False
    assert err is not None
    assert [call["action"] for call in calls] == ["click"]
    assert "visible_ref_timeout_no_retry" in agent.reason_codes
    assert "ref_recovery_failed_resnapshot" in agent.reason_codes
    assert "visual_coordinate_fallback" not in agent.reason_codes
    assert agent._dom_analyze_cache == {}
    assert agent._prev_raw_snapshot_text == ""


def test_execute_decision_disables_visual_fallback_by_default_after_not_found(monkeypatch) -> None:
    class _VisionLLM:
        def find_element_coordinates(self, screenshot: str, description: str) -> dict[str, object]:
            raise AssertionError("visual coordinate fallback should be opt-in")

    class _NoReboundAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._browser_backend_name = "openclaw"
            self.llm = _VisionLLM()
            self._dom_cache_generation = 4
            self._dom_analyze_cache = {"key": "stale"}
            self._prev_raw_snapshot_text = "stale snapshot"

        def _analyze_dom(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self._active_snapshot_id = "snap-no-rebound"
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
            self._element_ref_meta_by_id = {}
            self._last_snapshot_elements_by_ref = {}
            self._selector_to_ref_id = {}
            return []

        def _capture_screenshot(self) -> str:
            raise AssertionError("screenshot should not be captured when fallback is disabled")

        def _contains_next_pagination_hint(self, _field: object) -> bool:
            return False

        def _is_numeric_page_label(self, _field: object) -> bool:
            return False

    monkeypatch.delenv("GAIA_VISUAL_COORDINATE_FALLBACK", raising=False)
    agent = _NoReboundAgent()
    prior = DOMElement(id=23, tag="button", text="바로 추가", ref_id="old-ref")
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-ref", reasoning="바로 추가 클릭")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "ref_id": ref_id, "value": value})
        return ActionExecResult(
            success=False,
            effective=False,
            reason_code="not_found",
            reason='Error: Element "old-ref" not found or not visible. Run a new snapshot to see current page elements.',
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [prior])

    assert ok is False
    assert "[not_found]" in str(err)
    assert [call["action"] for call in calls] == ["click"]
    assert "ref_recovery_failed_resnapshot" in agent.reason_codes
    assert "visual_coordinate_fallback" not in agent.reason_codes
    assert agent._dom_analyze_cache == {}
    assert agent._prev_raw_snapshot_text == ""


def test_execute_decision_uses_visual_fallback_when_enabled_after_same_ref_year_timeout(monkeypatch) -> None:
    class _VisionLLM:
        def find_element_coordinates(self, screenshot: str, description: str) -> dict[str, object]:
            assert screenshot == "shot"
            assert description == "2008"
            return {"x": 512, "y": 380, "confidence": 0.94, "reasoning": "visible year option"}

    class _SameRefCoordinateAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._browser_backend_name = "openclaw"
            self.llm = _VisionLLM()
            self._active_snapshot_id = "snap-year"
            self._element_selectors = {88: "span.year-2008"}
            self._element_full_selectors = {88: "div.skyview-years span.year-2008"}
            self._element_ref_ids = {88: "e381"}
            self._element_ref_meta_by_id = {
                88: {
                    "ref": "e381",
                    "selector": "span.year-2008",
                    "full_selector": "div.skyview-years span.year-2008",
                    "text": "2008",
                    "tag": "span",
                    "role_ref_role": "radio",
                    "role_ref_name": "2008",
                    "role_ref_nth": 8,
                    "container_name": "스카이뷰 촬영연도 2008",
                }
            }
            self._last_snapshot_elements_by_ref = {"e381": dict(self._element_ref_meta_by_id[88])}

        def _analyze_dom(self):
            self._active_snapshot_id = "snap-year"
            self._element_selectors = {88: "span.year-2008"}
            self._element_full_selectors = {88: "div.skyview-years span.year-2008"}
            self._element_ref_ids = {88: "e381"}
            self._element_ref_meta_by_id = {
                88: {
                    "ref": "e381",
                    "selector": "span.year-2008",
                    "full_selector": "div.skyview-years span.year-2008",
                    "text": "2008",
                    "tag": "span",
                    "role_ref_role": "radio",
                    "role_ref_name": "2008",
                    "role_ref_nth": 8,
                    "container_name": "스카이뷰 촬영연도 2008",
                }
            }
            self._last_snapshot_elements_by_ref = {"e381": dict(self._element_ref_meta_by_id[88])}
            return [
                DOMElement(
                    id=88,
                    tag="span",
                    text="2008",
                    ref_id="e381",
                    role_ref_role="radio",
                    role_ref_name="2008",
                    role_ref_nth=8,
                    container_name="스카이뷰 촬영연도 2008",
                )
            ]

        def _capture_screenshot(self) -> str:
            return "shot"

        def _contains_next_pagination_hint(self, _field: object) -> bool:
            return False

        def _is_numeric_page_label(self, _field: object) -> bool:
            return False

    monkeypatch.setenv("GAIA_VISUAL_COORDINATE_FALLBACK", "1")
    agent = _SameRefCoordinateAgent()
    decision = ActionDecision(action=ActionType.CLICK, ref_id="e381", reasoning="2008년 옵션을 클릭한다.")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "ref_id": ref_id, "value": value})
        if action_name == "click":
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="action_timeout",
                reason='TimeoutError: locator.click: Timeout 8000ms exceeded. Call log: waiting for locator("aria-ref=e381")',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [])

    assert ok is True
    assert err is None
    assert [call["action"] for call in calls] == ["click", "evaluate"]
    assert calls[0]["ref_id"] == "e381"
    assert "elementFromPoint" in str(calls[1]["value"])
    assert agent._last_exec_result is not None
    assert agent._last_exec_result.state_change["visual_coordinate_fallback"] is True
    assert agent._last_exec_result.state_change["backend_progress"] is True
    assert agent._last_exec_result.state_change["visual_target_label"] == "2008"
    assert "visible_ref_timeout_no_retry" in agent.reason_codes
    assert "visual_coordinate_fallback" in agent.reason_codes


def test_execute_decision_rejects_text_mismatched_resolved_ref(monkeypatch) -> None:
    class _YearAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._active_snapshot_id = "snap-year-1"
            self._element_selectors = {
                2: "a.skip",
                88: "a.year-2008",
            }
            self._element_full_selectors = {
                2: "body a.skip",
                88: "div.skyview-years a.year-2008",
            }
            self._element_ref_ids = {
                2: "e2",
                88: "e389",
            }
            self._element_ref_meta_by_id = {
                88: {
                    "ref": "e389",
                    "selector": "a.year-2008",
                    "full_selector": "div.skyview-years a.year-2008",
                    "text": "2008",
                    "tag": "a",
                    "role_ref_role": "link",
                    "role_ref_name": "2008",
                    "role_ref_nth": 14,
                }
            }
            self._last_snapshot_elements_by_ref = {"e389": dict(self._element_ref_meta_by_id[88])}

        def _analyze_dom(self):
            self._active_snapshot_id = "snap-year-2"
            self._element_selectors = {
                2: "a.skip",
                88: "a.year-2008",
            }
            self._element_full_selectors = {
                2: "body a.skip",
                88: "div.skyview-years a.year-2008",
            }
            self._element_ref_ids = {
                2: "e2",
                88: "e389",
            }
            self._element_ref_meta_by_id = {
                2: {
                    "ref": "e2",
                    "selector": "a.skip",
                    "full_selector": "body a.skip",
                    "text": "본문 바로가기",
                    "tag": "a",
                    "role_ref_role": "link",
                    "role_ref_name": "본문 바로가기",
                    "role_ref_nth": 1,
                },
                88: {
                    "ref": "e389",
                    "selector": "a.year-2008",
                    "full_selector": "div.skyview-years a.year-2008",
                    "text": "2008",
                    "tag": "a",
                    "role_ref_role": "link",
                    "role_ref_name": "2008",
                    "role_ref_nth": 14,
                },
            }
            self._last_snapshot_elements_by_ref = {
                "e2": dict(self._element_ref_meta_by_id[2]),
                "e389": dict(self._element_ref_meta_by_id[88]),
            }
            return [
                DOMElement(
                    id=2,
                    tag="a",
                    text="본문 바로가기",
                    ref_id="e2",
                    role_ref_role="link",
                    role_ref_name="본문 바로가기",
                    role_ref_nth=1,
                ),
                DOMElement(
                    id=88,
                    tag="a",
                    text="2008",
                    ref_id="e389",
                    role_ref_role="link",
                    role_ref_name="2008",
                    role_ref_nth=14,
                ),
            ]

    agent = _YearAgent()
    dom_elements = [
        DOMElement(
            id=88,
            tag="a",
            text="2008",
            ref_id="e389",
            role_ref_role="link",
            role_ref_name="2008",
            role_ref_nth=14,
        )
    ]
    decision = ActionDecision(action=ActionType.CLICK, ref_id="e389", reasoning="2008년 항목을 클릭한다.")
    calls: list[str] = []

    def fake_resolve_stale_ref(_previous_meta, _fresh_snapshot_payload):
        return {
            "ref": "e2",
            "selector": "a.skip",
            "full_selector": "body a.skip",
            "text": "본문 바로가기",
        }

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        if len(calls) == 1:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="action_timeout",
                reason='TimeoutError: locator.click: Timeout 8000ms exceeded. Call log: waiting for locator("aria-ref=e389")',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok")

    monkeypatch.setattr(runtime, "resolve_stale_ref", fake_resolve_stale_ref)
    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == ["e389", "e389"]
    assert "ref_recovery_text_mismatch" in agent.reason_codes


def test_execute_decision_keeps_rebound_live_ref_over_generic_selector_map(monkeypatch) -> None:
    agent = _FakeAgent()
    dom_elements = [
        DOMElement(
            id=23,
            tag="button",
            text="바로 추가",
            ref_id="old-ref",
            container_name="(HUSS국립부경대)포용사회와문화탐방1 | 바로 추가",
            context_text="강의평 | (HUSS국립부경대)포용사회와문화탐방1 | 미배정",
            role_ref_role="button",
            role_ref_name="바로 추가",
            role_ref_nth=5,
        )
    ]
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-ref", reasoning="click target")
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        if len(calls) == 1:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="action_timeout",
                reason='"old-ref"를 찾을 수 없거나 표시되지 않습니다. 최신 snapshot을 기반으로 요소를 다시 확인하세요.',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok")

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None


def test_execute_decision_focus_routes_to_browser_tabs_focus(monkeypatch) -> None:
    agent = _FakeAgent()
    dom_elements = []
    decision = ActionDecision(action=ActionType.FOCUS, value="tab-2", reasoning="switch into popup")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "value": value, "ref_id": ref_id})
        return ActionExecResult(
            success=True,
            effective=True,
            reason_code="ok",
            reason="ok",
            state_change={
                "backend": "browser_tabs_focus",
                "backend_progress": True,
                "focus_changed": True,
                "focused_target_id": str(value or ""),
            },
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == [{"action": "focus", "value": "tab-2", "ref_id": None}]


def test_execute_action_focus_dispatches_tabs_focus(monkeypatch) -> None:
    agent = _FakeAgent()
    seen: dict[str, object] = {}

    class _Response:
        status_code = 200
        payload = {
            "success": True,
            "reason_code": "ok",
            "targetId": "tab-2",
            "current_url": "https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868",
        }
        text = ""

    def fake_execute_mcp_action_with_recovery(*, raw_base_url, action, params, timeout, attempts, is_transport_error, context):
        seen["action"] = action
        seen["params"] = dict(params)
        return _Response()

    monkeypatch.setattr(runtime, "execute_mcp_action_with_recovery", fake_execute_mcp_action_with_recovery)

    result = runtime.execute_action(agent, "focus", value="tab-2")

    assert result.success is True
    assert result.effective is True
    assert result.state_change["backend"] == "browser_tabs_focus"
    assert result.state_change["backend_progress"] is True
    assert result.state_change["focus_changed"] is True
    assert result.state_change["focused_target_id"] == "tab-2"
    assert result.state_change["focused_url"] == "https://cyber.inu.ac.kr/mod/vod/viewer.php?id=1346868"
    assert seen["action"] == "browser_tabs_focus"
    assert seen["params"] == {"session_id": agent.session_id, "targetId": "tab-2"}


def test_execute_action_fill_uses_type_selector_for_iframe_scope(monkeypatch) -> None:
    agent = _FakeAgent()
    seen: dict[str, object] = {}
    frame_selector = 'iframe >> nth=4 >> internal:control=enter-frame >> [aria-label="본문 내용"]'

    class _Response:
        status_code = 200
        payload = {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "state_change": {"backend": "openclaw"},
        }
        text = ""

    def fake_execute_mcp_action_with_recovery(*, raw_base_url, action, params, timeout, attempts, is_transport_error, context):
        seen["action"] = action
        seen["params"] = dict(params)
        return _Response()

    monkeypatch.setattr(runtime, "execute_mcp_action_with_recovery", fake_execute_mcp_action_with_recovery)

    result = runtime.execute_action(
        agent,
        "fill",
        ref_id="mail-body-ref",
        value="테스트다 이눔아",
        frame_scoped_selector=frame_selector,
    )

    assert result.success is True
    assert result.effective is True
    assert seen["action"] == "browser_act"
    assert seen["params"]["action"] == "type"
    assert seen["params"]["selector"] == frame_selector
    assert seen["params"]["value"] == "테스트다 이눔아"


def test_execute_action_type_dispatches_ref_protocol(monkeypatch) -> None:
    agent = _FakeAgent()
    seen: dict[str, object] = {}

    class _Response:
        status_code = 200
        payload = {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "state_change": {"backend": "openclaw"},
            "snapshot_id_used": "snap-1",
            "ref_id_used": "recipient-ref",
        }
        text = ""

    def fake_execute_mcp_action_with_recovery(*, raw_base_url, action, params, timeout, attempts, is_transport_error, context):
        seen["action"] = action
        seen["params"] = dict(params)
        return _Response()

    monkeypatch.setattr(runtime, "execute_mcp_action_with_recovery", fake_execute_mcp_action_with_recovery)

    result = runtime.execute_action(
        agent,
        "type",
        ref_id="recipient-ref",
        value="jangboss02@gmail.com",
    )

    assert result.success is True
    assert result.effective is True
    assert seen["action"] == "browser_act"
    assert seen["params"]["snapshot_id"] == "snap-1"
    assert seen["params"]["ref_id"] == "recipient-ref"
    assert seen["params"]["action"] == "type"
    assert seen["params"]["value"] == "jangboss02@gmail.com"


def test_execute_action_inspect_dispatches_evaluate_and_summarizes(monkeypatch) -> None:
    agent = _FakeAgent()
    seen: dict[str, object] = {}
    inspection = {
        "activeElement": {
            "tag": "input",
            "role": "combobox",
            "label": "recipient",
            "value": "jangboss02@gmail.com",
        },
        "fields": [
            {"tag": "input", "role": "combobox", "label": "recipient", "value": "jangboss02@gmail.com"},
            {"tag": "div", "role": "textbox", "label": "body", "text": "테스트다 이눔아"},
        ],
        "tokenAreas": [
            {
                "fieldIndex": 0,
                "nearbyControls": [
                    {"tag": "button", "text": "jangboss02@gmail.com"},
                    {"tag": "button", "ariaLabel": "delete"},
                ],
            }
        ],
        "dialogs": [],
        "frames": [
            {"index": 0, "accessible": True, "title": "editor", "bodyText": "테스트다 이눔아", "fields": []}
        ],
    }

    class _Response:
        status_code = 200
        payload = {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "state_change": {"backend": "openclaw", "evaluate_result": inspection},
        }
        text = ""

    def fake_execute_mcp_action_with_recovery(*, raw_base_url, action, params, timeout, attempts, is_transport_error, context):
        seen["action"] = action
        seen["params"] = dict(params)
        return _Response()

    monkeypatch.setattr(runtime, "execute_mcp_action_with_recovery", fake_execute_mcp_action_with_recovery)

    result = runtime.execute_action(agent, "inspect")

    assert result.success is True
    assert result.effective is True
    assert seen["action"] == "browser_act"
    assert seen["params"]["action"] == "evaluate"
    assert "activeElement" in seen["params"]["fn"]
    assert result.state_change["inspection_tool"] == "browser_inspect"
    assert result.state_change["inspection"]["activeElement"]["value"] == "jangboss02@gmail.com"
    assert "nearby interactive state" in result.state_change["inspection_summary"]


def test_execute_decision_inspect_appends_action_feedback(monkeypatch) -> None:
    agent = _FakeAgent()
    agent._action_feedback = []
    decision = ActionDecision(
        action=ActionType.INSPECT,
        value="check committed input state",
        reasoning="ambiguous widget state",
    )

    def fake_execute_action(_agent_obj, action_name, **_kwargs):
        assert action_name == "inspect"
        return ActionExecResult(
            success=True,
            effective=True,
            reason_code="ok",
            reason="ok",
            state_change={
                "inspection_tool": "browser_inspect",
                "inspection_summary": "active: input role=combobox value=jangboss02@gmail.com",
            },
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [])

    assert ok is True
    assert err is None
    assert agent._action_feedback == ["inspect: active: input role=combobox value=jangboss02@gmail.com"]


def test_execute_decision_marks_select_target_value_changed(monkeypatch) -> None:
    agent = _FakeAgent()
    agent._element_selectors = {33: "select[name='division']"}
    agent._element_full_selectors = {33: "main select[name='division']"}
    agent._element_ref_ids = {33: "e33"}
    dom_elements = [
        DOMElement(
            id=33,
            tag="select",
            role="combobox",
            text="전체",
            ref_id="e33",
            selected_value="전체",
            role_ref_role="combobox",
            role_ref_name="전체",
            context_text="검색 | 전체 | &Service",
            is_visible=True,
            is_enabled=True,
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "전핵", "text": "전핵"},
                {"value": "전심", "text": "전심"},
            ],
        )
    ]
    decision = ActionDecision(action=ActionType.SELECT, ref_id="e33", value="전핵", reasoning="필터를 바꾼다.")

    def fake_execute_action(_agent_obj, _action_name, **_kwargs):
        return ActionExecResult(
            success=True,
            effective=True,
            reason_code="ok",
            reason="ok",
            state_change={"text_digest_changed": True},
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert agent._last_exec_result is not None
    assert agent._last_exec_result.state_change["target_value_changed"] is True
    assert agent._recent_signal_history[-1]["state_change"]["target_value_changed"] is True
    assert agent._persistent_state_memory[-1]["previous_selected_value"] == "전체"


def test_execute_decision_retries_stale_ref_using_snapshot_meta_when_dom_binding_is_ambiguous(monkeypatch) -> None:
    agent = _FakeAgent()
    agent._element_selectors = {23: "select >> nth=3"}
    agent._element_full_selectors = {23: "main select >> nth=3"}
    agent._element_ref_ids = {23: "old-select-ref"}
    agent._element_ref_meta_by_id = {
        23: {
            "ref": "old-select-ref",
            "selector": "select >> nth=3",
            "full_selector": "main select >> nth=3",
            "text": "전체",
            "tag": "select",
            "role_ref_role": "combobox",
            "role_ref_name": "전체",
            "role_ref_nth": 4,
        }
    }
    agent._last_snapshot_elements_by_ref = {"old-select-ref": dict(agent._element_ref_meta_by_id[23])}
    prior = DOMElement(
        id=23,
        tag="select",
        text="전체",
        ref_id="old-select-ref",
        role="combobox",
        role_ref_role="combobox",
        role_ref_name="전체",
        role_ref_nth=4,
        context_text="검색 | 전체 | &service",
        is_visible=True,
        is_enabled=True,
    )

    def analyze_dom_with_repeated_selects():
        agent._active_snapshot_id = "snap-3"
        agent._element_selectors = {30: "select >> nth=3", 31: "select >> nth=4"}
        agent._element_full_selectors = {30: "main select >> nth=3", 31: "main select >> nth=4"}
        agent._element_ref_ids = {30: "fresh-select-ref", 31: "other-select-ref"}
        agent._element_ref_meta_by_id = {
            30: {
                "ref": "fresh-select-ref",
                "selector": "select >> nth=3",
                "full_selector": "main select >> nth=3",
                "text": "전체",
                "tag": "select",
                "role_ref_role": "combobox",
                "role_ref_name": "전체",
                "role_ref_nth": 4,
            },
            31: {
                "ref": "other-select-ref",
                "selector": "select >> nth=4",
                "full_selector": "main select >> nth=4",
                "text": "전체",
                "tag": "select",
                "role_ref_role": "combobox",
                "role_ref_name": "전체",
                "role_ref_nth": 5,
            },
        }
        agent._last_snapshot_elements_by_ref = {
            "fresh-select-ref": dict(agent._element_ref_meta_by_id[30]),
            "other-select-ref": dict(agent._element_ref_meta_by_id[31]),
        }
        return [
            DOMElement(id=30, tag="select", text="전체", ref_id="fresh-select-ref", role="combobox", role_ref_role="combobox", role_ref_name="전체", role_ref_nth=4),
            DOMElement(id=31, tag="select", text="전체", ref_id="other-select-ref", role="combobox", role_ref_role="combobox", role_ref_name="전체", role_ref_nth=5),
        ]

    agent._analyze_dom = analyze_dom_with_repeated_selects
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-select-ref", reasoning="click select")
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(str(ref_id or ""))
        if len(calls) == 1:
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="action_timeout",
                reason='"old-select-ref"를 찾을 수 없거나 표시되지 않습니다. 최신 snapshot을 기반으로 요소를 다시 확인하세요.',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok")

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [prior])

    assert ok is True
    assert err is None
    assert calls == ["old-select-ref", "fresh-select-ref"]


def test_find_rebound_element_prefers_select_with_matching_option_signature() -> None:
    agent = _FakeAgent()
    prior = DOMElement(
        id=40,
        tag="select",
        role="combobox",
        text="전체",
        ref_id="old-select",
        role_ref_role="combobox",
        role_ref_name="전체",
        context_text="검색 | 전체 | 구분",
        options=[
            {"value": "전체", "text": "전체"},
            {"value": "교양", "text": "교양"},
            {"value": "전심", "text": "전심"},
        ],
        selected_value="전체",
    )
    live_dom = [
        DOMElement(
            id=41,
            tag="select",
            role="combobox",
            text="전체",
            ref_id="wrong-select",
            role_ref_role="combobox",
            role_ref_name="전체",
            context_text="검색 | 전체 | 구분",
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "1학점", "text": "1학점"},
            ],
            selected_value="전체",
        ),
        DOMElement(
            id=42,
            tag="select",
            role="combobox",
            text="전체",
            ref_id="right-select",
            role_ref_role="combobox",
            role_ref_name="전체",
            context_text="검색 | 전체 | 구분",
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "교양", "text": "교양"},
                {"value": "전심", "text": "전심"},
            ],
            selected_value="전체",
        ),
    ]

    rebound = _find_rebound_element(agent, prior, live_dom)

    assert rebound is not None
    assert rebound.ref_id == "right-select"


def test_execute_decision_repairs_select_target_when_value_matches_other_combobox(monkeypatch) -> None:
    agent = _FakeAgent()
    agent._element_selectors = {8: "select[data-filter='division']", 9: "select[data-filter='category']"}
    agent._element_full_selectors = {
        8: "main select[data-filter='division']",
        9: "main select[data-filter='category']",
    }
    agent._element_ref_ids = {8: "e33", 9: "e34"}
    dom_elements = [
        DOMElement(
            id=8,
            tag="select",
            role="combobox",
            text="전체",
            ref_id="e33",
            context_text="검색 | 전체 | 구분",
            role_ref_role="combobox",
            role_ref_name="전체",
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "전심", "text": "전심"},
            ],
            selected_value="전체",
        ),
        DOMElement(
            id=9,
            tag="select",
            role="combobox",
            text="전체",
            ref_id="e34",
            context_text="검색 | 전체 | 전공/교양",
            role_ref_role="combobox",
            role_ref_name="전체",
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "교양", "text": "교양"},
            ],
            selected_value="전체",
        ),
    ]
    decision = ActionDecision(action=ActionType.SELECT, ref_id="e33", value="교양", reasoning="change category filter")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "ref_id": ref_id, "value": value, "snapshot": agent_obj._active_snapshot_id})
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok")

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, dom_elements)

    assert ok is True
    assert err is None
    assert calls == [{"action": "select", "ref_id": "e34", "value": "교양", "snapshot": "snap-1"}]
    assert any("select 대상 재바인딩" in log for log in agent.logs)


def test_execute_decision_uses_safe_visual_coordinate_fallback(monkeypatch) -> None:
    class _VisionLLM:
        def find_element_coordinates(self, screenshot: str, description: str) -> dict[str, object]:
            assert screenshot == "shot"
            assert description == "낮은 가격순"
            return {"x": 120, "y": 240, "confidence": 0.93, "reasoning": "visible option"}

    class _CoordinateAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._browser_backend_name = "openclaw"
            self.llm = _VisionLLM()
            self.reason_codes: list[str] = []

        def _analyze_dom(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self._active_snapshot_id = "snap-2"
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
            self._element_ref_meta_by_id = {}
            self._last_snapshot_elements_by_ref = {}
            self._selector_to_ref_id = {}
            return []

        def _capture_screenshot(self) -> str:
            return "shot"

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

        def _contains_next_pagination_hint(self, _field: object) -> bool:
            return False

        def _is_numeric_page_label(self, _field: object) -> bool:
            return False

    monkeypatch.setenv("GAIA_VISUAL_COORDINATE_FALLBACK", "1")
    agent = _CoordinateAgent()
    prior = DOMElement(id=23, tag="button", role="option", text="낮은 가격순", ref_id="old-ref")
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-ref", reasoning="낮은 가격순 클릭")
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "ref_id": ref_id, "value": value})
        if action_name == "click":
            return ActionExecResult(
                success=False,
                effective=False,
                reason_code="action_timeout",
                reason='"old-ref"를 찾을 수 없거나 표시되지 않습니다. 최신 snapshot을 기반으로 요소를 다시 확인하세요.',
            )
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [prior])

    assert ok is True
    assert err is None
    assert calls[0]["action"] == "click"
    assert calls[1]["action"] == "evaluate"
    assert "elementFromPoint" in str(calls[1]["value"])
    assert agent._last_exec_result is not None
    assert agent._last_exec_result.state_change["visual_coordinate_fallback"] is True
    assert agent._last_exec_result.state_change["visual_target_label"] == "낮은 가격순"
    assert "ref_recovery_failed_resnapshot" in agent.reason_codes
    assert "visual_coordinate_fallback" in agent.reason_codes


def test_execute_decision_uses_visual_coordinate_fallback_when_ref_missing(monkeypatch) -> None:
    class _VisionLLM:
        def find_element_coordinates(self, screenshot: str, description: str) -> dict[str, object]:
            assert screenshot == "shot"
            assert description == "낮은 가격순"
            return {"x": 120, "y": 240, "confidence": 0.93, "reasoning": "visible option"}

    class _CoordinateAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._browser_backend_name = "openclaw"
            self.llm = _VisionLLM()
            self.reason_codes: list[str] = []

        def _capture_screenshot(self) -> str:
            return "shot"

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

        def _contains_next_pagination_hint(self, _field: object) -> bool:
            return False

        def _is_numeric_page_label(self, _field: object) -> bool:
            return False

    monkeypatch.setenv("GAIA_VISUAL_COORDINATE_FALLBACK", "1")
    agent = _CoordinateAgent()
    decision = ActionDecision(
        action=ActionType.CLICK,
        value="낮은 가격순",
        reasoning='화면의 "낮은 가격순" 옵션 클릭',
    )
    calls: list[dict[str, object]] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append({"action": action_name, "ref_id": ref_id, "value": value})
        return ActionExecResult(success=True, effective=True, reason_code="ok", reason="ok", state_change={})

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [])

    assert ok is True
    assert err is None
    assert [call["action"] for call in calls] == ["evaluate"]
    assert "elementFromPoint" in str(calls[0]["value"])
    assert agent._last_exec_result is not None
    assert agent._last_exec_result.state_change["visual_coordinate_fallback"] is True
    assert agent.reason_codes == ["visual_coordinate_fallback"]


def test_execute_decision_blocks_visual_coordinate_fallback_for_dangerous_label(monkeypatch) -> None:
    class _CoordinateAgent(_FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self._browser_backend_name = "openclaw"
            self.llm = object()
            self.reason_codes: list[str] = []
            self._selector_to_ref_id = {}

        def _analyze_dom(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
            self._element_ref_meta_by_id = {}
            self._last_snapshot_elements_by_ref = {}
            self._selector_to_ref_id = {}
            return []

        def _record_reason_code(self, code: str) -> None:
            self.reason_codes.append(code)

    monkeypatch.setenv("GAIA_VISUAL_COORDINATE_FALLBACK", "1")
    agent = _CoordinateAgent()
    prior = DOMElement(id=23, tag="button", text="결제하기", ref_id="old-ref")
    decision = ActionDecision(action=ActionType.CLICK, ref_id="old-ref", reasoning="결제하기 클릭")
    calls: list[str] = []

    def fake_execute_action(agent_obj, action_name, selector=None, full_selector=None, ref_id=None, value=None, **_kwargs):
        calls.append(action_name)
        return ActionExecResult(
            success=False,
            effective=False,
            reason_code="ref_stale",
            reason='Error: Unknown ref "old-ref". Run a new snapshot and use a ref from that snapshot.',
        )

    monkeypatch.setattr(runtime, "execute_action", fake_execute_action)

    ok, err = runtime.execute_decision(agent, decision, [prior])

    assert ok is False
    assert "[ref_stale]" in str(err)
    assert calls == ["click"]
    assert "ref_recovery_failed_resnapshot" in agent.reason_codes
    assert "visual_coordinate_fallback_blocked" in agent.reason_codes
