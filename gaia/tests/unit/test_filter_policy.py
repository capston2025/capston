from types import SimpleNamespace

from gaia.src.phase4.goal_driven.evidence_bundle import EvidenceBundle
from gaia.src.phase4.goal_driven.policies.filter import FilterPolicy
from gaia.src.phase4.goal_driven.runtime import ActionExecResult


class _FilterCtx:
    def __init__(self, goal_text: str, *, state_change: dict | None = None, backend: str = "openclaw") -> None:
        self._active_goal_text = goal_text
        self._browser_backend_name = backend
        self._last_exec_result = ActionExecResult(
            success=True,
            effective=True,
            reason_code="ok",
            reason="ok",
            state_change=state_change or {},
        )

    @staticmethod
    def _normalize_text(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _env_str(_name: str, default: str = "") -> str:
        return default


def test_filter_policy_does_not_use_semantic_validator_for_filter_goals() -> None:
    policy = FilterPolicy()
    ctx = _FilterCtx("구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.")

    mandatory = policy.mandatory_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())
    optional = policy.optional_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())

    assert mandatory == []
    assert optional == []


def test_filter_policy_accepts_openclaw_state_change_for_non_semantic_goal() -> None:
    policy = FilterPolicy()
    ctx = _FilterCtx(
        "구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        state_change={"text_digest_changed": True, "list_count_changed": True},
    )
    evidence = EvidenceBundle()

    result = policy.run_closer("apply_filter", ctx, SimpleNamespace(), evidence, [])

    assert result.status == "success"
    assert result.reason_code == "filter_state_change_confirmed"


def test_filter_policy_uses_state_change_even_when_goal_mentions_semantics() -> None:
    policy = FilterPolicy()
    ctx = _FilterCtx(
        "필터가 실제 결과 목록과 일치하는지 검증해줘.",
        state_change={"target_value_changed": True},
    )

    mandatory = policy.mandatory_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())
    optional = policy.optional_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())
    result = policy.run_closer("apply_filter", ctx, SimpleNamespace(), EvidenceBundle(), [])

    assert mandatory == []
    assert optional == []
    assert result.status == "success"
    assert result.reason_code == "filter_state_change_confirmed"
