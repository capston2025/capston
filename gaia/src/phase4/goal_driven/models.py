"""
Goal-Driven Test Automation Models

테스트 플랜에 세부 스텝 없이 목표만 정의
AI가 DOM을 보고 다음 액션을 스스로 결정
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
from enum import Enum


class ActionType(str, Enum):
    """가능한 액션 타입"""

    CLICK = "click"
    FILL = "fill"
    PRESS = "press"
    SCROLL = "scroll"
    WAIT = "wait"
    NAVIGATE = "navigate"
    HOVER = "hover"
    SELECT = "select"


class TestGoal(BaseModel):
    """
    테스트 목표 - 세부 스텝 없음!

    예시:
    {
        "id": "TC001",
        "name": "로그인 성공",
        "description": "유효한 자격 증명으로 로그인",
        "test_data": {"email": "test@example.com", "password": "xxx"},
        "success_criteria": ["환영 메시지", "로그아웃 버튼"],
        "max_steps": 15
    }
    """

    id: str = Field(..., description="테스트 ID")
    name: str = Field(..., description="테스트 이름")
    description: str = Field(..., description="목표 설명")
    priority: str = Field(default="MAY", description="우선순위 (MUST/SHOULD/MAY)")
    keywords: List[str] = Field(
        default_factory=list, description="목표를 유도하는 핵심 키워드"
    )

    preconditions: List[str] = Field(
        default_factory=list, description="사전 조건 (예: 로그아웃 상태)"
    )

    test_data: Dict[str, Any] = Field(
        default_factory=dict, description="테스트에 필요한 데이터 (이메일, 비밀번호 등)"
    )

    success_criteria: List[str] = Field(
        default_factory=list, description="성공 조건 (예: 환영 메시지 표시)"
    )

    failure_criteria: List[str] = Field(
        default_factory=list, description="실패 조건 (예: 오류 메시지 표시)"
    )

    max_steps: int = Field(default=20, description="최대 스텝 수 (무한 루프 방지)")

    start_url: Optional[str] = Field(
        default=None, description="시작 URL (없으면 현재 페이지에서 시작)"
    )


class DOMElement(BaseModel):
    """LLM에게 전달할 DOM 요소 (압축된 형태)"""

    id: int = Field(..., description="요소 고유 ID (클릭 시 사용)")
    tag: str = Field(..., description="HTML 태그")
    text: str = Field(default="", description="보이는 텍스트")

    # 주요 속성만 포함
    role: Optional[str] = Field(default=None, description="ARIA role")
    type: Optional[str] = Field(default=None, description="input type")
    placeholder: Optional[str] = Field(default=None)
    aria_label: Optional[str] = Field(default=None)
    href: Optional[str] = Field(default=None, description="링크 URL")
    bounding_box: Optional[dict] = Field(default=None, description="요소 위치 정보")

    # 상태
    is_visible: bool = Field(default=True)
    is_enabled: bool = Field(default=True)
    is_focused: bool = Field(default=False)


class ActionDecision(BaseModel):
    """
    LLM이 결정한 다음 액션

    예시:
    {
        "action": "click",
        "element_id": 5,
        "reasoning": "로그인을 위해 먼저 로그인 탭을 선택해야 함",
        "is_goal_achieved": false
    }
    """

    action: ActionType = Field(..., description="수행할 액션")
    element_id: Optional[int] = Field(
        default=None, description="대상 요소 ID (click, fill 등에 필요)"
    )
    value: Optional[str] = Field(
        default=None, description="입력값 (fill, press 등에 필요)"
    )

    reasoning: str = Field(default="", description="이 액션을 선택한 이유")

    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="확신도")

    is_goal_achieved: bool = Field(
        default=False, description="목표가 달성되었는지 여부"
    )

    goal_achievement_reason: Optional[str] = Field(
        default=None, description="목표 달성 판단 이유 (is_goal_achieved=True일 때)"
    )


class StepResult(BaseModel):
    """단일 스텝 실행 결과"""

    step_number: int
    action: ActionDecision
    success: bool
    error_message: Optional[str] = None
    screenshot_before: Optional[str] = None
    screenshot_after: Optional[str] = None
    duration_ms: int = 0


class GoalResult(BaseModel):
    """목표 실행 결과"""

    goal_id: str
    goal_name: str
    success: bool

    steps_taken: List[StepResult] = Field(default_factory=list)
    total_steps: int = 0

    final_reason: str = Field(default="", description="성공/실패 이유")

    duration_seconds: float = 0.0


class GoalTestPlan(BaseModel):
    """
    목표 기반 테스트 플랜

    기존 방식: steps 배열에 모든 단계 정의
    새 방식: goals 배열에 목표만 정의, AI가 스텝 결정
    """

    profile: str = Field(..., description="테스트 프로필/사이트 식별자")
    url: str = Field(..., description="시작 URL")
    version: str = Field(default="2.0", description="플랜 버전")

    goals: List[TestGoal] = Field(default_factory=list, description="테스트 목표 목록")

    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="추가 메타데이터"
    )
