from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from typing import Any

from ..report_schema import GraderOutcome
from .base import BaseGrader


DEFAULT_FINAL_STATUS_PATHS = (
    "final_status",
    "summary.final_status",
    "result.final_status",
    "report.final_status",
    "validation_summary.final_status",
)

DEFAULT_REASON_PATHS = (
    "reason",
    "summary.reason",
    "result.reason",
    "report.reason",
    "validation_summary.reason",
)

DEFAULT_REASON_CODE_SUMMARY_PATHS = (
    "reason_code_summary",
    "validation_reason_counts",
    "summary.reason_code_summary",
    "summary.validation_reason_counts",
    "report.reason_code_summary",
)

DEFAULT_ALLOWED_BLOCKED_STATUSES = ("blocked",)
DEFAULT_FORBIDDEN_FAIL_MARKERS = (
    "error",
    "failed",
    "failure",
    "exception",
    "invalid",
    "timeout",
)
DEFAULT_FORBIDDEN_FAIL_STATUSES = ("failed", "error", "failure")


@dataclass(frozen=True, slots=True)
class BlockedVsFailConfig:
    name: str = "blocked_vs_fail"
    payload_paths: tuple[str, ...] = ()
    final_status_paths: tuple[str, ...] = DEFAULT_FINAL_STATUS_PATHS
    reason_paths: tuple[str, ...] = DEFAULT_REASON_PATHS
    reason_code_summary_paths: tuple[str, ...] = DEFAULT_REASON_CODE_SUMMARY_PATHS
    allowed_blocked_statuses: tuple[str, ...] = DEFAULT_ALLOWED_BLOCKED_STATUSES
    allowed_blocked_markers: tuple[str, ...] = ()
    forbidden_fail_markers: tuple[str, ...] = DEFAULT_FORBIDDEN_FAIL_MARKERS
    forbidden_fail_statuses: tuple[str, ...] = DEFAULT_FORBIDDEN_FAIL_STATUSES


def _normalize_markers(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        marker = BaseGrader.normalize_text(value)
        if marker:
            normalized.append(marker)
    return tuple(dict.fromkeys(normalized))


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return BaseGrader.normalize_text(value)
    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, nested in value.items():
            key_text = BaseGrader.normalize_text(key)
            if key_text:
                parts.append(key_text)
            nested_text = _flatten_text(nested)
            if nested_text:
                parts.append(nested_text)
        return " ".join(parts).strip()
    if isinstance(value, (list, tuple, set)):
        parts = [_flatten_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    return BaseGrader.normalize_text(value)


def _contains_marker(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker and marker in text:
            return marker
    return None


class BlockedVsFailGrader(BaseGrader):
    """Deterministic grader that accepts blocked outcomes and rejects real failures."""

    def __init__(
        self,
        allowed_blocked_statuses: Iterable[str] = DEFAULT_ALLOWED_BLOCKED_STATUSES,
        forbidden_fail_markers: Iterable[str] = DEFAULT_FORBIDDEN_FAIL_MARKERS,
        allowed_blocked_markers: Iterable[str] = (),
        forbidden_fail_statuses: Iterable[str] = DEFAULT_FORBIDDEN_FAIL_STATUSES,
        payload_paths: Iterable[str] = (),
        final_status_paths: Iterable[str] = DEFAULT_FINAL_STATUS_PATHS,
        reason_paths: Iterable[str] = DEFAULT_REASON_PATHS,
        reason_code_summary_paths: Iterable[str] = DEFAULT_REASON_CODE_SUMMARY_PATHS,
    ) -> None:
        super().__init__("blocked_vs_fail", payload_paths)
        self.config = BlockedVsFailConfig(
            payload_paths=tuple(payload_paths),
            final_status_paths=tuple(final_status_paths),
            reason_paths=tuple(reason_paths),
            reason_code_summary_paths=tuple(reason_code_summary_paths),
            allowed_blocked_statuses=_normalize_markers(allowed_blocked_statuses),
            allowed_blocked_markers=_normalize_markers(allowed_blocked_markers),
            forbidden_fail_markers=_normalize_markers(forbidden_fail_markers),
            forbidden_fail_statuses=_normalize_markers(forbidden_fail_statuses),
        )

    def _extract_field(self, payload: Any, paths: tuple[str, ...]) -> Any:
        resolved = self._resolve_payload(payload)
        value = self.first_present(resolved, paths)
        if value is None:
            value = self.first_present(payload, paths)
        return value

    def grade(self, payload: Any) -> GraderOutcome:
        final_status_value = self._extract_field(payload, self.config.final_status_paths)
        reason_value = self._extract_field(payload, self.config.reason_paths)
        reason_code_summary_value = self._extract_field(payload, self.config.reason_code_summary_paths)

        final_status = self.normalize_text(final_status_value)
        reason_text = _flatten_text(reason_value)
        reason_code_summary_text = _flatten_text(reason_code_summary_value)
        combined_text = " ".join(
            part for part in (reason_text, reason_code_summary_text) if part
        ).strip()
        status_and_reason_text = " ".join(
            part for part in (final_status, combined_text) if part
        ).strip()

        blocked_status = final_status in self.config.allowed_blocked_statuses if final_status else False
        blocked_marker = _contains_marker(combined_text, self.config.allowed_blocked_markers)
        forbidden_status = final_status in self.config.forbidden_fail_statuses if final_status else False
        forbidden_marker = _contains_marker(status_and_reason_text, self.config.forbidden_fail_markers)

        passed = (blocked_status or blocked_marker is not None) and not (forbidden_status or forbidden_marker)

        if passed:
            reason = "blocked outcome accepted"
            score = 1.0
        else:
            reason = "true failure detected" if (forbidden_status or forbidden_marker) else "blocked outcome not recognized"
            score = 0.0

        expected_parts = [f"status in {sorted(self.config.allowed_blocked_statuses)}"]
        if self.config.allowed_blocked_markers:
            expected_parts.append(f"or markers in {sorted(self.config.allowed_blocked_markers)}")
        if self.config.forbidden_fail_markers:
            expected_parts.append(f"without fail markers {sorted(self.config.forbidden_fail_markers)}")
        expected = "; ".join(expected_parts)
        observed = final_status or combined_text or "unknown"

        return GraderOutcome(
            grader=self.name,
            passed=passed,
            score=score,
            reason=reason,
            observed=observed,
            expected=expected,
            details={
                "final_status_paths": list(self.config.final_status_paths),
                "reason_paths": list(self.config.reason_paths),
                "reason_code_summary_paths": list(self.config.reason_code_summary_paths),
                "allowed_blocked_statuses": list(self.config.allowed_blocked_statuses),
                "allowed_blocked_markers": list(self.config.allowed_blocked_markers),
                "forbidden_fail_markers": list(self.config.forbidden_fail_markers),
                "forbidden_fail_statuses": list(self.config.forbidden_fail_statuses),
                "raw_final_status": final_status_value,
                "raw_reason": reason_value,
                "raw_reason_code_summary": reason_code_summary_value,
                "matched_blocked_marker": blocked_marker,
                "matched_forbidden_marker": forbidden_marker,
                "matched_forbidden_status": forbidden_status,
            },
        )
