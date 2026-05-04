"""
Turn Scheduler - event-driven 스케줄링.

각 참여자(subagent)는 1 step을 마치면 다음 상태를 명시한다:
- continue: 같은 참여자를 명시적으로 다시 예약
- wait_for(...): Wake Condition 충족 시까지 대기
- done: 종료

스케줄러는 round-robin을 하지 않는다. explicit request_next 또는
이벤트(메시지 도착, blackboard write, timeout)로 깨어난 participant만 실행한다.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .models import (
    BlackboardEntry,
    Message,
    TurnPolicySpec,
    WakeCondition,
    WakeConditionKind,
)


@dataclass
class _ParticipantState:
    """스케줄러 내부에서 관리하는 참여자 상태."""

    participant_id: str
    is_done: bool = False
    consecutive_turns: int = 0
    last_no_progress: bool = False
    wake_conditions: List[WakeCondition] = field(default_factory=list)
    idle_since: Optional[float] = None


class TurnScheduler(ABC):
    """스케줄러 추상 인터페이스."""

    @abstractmethod
    def register(self, participant_id: str) -> None: ...

    @abstractmethod
    def next_participant(self) -> Optional[str]:
        """다음에 1 step 실행할 참여자 id. 모두 idle/done이면 None."""

    @abstractmethod
    def mark_idle(
        self,
        participant_id: str,
        *,
        wake_conditions: Optional[List[WakeCondition]] = None,
    ) -> None: ...

    @abstractmethod
    def mark_done(self, participant_id: str) -> None: ...

    @abstractmethod
    def record_outcome(
        self,
        participant_id: str,
        *,
        observation_changed: bool,
    ) -> None: ...

    @abstractmethod
    def on_message(self, message: Message) -> List[str]:
        """메시지 도착 시 깨워야 할 참여자 id 목록 반환."""

    @abstractmethod
    def on_blackboard(self, entry: BlackboardEntry) -> List[str]:
        """blackboard write 시 깨워야 할 참여자 id 목록 반환."""

    @abstractmethod
    def all_done(self) -> bool: ...

    @abstractmethod
    def request_next(self, participant_id: str) -> None:
        """LLM이 next_participant를 명시적으로 지명한 경우 우선순위 부여."""


class EventDrivenScheduler(TurnScheduler):
    """
    Wake Condition 기반 이벤트 스케줄러.

    동작:
    - register된 참여자는 처음에는 idle이다.
    - 첫 실행/연속 실행은 request_next(pid)로 명시해야 한다.
    - mark_idle(participant, wake_conditions=[...])이 불리면 ready에서 빠지고
      wake_conditions가 충족되기 전까지 idle.
    - on_message / on_blackboard 가 매칭되면 해당 참여자를 다시 ready로.
    - request_next(pid)는 그 참여자를 ready 큐 맨 앞으로 둔다.
    - max_consecutive_turns 초과 + observation_changed=False면 해당 participant를 보류한다.
    """

    def __init__(self, policy: Optional[TurnPolicySpec] = None) -> None:
        self._policy = policy or TurnPolicySpec()
        self._states: Dict[str, _ParticipantState] = {}
        self._ready: Deque[str] = deque()
        self._priority: Deque[str] = deque()  # request_next로 들어온 우선순위 큐

    # ------------------------------------------------------------------
    # 등록 / 라이프사이클
    # ------------------------------------------------------------------
    def register(self, participant_id: str) -> None:
        if participant_id in self._states:
            return
        self._states[participant_id] = _ParticipantState(participant_id=participant_id)

    def mark_done(self, participant_id: str) -> None:
        state = self._states.get(participant_id)
        if state is None:
            return
        state.is_done = True
        state.wake_conditions = []
        self._remove_from_queues(participant_id)

    def mark_idle(
        self,
        participant_id: str,
        *,
        wake_conditions: Optional[List[WakeCondition]] = None,
    ) -> None:
        state = self._states.get(participant_id)
        if state is None:
            return
        state.wake_conditions = list(wake_conditions) if wake_conditions else []
        state.idle_since = time.time()
        self._remove_from_queues(participant_id)

        # IMMEDIATE 조건이 하나라도 있으면 즉시 ready
        for cond in state.wake_conditions:
            if cond.kind is WakeConditionKind.IMMEDIATE:
                state.wake_conditions = []
                state.idle_since = None
                self._ready.append(participant_id)
                return

    def record_outcome(
        self,
        participant_id: str,
        *,
        observation_changed: bool,
    ) -> None:
        state = self._states.get(participant_id)
        if state is None:
            return
        if observation_changed:
            state.consecutive_turns = 0
            state.last_no_progress = False
        else:
            state.consecutive_turns += 1
            state.last_no_progress = True

    # ------------------------------------------------------------------
    # 다음 차례 결정
    # ------------------------------------------------------------------
    def next_participant(self) -> Optional[str]:
        # 1) timeout 만료된 idle 참여자를 우선 깨움
        self._wake_timed_out()

        # 2) explicit request_next 우선
        while self._priority:
            pid = self._priority.popleft()
            if not self._is_eligible(pid):
                continue
            if self._must_yield(pid):
                continue
            return pid

        # 3) 이벤트로 깨어난 ready 큐. 자동 round-robin 없이 들어온 순서만 소비한다.
        attempts = len(self._ready)
        for _ in range(attempts):
            pid = self._ready.popleft()
            if not self._is_eligible(pid):
                continue
            if self._must_yield(pid):
                continue
            return pid

        return None

    def request_next(self, participant_id: str) -> None:
        if participant_id not in self._states:
            return
        state = self._states[participant_id]
        if state.is_done:
            return
        # ready로 복귀
        state.wake_conditions = []
        state.idle_since = None
        self._remove_from_queues(participant_id)
        self._priority.append(participant_id)

    # ------------------------------------------------------------------
    # 이벤트 매칭
    # ------------------------------------------------------------------
    def on_message(self, message: Message) -> List[str]:
        woken: List[str] = []
        recipients = (
            list(self._states.keys())
            if message.is_broadcast()
            else [message.recipient]
        )
        for pid in recipients:
            state = self._states.get(pid)
            if state is None or state.is_done:
                continue
            if any(cond.matches_message(message) for cond in state.wake_conditions):
                self._wake(pid)
                woken.append(pid)
        return woken

    def on_blackboard(self, entry: BlackboardEntry) -> List[str]:
        woken: List[str] = []
        for pid, state in self._states.items():
            if state.is_done:
                continue
            if any(cond.matches_blackboard(entry) for cond in state.wake_conditions):
                self._wake(pid)
                woken.append(pid)
        return woken

    def all_done(self) -> bool:
        if not self._states:
            return True
        return all(state.is_done for state in self._states.values())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _wake(self, participant_id: str) -> None:
        state = self._states[participant_id]
        state.wake_conditions = []
        state.idle_since = None
        if participant_id not in self._ready and participant_id not in self._priority:
            self._ready.append(participant_id)

    def _wake_timed_out(self) -> None:
        now = time.time()
        for pid, state in self._states.items():
            if state.is_done or not state.wake_conditions or state.idle_since is None:
                continue
            for cond in state.wake_conditions:
                if cond.kind is WakeConditionKind.TIMEOUT and cond.timeout_seconds is not None:
                    if now - state.idle_since >= cond.timeout_seconds:
                        self._wake(pid)
                        break
            else:
                # 글로벌 deadlock 방지 timeout
                if now - state.idle_since >= self._policy.wake_timeout_seconds:
                    self._wake(pid)

    def _is_eligible(self, participant_id: str) -> bool:
        state = self._states.get(participant_id)
        if state is None:
            return False
        if state.is_done:
            return False
        if state.wake_conditions:
            # 아직 idle 상태로 ready 큐에 잘못 들어왔다면 제외
            return False
        return True

    def _must_yield(self, participant_id: str) -> bool:
        state = self._states[participant_id]
        if state.consecutive_turns >= self._policy.max_consecutive_turns:
            return state.last_no_progress
        return False

    def _remove_from_queues(self, participant_id: str) -> None:
        self._ready = deque(p for p in self._ready if p != participant_id)
        self._priority = deque(p for p in self._priority if p != participant_id)

    # 디버그용
    def snapshot(self) -> Tuple[List[str], List[str], Dict[str, _ParticipantState]]:
        return list(self._priority), list(self._ready), dict(self._states)
