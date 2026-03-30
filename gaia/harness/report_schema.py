from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NotRequired, TypedDict


class GAIAReasonCodeCount(TypedDict):
    """Single aggregated reason-code bucket."""

    reason_code: str
    count: int


class GAIAReasonCodeSummary(TypedDict, total=False):
    """Normalized reason-code counts emitted by GAIA runs."""

    reason_code_summary: dict[str, int]
    validation_reason_counts: dict[str, int]
    reason_codes: list[str]
    reason_code_counts: dict[str, int]
    top_reason_codes: list[GAIAReasonCodeCount]


class GAIAMembershipSummary(TypedDict, total=False):
    """Membership state summary emitted by GAIA runs."""

    membership_state: str
    membership_present: bool
    membership_verified: bool
    membership_required: bool
    membership_summary: dict[str, Any]


class GAIAAttemptSummary(TypedDict, total=False):
    """One deterministic attempt for a task."""

    attempt_index: int
    attempt_count: int
    attempt: int
    status: str
    final_status: str
    reason: str
    exit_code: int
    duration_seconds: float
    summary: dict[str, Any]
    grades: list[dict[str, Any]]
    overall_pass: bool
    reason_code_counts: dict[str, int]
    top_reason_codes: list[GAIAReasonCodeCount]


class GAIATaskSummary(TypedDict, total=False):
    """Task-level aggregation across repeated attempts."""

    task_id: str
    suite_id: str | None
    repeats: int
    status: str
    rows: list[GAIAAttemptSummary]
    attempts: list[GAIAAttemptSummary]
    attempt_count: int
    attempt_success_count: int
    attempt_failure_count: int
    pass_at_1: bool
    pass_at_k: bool
    pass_all_k: bool
    overall_pass: bool
    best_attempt_index: int
    grade_summary: dict[str, dict[str, int]]
    reason_code_summary: dict[str, int]
    reason_code_counts: dict[str, int]
    top_reason_codes: list[GAIAReasonCodeCount]
    grades: list[dict[str, Any]]


class GAIARunSummary(TypedDict, total=False):
    """Typed view of the GAIA summary/result payload used by the harness."""

    task_count: int
    attempts_total: int
    success_count: int
    failed_count: int
    attempt_success_count: int
    attempt_failed_count: int
    pass_at_1: float
    pass_at_k: float
    pass_all_k: float
    pass_rate: float
    task_pass_at_1: float
    task_pass_at_k: float
    task_pass_all_k: float
    task_success_rate: float
    attempt_success_rate: float
    status: str
    success: bool
    goal_satisfied: bool
    strict_failed: bool
    task_status_counts: dict[str, int]
    attempt_status_counts: dict[str, int]
    summary: dict[str, Any]
    validation_summary: dict[str, Any]
    checks: list[dict[str, Any]]
    cases: list[dict[str, Any]]
    rules_used: list[str]
    pages_checked: int
    reason_code_summary: dict[str, int]
    reason_code_counts: dict[str, int]
    reason_code_total: int
    top_reason_codes: list[GAIAReasonCodeCount]
    validation_reason_counts: dict[str, int]
    membership_state: str
    membership_present: bool
    membership_verified: bool
    membership_required: bool
    membership_summary: dict[str, Any]


class GAIARunResult(TypedDict, total=False):
    """Wrapper payload that may contain both raw result and derived summary."""

    result: dict[str, Any]
    report: dict[str, Any]
    summary: dict[str, Any]
    run_id: str
    generated_at: str
    registry: str | None
    task_count: int
    repeats: int
    results: list[GAIATaskSummary]
    tasks: list[GAIATaskSummary]
    grade_summary: dict[str, dict[str, int]]
    reason_code_summary: dict[str, int]
    reason_code_counts: dict[str, int]
    top_reason_codes: list[GAIAReasonCodeCount]
    artifact_dir: str
    status: str
    success: bool
    goal_satisfied: bool
    strict_failed: bool
    validation_summary: dict[str, Any]
    membership_summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GraderOutcome:
    """Uniform result returned by deterministic graders."""

    grader: str
    passed: bool
    score: float = 0.0
    reason: str = ""
    observed: str = ""
    expected: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader": self.grader,
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "observed": self.observed,
            "expected": self.expected,
            "details": dict(self.details),
        }
