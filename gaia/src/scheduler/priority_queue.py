"""
우선순위 큐 관리

적응형 테스트 스케줄링을 위한 힙 기반 우선순위 큐입니다.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any, Dict, List

from .scoring import compute_priority_score
from .state import GAIAState


@dataclass(order=True)
class PriorityItem:
    """
    힙 큐 항목을 감싸는 래퍼입니다.
    Python heapq는 최소 힙이므로 최대 힙처럼 동작하도록 점수에 음수를 붙입니다.
    """
    neg_score: int  # 최대 힙용 음수 점수
    item_id: str
    item: Dict[str, Any] = None  # 정렬 시 비교하지 않음

    def __post_init__(self):
        # item이 비교 대상에 포함되지 않도록 설정
        object.__setattr__(self, 'item', self.item)


class AdaptivePriorityQueue:
    """
    항목의 점수를 동적으로 재계산하는 적응형 우선순위 큐입니다.

    특징:
    - 힙 기반 구조로 삽입/추출이 O(log n)
    - 상태가 변하면 점수를 다시 계산
    - 완료된 테스트를 제외
    - 상위 N개의 실행 후보를 유지
    """

    def __init__(self, max_size: int = 100):
        """
        우선순위 큐를 초기화합니다.

        매개변수:
            max_size: 큐에 유지할 최대 항목 수
        """
        self._heap: List[PriorityItem] = []
        self._max_size = max_size
        self._item_map: Dict[str, Dict[str, Any]] = {}

    def push(self, item: Dict[str, Any], state: GAIAState) -> None:
        """
        계산된 우선순위 점수를 사용해 항목을 큐에 추가합니다.

        매개변수:
            item: 테스트 항목 dict (`id` 키 필수)
            state: 현재 GAIA 상태
        """
        item_id = item.get("id", "")
        if not item_id:
            raise ValueError("Item must have 'id' field")

        # 이미 완료된 경우 건너뜀
        if state.is_test_completed(item_id):
            return

        score = compute_priority_score(item, state)
        priority_item = PriorityItem(
            neg_score=-score,  # 최대 힙을 위해 음수로 변환
            item_id=item_id,
            item=item
        )

        heapq.heappush(self._heap, priority_item)
        self._item_map[item_id] = item

        # 최대 크기를 초과하면 잘라냄
        if len(self._heap) > self._max_size:
            self._trim_queue()

    def pop(self) -> Dict[str, Any] | None:
        """
        가장 높은 우선순위의 항목을 꺼냅니다.

        반환:
            큐가 비어 있지 않으면 항목 dict, 아니면 None
        """
        while self._heap:
            priority_item = heapq.heappop(self._heap)
            item_id = priority_item.item_id

            # 맵에서 제거
            self._item_map.pop(item_id, None)

            return priority_item.item

        return None

    def peek(self) -> Dict[str, Any] | None:
        """
        제거하지 않고 가장 높은 우선순위 항목을 확인합니다.

        반환:
            큐가 비어 있지 않으면 항목 dict, 아니면 None
        """
        if self._heap:
            return self._heap[0].item
        return None

    def rescore_all(self, state: GAIAState) -> None:
        """
        모든 점수를 다시 계산하고 힙을 재구성합니다.

        DOM 변경이나 상태가 크게 업데이트될 때 호출합니다.

        매개변수:
            state: 갱신된 GAIA 상태
        """
        # 모든 항목 추출
        items = [pi.item for pi in self._heap if pi.item is not None]

        # 힙 초기화
        self._heap.clear()
        self._item_map.clear()

        # 새 점수로 다시 삽입
        for item in items:
            # 완료된 테스트는 건너뜀
            if not state.is_test_completed(item.get("id", "")):
                self.push(item, state)

    def get_top_n(self, n: int) -> List[Dict[str, Any]]:
        """
        제거하지 않고 상위 N개의 항목을 가져옵니다.

        매개변수:
            n: 가져올 항목 수

        반환:
            최대 N개의 우선순위 높은 항목 목록
        """
        sorted_heap = sorted(self._heap, key=lambda pi: pi.neg_score)
        return [pi.item for pi in sorted_heap[:n] if pi.item is not None]

    def size(self) -> int:
        """현재 큐 크기를 반환합니다."""
        return len(self._heap)

    def is_empty(self) -> bool:
        """큐가 비어 있는지 확인합니다."""
        return len(self._heap) == 0

    def clear(self) -> None:
        """큐에서 모든 항목을 제거합니다."""
        self._heap.clear()
        self._item_map.clear()

    def _trim_queue(self) -> None:
        """최대 크기를 유지하기 위해 우선순위가 낮은 항목을 제거합니다."""
        # 정렬 후 상위 max_size개의 항목만 유지
        sorted_heap = sorted(self._heap, key=lambda pi: pi.neg_score)
        self._heap = sorted_heap[:self._max_size]
        heapq.heapify(self._heap)

        # 맵 갱신
        valid_ids = {pi.item_id for pi in self._heap}
        self._item_map = {
            k: v for k, v in self._item_map.items() if k in valid_ids
        }

    def contains(self, item_id: str) -> bool:
        """항목이 큐에 존재하는지 확인합니다."""
        return item_id in self._item_map

    def remove(self, item_id: str) -> bool:
        """
        특정 항목을 큐에서 제거합니다.

        매개변수:
            item_id: 항목 식별자

        반환:
            항목이 존재하여 제거되었으면 True
        """
        if item_id not in self._item_map:
            return False

        # 맵에서 제거
        self._item_map.pop(item_id)

        # 해당 항목 없이 힙 재구성
        self._heap = [pi for pi in self._heap if pi.item_id != item_id]
        heapq.heapify(self._heap)

        return True
