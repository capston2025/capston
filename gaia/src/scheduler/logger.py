"""
Priority Score Logging

Records priority score calculations for analysis and debugging.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from gaia.src.scheduler.scoring import compute_score_breakdown
from gaia.src.scheduler.state import GAIAState


class PriorityLogger:
    """
    Logs priority score calculations and scheduling decisions.

    Log Format:
    {
        "id": "TC001",
        "score": 135,
        "priority": "MUST",
        "base_score": 100,
        "dom_bonus": 15,
        "url_bonus": 20,
        "fail_bonus": 0,
        "no_change_penalty": 0,
        "timestamp": "2025-10-22T14:00:00Z",
        "execution_round": 1
    }
    """

    def __init__(self, log_file: Path | str = "priority_log.json"):
        """
        Initialize logger.

        Args:
            log_file: Path to JSON log file
        """
        self.log_file = Path(log_file)
        self._entries: List[Dict[str, Any]] = []

    def log_score(
        self,
        item: Dict[str, Any],
        state: GAIAState,
        action: str = "scored"
    ) -> None:
        """
        Log a score calculation.

        Args:
            item: Test item
            state: Current GAIA state
            action: Action type (e.g., "scored", "executed", "skipped")
        """
        breakdown = compute_score_breakdown(item, state)

        entry = {
            "id": item.get("id", ""),
            "action": action,
            "score": breakdown["total_score"],
            "priority": item.get("priority", "MAY"),
            "base_score": breakdown["base_priority_score"],
            "dom_bonus": breakdown["dom_bonus"],
            "url_bonus": breakdown["url_bonus"],
            "fail_bonus": breakdown["fail_bonus"],
            "no_change_penalty": breakdown["no_change_penalty"],
            "new_elements_count": breakdown["new_elements_count"],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "execution_round": state.execution_round,
        }

        self._entries.append(entry)

    def log_execution(
        self,
        item: Dict[str, Any],
        state: GAIAState,
        result: str,
        details: Dict[str, Any] | None = None
    ) -> None:
        """
        Log test execution result.

        Args:
            item: Test item
            state: Current GAIA state
            result: Result status ("success", "failed", "skipped")
            details: Optional execution details
        """
        breakdown = compute_score_breakdown(item, state)

        entry = {
            "id": item.get("id", ""),
            "action": "executed",
            "result": result,
            "score": breakdown["total_score"],
            "priority": item.get("priority", "MAY"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "execution_round": state.execution_round,
        }

        if details:
            entry["details"] = details

        self._entries.append(entry)

    def log_rescore(self, state: GAIAState, reason: str) -> None:
        """
        Log queue re-scoring event.

        Args:
            state: Current GAIA state
            reason: Reason for re-scoring (e.g., "dom_change", "new_url")
        """
        entry = {
            "action": "rescore",
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "execution_round": state.execution_round,
            "state_summary": {
                "visited_urls": len(state.visited_urls),
                "visited_doms": len(state.visited_dom_signatures),
                "failed_tests": len(state.failed_test_ids),
                "completed_tests": len(state.completed_test_ids),
            }
        }

        self._entries.append(entry)

    def save(self) -> None:
        """Write log entries to file."""
        with self.log_file.open("w", encoding="utf-8") as f:
            json.dump(self._entries, f, indent=2, ensure_ascii=False)

    def get_entries(self) -> List[Dict[str, Any]]:
        """Get all log entries."""
        return self._entries.copy()

    def get_summary(self) -> Dict[str, Any]:
        """
        Generate summary statistics from log.

        Returns:
            Dict with summary metrics
        """
        if not self._entries:
            return {"total_entries": 0}

        executed = [e for e in self._entries if e.get("action") == "executed"]
        rescores = [e for e in self._entries if e.get("action") == "rescore"]

        success_count = sum(1 for e in executed if e.get("result") == "success")
        failed_count = sum(1 for e in executed if e.get("result") == "failed")

        # Average scores by priority
        priority_scores = {}
        for entry in self._entries:
            if "priority" in entry and "score" in entry:
                priority = entry["priority"]
                if priority not in priority_scores:
                    priority_scores[priority] = []
                priority_scores[priority].append(entry["score"])

        avg_scores = {
            p: sum(scores) / len(scores) if scores else 0
            for p, scores in priority_scores.items()
        }

        return {
            "total_entries": len(self._entries),
            "executed_tests": len(executed),
            "success_count": success_count,
            "failed_count": failed_count,
            "rescore_events": len(rescores),
            "average_scores_by_priority": avg_scores,
        }

    def clear(self) -> None:
        """Clear all log entries."""
        self._entries.clear()
