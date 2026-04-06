from gaia.src.phase4.goal_driven.filter_validation_engine import (
    OptionTextConsistencyRule,
    _collect_option_cases,
    _collect_result_rows,
    _is_noise_row_for_result_validation,
    _pick_filter_control,
    build_filter_validation_config,
    run_filter_validation,
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


def test_result_validation_noise_gate_keeps_real_result_rows_even_with_total_count_suffix() -> None:
    row = "(1학점) | (HUSS국립부경대)포용사회와문화탐방1 | 전심 | 검색 결과(총 695개 중 20개 표시)"

    assert _is_noise_row_for_result_validation(row) is False


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


def test_collect_option_cases_skips_placeholder_option_matching_control_label() -> None:
    control = DOMElement(
        id=35,
        tag="select",
        role="combobox",
        text="학점",
        aria_label="학점 필터",
        placeholder="학점",
        is_visible=True,
        is_enabled=True,
        options=[
            {"value": "학점", "text": "학점"},
            {"value": "1학점", "text": "1학점"},
            {"value": "2학점", "text": "2학점"},
            {"value": "3학점", "text": "3학점"},
        ],
    )

    cases = _collect_option_cases(control)

    assert cases == [
        {"value": "1학점", "text": "1학점"},
        {"value": "2학점", "text": "2학점"},
        {"value": "3학점", "text": "3학점"},
    ]


def test_collect_option_cases_skips_credit_placeholder_even_when_control_label_differs() -> None:
    control = DOMElement(
        id=36,
        tag="select",
        role="combobox",
        text="2학점",
        aria_label="검색 조건",
        placeholder="",
        is_visible=True,
        is_enabled=True,
        options=[
            {"value": "학점", "text": "학점"},
            {"value": "1학점", "text": "1학점"},
            {"value": "2학점", "text": "2학점"},
            {"value": "3학점", "text": "3학점"},
        ],
    )

    cases = _collect_option_cases(control)

    assert cases == [
        {"value": "1학점", "text": "1학점"},
        {"value": "2학점", "text": "2학점"},
        {"value": "3학점", "text": "3학점"},
    ]


def test_option_text_consistency_samples_only_rows_with_any_option_evidence() -> None:
    rule = OptionTextConsistencyRule(
        match_ratio=0.6,
        option_profiles=[
            (["alpha"], ["alpha"]),
            (["beta"], ["beta"]),
        ],
    )

    success, _reason, evidence = rule.evaluate_rows(
        selected_text="alpha",
        selected_value="alpha",
        row_texts=[
            "검색 결과(총 20개 중 20개 표시)",
            "alpha | item one | active",
            "alpha | item two | active",
            "no explicit option label here",
        ],
    )

    assert success is True
    assert evidence["sampled_rows"] == 2
    assert evidence["matched_rows"] == 2


def test_option_text_consistency_treats_non_observable_option_rows_as_non_failing() -> None:
    rule = OptionTextConsistencyRule(
        match_ratio=0.6,
        option_profiles=[
            (["alpha"], ["alpha"]),
            (["beta"], ["beta"]),
        ],
    )

    success, _reason, evidence = rule.evaluate_rows(
        selected_text="alpha",
        selected_value="alpha",
        row_texts=[
            "result card | item one | visible",
            "result card | item two | visible",
        ],
    )

    assert success is True
    assert evidence["sampled_rows"] == 0
    assert evidence["non_observable_option"] is True


class _FakeFilterValidationAdapter:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.selected_value = "전체"

    def analyze_dom(self):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        dom = []
        for item in frame:
            if item.tag == "select":
                dom.append(item.model_copy(update={"selected_value": self.selected_value}))
            else:
                dom.append(item)
        return dom

    def apply_select(self, element_id: int, value: str):
        _ = element_id
        self.selected_value = value
        return {"success": True, "effective": True, "reason_code": "ok", "reason": "ok", "state_change": {}}

    def click_element(self, element_id: int):
        _ = element_id
        return {
            "success": True,
            "effective": True,
            "reason_code": "ok",
            "reason": "ok",
            "state_change": {},
            "before_url": "https://example.test/",
        }

    def scroll_for_pagination(self, anchor_element_id: int):
        _ = anchor_element_id
        return {"success": False, "effective": False, "reason_code": "not_found", "reason": "no scroll", "state_change": {}}

    def wait_for_pagination_probe(self, wait_ms: int = 900):
        _ = wait_ms
        if self.index < len(self.frames) - 1:
            self.index += 1
        return {"success": True, "effective": True, "reason_code": "ok", "reason": "ok", "state_change": {}}

    def reload_page(self, wait_ms: int = 900):
        _ = wait_ms
        return {"success": True, "effective": True, "reason_code": "ok", "reason": "ok", "state_change": {}}

    def resolve_ref(self, element_id: int) -> str:
        return f"e{element_id}"

    def current_url(self) -> str:
        return "https://example.test/"

    def record_reason(self, code: str) -> None:
        _ = code

    def log(self, message: str) -> None:
        _ = message

    def capture_case_attachment(self, label: str):
        _ = label
        return None


def test_run_filter_validation_waits_for_stable_non_loading_rows_before_failing() -> None:
    select = DOMElement(
        id=35,
        tag="select",
        role="combobox",
        text="학점",
        aria_label="학점 필터",
        container_name="검색 결과",
        context_text="검색 결과 | 학점 필터 | 결과 목록",
        ref_id="e35",
        is_visible=True,
        is_enabled=True,
        options=[
            {"value": "전체", "text": "전체"},
            {"value": "2학점", "text": "2학점"},
            {"value": "3학점", "text": "3학점"},
        ],
        selected_value="전체",
    )
    loading_rows = [
        DOMElement(
            id=101,
            tag="div",
            role="generic",
            text="(1학점) | stale row | 검색 결과(총 695개 중 20개 표시) 로딩 중...",
            container_name="검색 결과",
            container_role="main",
            container_source="openclaw-role-tree",
            context_score_hint=10.0,
            is_visible=True,
            is_enabled=True,
        )
    ]
    stable_rows = [
        DOMElement(
            id=201,
            tag="div",
            role="generic",
            text="(2학점) | 자료구조 | 전공",
            container_name="검색 결과",
            container_role="main",
            container_source="openclaw-role-tree",
            context_score_hint=10.0,
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=202,
            tag="div",
            role="generic",
            text="(2학점) | 운영체제 | 전공",
            container_name="검색 결과",
            container_role="main",
            container_source="openclaw-role-tree",
            context_score_hint=10.0,
            is_visible=True,
            is_enabled=True,
        ),
        DOMElement(
            id=301,
            tag="button",
            role="button",
            text="다음",
            container_name="검색 결과",
            container_role="main",
            is_visible=True,
            is_enabled=True,
        ),
    ]
    adapter = _FakeFilterValidationAdapter(
        frames=[
            [select, *loading_rows],
            [select, *stable_rows],
            [select, *stable_rows],
        ]
    )

    report = run_filter_validation(
        adapter=adapter,
        goal_text="학점 필터가 실제 결과 과목의 학점과 맞게 동작하는지 의미 검증해줘.",
        config=build_filter_validation_config(
            max_pages=1,
            max_cases=1,
            validation_contract={
                "required_options": [{"value": "2학점", "text": "2학점"}],
            },
            preferred_control_hint={
                "ref_id": "e35",
                "include_terms": ["검색 결과", "학점 필터"],
            },
            selection_probe_schedule_ms=[100],
            result_probe_schedule_ms=[100, 100],
            capture_case_screenshots=False,
        ),
    )

    assert report["success"] is True
    page1_check = next(
        row for row in report["checks"] if row["check_id"] == "case_1_result_consistency_page1"
    )
    assert page1_check["status"] == "pass"
    assert page1_check["evidence"]["stability"]["stable"] is True
