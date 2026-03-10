from __future__ import annotations

from typing import Any, Dict, List

from ..evidence_bundle import CloserResult
from ..goal_kinds import GoalKind


class AddToListPolicy:
    kind = GoalKind.ADD_TO_LIST

    def initial_phase(self, semantics: Any) -> str:
        return "locate_target"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        if event == "blocked_auth":
            return "handle_auth_or_block"
        if current_phase == "handle_auth_or_block" and event in {"action_ok", "wait_progress"}:
            return "locate_target"
        if current_phase == "locate_target" and event in {"action_ok", "action_no_state_change"}:
            return "verify_destination_membership"
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        mapping = {
            "locate_target": ["target_candidate_validator"],
            "handle_auth_or_block": ["auth_prompt_visible_validator"],
            "verify_destination_membership": [
                "destination_anchor_validator",
                "membership_state_validator",
                "aggregate_delta_validator",
            ],
        }
        return list(mapping.get(phase, []))

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        return []

    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: List[Any]) -> CloserResult:
        if phase != "verify_destination_membership":
            return CloserResult(status="continue", reason_code="membership_verification_pending")
        mandatory_failed = any(bool(getattr(v, "mandatory", False)) and str(getattr(v, "status", "")) == "fail" for v in validation_results)
        destination_anchor_found = bool(evidence.derived.get("destination_anchor_found"))
        target_in_destination = bool(evidence.derived.get("target_in_destination"))
        before_present = bool(evidence.baseline.get("target_in_destination"))
        after_present = bool(evidence.current.get("target_in_destination"))
        aggregate_delta = evidence.delta.get("aggregate_metric_delta")
        aggregate_delta_reflected = isinstance(aggregate_delta, (int, float)) and aggregate_delta > 0
        already_satisfied = bool(after_present and semantics.already_satisfied_ok and not semantics.mutate_required)
        before_absent_and_after_present = bool((not before_present) and after_present)
        if (not mandatory_failed) and destination_anchor_found and target_in_destination and (
            before_absent_and_after_present or aggregate_delta_reflected or already_satisfied
        ):
            proof = "목표 대상이 목적지 영역 안에서 확인되었습니다."
            if aggregate_delta_reflected:
                proof = "목표 대상이 목적지 영역 안에서 확인되고 요약 수치도 증가했습니다."
            return CloserResult(
                status="success",
                reason_code="membership_state_confirmed",
                proof=proof,
                proof_source="membership_state",
                evidence={
                    "destination_anchor_found": destination_anchor_found,
                    "target_in_destination": target_in_destination,
                    "before_present": before_present,
                    "after_present": after_present,
                    "aggregate_metric_delta": aggregate_delta,
                },
            )
        return CloserResult(status="continue", reason_code="membership_not_confirmed")

    def is_blocked(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def is_fail_fast(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def budgets(self) -> Dict[str, Any]:
        return {
            "max_wait_steps": 2,
            "max_context_shifts": 1,
            "max_retries_per_action": 1,
            "max_total_steps": 12,
        }

    def progress_contract(self) -> Dict[str, Any]:
        return {
            "strong_progress_signals": [
                "destination_anchor_found",
                "target_in_destination",
                "aggregate_metric_delta",
            ],
            "weak_progress_signals": ["toast_only", "button_state_change_only", "scroll_change_only"],
        }
