"""Shared data models for GAIA components."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TestStep(BaseModel):
    """Single action inside a generated test scenario."""

    description: str
    action: str
    selector: str
    params: List[Any] = Field(default_factory=list)


class Assertion(BaseModel):
    """Post-conditions verified after running a scenario."""

    # Old format (for backward compatibility)
    description: Optional[str] = None
    selector: Optional[str] = None
    condition: Optional[str] = None
    params: List[Any] = Field(default_factory=list)

    # New Vision AI format
    expected_outcome: Optional[str] = None
    success_indicators: List[str] = Field(default_factory=list)


class TestScenario(BaseModel):
    """Structured UI automation scenario produced by the planner."""

    id: str
    priority: str
    scenario: str
    steps: List[TestStep] = Field(default_factory=list)
    assertion: Assertion


class DomElement(BaseModel):
    """Simplified representation of an interactive DOM element."""

    tag: str
    selector: str
    text: str = ""
    attributes: Dict[str, Any] = Field(default_factory=dict)
    element_type: str = ""


class ChecklistItem(BaseModel):
    """Runtime state for a single checklist entry."""

    feature_id: str
    description: str
    checked: bool = False
    evidence: Optional[str] = None
