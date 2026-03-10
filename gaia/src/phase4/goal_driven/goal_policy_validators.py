from __future__ import annotations

from typing import Any, Callable, Dict, List

from .evidence_bundle import EvidenceBundle, ValidatorResult


def _pass(name: str, mandatory: bool, reason_code: str = "ok", evidence: Dict[str, Any] | None = None) -> ValidatorResult:
    return ValidatorResult(
        status="pass",
        validator=name,
        mandatory=mandatory,
        reason_code=reason_code,
        evidence=dict(evidence or {}),
    )


def _fail(name: str, mandatory: bool, reason_code: str, evidence: Dict[str, Any] | None = None) -> ValidatorResult:
    return ValidatorResult(
        status="fail",
        validator=name,
        mandatory=mandatory,
        reason_code=reason_code,
        evidence=dict(evidence or {}),
    )


def _skip(name: str, mandatory: bool, reason_code: str = "not_applicable") -> ValidatorResult:
    return ValidatorResult(
        status="skipped_not_applicable",
        validator=name,
        mandatory=mandatory,
        reason_code=reason_code,
        evidence={},
    )


def _validate_target_candidate(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    hits = evidence.derived.get("target_hits") if isinstance(evidence.derived.get("target_hits"), list) else []
    if hits:
        return _pass("target_candidate_validator", mandatory, evidence={"target_hits": hits[:4]})
    return _fail("target_candidate_validator", mandatory, "target_candidate_missing")


def _validate_destination_anchor(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = bool(evidence.derived.get("destination_anchor_found"))
    if ok:
        return _pass("destination_anchor_validator", mandatory)
    return _fail("destination_anchor_validator", mandatory, "destination_anchor_missing")


def _validate_membership_state(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = bool(evidence.current.get("target_in_destination"))
    if ok:
        return _pass("membership_state_validator", mandatory)
    return _fail("membership_state_validator", mandatory, "membership_state_missing")


def _validate_aggregate_delta(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    delta = evidence.delta.get("aggregate_metric_delta")
    if not isinstance(delta, (int, float)):
        return _skip("aggregate_delta_validator", mandatory, "aggregate_metric_missing")
    mutate_direction = str(getattr(semantics, "mutation_direction", "") or "")
    if mutate_direction == "increase":
        return _pass("aggregate_delta_validator", mandatory, evidence={"aggregate_metric_delta": delta}) if delta > 0 else _fail(
            "aggregate_delta_validator", mandatory, "aggregate_delta_not_increased", {"aggregate_metric_delta": delta}
        )
    if mutate_direction == "decrease":
        return _pass("aggregate_delta_validator", mandatory, evidence={"aggregate_metric_delta": delta}) if delta < 0 else _fail(
            "aggregate_delta_validator", mandatory, "aggregate_delta_not_decreased", {"aggregate_metric_delta": delta}
        )
    return _pass("aggregate_delta_validator", mandatory, evidence={"aggregate_metric_delta": delta})


def _validate_target_present_before(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = bool(evidence.baseline.get("target_in_destination"))
    if ok:
        return _pass("target_present_before_validator", mandatory)
    return _fail("target_present_before_validator", mandatory, "target_not_present_before")


def _validate_target_absent_after(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = not bool(evidence.current.get("target_in_destination"))
    if ok:
        return _pass("target_absent_after_validator", mandatory)
    return _fail("target_absent_after_validator", mandatory, "target_still_present_after")


def _validate_empty_state(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = bool(evidence.derived.get("empty_state_visible"))
    if ok:
        return _pass("empty_state_validator", mandatory)
    return _fail("empty_state_validator", mandatory, "empty_state_missing")


def _validate_aggregate_zero(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    metric = evidence.current.get("aggregate_metric")
    if not isinstance(metric, (int, float)):
        return _skip("aggregate_zero_validator", mandatory, "aggregate_metric_missing")
    if float(metric) <= 0.0:
        return _pass("aggregate_zero_validator", mandatory, evidence={"aggregate_metric": metric})
    return _fail("aggregate_zero_validator", mandatory, "aggregate_not_zero", {"aggregate_metric": metric})


def _validate_filter_semantic(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = bool(evidence.derived.get("filter_validation_passed"))
    if ok:
        return _pass("filter_semantic_validator", mandatory)
    return _fail("filter_semantic_validator", mandatory, "filter_semantic_failed")


def _validate_auth_prompt_visible(ctx: Any, semantics: Any, evidence: EvidenceBundle, mandatory: bool) -> ValidatorResult:
    ok = bool(evidence.raw.get("auth_prompt_visible"))
    if ok:
        return _pass("auth_prompt_visible_validator", mandatory)
    return _fail("auth_prompt_visible_validator", mandatory, "auth_prompt_not_visible")


VALIDATOR_REGISTRY: Dict[str, Callable[[Any, Any, EvidenceBundle, bool], ValidatorResult]] = {
    "target_candidate_validator": _validate_target_candidate,
    "destination_anchor_validator": _validate_destination_anchor,
    "membership_state_validator": _validate_membership_state,
    "aggregate_delta_validator": _validate_aggregate_delta,
    "target_present_before_validator": _validate_target_present_before,
    "target_absent_after_validator": _validate_target_absent_after,
    "empty_state_validator": _validate_empty_state,
    "aggregate_zero_validator": _validate_aggregate_zero,
    "filter_semantic_validator": _validate_filter_semantic,
    "auth_prompt_visible_validator": _validate_auth_prompt_visible,
}


def run_policy_validators(policy: Any, phase: str, ctx: Any, semantics: Any, evidence: EvidenceBundle) -> List[ValidatorResult]:
    results: List[ValidatorResult] = []
    mandatory = list(policy.mandatory_validators(phase, ctx, semantics, evidence) or [])
    optional = list(policy.optional_validators(phase, ctx, semantics, evidence) or [])
    for name, is_mandatory in [(n, True) for n in mandatory] + [(n, False) for n in optional]:
        runner = VALIDATOR_REGISTRY.get(str(name))
        if runner is None:
            results.append(_skip(str(name), is_mandatory, "validator_missing"))
            continue
        results.append(runner(ctx, semantics, evidence, is_mandatory))
    return results


def has_mandatory_failures(results: List[ValidatorResult]) -> bool:
    return any(bool(r.mandatory) and str(r.status) == "fail" for r in results)
