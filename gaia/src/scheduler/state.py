"""
GAIA State Management

Tracks exploration state for adaptive scheduling decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Set


@dataclass
class GAIAState:
    """
    Maintains GAIA's current exploration state.

    Attributes:
        visited_urls: Set of URLs already explored
        visited_dom_signatures: Set of DOM structure hashes seen
        failed_test_ids: Set of test IDs that recently failed
        completed_test_ids: Set of test IDs successfully completed
        current_dom_signature: Hash of current DOM structure
        execution_round: Current execution round number
    """
    visited_urls: Set[str] = field(default_factory=set)
    visited_dom_signatures: Set[str] = field(default_factory=set)
    failed_test_ids: Set[str] = field(default_factory=set)
    completed_test_ids: Set[str] = field(default_factory=set)
    current_dom_signature: str | None = None
    execution_round: int = 0

    def mark_url_visited(self, url: str) -> None:
        """Mark a URL as visited."""
        if url:  # Ignore empty strings
            self.visited_urls.add(url)

    def mark_dom_seen(self, dom_signature: str) -> None:
        """Mark a DOM signature as seen."""
        if dom_signature:  # Ignore empty strings
            self.visited_dom_signatures.add(dom_signature)
            self.current_dom_signature = dom_signature

    def mark_test_failed(self, test_id: str) -> None:
        """Mark a test as failed."""
        if test_id:  # Ignore empty strings
            self.failed_test_ids.add(test_id)

    def mark_test_completed(self, test_id: str) -> None:
        """Mark a test as completed."""
        if test_id:  # Ignore empty strings
            self.completed_test_ids.add(test_id)
            # Remove from failed set if it was there
            self.failed_test_ids.discard(test_id)

    def is_url_new(self, url: str) -> bool:
        """Check if URL has not been visited."""
        return url not in self.visited_urls

    def is_dom_new(self, dom_signature: str) -> bool:
        """Check if DOM signature is new."""
        return dom_signature not in self.visited_dom_signatures

    def was_test_failed(self, test_id: str) -> bool:
        """Check if test recently failed."""
        return test_id in self.failed_test_ids

    def is_test_completed(self, test_id: str) -> bool:
        """Check if test is already completed."""
        return test_id in self.completed_test_ids

    def increment_round(self) -> None:
        """Move to next execution round."""
        self.execution_round += 1

    def reset(self) -> None:
        """Reset state to initial values."""
        self.visited_urls.clear()
        self.visited_dom_signatures.clear()
        self.failed_test_ids.clear()
        self.completed_test_ids.clear()
        self.current_dom_signature = None
        self.execution_round = 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get current state statistics.

        Returns:
            Dict with state metrics
        """
        return {
            "visited_urls_count": len(self.visited_urls),
            "visited_dom_count": len(self.visited_dom_signatures),
            "failed_tests_count": len(self.failed_test_ids),
            "completed_tests_count": len(self.completed_test_ids),
            "execution_round": self.execution_round,
        }
