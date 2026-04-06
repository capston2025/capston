from __future__ import annotations

import re
from types import SimpleNamespace

from gaia.src.phase4.goal_driven.auth_hints import contains_login_hint, contains_next_pagination_hint, is_numeric_page_label
from gaia.src.phase4.goal_driven.dom_prompt_formatting import (
    _compute_delta_snapshot,
    _goal_requires_full_raw_snapshot,
    context_score,
    detect_active_surface_context,
    fields_for_element,
    format_dom_for_llm,
    semantic_tags_for_element,
)
from gaia.src.phase4.goal_driven.models import DOMElement


class _FakeAgent:
    def __init__(self) -> None:
        self._goal_semantics = SimpleNamespace(target_terms=["포용사회와문화탐방1"], destination_terms=["시간표", "내 시간표"])
        self._goal_tokens = {"포용사회와문화탐방1", "바로", "추가", "시간표"}
        self._runtime_phase = "COLLECT"
        self._browser_backend_name = "openclaw"
        self._goal_constraints = {}
        self._last_role_snapshot = {
            "snapshot": "",
            "tree": [
                {"role": "button", "name": "바로 추가", "ref": "e213", "depth": 6, "ancestor_names": ["검색 결과", "(HUSS국립부경대)포용사회와문화탐방1"]},
                {"role": "textbox", "name": "아이디를 입력하세요", "ref": "e677", "depth": 1, "ancestor_names": ["로그인"]},
                {"role": "textbox", "name": "비밀번호를 입력하세요", "ref": "e680", "depth": 1, "ancestor_names": ["로그인"]},
                {"role": "button", "name": "로그인", "ref": "e681", "depth": 1, "ancestor_names": ["로그인"]},
            ],
            "refs_mode": "aria",
            "stats": {"lines": 4, "refs": 4, "interactive": 4},
        }
        self._last_context_snapshot = {}
        self._element_full_selectors = {}
        self._element_selectors = {}
        self._last_dom_top_ids = []
        self._recent_click_element_ids = []
        self._active_goal_text = "포용사회와문화탐방1 과목의 '바로 추가' 버튼을 눌러서 내 시간표에 반영"
        self._last_snapshot_evidence = {}
        self._prev_raw_snapshot_text = ""

    def _normalize_text(self, value: object) -> str:
        return str(value or "").strip().lower()

    def _tokenize_text(self, value: object) -> list[str]:
        return [token for token in re.split(r"[^0-9A-Za-z가-힣]+", self._normalize_text(value)) if token]

    def _fields_for_element(self, el: DOMElement) -> list[str]:
        return fields_for_element(self, el)

    def _contains_progress_cta_hint(self, value: object) -> bool:
        return False

    def _contains_next_pagination_hint(self, value: object) -> bool:
        return contains_next_pagination_hint(value, self._normalize_text)

    def _contains_context_shift_hint(self, value: object) -> bool:
        return False

    def _contains_expand_hint(self, value: object) -> bool:
        return False

    def _contains_wishlist_like_hint(self, value: object) -> bool:
        return False

    def _contains_add_like_hint(self, value: object) -> bool:
        return any(token in self._normalize_text(value) for token in ("바로 추가", "추가", "담기", "add"))

    def _contains_login_hint(self, value: object) -> bool:
        return contains_login_hint(value, self._normalize_text)

    def _contains_configure_hint(self, value: object) -> bool:
        return False

    def _contains_execute_hint(self, value: object) -> bool:
        return False

    def _contains_apply_hint(self, value: object) -> bool:
        return False

    def _context_score(self, el: DOMElement) -> float:
        return context_score(self, el)

    def _selector_bias_for_fields(self, fields: list[str]) -> float:
        return 0.0

    def _adaptive_intent_bias(self, key: str) -> float:
        return 0.0

    def _candidate_intent_key(self, action: str, fields: list[str]) -> str:
        return f"{action}:noop"

    def _clamp_score(self, score: float, low: float = -25.0, high: float = 35.0) -> float:
        return max(low, min(high, score))

    def _is_numeric_page_label(self, value: object) -> bool:
        return is_numeric_page_label(str(value or ""))


