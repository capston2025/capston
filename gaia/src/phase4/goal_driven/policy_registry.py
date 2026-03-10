from __future__ import annotations

from typing import Any, Dict, Optional, Protocol

from .evidence_bundle import InterruptResult
from .goal_kinds import GoalKind
from .interrupts import AuthInterruptPolicy, CloseOverlayInterruptPolicy
from .policies import AddToListPolicy, ClearListPolicy, FilterPolicy, RemoveFromListPolicy


class GoalPolicy(Protocol):
    kind: GoalKind

    def initial_phase(self, semantics: Any) -> str: ...
    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str: ...
    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> list[str]: ...
    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> list[str]: ...
    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: list[Any]) -> Any: ...
    def is_blocked(self, ctx: Any, semantics: Any, evidence: Any) -> bool: ...
    def is_fail_fast(self, ctx: Any, semantics: Any, evidence: Any) -> bool: ...
    def budgets(self) -> Dict[str, Any]: ...
    def progress_contract(self) -> Dict[str, Any]: ...


class GenericFallbackPolicy:
    kind = GoalKind.GENERIC_FALLBACK

    def initial_phase(self, semantics: Any) -> str:
        return "explore"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> list[str]:
        return []

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> list[str]:
        return []

    def run_closer(self, phase: str, ctx: Any, semantics: Any, evidence: Any, validation_results: list[Any]) -> Any:
        from .evidence_bundle import CloserResult

        return CloserResult(status="continue", reason_code="generic_fallback_pending")

    def is_blocked(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def is_fail_fast(self, ctx: Any, semantics: Any, evidence: Any) -> bool:
        return False

    def budgets(self) -> Dict[str, Any]:
        return {"max_wait_steps": 1, "max_context_shifts": 1, "max_retries_per_action": 1, "max_total_steps": 8}

    def progress_contract(self) -> Dict[str, Any]:
        return {"strong_progress_signals": [], "weak_progress_signals": []}


GOAL_POLICY_REGISTRY: Dict[GoalKind, GoalPolicy] = {
    GoalKind.ADD_TO_LIST: AddToListPolicy(),
    GoalKind.REMOVE_FROM_LIST: RemoveFromListPolicy(),
    GoalKind.CLEAR_LIST: ClearListPolicy(),
    GoalKind.FILTER: FilterPolicy(),
    GoalKind.GENERIC_FALLBACK: GenericFallbackPolicy(),
}

INTERRUPT_POLICIES = [AuthInterruptPolicy(), CloseOverlayInterruptPolicy()]


def get_goal_policy(goal_kind: GoalKind) -> GoalPolicy:
    return GOAL_POLICY_REGISTRY.get(goal_kind, GOAL_POLICY_REGISTRY[GoalKind.GENERIC_FALLBACK])


def run_interrupt_policies(semantics: Any, evidence: Any, ctx: Any = None) -> Optional[InterruptResult]:
    for policy in INTERRUPT_POLICIES:
        if policy.match(semantics, evidence):
            return policy.run(ctx, semantics, evidence)
    return None
