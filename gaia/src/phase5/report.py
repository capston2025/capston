"""Simple reporting helpers for MVP."""
from __future__ import annotations

from typing import Dict, List

from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.models import ChecklistItem


def build_summary(tracker: ChecklistTracker) -> Dict[str, object]:
    """Return a basic coverage summary for the checklist."""

    items: List[ChecklistItem] = list(tracker.items.values())
    covered = [item for item in items if item.checked]
    remaining = [item for item in items if not item.checked]

    return {
        "coverage": tracker.coverage(),
        "covered": [item.model_dump() for item in covered],
        "remaining": [item.model_dump() for item in remaining],
    }
