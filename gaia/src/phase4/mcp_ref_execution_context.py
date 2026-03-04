from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Tuple


def build_initial_state_change() -> Dict[str, Any]:
    return {
        "url_changed": False,
        "dom_changed": False,
        "target_visibility_changed": False,
        "target_value_changed": False,
        "target_value_matches": False,
        "target_focus_changed": False,
        "focus_changed": False,
        "counter_changed": False,
        "number_tokens_changed": False,
        "status_text_changed": False,
        "list_count_changed": False,
        "interactive_count_changed": False,
        "modal_count_changed": False,
        "backdrop_count_changed": False,
        "dialog_count_changed": False,
        "modal_state_changed": False,
        "auth_state_changed": False,
        "text_digest_changed": False,
        "evidence_changed": False,
        "probe_wait_ms": 0,
        "probe_scroll": "none",
        "live_texts_after": [],
    }


def _normalize_bbox(meta: Dict[str, Any]) -> Optional[Dict[str, float]]:
    raw = meta.get("bounding_box")
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw.get("x", 0.0) or 0.0)
        y = float(raw.get("y", 0.0) or 0.0)
        width = float(raw.get("width", 0.0) or 0.0)
        height = float(raw.get("height", 0.0) or 0.0)
    except Exception:
        return None
    if width <= 0.0 or height <= 0.0:
        return None
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "right": x + width,
        "bottom": y + height,
        "center_x": x + (width / 2.0),
        "center_y": y + (height / 2.0),
    }


def _is_unlabeled_icon_ref(meta: Dict[str, Any]) -> bool:
    attrs = meta.get("attributes") if isinstance(meta.get("attributes"), dict) else {}
    text = str(meta.get("text") or "").strip().lower()
    aria = str(attrs.get("aria-label") or attrs.get("aria_label") or "").strip().lower()
    title = str(attrs.get("title") or meta.get("title") or "").strip().lower()
    placeholder = str(meta.get("placeholder") or "").strip().lower()
    if text in {"x", "×", "✕", "close", "닫기"}:
        return True
    if aria or title or placeholder:
        return False
    return text == ""


def _is_relaxed_modal_corner_candidate(
    meta: Dict[str, Any],
    modal_regions: list[Dict[str, float]],
) -> bool:
    if not isinstance(meta, dict) or not modal_regions:
        return False
    bbox = _normalize_bbox(meta)
    if not bbox:
        return False
    if bbox["width"] > 120 or bbox["height"] > 120:
        return False
    if (bbox["width"] * bbox["height"]) > 6400:
        return False
    if not _is_unlabeled_icon_ref(meta):
        return False
    for region in modal_regions:
        try:
            rx = float(region.get("x", 0.0) or 0.0)
            ry = float(region.get("y", 0.0) or 0.0)
            rw = float(region.get("width", 0.0) or 0.0)
            rh = float(region.get("height", 0.0) or 0.0)
        except Exception:
            continue
        if rw <= 0.0 or rh <= 0.0:
            continue
        rr = rx + rw
        rb = ry + rh
        cx = bbox["center_x"]
        cy = bbox["center_y"]
        if not (rx <= cx <= rr and ry <= cy <= rb):
            continue
        rel_x = (cx - rx) / max(rw, 1.0)
        rel_y = (cy - ry) / max(rh, 1.0)
        if rel_x >= 0.60 and rel_y <= 0.40:
            return True
    return False


