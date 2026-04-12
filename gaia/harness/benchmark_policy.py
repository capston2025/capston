from __future__ import annotations

from collections.abc import Mapping
from typing import Any


REJECTED_SUCCESS_COMPLETION_SOURCES: set[str] = set()


def apply_benchmark_success_policy(
    *,
    status: str,
    reason: str,
    summary: Mapping[str, Any] | None,
) -> tuple[str, str, dict[str, Any]]:
    normalized_status = str(status or "").strip().upper()
    base_reason = str(reason or "").strip()
    if normalized_status != "SUCCESS" or not isinstance(summary, Mapping):
        return normalized_status or "FAIL", base_reason, {}

    completion_source = str(summary.get("goal_completion_source") or "").strip().lower()
    if completion_source not in REJECTED_SUCCESS_COMPLETION_SOURCES:
        return normalized_status, base_reason, {}

    policy_reason = f"benchmark_policy_rejected_completion_source({completion_source})"
    merged_reason = f"{policy_reason}: {base_reason}" if base_reason else policy_reason
    return (
        "FAIL",
        merged_reason,
        {
            "rejected_completion_source": completion_source,
            "raw_status": normalized_status,
            "policy_reason": policy_reason,
        },
    )
