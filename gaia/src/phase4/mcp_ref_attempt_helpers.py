from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List


def append_attempt_timeout_log(
    *,
    attempt_logs: List[Dict[str, Any]],
    attempt_idx: int,
    mode: str,
    candidate_selector: str,
    max_action_seconds: float,
) -> None:
    attempt_logs.append(
        {
            "attempt": attempt_idx,
            "mode": mode,
            "selector": candidate_selector,
            "reason_code": "action_timeout",
            "error": f"action budget exceeded ({max_action_seconds:.1f}s)",
        }
    )


async def resolve_locator_for_attempt(
    *,
    page: Any,
    requested_meta: Dict[str, Any],
    candidate_selector: str,
    attempt_idx: int,
    mode: str,
    attempt_logs: List[Dict[str, Any]],
    resolve_locator_from_ref_fn: Callable[..., Awaitable[Any]],
) -> Dict[str, Any]:
    locator, frame_index, resolved_selector, locator_error = await resolve_locator_from_ref_fn(
        page, requested_meta, candidate_selector
    )
    if locator is None:
        locator_error_text = str(locator_error or "")
        if locator_error_text.startswith("ambiguous_selector_matches"):
            reason_code = "ambiguous_ref_target"
        elif locator_error_text in {"dom_ref_missing"}:
            reason_code = "stale_snapshot"
        elif mode == "role_ref" and locator_error_text in {"role_ref_not_found", "invalid_role_ref_hint"}:
            reason_code = "role_ref_recovery_failed"
        else:
            reason_code = "not_found"
        attempt_logs.append(
            {
                "attempt": attempt_idx,
                "mode": mode,
                "selector": resolved_selector,
                "reason_code": reason_code,
                "error": locator_error,
            }
        )
        print(f"[execute_ref_action] step={attempt_idx} mode={mode} reason={reason_code}")
        return {
            "ok": False,
            "reason_code": reason_code,
            "locator": None,
            "frame_index": None,
            "resolved_selector": resolved_selector,
        }
    return {
        "ok": True,
        "reason_code": "role_ref_recovered" if mode == "role_ref" else "ok",
        "locator": locator,
        "frame_index": frame_index,
        "resolved_selector": resolved_selector,
    }
