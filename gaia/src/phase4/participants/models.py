"""
Participant 시스템의 데이터 모델.

모든 모델은 pydantic v2 BaseModel 기반으로, immutable한 사용을 권장한다
(필드 갱신은 model_copy(update=...) 사용).
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ContextMode(str, Enum):
    """
    각 참여자(subagent)의 LLM 컨텍스트 분리 정책.

    - ISOLATED: 시스템 프롬프트까지 페르소나별로 완전 분리. 페르소나 강함, 토큰 비용 큼.
    - SHARED_SYSTEM: 공통 시스템 프롬프트 + 참여자별 history만 분리. 토큰 절약.
    """

    ISOLATED = "isolated"
    SHARED_SYSTEM = "shared_system"


class TurnPolicyKind(str, Enum):
    """
    턴 스케줄링 방식.

    현재는 event_driven 단일 정책만 지원한다 (Wake Condition 기반).
    LLM이 명시적으로 next_participant를 지명하면 그것이 우선한다.
    """

    EVENT_DRIVEN = "event_driven"


class TurnPolicySpec(BaseModel):
    """턴 스케줄링 정책 스펙."""

    kind: TurnPolicyKind = Field(
        default=TurnPolicyKind.EVENT_DRIVEN,
        description="스케줄러 종류",
    )
    wake_timeout_seconds: float = Field(
        default=30.0,
        ge=0.1,
        description="Wake Condition 미충족 시 deadlock 방지용 timeout",
    )
    max_consecutive_turns: int = Field(
        default=5,
        ge=1,
        description="동일 참여자가 변화 없이 연속 행동 가능한 최대 턴 수 (강제 양보)",
    )


class ParticipantSpec(BaseModel):
    """
    참여자(subagent) 정의.

    각 참여자는 자기만의 BrowserContext(쿠키/스토리지 격리)와
    독립된 LLM 컨텍스트를 가진다.
    """

    id: str = Field(..., description="참여자 식별자 (Goal 내 unique)")
    display_name: str = Field(default="", description="UI/로그 표시명")
    role: str = Field(
        default="",
        description="이 참여자의 상호작용 역할 (예: sender, receiver, approver)",
    )
    persona: str = Field(
        default="",
        description="시스템 프롬프트에 주입될 페르소나/역할 설명",
    )
    start_url: Optional[str] = Field(
        default=None,
        description="이 참여자가 시작할 URL (없으면 Goal.start_url 상속)",
    )
    test_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="이 참여자 전용 데이터 (계정 정보 등)",
    )
    context_args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Playwright BrowserContext 생성 옵션 (locale, user_agent 등)",
    )
    storage_state_path: Optional[str] = Field(
        default=None,
        description="저장된 storage_state JSON 경로 (사전 로그인 상태 복원용)",
    )

    def resolved_display_name(self) -> str:
        return self.display_name or self.id


class ParticipantCredentialRequest(BaseModel):
    """특정 참여자에게 필요한 사용자 제공 필드."""

    participant_id: str = Field(..., description="자격 정보가 필요한 참여자 id")
    fields: List[str] = Field(
        default_factory=lambda: ["username", "password"],
        description="참여자별로 필요한 필드명. human_answer 요청 시 participant_id 접두사를 붙인다.",
    )
    required: bool = Field(default=True, description="누락 시 실행을 멈추고 사용자 입력을 요구")


class ParticipantBrowserBinding(BaseModel):
    """
    참여자와 실제 독립 브라우저 세션의 연결.

    session_id는 하네스 라우팅 키이고, profile_name은 OpenClaw의 쿠키/스토리지
    격리 단위다. Playwright 런타임에서는 session_id가 별도 BrowserContext를 만든다.
    """

    participant_id: str
    session_id: str
    profile_name: str = ""
    start_url: Optional[str] = None
    context_args: Dict[str, Any] = Field(default_factory=dict)
    storage_state_path: Optional[str] = None
    created: bool = False


class ParticipantPlan(BaseModel):
    """
    multi_user_interaction skill이 반환하는 실행 토폴로지.

    하네스는 이 계획을 자동 추론하지 않고, 에이전트가 명시적으로 선언한 경우에만 활성화한다.
    """

    skill: str = Field(default="multi_user_interaction")
    required: bool = Field(default=False, description="다중 참여자 실행이 필요한지 여부")
    reason: str = Field(default="", description="왜 단일 세션으로 검증할 수 없는지")
    participants: List[ParticipantSpec] = Field(default_factory=list)
    credential_requests: List[ParticipantCredentialRequest] = Field(default_factory=list)
    coordination_plan: List[str] = Field(default_factory=list)
    expected_events: List[str] = Field(
        default_factory=lambda: [
            "message_sent",
            "message_received",
            "notification_visible",
        ],
        description="LLM이 Blackboard에 명시적으로 게시할 수 있는 대표 관찰 이벤트",
    )


class TurnControlStatus(str, Enum):
    """한 participant step 이후 scheduler에 넘기는 명시적 턴 제어."""

    CONTINUE = "continue"
    WAIT_FOR = "wait_for"
    DONE = "done"


class TurnControl(BaseModel):
    """
    모델이 action 이후 participant lifecycle을 명시적으로 제어하는 계약.

    round-robin fallback 없이 causal event/explicit handoff만으로 다음 턴을 고른다.
    """

    status: TurnControlStatus = Field(
        default=TurnControlStatus.WAIT_FOR,
        description="continue이면 같은 participant 재실행, wait_for이면 조건 충족까지 idle, done이면 종료",
    )
    wait_for: List["WakeCondition"] = Field(
        default_factory=list,
        description="status=wait_for 일 때 다시 깨어날 조건들",
    )
    reason: str = Field(default="", description="왜 이 턴 제어를 선택했는지")


class MessageKind(str, Enum):
    """
    참여자 간 메시지 종류.

    - MSG: 일반 메시지 (대화/지시)
    - OBSERVATION: 자기 페이지에서 관찰한 사실 공유
    - SIGNAL: 이벤트 시그널 (예: "logged_in", "message_sent")
    - REQUEST_REPLY: 답장을 명시적으로 기다리는 요청
    - REPLY: REQUEST_REPLY에 대한 답
    """

    MSG = "msg"
    OBSERVATION = "observation"
    SIGNAL = "signal"
    REQUEST_REPLY = "request_reply"
    REPLY = "reply"


class Message(BaseModel):
    """
    참여자 간 1:1 또는 브로드캐스트 메시지.

    correlation_id로 REQUEST_REPLY ↔ REPLY 매칭.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = Field(..., description="송신 참여자 id")
    recipient: str = Field(
        ...,
        description="수신 참여자 id (또는 '*' 브로드캐스트)",
    )
    kind: MessageKind = Field(default=MessageKind.MSG)
    payload: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = Field(
        default=None,
        description="REQUEST_REPLY의 답을 추적할 때 사용",
    )
    created_at: float = Field(default_factory=lambda: time.time())
    step: Optional[int] = Field(
        default=None, description="송신 시점의 Goal 스텝 번호"
    )

    def is_broadcast(self) -> bool:
        return self.recipient == "*"


