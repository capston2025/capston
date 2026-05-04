"""
Blackboard - 참여자 간 공유 사실 저장소 (N:N).

참여자가 자기 행동/관찰을 게시하면 다른 참여자(혹은 LLM 프롬프트 빌더)가
이를 읽어 상황을 파악한다. 메시지(Message)와 달리 특정 수신자가 없는
"공개 게시판" 모델이다.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, List, Optional

from .models import BlackboardEntry


class Blackboard:
    """
    Goal 실행 동안 살아있는 in-memory 공유 게시판.

    - write/read는 thread-safe (asyncio + 동기 호출 혼용 가능)
    - subscribe는 asyncio 기반: 술어(predicate)가 True가 되는 entry를 await
    - 새 entry는 항상 append-only, 기존 entry 수정 금지 (immutable)
    """

    def __init__(self) -> None:
        self._entries: List[BlackboardEntry] = []
        self._lock = threading.Lock()
        # 새 entry가 추가될 때 모든 대기자에게 통지 (asyncio.Condition은 event loop 종속이라
        # 이벤트 루프 외부에서도 안전한 콜백 리스트로 처리)
        self._waiters: List["_Waiter"] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def write(
        self,
        participant_id: str,
        key: str,
        value: Any = None,
        *,
        step: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ) -> BlackboardEntry:
        entry = BlackboardEntry(
            participant_id=participant_id,
            key=key,
            value=value,
            step=step,
            tags=list(tags) if tags else [],
        )
        with self._lock:
            self._entries = [*self._entries, entry]
            # 통지 대상 추출 (락 안에서 매칭 평가)
            triggered: List["_Waiter"] = []
            remaining: List["_Waiter"] = []
            for waiter in self._waiters:
                if waiter.predicate(entry):
                    triggered.append(waiter)
                else:
                    remaining.append(waiter)
            self._waiters = remaining

        # 통지는 락 밖에서 (콜백이 다시 락을 잡으면 데드락)
        for waiter in triggered:
            waiter.fulfill(entry)
        return entry

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def read_recent(
        self,
        *,
        participant_id: Optional[str] = None,
        key: Optional[str] = None,
        limit: int = 10,
    ) -> List[BlackboardEntry]:
        """가장 최근 entry부터 limit개 반환."""
        with self._lock:
            snapshot = list(self._entries)

        filtered = snapshot
        if participant_id is not None:
            filtered = [e for e in filtered if e.participant_id == participant_id]
        if key is not None:
            filtered = [e for e in filtered if e.key == key]
        return list(reversed(filtered[-limit:]))

    def latest(self, key: str) -> Optional[BlackboardEntry]:
        """특정 key의 가장 최근 entry."""
        with self._lock:
            for entry in reversed(self._entries):
                if entry.key == key:
                    return entry
        return None

    def all_entries(self) -> List[BlackboardEntry]:
        with self._lock:
            return list(self._entries)

    # ------------------------------------------------------------------
    # Subscribe (asyncio)
    # ------------------------------------------------------------------
    async def wait_for(
        self,
        predicate: Callable[[BlackboardEntry], bool],
        *,
        timeout: Optional[float] = None,
    ) -> Optional[BlackboardEntry]:
        """
        predicate가 True인 entry가 들어올 때까지 await.

        - 호출 시점에 이미 만족하는 entry가 있으면 즉시 그 entry 반환 (가장 최근 것)
        - timeout 초과 시 None 반환 (asyncio.TimeoutError 잡아서 None)
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BlackboardEntry] = loop.create_future()

        with self._lock:
            for entry in reversed(self._entries):
                if predicate(entry):
                    return entry
            waiter = _Waiter(predicate=predicate, future=future, loop=loop)
            self._waiters = [*self._waiters, waiter]

        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._waiters = [w for w in self._waiters if w is not waiter]
            return None

    # ------------------------------------------------------------------
    # Prompt summary
    # ------------------------------------------------------------------
    def to_prompt_summary(
        self,
        viewer_participant_id: str,
        *,
        limit: int = 20,
        name_resolver: Optional[Callable[[str], str]] = None,
    ) -> str:
        """
        LLM 프롬프트에 주입할 압축 텍스트.

        자기 발언/관찰은 1인칭으로, 타인은 3인칭으로 표현한다.
        """
        with self._lock:
            recent = list(self._entries[-limit:])
        if not recent:
            return ""

        def render_actor(pid: str) -> str:
            if pid == viewer_participant_id:
                return "I"
            if name_resolver:
                return name_resolver(pid)
            return pid

        lines: List[str] = []
        for entry in recent:
            actor = render_actor(entry.participant_id)
            value_repr = "" if entry.value is None else f" — {entry.value!r}"
            lines.append(f"- step {entry.step}: {actor} :: {entry.key}{value_repr}")
        return "\n".join(lines)


class _Waiter:
    """asyncio.Future + predicate를 묶어둔 내부 보관소."""

    __slots__ = ("predicate", "future", "loop")

    def __init__(
        self,
        *,
        predicate: Callable[[BlackboardEntry], bool],
        future: asyncio.Future,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.predicate = predicate
        self.future = future
        self.loop = loop

    def fulfill(self, entry: BlackboardEntry) -> None:
        # write가 다른 스레드에서 호출됐을 수도 있으므로 loop에 안전 dispatch
        if self.future.done():
            return

        def _set_result() -> None:
            if not self.future.done():
                self.future.set_result(entry)

        try:
            self.loop.call_soon_threadsafe(_set_result)
        except RuntimeError:
            # 루프가 이미 닫힘
            pass