def test_semantic_tags_include_auth_field_hints():
    agent = _FakeAgent()
    username_input = DOMElement(
        id=0,
        tag="input",
        role="textbox",
        ref_id="u0",
        container_name="로그인",
        container_role="div",
        group_action_labels=["로그인"],
        type="text",
        role_ref_nth=0,
    )
    password_input = DOMElement(
        id=1,
        tag="input",
        role="textbox",
        ref_id="p0",
        container_name="로그인",
        container_role="div",
        group_action_labels=["로그인"],
        role_ref_nth=1,
    )
    submit_button = DOMElement(
        id=2,
        tag="button",
        role="button",
        text="로그인",
        ref_id="b0",
        container_name="로그인",
        container_role="div",
        group_action_labels=["로그인"],
    )

    assert "auth_identifier_field" in semantic_tags_for_element(agent, username_input)
    assert "auth_password_field" in semantic_tags_for_element(agent, password_input)
    assert "auth_submit_candidate" in semantic_tags_for_element(agent, submit_button)


def test_semantic_tags_do_not_promote_username_field_to_password_from_shared_context():
    agent = _FakeAgent()
    username_input = DOMElement(
        id=10,
        tag="input",
        role="textbox",
        text="",
        aria_label="아이디를 입력하세요",
        placeholder="아이디를 입력하세요",
        ref_id="u10",
        container_name="로그인",
        container_role="div",
        context_text="로그인 | 아이디 | 비밀번호 | 계정이 없으신가요? 회원가입",
        role_ref_name="아이디를 입력하세요",
        role_ref_nth=0,
        type="text",
    )
    password_input = DOMElement(
        id=11,
        tag="input",
        role="textbox",
        text="",
        aria_label="비밀번호를 입력하세요",
        placeholder="비밀번호를 입력하세요",
        ref_id="p11",
        container_name="로그인",
        container_role="div",
        context_text="로그인 | 아이디 | 비밀번호 | 계정이 없으신가요? 회원가입",
        role_ref_name="비밀번호를 입력하세요",
        role_ref_nth=1,
        type="text",
    )

    assert "auth_identifier_field" in semantic_tags_for_element(agent, username_input)
    assert "auth_password_field" not in semantic_tags_for_element(agent, username_input)
    assert "auth_password_field" in semantic_tags_for_element(agent, password_input)


def test_semantic_tags_do_not_mark_course_search_input_as_auth_identifier_from_background_actions():
    agent = _FakeAgent()
    course_search_input = DOMElement(
        id=12,
        tag="input",
        role="textbox",
        text="",
        aria_label="과목명 검색",
        placeholder="과목명으로 검색...",
        ref_id="s12",
        container_name="과목 검색",
        container_role="div",
        context_text="과목 검색 | 강의평 | 내 시간표 월 화 수 목 금 토 1교시 2교시",
        group_action_labels=["로그인", "검색", "강의평", "담기", "바로 추가"],
        role_ref_name="과목명 검색",
        role_ref_nth=0,
        type="text",
    )

    tags = semantic_tags_for_element(agent, course_search_input)

    assert "auth_identifier_field" not in tags
    assert "auth_password_field" not in tags


def test_semantic_tags_include_feedback_success_signal_for_added_to_timetable_toast():
    agent = _FakeAgent()
    success_toast = DOMElement(
        id=9,
        tag="div",
        role="generic",
        text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\" | X | 과목 검색",
        aria_label="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\" | X | 과목 검색",
        ref_id="e4",
        container_role="generic",
        container_source="openclaw-role-tree",
        context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\" | X",
        role_ref_role="generic",
        role_ref_name="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목을 시간표에 추가했어요!\"",
    )

    tags = semantic_tags_for_element(agent, success_toast)

    assert "feedback_success_signal" in tags
    assert "target_match" in tags