class BlackboardEntry(BaseModel):
    """
    Blackboard에 기록되는 한 줄. 참여자가 자기 행동/관찰을 공유.

    Message가 1:1 통신이라면, BlackboardEntry는 N:N 사실 게시판이다.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    participant_id: str = Field(..., description="작성자 참여자 id")
    key: str = Field(
        ...,
        description="사실의 키 (자유 문자열, 예: 'message_sent', 'logged_in')",
    )
    value: Any = Field(default=None, description="JSON-serializable 값")
    tags: List[str] = Field(default_factory=list)
    step: Optional[int] = Field(default=None, description="기록 시점의 스텝 번호")
    created_at: float = Field(default_factory=lambda: time.time())


class WakeConditionKind(str, Enum):
    """
    Wake Condition 종류 — 참여자가 idle 상태에서 다시 깨어나는 트리거.
    """

    IMMEDIATE = "immediate"  # 다음 턴에 즉시 깨어남
    INBOX_MESSAGE = "inbox_message"  # 자기 inbox에 매칭 메시지 도착
    BLACKBOARD_KEY = "blackboard_key"  # 특정 key가 blackboard에 기록됨
    PAGE_CHANGE = "page_change"  # 자기 페이지의 DOM/URL 변화 감지
    TIMEOUT = "timeout"  # 일정 시간 경과 후 깨어남
    DONE = "done"  # 더 이상 깨어나지 않음 (Goal 완료 또는 포기)


class WakeCondition(BaseModel):
    """
    참여자가 idle 진입 시 등록하는 Wake 조건.

    여러 조건을 가지려면 별도 인스턴스 여러 개를 등록한다 (OR 매칭).
    """

    kind: WakeConditionKind = Field(default=WakeConditionKind.IMMEDIATE)

    # INBOX_MESSAGE 매칭 필터
    from_participant: Optional[str] = Field(
        default=None,
        description="INBOX_MESSAGE: 특정 송신자만 (None이면 누구나)",
    )
    message_kind: Optional[MessageKind] = Field(
        default=None,
        description="INBOX_MESSAGE: 특정 종류만",
    )

    # BLACKBOARD_KEY 매칭 필터
    blackboard_key: Optional[str] = Field(
        default=None,
        description="BLACKBOARD_KEY: 정확히 일치할 key",
    )

    # TIMEOUT
    timeout_seconds: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="TIMEOUT: 몇 초 후 깨어날지",
    )

    # 디버그/관측용
    note: str = Field(default="", description="이 조건을 등록한 이유 (디버그용)")

    def matches_message(self, message: Message) -> bool:
        """이 조건이 INBOX_MESSAGE 종류이고 주어진 메시지가 매칭되면 True."""
        if self.kind is not WakeConditionKind.INBOX_MESSAGE:
            return False
        if self.from_participant and message.sender != self.from_participant:
            return False
        if self.message_kind and message.kind is not self.message_kind:
            return False
        return True

    def matches_blackboard(self, entry: BlackboardEntry) -> bool:
        """이 조건이 BLACKBOARD_KEY 종류이고 entry가 매칭되면 True."""
        if self.kind is not WakeConditionKind.BLACKBOARD_KEY:
            return False
        if self.blackboard_key is None:
            return False
        return entry.key == self.blackboard_key
