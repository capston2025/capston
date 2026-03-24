from __future__ import annotations

from typing import Any, Dict, List

from ..evidence_bundle import CloserResult
from ..goal_kinds import GoalKind


class AddToListPolicy:
    kind = GoalKind.ADD_TO_LIST

    def initial_phase(self, semantics: Any) -> str:
        if bool(getattr(semantics, "requires_pre_action_membership_check", False)):
            return "precheck_destination_membership"
        return "locate_target"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        if event == "blocked_auth":
            return "handle_auth_or_block"
        if current_phase == "handle_auth_or_block" and event == "auth_resolved":
            return "locate_target"
        if current_phase == "precheck_destination_membership" and event == "precheck_present":
            return "remediate_existing_membership"
        if current_phase == "precheck_destination_membership" and event == "precheck_absent":
            return "locate_target"
        if current_phase == "locate_target" and event == "possible_present_noop":
            return "remediate_existing_membership"
        if current_phase == "locate_target" and event in {"discovery_progress", "discovery_no_state_change"}:
            return "locate_target"
        if current_phase == "locate_target" and event == "action_ok":
            return "verify_destination_membership"
        if (
            current_phase == "verify_destination_membership"
            and event == "evidence_reacquire"
            and bool(evidence.derived.get("remediation_needed"))
        ):
            return "remediate_existing_membership"
        if current_phase == "verify_destination_membership" and event == "evidence_reacquire":
            return "locate_target"
        if current_phase == "remediate_existing_membership" and event in {"action_ok", "wait_progress"}:
            return "verify_remediation_removal"
        if current_phase == "verify_remediation_removal" and not bool(evidence.current.get("target_in_destination")):
            return "locate_target"
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        mapping = {
            "precheck_destination_membership": [
                "destination_anchor_validator",
                "membership_state_validator",
            ],
            "locate_target": ["target_candidate_validator"],
            "handle_auth_or_block": ["auth_prompt_visible_validator"],
            "verify_destination_membership": [
                "destination_anchor_validator",
                "membership_state_validator",
                "aggregate_delta_validator",
            ],
            "remediate_existing_membership": [
                "destination_anchor_validator",
                "destination_surface_actionable_validator",
            ],
            "verify_remediation_removal": [
                "destination_anchor_validator",
                "target_present_before_validator",
                "target_absent_after_validator",
            ],
        }
        return list(mapping.get(phase, []))

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        return []

    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: List[Any]) -> CloserResult:
        if bool(evidence.derived.get("remediation_needed")) and phase == "verify_destination_membership":
            return CloserResult(status="continue", reason_code="remediation_pending")
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
        plan_requires_precheck = bool(getattr(ctx, "_goal_plan_requires_precheck", False))
        plan_precheck_result = str(getattr(ctx, "_goal_plan_precheck_result", "") or "").strip().lower()
        plan_remediation_completed = bool(getattr(ctx, "_goal_plan_remediation_completed", False))
        goal_state = getattr(ctx, "_goal_state_cache", None)
        proof_state = goal_state.get("proof", {}) if isinstance(goal_state, dict) else {}
        if plan_requires_precheck:
            if plan_precheck_result not in {"present", "absent"}:
                return CloserResult(status="continue", reason_code="precheck_pending")
            if plan_precheck_result == "present" and not bool(proof_state.get("remove_done") or plan_remediation_completed):
                return CloserResult(status="continue", reason_code="remediation_pending")
            add_branch_done = bool(proof_state.get("add_done"))
            readd_branch_done = bool(proof_state.get("readd_done"))
            final_present_verified = bool(proof_state.get("final_present_verified"))
            branch_proof_complete = (
                add_branch_done if plan_precheck_result == "absent" else bool(proof_state.get("remove_done") and readd_branch_done)
            )
            if (
                (not mandatory_failed)
                and branch_proof_complete
                and final_present_verified
                and destination_anchor_found
                and target_in_destination
                and after_present
            ):
                proof = (
                    "사전 membership 확인 결과가 없어서 바로 추가 경로를 탔고, 이후 목표 대상이 목적지 영역 안에서 확인되었습니다."
                    if plan_precheck_result == "absent"
                    else "사전 membership 확인 후 제거를 완료했고, 이후 목표 대상이 다시 목적지 영역 안에서 확인되었습니다."
                )
                return CloserResult(
                    status="success",
                    reason_code="membership_state_confirmed",
                    proof=proof,
                    proof_source="membership_state",
                    evidence={
                        "destination_anchor_found": destination_anchor_found,
                        "target_in_destination": target_in_destination,
                        "precheck_result": plan_precheck_result,
                        "remediation_completed": plan_remediation_completed,
                        "before_present": before_present,
                        "after_present": after_present,
                        "aggregate_metric_delta": aggregate_delta,
                    },
                )
            return CloserResult(status="continue", reason_code="membership_not_confirmed")
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
                "destination_surface_actionable",
            ],
            "weak_progress_signals": ["toast_only", "button_state_change_only", "scroll_change_only"],
        }
