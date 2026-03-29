from __future__ import annotations

from types import SimpleNamespace

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
