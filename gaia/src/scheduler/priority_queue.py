"""
Priority Queue Management

Heap-based priority queue for adaptive test scheduling.
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
    Wrapper for heap queue items.
    Python heapq is a min-heap, so we negate score for max-heap behavior.
    """
    neg_score: int  # Negative score for max-heap
    item_id: str
    item: Dict[str, Any] = None  # Not compared in ordering

    def __post_init__(self):
        # Ensure item is not compared
        object.__setattr__(self, 'item', self.item)


class AdaptivePriorityQueue:
    """
    Adaptive priority queue that dynamically re-scores items.

    Features:
    - Heap-based for O(log n) insertion/extraction
    - Dynamic re-scoring when state changes
    - Filters out completed tests
    - Maintains top-N execution candidates
    """

    def __init__(self, max_size: int = 100):
        """
        Initialize priority queue.

        Args:
            max_size: Maximum number of items to keep in queue
        """
        self._heap: List[PriorityItem] = []
        self._max_size = max_size
        self._item_map: Dict[str, Dict[str, Any]] = {}

    def push(self, item: Dict[str, Any], state: GAIAState) -> None:
        """
        Add item to queue with computed priority score.

        Args:
            item: Test item dict (must have 'id' key)
            state: Current GAIA state
        """
        item_id = item.get("id", "")
        if not item_id:
            raise ValueError("Item must have 'id' field")

        # Skip if already completed
        if state.is_test_completed(item_id):
            return

        score = compute_priority_score(item, state)
        priority_item = PriorityItem(
            neg_score=-score,  # Negate for max-heap
            item_id=item_id,
            item=item
        )

        heapq.heappush(self._heap, priority_item)
        self._item_map[item_id] = item

        # Trim if exceeds max size
        if len(self._heap) > self._max_size:
            self._trim_queue()

    def pop(self) -> Dict[str, Any] | None:
        """
        Extract highest priority item.

        Returns:
            Item dict or None if queue is empty
        """
        while self._heap:
            priority_item = heapq.heappop(self._heap)
            item_id = priority_item.item_id

            # Remove from map
            self._item_map.pop(item_id, None)

            return priority_item.item

        return None

    def peek(self) -> Dict[str, Any] | None:
        """
        View highest priority item without removing.

        Returns:
            Item dict or None if queue is empty
        """
        if self._heap:
            return self._heap[0].item
        return None

    def rescore_all(self, state: GAIAState) -> None:
        """
        Recalculate all scores and rebuild heap.

        Called when DOM changes or state updates significantly.

        Args:
            state: Updated GAIA state
        """
        # Extract all items
        items = [pi.item for pi in self._heap if pi.item is not None]

        # Clear heap
        self._heap.clear()
        self._item_map.clear()

        # Re-insert with new scores
        for item in items:
            # Skip completed tests
            if not state.is_test_completed(item.get("id", "")):
                self.push(item, state)

    def get_top_n(self, n: int) -> List[Dict[str, Any]]:
        """
        Get top N items without removing them.

        Args:
            n: Number of top items to retrieve

        Returns:
            List of up to N highest priority items
        """
        sorted_heap = sorted(self._heap, key=lambda pi: pi.neg_score)
        return [pi.item for pi in sorted_heap[:n] if pi.item is not None]

    def size(self) -> int:
        """Return current queue size."""
        return len(self._heap)

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return len(self._heap) == 0

    def clear(self) -> None:
        """Clear all items from queue."""
        self._heap.clear()
        self._item_map.clear()

    def _trim_queue(self) -> None:
        """Remove lowest priority items to maintain max size."""
        # Sort and keep top max_size items
        sorted_heap = sorted(self._heap, key=lambda pi: pi.neg_score)
        self._heap = sorted_heap[:self._max_size]
        heapq.heapify(self._heap)

        # Update map
        valid_ids = {pi.item_id for pi in self._heap}
        self._item_map = {
            k: v for k, v in self._item_map.items() if k in valid_ids
        }

    def contains(self, item_id: str) -> bool:
        """Check if item is in queue."""
        return item_id in self._item_map

    def remove(self, item_id: str) -> bool:
        """
        Remove specific item from queue.

        Args:
            item_id: Item identifier

        Returns:
            True if item was found and removed
        """
        if item_id not in self._item_map:
            return False

        # Remove from map
        self._item_map.pop(item_id)

        # Rebuild heap without the item
        self._heap = [pi for pi in self._heap if pi.item_id != item_id]
        heapq.heapify(self._heap)

        return True
