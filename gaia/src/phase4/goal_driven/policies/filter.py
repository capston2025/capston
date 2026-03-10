from __future__ import annotations

from typing import Any, Dict, List

from ..evidence_bundle import CloserResult
from ..goal_kinds import GoalKind


class FilterPolicy:
    kind = GoalKind.FILTER

    def initial_phase(self, semantics: Any) -> str:
        return "apply_filter"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        return ["filter_semantic_validator"]

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        return []

    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: List[Any]) -> CloserResult:
        mandatory_failed = any(bool(getattr(v, "mandatory", False)) and str(getattr(v, "status", "")) == "fail" for v in validation_results)
        if not mandatory_failed and bool(evidence.derived.get("filter_validation_passed")):
            return CloserResult(
                status="success",
                reason_code="filter_semantic_confirmed",
                proof="필터 의미 검증 필수 항목이 모두 통과했습니다.",
                proof_source="filter_semantic",
            )
        return CloserResult(status="continue", reason_code="filter_semantic_pending")

    def is_blocked(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def is_fail_fast(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def budgets(self) -> Dict[str, Any]:
        return {"max_wait_steps": 2, "max_context_shifts": 1, "max_retries_per_action": 1, "max_total_steps": 10}

    def progress_contract(self) -> Dict[str, Any]:
        return {"strong_progress_signals": ["selected_filter_state_changed"], "weak_progress_signals": ["dom_count_change_only"]}
