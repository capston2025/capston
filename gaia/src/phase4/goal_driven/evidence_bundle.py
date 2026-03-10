from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class EvidenceBundle:
    raw: Dict[str, Any] = field(default_factory=dict)
    derived: Dict[str, Any] = field(default_factory=dict)
    baseline: Dict[str, Any] = field(default_factory=dict)
    current: Dict[str, Any] = field(default_factory=dict)
    delta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidatorResult:
    status: str = "skipped_not_applicable"
    validator: str = ""
    mandatory: bool = False
    reason_code: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CloserResult:
    status: str = "continue"
    reason_code: str = ""
    proof: str = ""
    proof_source: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InterruptResult:
    matched: bool = False
    status: str = "continue"
    reason_code: str = ""
    proof: str = ""
    policy_name: str = ""
    payload: Optional[Dict[str, Any]] = None
