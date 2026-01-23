"""
Exploratory Testing Models

완전 자율 탐색 모드를 위한 데이터 모델
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime


class ElementState(BaseModel):
    """UI 요소의 상태 스냅샷"""

    element_id: str = Field(..., description="요소 고유 ID (selector 기반)")
    tag: str = Field(..., description="HTML 태그")
    text: str = Field(default="", description="텍스트 내용")
    selector: str = Field(..., description="CSS Selector")

    # 요소 속성
    role: Optional[str] = None
    type: Optional[str] = None
    aria_label: Optional[str] = None
    title: Optional[str] = None
    href: Optional[str] = None
    placeholder: Optional[str] = None
    bounding_box: Optional[dict] = None

    # 테스트 상태
    tested: bool = Field(default=False, description="테스트 완료 여부")
    test_count: int = Field(default=0, description="테스트 실행 횟수")
    last_tested_at: Optional[datetime] = None


class PageState(BaseModel):
    """페이지 상태 스냅샷"""

    url: str = Field(..., description="현재 URL")
    url_hash: str = Field(..., description="URL의 해시값 (중복 방지)")
    title: str = Field(default="", description="페이지 제목")

    # 페이지의 모든 상호작용 가능한 요소
    interactive_elements: List[ElementState] = Field(default_factory=list)

    # 방문 정보
    visit_count: int = Field(default=0, description="방문 횟수")
    first_visited_at: datetime = Field(default_factory=datetime.now)
    last_visited_at: datetime = Field(default_factory=datetime.now)


class TestableAction(BaseModel):
    """테스트 가능한 액션"""

    element_id: str = Field(..., description="대상 요소 ID")
    action_type: str = Field(..., description="액션 타입 (click, fill, hover 등)")
    description: str = Field(..., description="액션 설명")
    priority: float = Field(default=0.5, ge=0.0, le=1.0, description="우선순위")
    reasoning: str = Field(default="", description="이 액션을 테스트해야 하는 이유")


class IssueType(str, Enum):
    """발견된 이슈 타입"""

    ERROR = "error"  # JavaScript 에러
    BROKEN_LINK = "broken_link"  # 깨진 링크
    VISUAL_GLITCH = "visual_glitch"  # 시각적 버그
    UNEXPECTED_BEHAVIOR = "unexpected_behavior"  # 예상치 못한 동작
    ACCESSIBILITY = "accessibility"  # 접근성 문제
    PERFORMANCE = "performance"  # 성능 문제
    TIMEOUT = "timeout"  # 타임아웃


class FoundIssue(BaseModel):
    """발견된 버그/이슈"""

    issue_id: str = Field(..., description="이슈 고유 ID")
    issue_type: IssueType = Field(..., description="이슈 타입")
    severity: str = Field(..., description="심각도 (critical, high, medium, low)")

    title: str = Field(..., description="이슈 제목")
    description: str = Field(..., description="이슈 상세 설명")

    # 재현 정보
    url: str = Field(..., description="이슈 발생 URL")
    steps_to_reproduce: List[str] = Field(default_factory=list, description="재현 단계")

    # 증거
    screenshot_before: Optional[str] = None
    screenshot_after: Optional[str] = None
    error_message: Optional[str] = None
    console_logs: List[str] = Field(default_factory=list)

    # 메타데이터
    found_at: datetime = Field(default_factory=datetime.now)
    verified: bool = Field(default=False, description="검증 완료 여부")


class ExplorationConfig(BaseModel):
    """탐색 설정"""

    max_actions: int = Field(default=100, description="최대 액션 수")
    max_depth: int = Field(default=5, description="최대 탐색 깊이 (페이지 이동)")

    # 탐색 전략
    prioritize_untested: bool = Field(default=True, description="미테스트 요소 우선")
    avoid_destructive: bool = Field(default=True, description="삭제/파괴적 액션 회피")
    test_forms: bool = Field(default=True, description="폼 테스트")
    test_navigation: bool = Field(default=True, description="네비게이션 테스트")

    # 녹화 설정
    enable_recording: bool = Field(default=True, description="스크린샷/GIF 녹화 활성화")
    screenshot_interval_ms: int = Field(default=500, description="스크린샷 간격 (밀리초)")
    generate_gif: bool = Field(default=True, description="GIF 자동 생성")

    # 제외 패턴
    excluded_urls: List[str] = Field(
        default_factory=list, description="제외할 URL 패턴"
    )
    excluded_selectors: List[str] = Field(
        default_factory=list, description="제외할 셀렉터"
    )

    # 타임아웃
    action_timeout: int = Field(default=30, description="액션 타임아웃 (초)")
    page_load_timeout: int = Field(default=30, description="페이지 로드 타임아웃 (초)")


class ExplorationDecision(BaseModel):
    """LLM의 탐색 결정"""

    should_continue: bool = Field(..., description="탐색을 계속할지 여부")

    # 선택된 액션
    selected_action: Optional[TestableAction] = None

    # 입력값 (폼인 경우)
    input_values: Dict[str, str] = Field(
        default_factory=dict, description="입력 필드에 넣을 값"
    )

    # 결정 근거
    reasoning: str = Field(default="", description="결정 이유")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # 예상 결과
    expected_outcome: str = Field(default="", description="예상되는 결과")


class ExplorationStep(BaseModel):
    """탐색 단계 결과"""

    step_number: int
    url: str

    decision: ExplorationDecision

    # 실행 결과
    success: bool
    error_message: Optional[str] = None

    # 기능 중심 설명 (베타테스터 관점)
    feature_description: str = Field(default="", description="테스트한 기능 설명 (예: 로그인 기능 테스트)")
    test_scenario: str = Field(default="", description="테스트 시나리오 그룹 (예: 사용자 인증 플로우)")
    business_impact: str = Field(default="", description="비즈니스 관점에서의 영향")

    # 발견된 이슈
    issues_found: List[FoundIssue] = Field(default_factory=list)

    # 새로운 요소 발견
    new_elements_found: int = 0
    new_pages_found: int = 0

    # 스크린샷
    screenshot_before: Optional[str] = None
    screenshot_after: Optional[str] = None

    # 메타데이터
    duration_ms: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)


class ExplorationResult(BaseModel):
    """탐색 세션 전체 결과"""

    session_id: str = Field(..., description="세션 ID")
    start_url: str = Field(..., description="시작 URL")

    # 통계
    total_actions: int = 0
    total_pages_visited: int = 0
    total_elements_tested: int = 0

    # 커버리지
    coverage: Dict[str, Any] = Field(
        default_factory=dict, description="테스트 커버리지 통계"
    )

    # 발견된 이슈
    issues_found: List[FoundIssue] = Field(default_factory=list)

    # 실행 단계
    steps: List[ExplorationStep] = Field(default_factory=list)

    # 종료 이유
    completion_reason: str = Field(..., description="탐색 종료 이유")

    # 녹화 파일
    recording_gif_path: Optional[str] = Field(default=None, description="GIF 녹화 파일 경로")
    screenshots_dir: Optional[str] = Field(default=None, description="스크린샷 디렉토리 경로")

    # 테스트 시나리오 요약 (기능 중심)
    test_scenarios_summary: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="테스트된 기능 시나리오 요약 (예: [{name: '로그인 테스트', steps: [1,2,3], result: 'pass'}])"
    )

    # 메타데이터
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    def get_critical_issues(self) -> List[FoundIssue]:
        """Critical/High 심각도 이슈만 반환"""
        return [i for i in self.issues_found if i.severity in ["critical", "high"]]

    def get_coverage_percentage(self) -> float:
        """테스트 커버리지 퍼센트 계산"""
        total = self.coverage.get("total_interactive_elements", 0)
        tested = self.coverage.get("tested_elements", 0)
        return (tested / total * 100) if total > 0 else 0.0
