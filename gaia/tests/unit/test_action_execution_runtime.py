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
        self._browser_backend_name = ""
        self._goal_policy_phase = ""
        self._goal_phase_intent = ""
        self._active_snapshot_id = "snap-1"
        self._element_selectors = {23: "button[data-course='target']"}
        self._element_full_selectors = {23: "main button[data-course='target']"}
        self._element_ref_ids = {23: "old-ref"}
        self._selector_to_ref_id = {"main button[data-course='target']": "new-ref"}
        self.logs: list[str] = []
        self._last_exec_result = None

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
    assert calls == ["old-ref", "new-ref"]
