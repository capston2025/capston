"""
GAIA Adaptive Scheduler Module

Implements priority-based adaptive test execution scheduling.
Dynamically adjusts test execution order based on:
- Base priority (MUST/SHOULD/MAY)
- DOM exploration bonuses
- URL novelty bonuses
- Failure retry incentives
- DOM stagnation penalties
"""
from gaia.src.scheduler.adaptive_scheduler import AdaptiveScheduler
from gaia.src.scheduler.scoring import compute_priority_score
from gaia.src.scheduler.state import GAIAState

__all__ = [
    "AdaptiveScheduler",
    "compute_priority_score",
    "GAIAState",
]
