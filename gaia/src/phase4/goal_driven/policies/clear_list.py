from __future__ import annotations

from typing import Any, Dict, List

from ..evidence_bundle import CloserResult
from ..goal_kinds import GoalKind


class ClearListPolicy:
    kind = GoalKind.CLEAR_LIST

    def initial_phase(self, semantics: Any) -> str:
        return "reveal_destination_surface"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        if event == "blocked_auth":
            return "handle_auth_or_block"
        if current_phase == "handle_auth_or_block" and event in {"action_ok", "wait_progress"}:
            return "reveal_destination_surface"
        if current_phase == "reveal_destination_surface" and event in {"action_ok", "wait_progress"}:
            destination_surface_actionable = bool(getattr(evidence, "derived", {}).get("destination_surface_actionable"))
            target_cta_visible = bool(getattr(evidence, "derived", {}).get("target_action_cta_visible"))
            return "act_on_target" if destination_surface_actionable and target_cta_visible else current_phase
        if current_phase == "act_on_target" and event in {"action_ok", "action_no_state_change"}:
            return "verify_empty"
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        mapping = {
            "reveal_destination_surface": ["destination_anchor_validator", "destination_surface_actionable_validator"],
            "verify_empty": [
                "destination_anchor_validator",
                "empty_state_validator",
                "aggregate_zero_validator",
            ],
        }
        return list(mapping.get(phase, []))

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        return []

    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: List[Any]) -> CloserResult:
        if phase != "verify_empty":
            return CloserResult(status="continue", reason_code="empty_state_verification_pending")
        mandatory_failed = any(bool(getattr(v, "mandatory", False)) and str(getattr(v, "status", "")) == "fail" for v in validation_results)
        destination_anchor_found = bool(evidence.derived.get("destination_anchor_found"))
        empty_state_visible = bool(evidence.derived.get("empty_state_visible"))
        aggregate_metric = evidence.current.get("aggregate_metric")
        aggregate_zero = isinstance(aggregate_metric, (int, float)) and float(aggregate_metric) <= 0.0
        if (not mandatory_failed) and destination_anchor_found and empty_state_visible and aggregate_zero:
            return CloserResult(
                status="success",
                reason_code="empty_state_confirmed",
                proof="목적지 영역이 비어 있고 요약 수치도 0으로 확인되었습니다.",
                proof_source="empty_state",
                evidence={
                    "destination_anchor_found": destination_anchor_found,
                    "empty_state_visible": empty_state_visible,
                    "aggregate_metric": aggregate_metric,
                },
            )
        return CloserResult(status="continue", reason_code="empty_state_pending")

    def is_blocked(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def is_fail_fast(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def budgets(self) -> Dict[str, Any]:
        return {"max_wait_steps": 2, "max_context_shifts": 1, "max_retries_per_action": 1, "max_total_steps": 10}

    def progress_contract(self) -> Dict[str, Any]:
        return {
            "strong_progress_signals": ["destination_anchor_found", "destination_surface_actionable", "empty_state_visible", "aggregate_metric_delta"],
            "weak_progress_signals": ["toast_only", "scroll_change_only"],
        }
