"""
ParticipantRegistry - Goal 실행 동안의 참여자 단일 진실원천(SSOT).

Phase 1에서는 골격(데이터 보유 + 메시지 라우팅)만 둔다.
실제 BrowserSession/GoalDrivenAgent 인스턴스 생성 및 페이지 조작은
Phase 2~3에서 채운다.

설계 원칙:
- registry는 thread-safe 하지 않음. 단일 event loop에서만 사용.
- bootstrap 시 participants가 비어있으면 'default' 1명을 자동 생성 → 하위 호환.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from .blackboard import Blackboard
from .models import (
    ContextMode,
    Message,
    ParticipantSpec,
    TurnPolicySpec,
    WakeCondition,
)
from .turn_scheduler import EventDrivenScheduler, TurnScheduler


@dataclass
class ParticipantRuntime:
    """
    참여자 1명에 대한 런타임 상태.

    Phase 2부터 browser_session, agent 등의 필드가 채워진다.
    Phase 1 시점에는 spec과 inbox/outbox만 의미 있음.
    """

    spec: ParticipantSpec
    inbox: Deque[Message] = field(default_factory=deque)
    outbox: Deque[Message] = field(default_factory=deque)
    # Phase 2+에서 채울 자리 (지금은 Any로 비워둠)
    browser_session: Optional[Any] = None
    agent: Optional[Any] = None
    last_observed_url: str = ""
    last_observed_dom_hash: str = ""

    @property
    def participant_id(self) -> str:
        return self.spec.id


@dataclass
class ParticipantRegistry:
    """
    Goal 실행 컨텍스트 1개당 1개. 모든 참여자/blackboard/scheduler의 공유 컨테이너.
    """

    participants: Dict[str, ParticipantRuntime]
    blackboard: Blackboard
    scheduler: TurnScheduler
    context_mode: ContextMode
    turn_policy: TurnPolicySpec
    active_participant_id: str = ""
    goal_run_id: str = ""

    # ------------------------------------------------------------------
    # 생성 / 부트스트랩
    # ------------------------------------------------------------------
    @classmethod
    def bootstrap(
        cls,
        *,
        specs: List[ParticipantSpec],
        context_mode: ContextMode = ContextMode.ISOLATED,
        turn_policy: Optional[TurnPolicySpec] = None,
        goal_run_id: str = "",
        default_start_url: Optional[str] = None,
        default_test_data: Optional[Dict[str, Any]] = None,
    ) -> "ParticipantRegistry":
        """
        specs가 비어 있으면 'default' 단일 참여자를 자동 생성한다 (하위 호환).
        default_start_url / default_test_data 는 spec.start_url / spec.test_data 가 비어 있을 때 상속 기본값.
        """
        if not specs:
            specs = [
                ParticipantSpec(
                    id="default",
                    display_name="default",
                    start_url=default_start_url,
                    test_data=dict(default_test_data or {}),
                )
            ]

        runtimes: Dict[str, ParticipantRuntime] = {}
        seen_ids: set = set()
        for spec in specs:
            if spec.id in seen_ids:
                raise ValueError(f"중복된 participant id: {spec.id}")
            seen_ids.add(spec.id)
            # start_url / test_data 기본값 상속
            resolved_start = spec.start_url or default_start_url
            resolved_data: Dict[str, Any] = {**(default_test_data or {}), **spec.test_data}
            resolved_spec = spec.model_copy(
                update={
                    "start_url": resolved_start,
                    "test_data": resolved_data,
                }
            )
            runtimes[spec.id] = ParticipantRuntime(spec=resolved_spec)

        policy = turn_policy or TurnPolicySpec()
        scheduler = EventDrivenScheduler(policy=policy)
        for pid in runtimes.keys():
            scheduler.register(pid)

        active_id = next(iter(runtimes.keys()))

        return cls(
            participants=runtimes,
            blackboard=Blackboard(),
            scheduler=scheduler,
            context_mode=context_mode,
            turn_policy=policy,
            active_participant_id=active_id,
            goal_run_id=goal_run_id,
        )

    # ------------------------------------------------------------------
    # 메시지 라우팅
    # ------------------------------------------------------------------
    def deliver(self, message: Message) -> List[str]:
        """
        outbox에서 빠져나온 메시지를 적절한 inbox로 라우팅하고,
        scheduler에 통지하여 깨워야 할 참여자를 결정한다.

        Returns: 깨어난(woken) 참여자 id 목록.
        """
        if message.is_broadcast():
            recipients = [pid for pid in self.participants.keys() if pid != message.sender]
        else:
            if message.recipient not in self.participants:
                raise KeyError(f"unknown recipient: {message.recipient}")
            recipients = [message.recipient]

        for pid in recipients:
            self.participants[pid].inbox.append(message)

        return self.scheduler.on_message(message)

    def post_blackboard(
        self,
        participant_id: str,
        key: str,
        value: Any = None,
        *,
        step: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ) -> List[str]:
        """blackboard.write + scheduler 통지를 한 번에. Returns: woken ids."""
        entry = self.blackboard.write(
            participant_id, key, value, step=step, tags=tags
        )
        return self.scheduler.on_blackboard(entry)

    # ------------------------------------------------------------------
    # 참여자 액세스
    # ------------------------------------------------------------------
    def get(self, participant_id: str) -> ParticipantRuntime:
        if participant_id not in self.participants:
            raise KeyError(participant_id)
        return self.participants[participant_id]

    def set_active(self, participant_id: str) -> None:
        if participant_id not in self.participants:
            raise KeyError(participant_id)
        self.active_participant_id = participant_id

    def display_name_resolver(self) -> Callable[[str], str]:
        return lambda pid: (
            self.participants[pid].spec.resolved_display_name()
            if pid in self.participants
            else pid
        )

    def is_multi(self) -> bool:
        return len(self.participants) > 1
