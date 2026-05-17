from __future__ import annotations

from types import SimpleNamespace

from gaia.src.phase4.goal_driven.goal_kinds import GoalKind
from gaia.src.phase4.goal_driven.goal_semantics import extract_goal_semantics


def test_extract_goal_semantics_ignores_action_label_quotes_for_add_goal() -> None:
    goal = SimpleNamespace(
        name="포용사회와문화탐방1 과목의 '바로 추가' 버튼을 눌러서 내 시간표에 반영",
        description="포용사회와문화탐방1 과목의 '바로 추가' 버튼을 눌러서 내 시간표에 반영되는지 테스트",
        success_criteria=[],
    )

    semantics = extract_goal_semantics(
        goal,
        {
            "target_terms": ["포용사회와문화탐방1", "바로 추가"],
            "mutation_direction": "increase",
        },
    )

    assert semantics.target_terms == ["포용사회와문화탐방1"]


def test_extract_goal_semantics_derives_generic_destination_without_alias_table() -> None:
    goal = SimpleNamespace(
        name="archive this item into saved queue",
        description="Add the selected item to saved queue and verify it appears there.",
        success_criteria=[],
    )

    semantics = extract_goal_semantics(
        goal,
        {
            "target_terms": ["selected item"],
            "mutation_direction": "increase",
        },
    )

    assert semantics.goal_kind == GoalKind.ADD_TO_LIST
    assert semantics.destination_terms == ["saved queue"]


def test_extract_goal_semantics_does_not_route_filter_word_to_filter_policy() -> None:
    goal = SimpleNamespace(
        name="스포츠 필터를 선택한 후 축구 카테고리로 이동하고 순위표를 확인한다",
        description="상위 3개 팀의 순위 정보가 정상적으로 표시되는지 확인한다",
        success_criteria=[],
    )

    semantics = extract_goal_semantics(goal, {})

    assert semantics.goal_kind == GoalKind.GENERIC_FALLBACK
