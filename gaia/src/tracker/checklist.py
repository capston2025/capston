"""Runtime checklist tracker for GAIA."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from gaia.src.utils.models import ChecklistItem, TestScenario


@dataclass(slots=True)
class ChecklistTracker:
    """Stores checklist progress and provides simple coverage metrics."""

    items: Dict[str, ChecklistItem] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def seed_from_scenarios(self, scenarios: Iterable[TestScenario]) -> None:
        for scenario in scenarios:
            feature_id = scenario.id or scenario.scenario
            description = scenario.scenario
            self.items[feature_id] = ChecklistItem(
                feature_id=feature_id,
                description=description,
                checked=False,
            )

    def mark_found(self, feature_id: str, *, evidence: str | None = None) -> bool:
        item = self.items.get(feature_id)
        if not item:
            return False
        item.checked = True
        if evidence:
            item.evidence = evidence
        return True

    def mark_by_predicate(self, predicate: str, *, evidence: str | None = None) -> List[ChecklistItem]:
        hits: List[ChecklistItem] = []
        for item in self.items.values():
            if predicate.lower() in item.description.lower():
                item.checked = True
                if evidence:
                    item.evidence = evidence
                hits.append(item)
        return hits

    def as_dict(self) -> Dict[str, ChecklistItem]:
        return self.items

    def coverage(self) -> float:
        total = len(self.items)
        if total == 0:
            return 0.0
        covered = sum(1 for item in self.items.values() if item.checked)
        return covered / total
