from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..evidence_bundle import CloserResult
from ..goal_kinds import GoalKind


def filter_goal_requires_semantic_validation(ctx: Any) -> bool:
    normalize = getattr(ctx, "_normalize_text", None)
    if not callable(normalize):
        normalize = lambda value: str(value or "").strip().lower()
    goal_blob = normalize(str(getattr(ctx, "_active_goal_text", "") or ""))
    semantic_tokens = (
        "semantic",
        "의미",
        "맞게",
        "일치",
        "consisten",
    )
    return any(token in goal_blob for token in semantic_tokens)


def openclaw_state_change_confirmed(
    ctx: Any,
    *,
    state_change: Optional[Dict[str, Any]] = None,
) -> bool:
    backend = str(
        getattr(ctx, "_browser_backend_name", "")
        or getattr(ctx, "_env_str", lambda *_args, **_kwargs: "")("GAIA_BROWSER_BACKEND", "")
        or ""
    ).strip().lower()
    if not isinstance(state_change, dict):
        state_change = getattr(getattr(ctx, "_last_exec_result", None), "state_change", None)
    if backend != "openclaw" or not isinstance(state_change, dict):
        return False
    strong_keys = (
        "text_digest_changed",
        "status_text_changed",
        "interactive_count_changed",
        "list_count_changed",
        "target_value_changed",
        "target_value_matches",
    )
    return any(bool(state_change.get(key)) for key in strong_keys)


class FilterPolicy:
    kind = GoalKind.FILTER

    def initial_phase(self, semantics: Any) -> str:
        return "apply_filter"

    def next_phase(self, current_phase: str, event: str, evidence: Any, budgets: Dict[str, Any]) -> str:
        return current_phase

    def mandatory_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        if not filter_goal_requires_semantic_validation(ctx):
            return []
        return ["filter_semantic_validator"]

    def optional_validators(self, phase: str, ctx: Any, semantics: Any, evidence: Any) -> List[str]:
        if not filter_goal_requires_semantic_validation(ctx):
            return ["filter_semantic_validator"]
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
        if not filter_goal_requires_semantic_validation(ctx) and openclaw_state_change_confirmed(ctx):
            return CloserResult(
                status="success",
                reason_code="filter_state_change_confirmed",
                proof="OpenClaw post-action state change와 현재 화면 증거상 필터 변경이 실제 결과 목록에 반영된 것으로 확인했습니다.",
                proof_source="openclaw_state_change",
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
