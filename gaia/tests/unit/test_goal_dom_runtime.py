from gaia.src.phase4.goal_driven.goal_dom_runtime import analyze_dom
from gaia.src.phase4.goal_driven.models import DOMElement


class _CachedDomAgent:
    def __init__(self) -> None:
        self.session_id = "sender-session"
        self.mcp_host_url = ""
        self._dom_cache_generation = 0
        self._dom_analyze_cache = {
            "key": (0, "sender-session", "", ""),
            "elements": [
                DOMElement(
                    id=3,
                    tag="button",
                    text="검색",
                    ref_id="e26",
                    is_visible=True,
                    is_enabled=True,
                )
            ],
            "snapshot_id": "snap-1",
            "dom_hash": "dom-1",
            "epoch": 1,
            "active_url": "https://example.com",
            "active_scope": "",
            "context_snapshot": {},
            "role_snapshot": {},
            "elements_by_ref": {
                "e26": {
                    "ref": "e26",
                    "selector": "button[name='검색']",
                    "full_selector": "main button[name='검색']",
                }
            },
            "evidence": {},
            "container_source_summary": {},
        }
        self._active_snapshot_id = ""
        self._active_dom_hash = ""
        self._active_snapshot_epoch = 0
        self._active_url = ""
        self._active_scoped_container_ref = ""
        self._last_context_snapshot = {}
        self._last_role_snapshot = {}
        self._last_snapshot_elements_by_ref = {}
        self._last_snapshot_evidence = {}
        self._last_container_source_summary = {}
        self._element_ref_meta_by_id = {}

    def _record_reason_code(self, _code: str) -> None:
        return None

    def _log(self, _message: str) -> None:
        return None


def test_analyze_dom_cache_hit_restores_ref_meta_index() -> None:
    agent = _CachedDomAgent()

    elements = analyze_dom(agent)

    assert len(elements) == 1
    assert agent._active_snapshot_id == "snap-1"
    assert agent._element_ref_meta_by_id[3]["selector"] == "button[name='검색']"
    assert agent._element_selectors[3] == "button[name='검색']"
    assert agent._element_full_selectors[3] == "main button[name='검색']"
    assert agent._element_ref_ids[3] == "e26"
    assert agent._selector_to_ref_id["button[name='검색']"] == "e26"


def test_analyze_dom_cache_key_is_scoped_by_session(monkeypatch) -> None:
    agent = _CachedDomAgent()
    agent.session_id = "receiver-session"
    calls = []

    class _Dispatch:
        status_code = 200
        text = ""
        payload = {
            "snapshot_id": "snap-receiver",
            "dom_hash": "dom-receiver",
            "epoch": 1,
            "url": "https://example.com/receiver",
            "elements": [
                {
                    "tag": "button",
                    "text": "수신자",
                    "selector": "button[name='수신자']",
                    "full_selector": "main button[name='수신자']",
                    "ref_id": "e99",
                    "attributes": {},
                    "is_visible": True,
                }
            ],
            "elements_by_ref": {
                "e99": {
                    "ref": "e99",
                    "selector": "button[name='수신자']",
                    "full_selector": "main button[name='수신자']",
                }
            },
        }

    def fake_execute(**kwargs):
        calls.append(kwargs)
        return _Dispatch()

    monkeypatch.setattr(
        "gaia.src.phase4.goal_driven.goal_dom_runtime.execute_mcp_action_with_recovery",
        fake_execute,
    )

    elements = analyze_dom(agent)

    assert len(elements) == 1
    assert elements[0].text == "수신자"
    assert agent._active_snapshot_id == "snap-receiver"
    assert calls[0]["params"]["session_id"] == "receiver-session"
