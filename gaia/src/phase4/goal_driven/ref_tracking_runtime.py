from __future__ import annotations

from typing import Optional


def is_ref_temporarily_blocked(agent, ref_id: Optional[str]) -> bool:
    if not ref_id:
        return False
    limit = agent._loop_policy_value("ref_soft_fail_limit", 2)
    return int(agent._ineffective_ref_counts.get(ref_id, 0)) >= max(1, limit)


def track_ref_outcome(
    agent,
    *,
    ref_id: Optional[str],
    reason_code: str,
    success: bool,
    changed: bool,
) -> None:
    if not ref_id:
        return
    if success and changed:
        agent._ineffective_ref_counts.pop(ref_id, None)
        return
    if reason_code in {"no_state_change", "not_actionable", "modal_not_open", "ambiguous_ref_target", "ambiguous_selector"}:
        agent._ineffective_ref_counts[ref_id] = int(agent._ineffective_ref_counts.get(ref_id, 0)) + 1
