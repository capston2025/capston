from __future__ import annotations

from typing import Any, Dict, List

from ..evidence_bundle import CloserResult
from ..goal_kinds import GoalKind


class RemoveFromListPolicy:
    kind = GoalKind.REMOVE_FROM_LIST

    def initial_phase(self, semantics: Any) -> str:
        return "locate_target"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        if event == "blocked_auth":
            return "handle_auth_or_block"
        if current_phase == "handle_auth_or_block" and event in {"action_ok", "wait_progress"}:
            return "reveal_destination_surface"
        if current_phase == "locate_target" and event in {"action_ok", "action_no_state_change"}:
            return "reveal_destination_surface"
        if current_phase == "reveal_destination_surface" and event in {"action_ok", "wait_progress"}:
            destination_surface_actionable = bool(getattr(evidence, "derived", {}).get("destination_surface_actionable"))
            target_cta_visible = bool(getattr(evidence, "derived", {}).get("target_action_cta_visible"))
            return "act_on_target" if destination_surface_actionable and target_cta_visible else current_phase
        if current_phase == "act_on_target" and event in {"action_ok", "action_no_state_change"}:
            return "verify_removal"
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        mapping = {
            "locate_target": ["target_candidate_validator"],
            "reveal_destination_surface": ["destination_anchor_validator", "destination_surface_actionable_validator"],
            "verify_removal": [
                "destination_anchor_validator",
                "target_present_before_validator",
                "target_absent_after_validator",
            ],
        }
        return list(mapping.get(phase, []))

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        return []

    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: List[Any]) -> CloserResult:
        if phase != "verify_removal":
            return CloserResult(status="continue", reason_code="membership_remove_verification_pending")
        mandatory_failed = any(bool(getattr(v, "mandatory", False)) and str(getattr(v, "status", "")) == "fail" for v in validation_results)
        destination_anchor_found = bool(evidence.derived.get("destination_anchor_found"))
        before_present = bool(evidence.baseline.get("target_in_destination"))
        after_present = bool(evidence.current.get("target_in_destination"))
        aggregate_delta = evidence.delta.get("aggregate_metric_delta")
        aggregate_delta_reflected = isinstance(aggregate_delta, (int, float)) and aggregate_delta < 0
        already_absent = bool((not after_present) and semantics.already_satisfied_ok and not semantics.mutate_required)
        if (not mandatory_failed) and destination_anchor_found and ((before_present and not after_present) or already_absent):
            proof = "목표 대상이 목적지 영역에서 제거된 것이 확인되었습니다."
            if aggregate_delta_reflected:
                proof = "목표 대상 제거가 확인되고 요약 수치도 감소했습니다."
            return CloserResult(
                status="success",
                reason_code="membership_removed",
                proof=proof,
                proof_source="membership_state",
                evidence={
                    "destination_anchor_found": destination_anchor_found,
                    "before_present": before_present,
                    "after_present": after_present,
                    "aggregate_metric_delta": aggregate_delta,
                },
            )
        return CloserResult(status="continue", reason_code="membership_remove_pending")

    def is_blocked(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def is_fail_fast(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def budgets(self) -> Dict[str, Any]:
        return {"max_wait_steps": 2, "max_context_shifts": 1, "max_retries_per_action": 1, "max_total_steps": 12}

    def progress_contract(self) -> Dict[str, Any]:
        return {
            "strong_progress_signals": ["destination_anchor_found", "destination_surface_actionable", "target_seen_during_run", "aggregate_metric_delta"],
            "weak_progress_signals": ["toast_only", "button_state_change_only", "scroll_change_only"],
        }
