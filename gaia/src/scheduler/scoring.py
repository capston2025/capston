"""
Priority Score Calculation Module

Implements the scoring policy for adaptive test scheduling.
"""
from __future__ import annotations

from typing import Any, Dict

from .state import GAIAState


# Priority base scores
PRIORITY_SCORES = {
    "MUST": 100,
    "SHOULD": 60,
    "MAY": 30,
}

# Bonus/penalty constants
BONUS_NEW_ELEMENTS = 15  # Per new element discovered
BONUS_UNSEEN_URL = 20     # For exploring new URLs
BONUS_RECENT_FAIL = 10    # Retry failed tests
PENALTY_NO_DOM_CHANGE = 25  # Penalize stagnant tests


def compute_priority_score(item: Dict[str, Any], state: GAIAState) -> int:
    """
    Calculate priority score for a test item based on:
    - Base priority (MUST/SHOULD/MAY)
    - New DOM elements discovered
    - URL novelty
    - Recent failure status
    - DOM change activity

    Args:
        item: Test item dict with keys:
            - id: Test identifier
            - priority: "MUST" | "SHOULD" | "MAY"
            - new_elements: Number of new elements (default 0)
            - target_url: URL to test (optional)
            - no_dom_change: Boolean flag (default False)
        state: Current GAIA exploration state

    Returns:
        Integer score (0-150+ range)

    Formula:
        score = base_priority
              + (new_elements * 15)
              + (unseen_url ? 20 : 0)
              + (recent_fail ? 10 : 0)
              - (no_dom_change ? 25 : 0)
    """
    # Base priority score
    priority = item.get("priority", "MAY")
    score = PRIORITY_SCORES.get(priority, 0)

    # New elements bonus
    new_elements = item.get("new_elements", 0)
    score += new_elements * BONUS_NEW_ELEMENTS

    # Unseen URL bonus
    target_url = item.get("target_url")
    if target_url and state.is_url_new(target_url):
        score += BONUS_UNSEEN_URL

    # Recent failure bonus (retry incentive)
    test_id = item.get("id", "")
    if state.was_test_failed(test_id):
        score += BONUS_RECENT_FAIL

    # No DOM change penalty
    if item.get("no_dom_change", False):
        score -= PENALTY_NO_DOM_CHANGE

    return max(0, score)  # Ensure non-negative


def compute_score_breakdown(item: Dict[str, Any], state: GAIAState) -> Dict[str, Any]:
    """
    Compute score with detailed breakdown for logging.

    Returns:
        Dict with score and breakdown components
    """
    priority = item.get("priority", "MAY")
    base_score = PRIORITY_SCORES.get(priority, 0)

    new_elements = item.get("new_elements", 0)
    dom_bonus = new_elements * BONUS_NEW_ELEMENTS

    target_url = item.get("target_url")
    url_bonus = BONUS_UNSEEN_URL if (target_url and state.is_url_new(target_url)) else 0

    test_id = item.get("id", "")
    fail_bonus = BONUS_RECENT_FAIL if state.was_test_failed(test_id) else 0

    no_change_penalty = PENALTY_NO_DOM_CHANGE if item.get("no_dom_change", False) else 0

    total_score = base_score + dom_bonus + url_bonus + fail_bonus - no_change_penalty

    return {
        "total_score": max(0, total_score),
        "base_priority_score": base_score,
        "dom_bonus": dom_bonus,
        "url_bonus": url_bonus,
        "fail_bonus": fail_bonus,
        "no_change_penalty": -no_change_penalty,
        "new_elements_count": new_elements,
    }
