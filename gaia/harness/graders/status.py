from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..report_schema import GraderOutcome
from .base import BaseGrader


DEFAULT_STATUS_PATHS = (
    "status",
    "summary.status",
    "result.status",
    "report.status",
    "validation_summary.status",
)


class StatusGrader(BaseGrader):
    """Deterministic grader that checks the high-level GAIA status."""

    def __init__(
        self,
        expected_statuses: Iterable[str] = ("passed",),
        payload_paths: Iterable[str] = (),
        status_paths: Iterable[str] = DEFAULT_STATUS_PATHS,
    ) -> None:
        super().__init__("status", payload_paths)
        self._expected_statuses = {
            self.normalize_text(status) for status in expected_statuses if self.normalize_text(status)
        }
        self._status_paths = tuple(status_paths)

    def grade(self, payload: Any) -> GraderOutcome:
        resolved = self._resolve_payload(payload)
        actual = self.first_present(resolved, self._status_paths)
        if actual is None:
            actual = self.first_present(payload, self._status_paths)

        if actual is None:
            success_value = self.first_present(
                payload,
                ("success", "summary.success", "result.success", "report.success"),
            )
            if isinstance(success_value, bool):
                actual = "passed" if success_value else "failed"

        observed = self.normalize_text(actual) or "unknown"
        passed = observed in self._expected_statuses
        expected = ", ".join(sorted(self._expected_statuses)) or "passed"
        reason = "status matched expected value" if passed else "status did not match expected value"
        score = 1.0 if passed else 0.0
        return GraderOutcome(
            grader=self.name,
            passed=passed,
            score=score,
            reason=reason,
            observed=observed,
            expected=expected,
            details={
                "status_paths": list(self._status_paths),
                "expected_statuses": sorted(self._expected_statuses),
                "raw_status": actual,
            },
        )

