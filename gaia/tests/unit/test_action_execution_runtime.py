from gaia.src.phase4.goal_driven import action_execution_runtime as runtime
from gaia.src.phase4.goal_driven.action_execution_runtime import _find_rebound_element, _is_stale_like_timeout
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

    def fake_execute_mcp_action_with_recovery(*, raw_base_url, action, params, timeout, attempts, is_transport_error, recover_host, context):
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