def test_semantic_tags_include_openclaw_auth_hints_without_container_name():
    agent = _FakeAgent()
    username_input = DOMElement(
        id=4,
        tag="input",
        role="textbox",
        text="아이디를 입력하세요",
        aria_label="아이디를 입력하세요",
        placeholder="아이디를 입력하세요",
        title="아이디를 입력하세요",
        ref_id="e677",
        container_role="generic",
        container_source="openclaw-role-tree",
        role_ref_role="textbox",
        role_ref_name="아이디를 입력하세요",
    )
    password_input = DOMElement(
        id=6,
        tag="input",
        role="textbox",
        text="비밀번호를 입력하세요",
        aria_label="비밀번호를 입력하세요",
        placeholder="비밀번호를 입력하세요",
        title="비밀번호를 입력하세요",
        ref_id="e680",
        container_role="generic",
        container_source="openclaw-role-tree",
        role_ref_role="textbox",
        role_ref_name="비밀번호를 입력하세요",
    )
    submit_button = DOMElement(
        id=7,
        tag="button",
        role="button",
        text="로그인",
        aria_label="로그인",
        title="로그인",
        ref_id="e681",
        container_role="generic",
        container_source="openclaw-role-tree",
        role_ref_role="button",
        role_ref_name="로그인",
    )

    assert "auth_identifier_field" in semantic_tags_for_element(agent, username_input)
    assert "auth_password_field" in semantic_tags_for_element(agent, password_input)
    assert "auth_submit_candidate" in semantic_tags_for_element(agent, submit_button)


