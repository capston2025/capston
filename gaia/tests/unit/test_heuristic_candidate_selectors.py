from __future__ import annotations

from gaia.src.phase4.goal_driven.heuristic_candidate_selectors import pick_collect_element
from gaia.src.phase4.goal_driven.models import DOMElement


class _FakeCollectAgent:
    def __init__(self) -> None:
        self._recent_click_element_ids = [1]
        self._element_ref_ids = {1: "e67", 2: "e68"}
        self._element_full_selectors = {}
        self._element_selectors = {}

    @staticmethod
    def _fields_for_element(el: DOMElement) -> list[str]:
        return [
            str(el.text or ""),
            str(el.aria_label or ""),
            str(getattr(el, "title", None) or ""),
            str(getattr(el, "context_text", None) or ""),
            str(getattr(el, "container_name", None) or ""),
        ]

    @staticmethod
    def _is_ref_temporarily_blocked(ref_id: str | None) -> bool:
        return False

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _goal_overlap_score(*_args: object) -> float:
        return 0.0

    @staticmethod
    def _selector_bias_for_fields(_fields: list[str]) -> float:
        return 0.0

    @staticmethod
    def _adaptive_intent_bias(_key: str) -> float:
        return 0.0

    @staticmethod
    def _candidate_intent_key(action: str, _fields: list[str]) -> str:
        return action

    @staticmethod
    def _clamp_score(score: float, low: float = -20.0, high: float = 30.0) -> float:
        return max(low, min(high, score))


def test_pick_collect_element_prefers_unseen_candidate_over_recent_repeat() -> None:
    agent = _FakeCollectAgent()
    dom = [
        DOMElement(id=1, tag="button", role="button", text="담기", ref_id="e67", is_visible=True, is_enabled=True),
        DOMElement(id=2, tag="button", role="button", text="담기", ref_id="e68", is_visible=True, is_enabled=True),
    ]

    picked = pick_collect_element(agent, dom)

    assert picked is not None
    assert picked[0] == 2
    assert "새 수집 후보를 우선" in picked[1]


def test_pick_collect_element_falls_back_to_repeat_when_only_one_candidate_exists() -> None:
    agent = _FakeCollectAgent()
    dom = [
        DOMElement(id=1, tag="button", role="button", text="담기", ref_id="e67", is_visible=True, is_enabled=True),
    ]

    picked = pick_collect_element(agent, dom)

    assert picked is not None
    assert picked[0] == 1
