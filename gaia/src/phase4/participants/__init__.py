"""
Participants Module

다중 참여자(Participant) 시스템.

각 참여자는 자기만의 BrowserContext와 LLM 컨텍스트를 가지고
독립적인 subagent로 동작하며, 메시지 큐와 Blackboard를 통해 상호작용한다.

모듈 구성:
- models: ParticipantSpec, Message, BlackboardEntry, TurnPolicySpec 등 데이터 모델
- blackboard: 참여자 간 공유 사실 저장소 (N:N)
- turn_scheduler: Wake Condition 기반 event-driven 스케줄러
- registry: Goal 실행 동안의 참여자 레지스트리 (단일 진실원천)
"""

from .models import (
    ContextMode,
    Message,
    MessageKind,
    ParticipantBrowserBinding,
    ParticipantCredentialRequest,
    ParticipantPlan,
    ParticipantSpec,
    BlackboardEntry,
    TurnControl,
    TurnControlStatus,
    TurnPolicySpec,
    TurnPolicyKind,
    WakeCondition,
    WakeConditionKind,
)
from .blackboard import Blackboard
from .turn_scheduler import EventDrivenScheduler, TurnScheduler

__all__ = [
    "ContextMode",
    "Message",
    "MessageKind",
    "ParticipantBrowserBinding",
    "ParticipantCredentialRequest",
    "ParticipantPlan",
    "ParticipantSpec",
    "BlackboardEntry",
    "TurnControl",
    "TurnControlStatus",
    "TurnPolicySpec",
    "TurnPolicyKind",
    "WakeCondition",
    "WakeConditionKind",
    "Blackboard",
    "EventDrivenScheduler",
    "TurnScheduler",
]
