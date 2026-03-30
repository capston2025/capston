from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from ..report_schema import GraderOutcome
from .base import BaseGrader


DEFAULT_REASON_CODE_PATHS = (
    "reason_code_summary",
    "validation_reason_counts",
    "summary.reason_code_summary",
    "summary.validation_reason_counts",
    "report.reason_code_summary",
)


class ReasonCodesGrader(BaseGrader):
    """Deterministic grader that checks expected reason-code coverage."""

    def __init__(
        self,
        required_reason_codes: Iterable[str] = (),
        forbidden_reason_codes: Iterable[str] = (),
        payload_paths: Iterable[str] = (),
        reason_code_paths: Iterable[str] = DEFAULT_REASON_CODE_PATHS,
        minimum_counts: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__("reason_codes", payload_paths)
        self._required_reason_codes = {
            self.normalize_text(code) for code in required_reason_codes if self.normalize_text(code)
        }
        self._forbidden_reason_codes = {
            self.normalize_text(code) for code in forbidden_reason_codes if self.normalize_text(code)
        }
        self._reason_code_paths = tuple(reason_code_paths)
        self._minimum_counts = {
            self.normalize_text(code): int(count)
            for code, count in (minimum_counts or {}).items()
            if self.normalize_text(code)
        }

    def _collect_counts(self, payload: Any) -> dict[str, int]:
        resolved = self._resolve_payload(payload)
        candidate = self.first_present(resolved, self._reason_code_paths)
        if candidate is None:
            candidate = self.first_present(payload, self._reason_code_paths)

        counts: Counter[str] = Counter()
        if isinstance(candidate, Mapping):
            for code, value in candidate.items():
                normalized = self.normalize_text(code)
                if not normalized:
                    continue
                try:
                    counts[normalized] = int(value)
                except Exception:
                    counts[normalized] = 0
        elif isinstance(candidate, (list, tuple, set)):
            counts.update(
                code
                for code in (self.normalize_text(item) for item in candidate)
                if code
            )
        elif candidate is not None:
            normalized = self.normalize_text(candidate)
            if normalized:
                counts[normalized] = 1

        return dict(counts)

    def grade(self, payload: Any) -> GraderOutcome:
        counts = self._collect_counts(payload)
        missing: list[str] = []
        failed_thresholds: list[str] = []
        forbidden_present: list[str] = []

        for code in sorted(self._required_reason_codes):
            if counts.get(code, 0) <= 0:
                missing.append(code)

        for code, minimum in sorted(self._minimum_counts.items()):
            if counts.get(code, 0) < minimum:
                failed_thresholds.append(f"{code}:{minimum}")

        for code in sorted(self._forbidden_reason_codes):
            if counts.get(code, 0) > 0:
                forbidden_present.append(code)

        passed = not missing and not failed_thresholds and not forbidden_present
        expected_parts = sorted(self._required_reason_codes)
        if self._forbidden_reason_codes:
            expected_parts.extend(f"!{code}" for code in sorted(self._forbidden_reason_codes))
        if self._minimum_counts:
            expected_parts.extend(f"{code}>={minimum}" for code, minimum in sorted(self._minimum_counts.items()))
        expected = ", ".join(expected_parts) if expected_parts else "any reason-code summary"
        observed = ", ".join(f"{code}={count}" for code, count in sorted(counts.items())) or "none"
        reason = "reason codes satisfied" if passed else "required reason codes missing"
        if failed_thresholds:
            reason = "reason code counts below threshold"
        if forbidden_present:
            reason = "forbidden reason codes present"

        satisfied = (
            len(self._required_reason_codes) - len(missing)
            + len(self._minimum_counts) - len(failed_thresholds)
            + len(self._forbidden_reason_codes) - len(forbidden_present)
        )
        total = len(self._required_reason_codes) + len(self._minimum_counts) + len(self._forbidden_reason_codes)
        score = 1.0 if passed else (satisfied / total if total else 0.0)

        return GraderOutcome(
            grader=self.name,
            passed=passed,
            score=score,
            reason=reason,
            observed=observed,
            expected=expected,
            details={
                "reason_code_paths": list(self._reason_code_paths),
                "counts": counts,
                "missing": missing,
                "failed_thresholds": failed_thresholds,
                "forbidden_present": forbidden_present,
            },
        )
