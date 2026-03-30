from gaia.src.phase4.goal_driven.filter_validation_engine import (
    _collect_result_rows,
    _is_noise_row_for_credit,
    _pick_filter_control,
)
from gaia.src.phase4.goal_driven.models import DOMElement


def test_collect_result_rows_prefers_openclaw_main_results_over_wishlist_side_list() -> None:
    dom = [
        DOMElement(
            id=1,
            tag="div",
            role="paragraph",
            text="3학점 | 박영은",
            container_name="(HUSS)디지털포용과스타트업창업실무",
            container_role="listitem",
            container_source="openclaw-role-tree",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="div",
            role="generic",
            text="(1학점) | (HUSS국립부경대)포용사회와문화탐방1 | 전심 | 검색 결과(총 695개 중 20개 표시)",
            container_name="검색 결과(총 695개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(1학점) | (HUSS국립부경대)포용사회와문화탐방1 | (HUSS국립부경대)포용사회와문화탐방1",
            context_score_hint=10.0,
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=3,
            tag="div",
            role="generic",
            text="(1학점) | 디지털교육 | 교직 | 검색 결과(총 695개 중 20개 표시)",
            container_name="검색 결과(총 695개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(1학점) | 디지털교육 | 디지털교육",
            context_score_hint=10.0,
            is_visible=True,
            is_enabled=True,
        ),
    ]

    rows = _collect_result_rows(dom)

    assert rows == [
        "(1학점) | (HUSS국립부경대)포용사회와문화탐방1 | 전심 | 검색 결과(총 695개 중 20개 표시)",
        "(1학점) | 디지털교육 | 교직 | 검색 결과(총 695개 중 20개 표시)",
    ]


def test_collect_result_rows_keeps_legacy_row_like_fallback_when_no_openclaw_main_rows() -> None:
    dom = [
        DOMElement(
            id=1,
            tag="li",
            role="listitem",
            text="3학점 | 자료구조 | 전공",
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="다음",
            is_visible=True,
            is_enabled=True,
        ),
    ]

    rows = _collect_result_rows(dom)

    assert rows == ["3학점 | 자료구조 | 전공"]


def test_credit_noise_gate_keeps_real_result_rows_even_with_total_count_suffix() -> None:
    row = "(1학점) | (HUSS국립부경대)포용사회와문화탐방1 | 전심 | 검색 결과(총 695개 중 20개 표시)"

    assert _is_noise_row_for_credit(row) is False


def test_pick_filter_control_respects_generic_include_exclude_terms() -> None:
    dom = [
        DOMElement(
            id=35,
            tag="select",
            role="combobox",
            text="전체",
            aria_label="학점 필터",
            container_name="검색 결과",
            context_text="검색 결과 | 학점 필터 | 결과 목록",
            ref_id="e35",
            is_visible=True,
            is_enabled=True,
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "1학점", "text": "1학점"},
                {"value": "2학점", "text": "2학점"},
            ],
        ),
        DOMElement(
            id=650,
            tag="select",
            role="combobox",
            text="12학점",
            aria_label="목표 학점",
            container_name="내 시간표",
            context_text="위시리스트 | 목표 학점 | 총 12학점",
            ref_id="e650",
            is_visible=True,
            is_enabled=True,
            options=[
                {"value": "12학점", "text": "12학점"},
                {"value": "13학점", "text": "13학점"},
            ],
        ),
    ]

    picked = _pick_filter_control(
        dom,
        "필터가 실제 결과와 맞게 동작하는지 의미 검증해줘.",
        preferred_control_hint={
            "include_terms": ["검색 결과", "학점 필터"],
            "exclude_terms": ["위시리스트", "목표 학점"],
        },
    )

    assert picked is dom[0]
