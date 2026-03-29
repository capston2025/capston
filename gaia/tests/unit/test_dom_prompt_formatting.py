from __future__ import annotations

import re
from types import SimpleNamespace

from gaia.src.phase4.goal_driven.auth_hints import contains_login_hint, contains_next_pagination_hint, is_numeric_page_label
from gaia.src.phase4.goal_driven.dom_prompt_formatting import (
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

    assert prompt.index('[ref=e681] <button> "로그인"') < prompt.index('[ref=e213] <button> within=')
    assert prompt.index('[ref=e677] <input> "아이디를 입력하세요"') < prompt.index('[ref=e213] <button> within=')
    assert 'semantics=[auth_submit_candidate]' in prompt
    assert 'semantics=[auth_identifier_field]' in prompt


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
    assert prompt.index('[ref=e724] <button> "내 시간표 보기 (10)"') < prompt.index('[ref=e5] <button> "X"')


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
