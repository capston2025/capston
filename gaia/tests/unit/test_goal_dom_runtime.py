from gaia.src.phase4.goal_driven.goal_dom_runtime import analyze_dom
from gaia.src.phase4.goal_driven.models import DOMElement


class _CachedDomAgent:
    def __init__(self) -> None:
        self._dom_cache_generation = 0
        self._dom_analyze_cache = {
            "key": (0, "", ""),
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
