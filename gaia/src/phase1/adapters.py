"""Adapters for Agent workflow outputs."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from gaia.src.utils.models import Assertion, TestScenario, TestStep


PRIORITY_MAP = {
    "MUST": "High",
    "SHOULD": "Medium",
    "MAY": "Low",
}


def checklist_to_scenarios(payload: Dict[str, Any]) -> List[TestScenario]:
    """Convert agent checklist entries into TestScenario objects."""

    checklist: Iterable[Dict[str, Any]] = payload.get("checklist", []) if isinstance(payload, dict) else []
    scenarios: List[TestScenario] = []
    for entry in checklist:
        if not isinstance(entry, dict):
            continue

        tc_id = str(entry.get("id") or "TC_UNKNOWN")
        name = str(entry.get("name") or "Unnamed scenario")
        priority = str(entry.get("priority") or "MAY").upper()

        steps_raw = entry.get("steps") or []
        steps: List[TestStep] = []
        for raw_step in steps_raw:
            if isinstance(raw_step, str):
                description = raw_step
            elif isinstance(raw_step, dict):
                description = raw_step.get("description", "")
            else:
                continue
            steps.append(
                TestStep(
                    description=description,
                    action=raw_step.get("action", "noop") if isinstance(raw_step, dict) else "noop",
                    selector=raw_step.get("selector", "") if isinstance(raw_step, dict) else "",
                    params=list(raw_step.get("params", [])) if isinstance(raw_step, dict) else [],
                )
            )

        assertion_text = str(entry.get("expected_result") or "")
        assertion = Assertion(
            description=assertion_text,
            selector="",
            condition="note",
            params=[],
        )

        scenarios.append(
            TestScenario(
                id=tc_id,
                priority=PRIORITY_MAP.get(priority, "Low"),
                scenario=name,
                steps=steps,
                assertion=assertion,
            )
        )
    return scenarios
