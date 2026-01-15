"""
Goal-Driven Test Automation Module

DOM 기반 + LLM 판단으로 목표 달성까지 자동 실행
- 사전 정의된 스텝 없음
- AI가 매 순간 다음 액션 결정
- 중간 단계 자동 발견

모드:
1. Goal-Driven Mode: 목표만 주면 AI가 달성 (체크리스트 기반)
2. Exploratory Mode: 화면의 모든 요소를 자율적으로 탐색 및 테스트
"""

from .models import TestGoal, ActionDecision, GoalResult, GoalTestPlan
from .agent import GoalDrivenAgent
from .goal_builder import goals_from_scenarios, normalize_priority, sort_goals_by_priority
from .exploratory_models import (
    ExplorationConfig,
    ExplorationResult,
    ExplorationDecision,
    FoundIssue,
    IssueType,
    PageState,
    ElementState,
)
from .exploratory_agent import ExploratoryAgent

__all__ = [
    # Goal-Driven
    "TestGoal",
    "ActionDecision",
    "GoalResult",
    "GoalTestPlan",
    "GoalDrivenAgent",
    "goals_from_scenarios",
    "normalize_priority",
    "sort_goals_by_priority",
    # Exploratory
    "ExplorationConfig",
    "ExplorationResult",
    "ExplorationDecision",
    "FoundIssue",
    "IssueType",
    "PageState",
    "ElementState",
    "ExploratoryAgent",
]
