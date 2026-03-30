from gaia.src.phase4.goal_driven.filter_validation_engine import _pick_filter_control
from gaia.src.phase4.goal_driven.filter_validation_runtime import (
    build_filter_validation_contract,
    filter_validation_contract_needs_refresh,
)
from gaia.src.phase4.goal_driven.models import DOMElement, TestGoal as GoalModel


class _DummyAgent:
    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _call_llm_text_only(_prompt: str) -> str:
        raise RuntimeError("skip llm")

    @staticmethod
    def _log(_message: str) -> None:
        return None


def _search_credit_filter() -> DOMElement:
    return DOMElement(
        id=35,
        tag="select",
        role="combobox",
        text="전체",
        aria_label="학점 필터",
        container_name="검색 결과",
        context_text="검색 결과 | 학점 필터 | 결과 목록",
        role_ref_name="전체",
        ref_id="e35",
        is_visible=True,
        is_enabled=True,
        options=[
            {"value": "전체", "text": "전체"},
            {"value": "1학점", "text": "1학점"},
            {"value": "2학점", "text": "2학점"},
            {"value": "3학점", "text": "3학점"},
        ],
    )


def _wishlist_credit_target() -> DOMElement:
    return DOMElement(
        id=650,
        tag="select",
        role="combobox",
        text="12학점",
        aria_label="목표 학점",
        container_name="내 시간표",
        context_text="위시리스트 | 목표 학점 | 총 12학점",
        role_ref_name="12학점",
        ref_id="e650",
        is_visible=True,
        is_enabled=True,
        options=[
            {"value": "12학점", "text": "12학점"},
            {"value": "13학점", "text": "13학점"},
            {"value": "14학점", "text": "14학점"},
        ],
    )


def _preferred_hint_from(element: DOMElement) -> dict:
    return {
        "ref_id": element.ref_id,
        "container_name": element.container_name,
        "context_text": element.context_text,
        "role_ref_name": element.role_ref_name,
        "selected_value": element.selected_value,
        "option_signature": [str(item.get("text") or item.get("value") or "") for item in list(element.options or [])],
    }


def test_build_filter_validation_contract_prefers_recently_selected_control() -> None:
    agent = _DummyAgent()
    goal = GoalModel(
        id="G1",
        name="학점 필터 의미 검증",
        description="학점 필터가 실제 결과 과목의 학점과 맞게 동작하는지 의미 검증해줘.",
        test_data={
            "filter_control_hint": {
                "include_terms": ["검색 결과", "학점 필터"],
                "exclude_terms": ["위시리스트", "목표 학점"],
            }
        },
    )
    search_filter = _search_credit_filter()
    wishlist_filter = _wishlist_credit_target()

    contract = build_filter_validation_contract(
        agent,
        goal=goal,
        dom_elements=[wishlist_filter, search_filter],
        preferred_control_hint=_preferred_hint_from(search_filter),
    )

    values = [str(item.get("value") or "") for item in list(contract.get("required_options") or [])]
    assert values == ["1학점", "2학점", "3학점"]
    assert contract["control_ref_id"] == "e35"
    assert dict(contract.get("control_hint") or {}).get("ref_id") == "e35"


def test_pick_filter_control_uses_generic_include_exclude_hint() -> None:
    search_filter = _search_credit_filter()
    wishlist_filter = _wishlist_credit_target()
    picked_with_hint = _pick_filter_control(
        [wishlist_filter, search_filter],
        "필터가 실제 결과와 맞게 동작하는지 의미 검증해줘.",
        preferred_control_hint={
            "include_terms": ["검색 결과", "학점 필터"],
            "exclude_terms": ["위시리스트", "목표 학점"],
        },
    )

    assert picked_with_hint is search_filter


class _NoLlmAgent(_DummyAgent):
    llm_calls = 0

    @classmethod
    def _call_llm_text_only(cls, _prompt: str) -> str:
        cls.llm_calls += 1
        raise AssertionError("credit contract should not call llm")


def test_build_filter_validation_contract_uses_deterministic_credit_options_without_llm() -> None:
    _NoLlmAgent.llm_calls = 0
    goal = GoalModel(
        id="G2",
        name="학점 필터 의미 검증",
        description="학점 필터가 실제 결과 과목의 학점과 맞게 동작하는지 의미 검증해줘.",
        test_data={
            "filter_control_hint": {
                "include_terms": ["검색 결과", "학점 필터"],
                "exclude_terms": ["위시리스트", "목표 학점"],
            }
        },
    )

    contract = build_filter_validation_contract(
        _NoLlmAgent(),
        goal=goal,
        dom_elements=[_wishlist_credit_target(), _search_credit_filter()],
    )

    assert contract["source"] == "deterministic_control_options"
    assert [str(item.get("value") or "") for item in contract["required_options"]] == ["1학점", "2학점", "3학점"]
    assert _NoLlmAgent.llm_calls == 0


def test_filter_validation_contract_refreshes_when_recent_control_ref_changes() -> None:
    agent = _DummyAgent()
    goal = GoalModel(
        id="G3",
        name="필터 의미 검증",
        description="필터 의미 검증",
        test_data={},
    )
    stale_contract = build_filter_validation_contract(
        agent,
        goal=goal,
        dom_elements=[_wishlist_credit_target()],
        preferred_control_hint=_preferred_hint_from(_wishlist_credit_target()),
    )

    assert filter_validation_contract_needs_refresh(
        agent,
        stale_contract,
        _preferred_hint_from(_search_credit_filter()),
    ) is True


def test_filter_validation_contract_keeps_same_control_even_if_selected_value_changes() -> None:
    agent = _DummyAgent()
    goal = GoalModel(
        id="G4",
        name="필터 의미 검증",
        description="필터 의미 검증",
        test_data={},
    )
    search_filter = _search_credit_filter()
    contract = build_filter_validation_contract(
        agent,
        goal=goal,
        dom_elements=[search_filter],
        preferred_control_hint=_preferred_hint_from(search_filter),
    )
    next_hint = _preferred_hint_from(search_filter)
    next_hint["selected_value"] = "2학점"

    assert filter_validation_contract_needs_refresh(agent, contract, next_hint) is False
