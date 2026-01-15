"""Utilities to build goal-driven plans from scenario-style inputs."""
from __future__ import annotations

import re
from typing import Iterable, List, Sequence

from gaia.src.phase4.goal_driven.models import TestGoal
from gaia.src.utils.models import Assertion, TestScenario


_PRIORITY_MAP = {
    "must": "MUST",
    "high": "MUST",
    "p0": "MUST",
    "p1": "MUST",
    "should": "SHOULD",
    "medium": "SHOULD",
    "p2": "SHOULD",
    "may": "MAY",
    "low": "MAY",
    "p3": "MAY",
}

_PRIORITY_ORDER = {"MUST": 0, "SHOULD": 1, "MAY": 2}

_TOKEN_RE = re.compile(r"[a-zA-Z0-9가-힣]+")

_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "that",
    "this",
    "페이지",
    "화면",
    "버튼",
    "클릭",
    "입력",
    "이동",
    "확인",
    "테스트",
    "기능",
    "사용자",
    "수행",
    "합니다",
    "한다",
}


def normalize_priority(priority: str | None) -> str:
    """Normalize priority values to MUST/SHOULD/MAY."""
    if not priority:
        return "MAY"
    raw = str(priority).strip()
    upper = raw.upper()
    if upper in _PRIORITY_ORDER:
        return upper
    return _PRIORITY_MAP.get(raw.lower(), "MAY")


def extract_keywords(text: str, *, max_keywords: int = 8) -> List[str]:
    """Extract lightweight keywords from free text."""
    if not text:
        return []

    tokens = _TOKEN_RE.findall(text.lower())
    keywords: List[str] = []
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if len(token) < 2:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= max_keywords:
            break
    return keywords


def _build_success_criteria(assertion: Assertion | None, fallback: str) -> List[str]:
    criteria: List[str] = []
    if assertion:
        if assertion.description:
            criteria.append(assertion.description)
        if getattr(assertion, "expected_outcome", None):
            criteria.append(assertion.expected_outcome)
        if getattr(assertion, "success_indicators", None):
            criteria.extend(assertion.success_indicators)
    if not criteria:
        criteria.append(fallback)
    return criteria


def goals_from_scenarios(
    scenarios: Sequence[TestScenario],
    *,
    extra_keywords: Iterable[str] | None = None,
    default_max_steps: int = 20,
) -> List[TestGoal]:
    """Convert scenario-style plans into goal-driven goals."""
    shared_keywords = [kw.strip() for kw in (extra_keywords or []) if kw.strip()]
    goals: List[TestGoal] = []
    for scenario in scenarios:
        priority = normalize_priority(scenario.priority)
        step_texts = [step.description for step in scenario.steps if step.description]
        assertion = scenario.assertion
        success_criteria = _build_success_criteria(assertion, scenario.scenario)

        keyword_text = " ".join([scenario.scenario] + step_texts + success_criteria)
        keywords = extract_keywords(keyword_text, max_keywords=8)
        for keyword in shared_keywords:
            if keyword not in keywords:
                keywords.append(keyword)

        goals.append(
            TestGoal(
                id=scenario.id,
                name=scenario.scenario,
                description=scenario.scenario,
                priority=priority,
                keywords=keywords,
                success_criteria=success_criteria,
                test_data={},
                max_steps=default_max_steps,
            )
        )
    return goals


def sort_goals_by_priority(goals: Sequence[TestGoal]) -> List[TestGoal]:
    """Return goals sorted by priority, keeping stable order for ties."""
    return sorted(
        goals,
        key=lambda goal: _PRIORITY_ORDER.get(normalize_priority(goal.priority), 3),
    )


__all__ = [
    "goals_from_scenarios",
    "normalize_priority",
    "sort_goals_by_priority",
]
