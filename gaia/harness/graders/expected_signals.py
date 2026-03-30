from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..report_schema import GraderOutcome
from .base import BaseGrader


DEFAULT_ACHIEVED_PATHS = (
    "summary.achieved_signals",
    "report.summary.achieved_signals",
    "result.summary.achieved_signals",
    "achieved_signals",
)


class ExpectedSignalsGrader(BaseGrader):
    """Checks whether runtime reported all required expected signals."""

    def __init__(
        self,
        required_signals: Iterable[str],
        payload_paths: Iterable[str] = (),
        achieved_paths: Iterable[str] = DEFAULT_ACHIEVED_PATHS,
    ) -> None:
        super().__init__("expected_signals", payload_paths)
        self._required_signals = tuple(
            token
            for token in (
                self.normalize_text(signal)
                for signal in required_signals
            )
            if token
        )
        self._achieved_paths = tuple(achieved_paths)

    def grade(self, payload: Any) -> GraderOutcome:
        resolved = self._resolve_payload(payload)
        achieved_raw = self.first_present(resolved, self._achieved_paths)
        if achieved_raw is None:
            achieved_raw = self.first_present(payload, self._achieved_paths)
        achieved = []
        if isinstance(achieved_raw, list):
            for item in achieved_raw:
                token = self.normalize_text(item)
                if token and token not in achieved:
                    achieved.append(token)
        missing = [signal for signal in self._required_signals if signal not in achieved]
        passed = not missing
        return GraderOutcome(
            grader=self.name,
            passed=passed,
            score=1.0 if passed else 0.0,
            reason=(
                "all required expected signals were achieved"
                if passed
                else "required expected signals were missing"
            ),
            observed=", ".join(achieved) if achieved else "none",
            expected=", ".join(self._required_signals) if self._required_signals else "none",
            details={
                "required_signals": list(self._required_signals),
                "achieved_signals": list(achieved),
                "missing_signals": list(missing),
                "achieved_paths": list(self._achieved_paths),
            },
        )
