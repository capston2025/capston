"""
Adaptive Scheduler - Core Orchestration

Implements adaptive test execution with dynamic priority re-scoring.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from .logger import PriorityLogger
from .priority_queue import AdaptivePriorityQueue
from .state import GAIAState


class AdaptiveScheduler:
    """
    Adaptive test scheduler that dynamically adjusts execution order.

    Workflow:
    1. Receive test items from external agent (with priorities)
    2. Compute scores and populate priority queue
    3. Execute top-priority items
    4. Detect DOM/URL changes
    5. Re-score queue based on new state
    6. Repeat until completion criteria met

    Features:
    - Dynamic priority adjustment
    - DOM change detection
    - Failure retry logic
    - Stagnation detection
    - Detailed logging
    """

    def __init__(
        self,
        max_queue_size: int = 100,
        top_n_execution: int = 5,
        log_file: str = "priority_log.json",
    ):
        """
        Initialize adaptive scheduler.

        Args:
            max_queue_size: Maximum items in priority queue
            top_n_execution: Number of top items to consider per round
            log_file: Path to priority log file
        """
        self.state = GAIAState()
        self.queue = AdaptivePriorityQueue(max_size=max_queue_size)
        self.logger = PriorityLogger(log_file=log_file)
        self.top_n_execution = top_n_execution

        # Execution statistics
        self.stats = {
            "total_received": 0,
            "total_executed": 0,
            "total_success": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "rescore_count": 0,
        }

    def ingest_items(self, items: List[Dict[str, Any]]) -> None:
        """
        Receive test items from external agent and add to queue.

        Args:
            items: List of test items with priority metadata
        """
        for item in items:
            # Validate required fields
            if "id" not in item or "priority" not in item:
                continue

            self.queue.push(item, self.state)
            self.logger.log_score(item, self.state, action="ingested")
            self.stats["total_received"] += 1

    def execute_next_batch(
        self,
        executor: Callable[[Dict[str, Any]], Dict[str, Any]],
        max_items: int | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute next batch of highest-priority tests.

        Args:
            executor: Callable that executes a test item
                     Returns: {"status": "success"|"failed", "dom_signature": str, ...}
            max_items: Maximum items to execute (defaults to top_n_execution)

        Returns:
            List of execution results
        """
        if max_items is None:
            max_items = self.top_n_execution

        results = []
        initial_dom = self.state.current_dom_signature

        for _ in range(max_items):
            if self.queue.is_empty():
                break

            item = self.queue.pop()
            if item is None:
                break

            # Execute test
            result = self._execute_item(item, executor)
            results.append(result)

            # Check for DOM change
            new_dom = result.get("dom_signature")
            if new_dom and new_dom != initial_dom:
                self._handle_dom_change(new_dom)
                initial_dom = new_dom

        return results

    def execute_until_complete(
        self,
        executor: Callable[[Dict[str, Any]], Dict[str, Any]],
        max_rounds: int = 20,
        completion_threshold: float = 0.9,
    ) -> Dict[str, Any]:
        """
        Execute tests adaptively until completion criteria met.

        Args:
            executor: Test execution callable
            max_rounds: Maximum execution rounds
            completion_threshold: Fraction of MUST tests to complete (0.0-1.0)

        Returns:
            Final execution summary
        """
        for round_num in range(1, max_rounds + 1):
            self.state.increment_round()

            if self.queue.is_empty():
                break

            # Check completion threshold
            if self._check_completion_threshold(completion_threshold):
                break

            # Execute batch
            results = self.execute_next_batch(executor)

            if not results:
                break

        # Final statistics
        self.logger.save()
        return self._generate_summary()

    def _execute_item(
        self,
        item: Dict[str, Any],
        executor: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Execute single test item.

        Args:
            item: Test item to execute
            executor: Execution callable

        Returns:
            Execution result dict
        """
        item_id = item.get("id", "")
        self.stats["total_executed"] += 1

        try:
            # Call executor (e.g., MCP host, Playwright)
            result = executor(item)

            status = result.get("status", "failed")

            if status == "success":
                self.state.mark_test_completed(item_id)
                self.stats["total_success"] += 1
                self.logger.log_execution(item, self.state, "success", result)

            elif status == "failed":
                self.state.mark_test_failed(item_id)
                self.stats["total_failed"] += 1
                self.logger.log_execution(item, self.state, "failed", result)

                # Re-add to queue with retry bonus
                if not result.get("fatal", False):
                    self.queue.push(item, self.state)

            # Track URL if present
            target_url = item.get("target_url") or result.get("current_url")
            if target_url:
                self.state.mark_url_visited(target_url)

            # Track DOM signature
            dom_sig = result.get("dom_signature")
            if dom_sig:
                self.state.mark_dom_seen(dom_sig)

            return result

        except Exception as e:
            # Execution error
            self.state.mark_test_failed(item_id)
            self.stats["total_failed"] += 1

            error_result = {
                "status": "failed",
                "error": str(e),
                "item_id": item_id,
            }

            self.logger.log_execution(item, self.state, "failed", error_result)
            return error_result

    def _handle_dom_change(self, new_dom_signature: str) -> None:
        """
        Handle DOM change by re-scoring queue.

        Args:
            new_dom_signature: New DOM structure hash
        """
        if self.state.is_dom_new(new_dom_signature):
            self.state.mark_dom_seen(new_dom_signature)
            self.queue.rescore_all(self.state)
            self.logger.log_rescore(self.state, reason="dom_change")
            self.stats["rescore_count"] += 1

    def _check_completion_threshold(self, threshold: float) -> bool:
        """
        Check if completion threshold has been met.

        Args:
            threshold: Required completion ratio (0.0-1.0)

        Returns:
            True if threshold met
        """
        # Count MUST priority items in queue
        top_items = self.queue.get_top_n(self.queue.size())
        must_items = [item for item in top_items if item.get("priority") == "MUST"]

        total_must = len(must_items) + len([
            tid for tid in self.state.completed_test_ids
            # Assume completed items were MUST (conservative)
        ])

        if total_must == 0:
            return True

        completed_must = len(self.state.completed_test_ids)
        completion_ratio = completed_must / total_must

        return completion_ratio >= threshold

    def _generate_summary(self) -> Dict[str, Any]:
        """
        Generate final execution summary.

        Returns:
            Summary dict with statistics
        """
        log_summary = self.logger.get_summary()

        return {
            "execution_stats": self.stats.copy(),
            "state_summary": {
                "visited_urls": len(self.state.visited_urls),
                "visited_dom_signatures": len(self.state.visited_dom_signatures),
                "completed_tests": len(self.state.completed_test_ids),
                "failed_tests": len(self.state.failed_test_ids),
                "execution_rounds": self.state.execution_round,
            },
            "queue_summary": {
                "remaining_items": self.queue.size(),
                "top_pending": self.queue.get_top_n(5),
            },
            "log_summary": log_summary,
        }

    def get_state(self) -> GAIAState:
        """Get current scheduler state."""
        return self.state

    def get_stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        return self.stats.copy()

    def clear(self) -> None:
        """Reset scheduler to initial state."""
        self.state = GAIAState()
        self.queue.clear()
        self.logger.clear()
        self.stats = {
            "total_received": 0,
            "total_executed": 0,
            "total_success": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "rescore_count": 0,
        }


def compute_dom_signature(dom_data: Dict[str, Any]) -> str:
    """
    Compute DOM signature hash for change detection.

    Args:
        dom_data: DOM structure data (e.g., from MCP host)

    Returns:
        MD5 hash of DOM structure
    """
    # Extract relevant DOM features
    elements = dom_data.get("elements", [])
    element_signatures = [
        f"{el.get('tag', '')}:{el.get('selector', '')}"
        for el in elements
    ]

    signature_str = "|".join(sorted(element_signatures))
    return hashlib.md5(signature_str.encode()).hexdigest()