async def prepare_ref_action_execution_context(
    *,
    action: str,
    value: Any = None,
    verify: bool,
    requested_meta: Dict[str, Any],
    requested_snapshot: Optional[Dict[str, Any]],
    page: Any,
    snapshot_id: str,
    ref_id: str,
    retry_path: list[str],
    attempt_logs: list[dict[str, Any]],
    stale_recovered: bool,
    max_action_seconds: float,
    collect_page_evidence_fn: Callable[[Any], Awaitable[Dict[str, Any]]],
    collect_modal_regions_from_snapshot_fn: Callable[[Optional[Dict[str, Any]]], list[Dict[str, float]]],
    is_close_intent_ref_fn: Callable[[Dict[str, Any]], bool],
    is_modal_corner_close_candidate_fn: Callable[[Dict[str, Any], list[Dict[str, float]]], bool],
) -> Dict[str, Any]:
    state_change = build_initial_state_change()
    ref_attrs = (
        requested_meta.get("attributes")
        if isinstance(requested_meta.get("attributes"), dict)
        else {}
    )
    ref_selector_text = " ".join(
        [
            str(requested_meta.get("selector") or ""),
            str(requested_meta.get("full_selector") or ""),
            str(requested_meta.get("text") or ""),
            str((ref_attrs or {}).get("type") or ""),
            str((ref_attrs or {}).get("role") or ""),
            str((ref_attrs or {}).get("aria-label") or ""),
        ]
    ).lower()
    submit_like_click = bool(
        action == "click"
        and (
            str((ref_attrs or {}).get("type") or "").lower() == "submit"
            or "submit" in ref_selector_text
            or "로그인" in ref_selector_text
            or "회원가입" in ref_selector_text
            or "sign in" in ref_selector_text
            or "log in" in ref_selector_text
            or "sign up" in ref_selector_text
            or "register" in ref_selector_text
        )
    )
    modal_regions_for_requested = collect_modal_regions_from_snapshot_fn(
        requested_snapshot if isinstance(requested_snapshot, dict) else None
    )
    value_text = str(value or "").strip().lower()
    close_like_click = bool(
        action == "click"
        and (
            value_text in {"__close_intent__", "close_intent", "intent:close"}
            or
            is_close_intent_ref_fn(requested_meta)
            or is_modal_corner_close_candidate_fn(
                requested_meta,
                modal_regions_for_requested,
            )
        )
    )
    if (
        action == "click"
        and (not close_like_click)
        and _is_relaxed_modal_corner_candidate(requested_meta, modal_regions_for_requested)
    ):
        close_like_click = True
    probe_wait_schedule: Tuple[int, ...] = (
        (350, 800, 1500, 3000, 5000) if submit_like_click else (350, 700, 1500)
    )
    verify_for_action = bool(verify)
    adjusted_max_action_seconds = max_action_seconds
    precheck_response: Optional[Dict[str, Any]] = None
    # soft_close 판단: "X" 텍스트만으로 감지된 경우 modal precheck 건너뛰기
    # _is_close_intent_ref 에서 설정한 마커 또는 visible text가 단일 닫기 문자인 경우
    visible_text_raw = str(requested_meta.get("text") or "").strip()
    is_soft_close = bool(
        requested_meta.get("_soft_close")
        or visible_text_raw in {"x", "X", "✕", "✖", "×"}
    )
    if close_like_click and not is_soft_close:
        close_gate_evidence: Dict[str, Any]
        try:
            close_gate_evidence = await collect_page_evidence_fn(page)
        except Exception:
            close_gate_evidence = {}
        if not bool(close_gate_evidence.get("modal_open")):
            reason_code = "modal_not_open"
            attempt_logs.append(
                {
                    "attempt": 0,
                    "mode": "precheck",
                    "reason_code": reason_code,
                    "error": "close intent requested but modal_open=false",
                    "state_change": {
                        "modal_open": bool(close_gate_evidence.get("modal_open")),
                        "modal_count": int(close_gate_evidence.get("modal_count") or 0),
                        "backdrop_count": int(
                            close_gate_evidence.get("backdrop_count") or 0
                        ),
                        "dialog_count": int(close_gate_evidence.get("dialog_count") or 0),
                    },
                }
            )
            precheck_response = {
                "success": False,
                "effective": False,
                "reason_code": reason_code,
                "reason": "닫기 대상 모달이 열려있지 않습니다. 최신 snapshot으로 재계획하세요.",
                "snapshot_id_used": snapshot_id,
                "ref_id_used": ref_id,
                "retry_path": retry_path,
                "attempt_count": 0,
                "state_change": {
                    "modal_open": bool(close_gate_evidence.get("modal_open")),
                    "modal_count": int(close_gate_evidence.get("modal_count") or 0),
                    "backdrop_count": int(close_gate_evidence.get("backdrop_count") or 0),
                    "dialog_count": int(close_gate_evidence.get("dialog_count") or 0),
                },
                "attempt_logs": attempt_logs,
                "current_url": page.url,
                "stale_recovered": stale_recovered,
            }
    return {
        "state_change": state_change,
        "submit_like_click": submit_like_click,
        "close_like_click": close_like_click,
        "modal_regions_for_requested": modal_regions_for_requested,
        "probe_wait_schedule": probe_wait_schedule,
        "verify_for_action": verify_for_action,
        "max_action_seconds": adjusted_max_action_seconds,
        "precheck_response": precheck_response,
    }
