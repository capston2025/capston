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


def test_filter_policy_uses_optional_validator_for_non_semantic_change_goal() -> None:
    policy = FilterPolicy()
    ctx = _FilterCtx("구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.")

    mandatory = policy.mandatory_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())
    optional = policy.optional_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())

    assert mandatory == []
    assert optional == ["filter_semantic_validator"]


def test_filter_policy_accepts_openclaw_state_change_for_non_semantic_goal() -> None:
    policy = FilterPolicy()
    ctx = _FilterCtx(
        "구분 또는 전공/교양 관련 필터를 바꿨을 때 결과 목록이 실제로 바뀌는지 검증해줘.",
        state_change={"text_digest_changed": True, "list_count_changed": True},
    )
    evidence = EvidenceBundle(derived={"filter_validation_passed": False})

    result = policy.run_closer("apply_filter", ctx, SimpleNamespace(), evidence, [])

    assert result.status == "success"
    assert result.reason_code == "filter_state_change_confirmed"


def test_filter_policy_keeps_semantic_validator_mandatory_for_generic_semantic_goal() -> None:
    policy = FilterPolicy()
    ctx = _FilterCtx("필터가 실제 결과 목록과 맞게 동작하는지 의미 검증해줘.")

    mandatory = policy.mandatory_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())
    optional = policy.optional_validators("apply_filter", ctx, SimpleNamespace(), EvidenceBundle())

    assert mandatory == ["filter_semantic_validator"]
    assert optional == []