def test_format_dom_for_llm_prioritizes_auth_controls_over_background_add_buttons():
    agent = _FakeAgent()
    elements = [
        DOMElement(
            id=74,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e213",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
        DOMElement(
            id=4,
            tag="input",
            role="textbox",
            text="아이디를 입력하세요",
            aria_label="아이디를 입력하세요",
            placeholder="아이디를 입력하세요",
            title="아이디를 입력하세요",
            ref_id="e677",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="textbox",
            role_ref_name="아이디를 입력하세요",
        ),
        DOMElement(
            id=6,
            tag="input",
            role="textbox",
            text="비밀번호를 입력하세요",
            aria_label="비밀번호를 입력하세요",
            placeholder="비밀번호를 입력하세요",
            title="비밀번호를 입력하세요",
            ref_id="e680",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="textbox",
            role_ref_name="비밀번호를 입력하세요",
        ),
        DOMElement(
            id=7,
            tag="button",
            role="button",
            text="로그인",
            aria_label="로그인",
            title="로그인",
            ref_id="e681",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="로그인",
        ),
    ]

    prompt = format_dom_for_llm(agent, elements)

    assert prompt.index('[ref=e213] <button> within=') < prompt.index('[ref=e677] <input> "아이디를 입력하세요"')
    assert prompt.index('[ref=e677] <input> "아이디를 입력하세요"') < prompt.index('[ref=e681] <button> "로그인"')
    assert 'semantics=[auth_submit_candidate]' in prompt
    assert 'semantics=[auth_identifier_field]' in prompt


def test_format_dom_for_llm_uses_openclaw_raw_role_tree_as_primary_input():
    agent = _FakeAgent()
    agent._last_role_snapshot = {
        "snapshot": "\n".join(
            [
                '- heading "과목 검색" [ref=e10]',
                '  - paragraph "(HUSS국립부경대)포용사회와문화탐방1" [ref=e193]',
                '  - button "바로 추가" [ref=e213]',
            ]
        ),
        "tree": [
            {"role": "heading", "name": "과목 검색", "ref": "e10", "depth": 0, "ancestor_names": []},
            {"role": "paragraph", "name": "(HUSS국립부경대)포용사회와문화탐방1", "ref": "e193", "depth": 1, "ancestor_names": ["과목 검색"]},
            {"role": "button", "name": "바로 추가", "ref": "e213", "depth": 1, "ancestor_names": ["과목 검색", "(HUSS국립부경대)포용사회와문화탐방1"]},
        ],
        "ref_line_index": {"e10": 0, "e193": 1, "e213": 2},
        "refs_mode": "aria",
        "stats": {"lines": 3, "refs": 3, "interactive": 1},
    }
    elements = [
        DOMElement(
            id=74,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e213",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
        DOMElement(
            id=75,
            tag="div",
            role="paragraph",
            text="(HUSS국립부경대)포용사회와문화탐방1",
            aria_label="(HUSS국립부경대)포용사회와문화탐방1",
            title="(HUSS국립부경대)포용사회와문화탐방1",
            ref_id="e193",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | (1학점)",
            role_ref_role="paragraph",
            role_ref_name="(HUSS국립부경대)포용사회와문화탐방1",
        ),
    ]

    prompt = format_dom_for_llm(agent, elements)

    assert "## OpenClaw 원본 역할 트리 (주 입력)" in prompt
    assert '  - button "바로 추가" [ref=e213]' in prompt
    assert "## 구조화 보조 힌트" not in prompt


def test_format_dom_for_llm_uses_scoped_snapshot_as_primary_when_scope_applied():
    agent = _FakeAgent()
    agent._last_role_snapshot = {
        "snapshot": "\n".join(
            [
                '- heading "과목 검색" [ref=e10]',
                '  - button "다른 버튼" [ref=e999]',
                '  - button "바로 추가" [ref=e213]',
            ]
        ),
        "scoped_snapshot": '- button "바로 추가" [ref=e213]',
        "scope_applied": True,
        "scope_container_ref_id": "ctx-44",
        "ref_line_index": {"e10": 0, "e999": 1, "e213": 2},
        "refs_mode": "aria",
        "stats": {"lines": 3, "refs": 3, "interactive": 2},
    }
    elements = [
        DOMElement(
            id=74,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e213",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
    ]

    prompt = format_dom_for_llm(agent, elements)

    assert "## OpenClaw scope 역할 트리 (주 입력)" in prompt
    assert '- button "바로 추가" [ref=e213]' in prompt
    assert '[ref=e999]' not in prompt


def test_format_dom_for_llm_caps_openclaw_structured_hints_to_small_sidecar():
    agent = _FakeAgent()
    agent._last_role_snapshot = None
    elements = [
        DOMElement(
            id=index,
            tag="button",
            role="button",
            text=f"CTA {index}",
            aria_label=f"CTA {index}",
            title=f"CTA {index}",
            ref_id=f"e{index}",
            container_name="검색 결과",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text=f"검색 결과 | CTA {index}",
            role_ref_role="button",
            role_ref_name=f"CTA {index}",
        )
        for index in range(1, 41)
    ]

    prompt = format_dom_for_llm(agent, elements)
    structured_lines = [
        line
        for line in prompt.splitlines()
        if line.startswith("[ref=e")
    ]

    assert "## 구조화 보조 힌트" in prompt
    assert len(structured_lines) == 40


def test_format_dom_for_llm_openclaw_fallback_keeps_original_element_order_without_rerank():
    agent = _FakeAgent()
    agent._last_role_snapshot = None
    elements = [
        DOMElement(
            id=1,
            tag="select",
            role="combobox",
            text="📊 학점 (전체)",
            aria_label="📊 학점 (전체)",
            title="📊 학점 (전체)",
            ref_id="e35",
            container_name="검색",
            container_role="form",
            container_source="openclaw-role-tree",
            context_text="검색 | 🏫 학과 (전체)",
            role_ref_role="combobox",
            role_ref_name="📊 학점 (전체)",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e72",
            container_name="검색 결과",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="검색 결과 | (HUSS국립부경대)포용사회와문화탐방1",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
    ]

    prompt = format_dom_for_llm(agent, elements)

    assert "## 구조화 보조 힌트" in prompt
    assert prompt.index('[ref=e35] <select> "📊 학점 (전체)"') < prompt.index('[ref=e72] <button> within="검색 결과"')


def test_format_dom_for_llm_keeps_select_option_line_with_parent_combobox_in_raw_tree() -> None:
    agent = _FakeAgent()
    agent._active_goal_text = "구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증"
    agent._goal_tokens = {"구분", "전공", "교양", "필터", "결과"}
    agent._goal_semantics = SimpleNamespace(target_terms=["구분", "전공", "교양"], destination_terms=[])
    agent._last_role_snapshot = {
        "snapshot": "\n".join(
            [
                '- generic [ref=e30]',
                '  - combobox "전체" [ref=e33]',
                '    - option "전체"',
                '    - option "교양"',
                '    - option "전심"',
                '  - combobox "전체" [ref=e35]',
                '    - option "1학점"',
            ]
        ),
        "tree": [],
        "ref_line_index": {"e30": 0, "e33": 1, "e35": 5},
        "refs_mode": "aria",
        "stats": {"lines": 7, "refs": 3, "interactive": 2},
    }
    elements = [
        DOMElement(
            id=8,
            tag="select",
            role="combobox",
            text="전체",
            aria_label="전체",
            title="전체",
            ref_id="e33",
            context_text="검색 | 전체 | 구분",
            role_ref_role="combobox",
            role_ref_name="전체",
            options=[
                {"value": "전체", "text": "전체"},
                {"value": "교양", "text": "교양"},
                {"value": "전심", "text": "전심"},
            ],
            selected_value="전체",
        )
    ]

    prompt = format_dom_for_llm(agent, elements)

    assert '  - combobox "전체" [ref=e33]' in prompt
    assert '    - option "교양"' in prompt
    assert prompt.index('  - combobox "전체" [ref=e33]') < prompt.index('    - option "교양"')
    assert '  - combobox "전체" [ref=e35]' in prompt
    assert '    - option "1학점"' in prompt
    assert "## 구조화 보조 힌트" not in prompt


def test_format_dom_for_llm_prioritizes_destination_inspection_over_close_on_conflict_signal():
    agent = _FakeAgent()
    elements = [
        DOMElement(
            id=1,
            tag="div",
            role="generic",
            text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X | 과목 검색",
            aria_label="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X | 과목 검색",
            ref_id="e4",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\" | X",
            role_ref_role="generic",
            role_ref_name="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\"",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="X",
            aria_label="X",
            title="X",
            ref_id="e5",
            container_role="generic",
            container_source="openclaw-role-tree",
            context_text="\"'(HUSS국립부경대)포용사회와문화탐방1' 과목은 기존 시간표와 시간이 겹쳐요! (서버 검사)\"",
            role_ref_role="button",
            role_ref_name="X",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="내 시간표 보기 (10)",
            aria_label="내 시간표 보기 (10)",
            title="내 시간표 보기 (10)",
            ref_id="e724",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="내 시간표 보기 (10)",
        ),
    ]

    prompt = format_dom_for_llm(agent, elements)

    assert "close_like" in prompt
    assert prompt.index('[ref=e5] <button> "X"') < prompt.index('[ref=e724] <button> "내 시간표 보기 (10)"')
    assert "destination_reveal_candidate" in prompt


def test_format_dom_for_llm_marks_surface_close_and_occluded_background_when_destination_surface_active():
    agent = _FakeAgent()
    agent._last_snapshot_evidence = {"modal_open": True}
    elements = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="내 시간표",
            aria_label="내 시간표",
            title="내 시간표",
            ref_id="e704",
            container_role="generic",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            ref_id="e706",
            container_role="generic",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="시간표에서 제거",
            aria_label="시간표에서 제거",
            title="시간표에서 제거",
            ref_id="e753",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="시간표에서 제거",
        ),
        DOMElement(
            id=4,
            tag="button",
            role="button",
            text="바로 추가",
            aria_label="바로 추가",
            title="바로 추가",
            ref_id="e72",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="바로 추가",
        ),
    ]

    surface = detect_active_surface_context(agent, elements)
    prompt = format_dom_for_llm(agent, elements)

    assert surface["active"] is True
    assert prompt.index('[ref=e706] <button>') < prompt.index('[ref=e72] <button> within=')
    assert 'semantics=[surface_close_candidate]' in prompt
    assert 'semantics=[target_match | source_mutation_candidate | occluded_background_candidate]' in prompt


def test_detect_active_surface_context_ignores_persistent_wishlist_sidebar_without_modal_or_close() -> None:
    agent = _FakeAgent()
    agent._goal_semantics = SimpleNamespace(target_terms=["포용사회와문화탐방1"], destination_terms=["위시리스트", "wishlist"])
    elements = [
        DOMElement(
            id=1,
            tag="h3",
            role="heading",
            text="위시리스트",
            aria_label="위시리스트",
            title="위시리스트",
            ref_id="e640",
            container_role="complementary",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            text="위시리스트 확장 보기",
            aria_label="위시리스트 확장 보기",
            title="위시리스트 확장 보기",
            ref_id="e642",
            container_name="위시리스트",
            container_role="complementary",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="위시리스트 확장 보기",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="담기",
            aria_label="담기",
            title="담기",
            ref_id="e67",
            container_name="검색 결과(총 2,894개 중 20개 표시)",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="(HUSS국립부경대)포용사회와문화탐방1 | 검색 결과",
            role_ref_role="button",
            role_ref_name="담기",
        ),
    ]

    surface = detect_active_surface_context(agent, elements)

    assert surface["active"] is False


def test_detect_active_surface_context_uses_goal_destination_terms_without_domain_keywords() -> None:
    agent = _FakeAgent()
    agent._goal_semantics = SimpleNamespace(
        target_terms=["Quarterly Report"],
        destination_terms=["Review Queue", "Pending Review"],
    )
    agent._last_snapshot_evidence = {"modal_open": True}
    elements = [
        DOMElement(
            id=1,
            tag="h2",
            role="heading",
            text="Review Queue",
            aria_label="Review Queue",
            title="Review Queue",
            ref_id="e901",
            container_role="generic",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=2,
            tag="button",
            role="button",
            ref_id="e902",
            container_role="generic",
            container_source="openclaw-role-tree",
        ),
        DOMElement(
            id=3,
            tag="button",
            role="button",
            text="Open item",
            aria_label="Open item",
            title="Open item",
            ref_id="e903",
            container_name="Review Queue",
            container_role="generic",
            container_source="openclaw-role-tree",
            role_ref_role="button",
            role_ref_name="Open item",
        ),
        DOMElement(
            id=4,
            tag="button",
            role="button",
            text="Add to queue",
            aria_label="Add to queue",
            title="Add to queue",
            ref_id="e904",
            container_name="Search results",
            container_role="main",
            container_source="openclaw-role-tree",
            context_text="Quarterly Report | Search results",
            role_ref_role="button",
            role_ref_name="Add to queue",
        ),
    ]

    surface = detect_active_surface_context(agent, elements)

    assert surface["active"] is True
    assert str(getattr(surface["heading"], "ref_id", "")) == "e901"
    assert {str(getattr(el, "ref_id", "")) for el in surface["background_elements"]} == {"e904"}


# --- Delta snapshot compression tests ---


def test_compute_delta_snapshot_identical():
    """동일 snapshot은 빈 delta와 change_ratio 0.0을 반환한다."""
    lines = ["line1", "line2", "line3"]
    delta, ratio = _compute_delta_snapshot(lines, lines)
    assert delta == []
    assert ratio == 0.0


def test_compute_delta_snapshot_no_prev():
    """이전 snapshot이 없으면 전체를 반환한다."""
    cur = ["a", "b", "c"]
    delta, ratio = _compute_delta_snapshot([], cur)
    assert delta == cur
    assert ratio == 1.0


def test_compute_delta_snapshot_partial_change():
    """일부만 바뀌면 변경 영역 + context만 추출된다."""
    prev = [f"line-{i}" for i in range(20)]
    cur = list(prev)
    cur[10] = "CHANGED-10"
    delta, ratio = _compute_delta_snapshot(prev, cur)
    assert ratio < 0.7
    assert "CHANGED-10" in delta
    assert len(delta) < len(cur)


def test_compute_delta_snapshot_full_change_high_ratio():
    """대부분 바뀌면 change_ratio가 높다."""
    prev = ["old-" + str(i) for i in range(10)]
    cur = ["new-" + str(i) for i in range(10)]
    delta, ratio = _compute_delta_snapshot(prev, cur)
    assert ratio >= 0.7


def test_render_openclaw_raw_tree_first_turn_full():
    """첫 턴은 전체 raw tree를 반환한다."""
    agent = _FakeAgent()
    snapshot_text = "role: main\n  role: button 'click me' [ref=e1]\n  role: link 'home' [ref=e2]"
    agent._last_role_snapshot = {"snapshot": snapshot_text, "tree": [], "refs_mode": "aria", "stats": {}}
    agent._prev_raw_snapshot_text = ""

    prompt = format_dom_for_llm(agent, [])
    assert "click me" in prompt
    assert "home" in prompt
    assert agent._prev_raw_snapshot_text == snapshot_text


def test_render_openclaw_raw_tree_second_turn_identical_compressed():
    """둘째 턴에서 DOM이 동일하면 압축된다."""
    agent = _FakeAgent()
    snapshot_text = "role: main\n  role: button 'click me' [ref=e1]\n  role: link 'home' [ref=e2]"
    agent._last_role_snapshot = {"snapshot": snapshot_text, "tree": [], "refs_mode": "aria", "stats": {}}
    agent._prev_raw_snapshot_text = snapshot_text

    prompt = format_dom_for_llm(agent, [])
    assert "DOM 변경 없음" in prompt
    assert "click me" not in prompt


def test_render_openclaw_raw_tree_second_turn_partial_change_delta():
    """둘째 턴에서 일부만 바뀌면 delta만 포함된다."""
    agent = _FakeAgent()
    prev_lines = [f"  role: item-{i} [ref=e{i}]" for i in range(20)]
    prev_text = "role: main\n" + "\n".join(prev_lines)

    cur_lines = list(prev_lines)
    cur_lines[10] = "  role: CHANGED-item [ref=e99]"
    cur_text = "role: main\n" + "\n".join(cur_lines)

    agent._last_role_snapshot = {"snapshot": cur_text, "tree": [], "refs_mode": "aria", "stats": {}}
    agent._prev_raw_snapshot_text = prev_text

    prompt = format_dom_for_llm(agent, [])
    assert "변경 영역만 표시" in prompt
    assert "CHANGED-item" in prompt
    prompt_lines = prompt.splitlines()
    raw_lines = cur_text.splitlines()
    assert len(prompt_lines) < len(raw_lines)


def test_render_openclaw_raw_tree_full_change_fallback():
    """대부분 바뀌면 full raw로 fallback한다."""
    agent = _FakeAgent()
    prev_text = "\n".join(f"old-line-{i}" for i in range(10))
    cur_text = "\n".join(f"new-line-{i}" for i in range(10))
    agent._last_role_snapshot = {"snapshot": cur_text, "tree": [], "refs_mode": "aria", "stats": {}}
    agent._prev_raw_snapshot_text = prev_text

    prompt = format_dom_for_llm(agent, [])
    assert "변경 영역만 표시" not in prompt
    assert "DOM 변경 없음" not in prompt
    assert "new-line-0" in prompt


def test_goal_requires_full_raw_snapshot_for_collect_goal():
    agent = _FakeAgent()
    agent._goal_constraints = {"collect_min": 3}

    assert _goal_requires_full_raw_snapshot(agent) is True


def test_render_openclaw_raw_tree_collect_goal_disables_delta():
    """수집/변경형 goal은 둘째 턴 이후에도 full raw를 유지한다."""
    agent = _FakeAgent()
    agent._goal_constraints = {"collect_min": 3, "mutation_direction": "increase"}
    prev_lines = [f"  role: item-{i} [ref=e{i}]" for i in range(20)]
    prev_text = "role: main\n" + "\n".join(prev_lines)
    cur_lines = list(prev_lines)
    cur_lines[10] = "  role: CHANGED-item [ref=e99]"
    cur_text = "role: main\n" + "\n".join(cur_lines)

    agent._last_role_snapshot = {"snapshot": cur_text, "tree": [], "refs_mode": "aria", "stats": {}}
    agent._prev_raw_snapshot_text = prev_text

    prompt = format_dom_for_llm(agent, [])
    assert "변경 영역만 표시" not in prompt
    assert "DOM 변경 없음" not in prompt
    assert "CHANGED-item" in prompt
    assert "role: item-0 [ref=e0]" in prompt
