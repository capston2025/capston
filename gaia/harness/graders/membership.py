from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..report_schema import GraderOutcome
from .base import BaseGrader


DEFAULT_MEMBERSHIP_PATHS = (
    "membership_present",
    "membership_verified",
    "membership_state",
    "membership_summary.present",
    "membership_summary.verified",
    "membership_summary.is_member",
    "membership_summary.state",
    "membership_summary.status",
    "summary.membership_present",
    "summary.membership_verified",
    "summary.membership_state",
)


class MembershipGrader(BaseGrader):
    """Deterministic grader that checks whether membership is present."""

    def __init__(
        self,
        expected_present: bool = True,
        destination_terms: Iterable[str] = (),
        target_terms: Iterable[str] = (),
        payload_paths: Iterable[str] = (),
        membership_paths: Iterable[str] = DEFAULT_MEMBERSHIP_PATHS,
    ) -> None:
        super().__init__("membership", payload_paths)
        self._expected_present = bool(expected_present)
        self._destination_terms = [str(term).strip() for term in destination_terms if str(term).strip()]
        self._target_terms = [str(term).strip() for term in target_terms if str(term).strip()]
        self._membership_paths = tuple(membership_paths)

    def _infer_membership(self, payload: Any) -> tuple[bool | None, str, dict[str, Any]]:
        resolved = self._resolve_payload(payload)
        raw = self.first_present(resolved, self._membership_paths)
        if raw is None:
            raw = self.first_present(payload, self._membership_paths)

        details: dict[str, Any] = {"raw_membership_value": raw}
        if isinstance(raw, bool):
            return raw, "boolean", details

        if isinstance(raw, dict):
            for key in ("present", "verified", "is_member", "exists"):
                if key in raw and isinstance(raw[key], bool):
                    details["derived_from"] = key
                    return bool(raw[key]), key, details
            for key in ("state", "status"):
                if key in raw:
                    normalized = self.normalize_text(raw[key])
                    if normalized:
                        details["derived_from"] = key
                        return normalized in {"passed", "present", "verified", "confirmed", "member", "true"}, normalized, details

        normalized = self.normalize_text(raw)
        details["normalized"] = normalized
        if normalized in {"passed", "present", "verified", "confirmed", "member", "yes", "true"}:
            return True, normalized, details
        if normalized in {"failed", "absent", "missing", "not_member", "false", "no", "unverified"}:
            return False, normalized, details
        return None, normalized or "unknown", details

    def grade(self, payload: Any) -> GraderOutcome:
        observed_present, observed_label, details = self._infer_membership(payload)
        if observed_present is None:
            text_fields = []
            if isinstance(payload, dict):
                text_fields.extend(
                    [
                        str(payload.get("reason") or ""),
                        str(payload.get("captured_log") or ""),
                    ]
                )
                summary = payload.get("summary")
                if isinstance(summary, dict):
                    text_fields.append(str(summary.get("reason") or ""))
            haystack = "\n".join(text_fields)
            destination_hit = any(term in haystack for term in self._destination_terms) if self._destination_terms else False
            target_hit = any(term in haystack for term in self._target_terms) if self._target_terms else not self._target_terms
            if destination_hit and target_hit:
                observed_present = True
                observed_label = "textual_evidence"
                details["derived_from_text"] = True
                details["destination_terms"] = list(self._destination_terms)
                details["target_terms"] = list(self._target_terms)
        if observed_present is None:
            passed = False
            observed = "unknown"
            reason = "membership state could not be determined"
        else:
            passed = observed_present is self._expected_present
            observed = "present" if observed_present else "absent"
            reason = "membership matched expected state" if passed else "membership did not match expected state"

        expected = "present" if self._expected_present else "absent"
        score = 1.0 if passed else 0.0
        details["membership_paths"] = list(self._membership_paths)
        details["observed_label"] = observed_label
        details["destination_terms"] = list(self._destination_terms)
        details["target_terms"] = list(self._target_terms)

        return GraderOutcome(
            grader=self.name,
            passed=passed,
            score=score,
            reason=reason,
            observed=observed,
            expected=expected,
            details=details,
        )
