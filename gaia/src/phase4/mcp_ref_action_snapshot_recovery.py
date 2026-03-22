from __future__ import annotations

from typing import Any, Dict, Optional
from gaia.src.phase4.mcp_ref_action_snapshot_recovery import (
    recover_snapshot_ref_state,
)


async def recover_snapshot_ref_state(
    *,
    session: Any,
    page: Any,
    session_id: str,
    snapshot_id: str,
    ref_id: str,
    retry_path: list[str],
    to_ai_friendly_error_fn,
    snapshot_page_fn,
    resolve_ref_meta_from_snapshot_fn,
    resolve_stale_ref_fn,
    get_tab_index_fn,
) -> Dict[str, Any]:
    requested_snapshot = session.snapshots.get(snapshot_id)
    requested_meta = (
        resolve_ref_meta_from_snapshot_fn(requested_snapshot, ref_id)
        if requested_snapshot
        else None
    )
    initial_ref_state: Optional[str] = None

    if isinstance(requested_snapshot, dict):
        snap_epoch = int(requested_snapshot.get("epoch") or 0)
        snap_dom_hash = str(requested_snapshot.get("dom_hash") or "")
        snap_tab_index = int(requested_snapshot.get("tab_index") or 0)
        parsed_epoch = 0
        parsed_hash_short = ""
        try:
            parts = str(snapshot_id).split(":")
            if len(parts) >= 3:
                parsed_epoch = int(parts[-2] or 0)
                parsed_hash_short = str(parts[-1] or "")
        except Exception:
            parsed_epoch = 0
            parsed_hash_short = ""

        if parsed_epoch and parsed_epoch != snap_epoch:
            initial_ref_state = "stale_snapshot"
        if parsed_hash_short and snap_dom_hash and not snap_dom_hash.startswith(parsed_hash_short):
            initial_ref_state = "stale_snapshot"
        if snap_tab_index != get_tab_index_fn(page):
            initial_ref_state = "stale_snapshot"

    if not requested_snapshot:
        initial_ref_state = "snapshot_not_found"
    elif requested_meta is None:
        initial_ref_state = "not_found"
    elif not str(requested_meta.get("dom_ref") or "").strip():
        initial_ref_state = "stale_snapshot"

    stale_recovered = False
    reason_code = "unknown_error"

    if initial_ref_state:
        retry_path.append(f"recover:{initial_ref_state}")
        try:
            fresh_snapshot_result = await snapshot_page_fn(
                url=(page.url or None),
                session_id=session_id,
            )
            fresh_snapshot_id = str(fresh_snapshot_result.get("snapshot_id") or "")
            fresh_snapshot = (
                session.snapshots.get(fresh_snapshot_id)
                if fresh_snapshot_id
                else None
            )
            recovered_meta: Optional[Dict[str, Any]] = None
            recovered_ref_id = ref_id

            if isinstance(fresh_snapshot, dict):
                recovered_meta = resolve_ref_meta_from_snapshot_fn(fresh_snapshot, ref_id)
                if recovered_meta is None:
                    recovered_meta = resolve_stale_ref_fn(requested_meta, fresh_snapshot)
                    if isinstance(recovered_meta, dict):
                        recovered_ref_id = str(
                            recovered_meta.get("ref_id") or recovered_ref_id
                        )

                if isinstance(recovered_meta, dict):
                    requested_snapshot = fresh_snapshot
                    requested_meta = recovered_meta
                    snapshot_id = fresh_snapshot_id or snapshot_id
                    ref_id = recovered_ref_id
                    stale_recovered = True
                    reason_code = "stale_ref_recovered"
                    retry_path.append("recover:ok")
        except Exception as recover_exc:
            friendly = to_ai_friendly_error_fn(recover_exc, ref_id=ref_id)
            retry_path.append(f"recover:error:{friendly}")

    if (
        not isinstance(requested_snapshot, dict)
        or not isinstance(requested_meta, dict)
        or not str(requested_meta.get("dom_ref") or "").strip()
    ):
        if initial_ref_state == "snapshot_not_found":
            fail_message = "snapshot을 찾을 수 없습니다. 최신 snapshot 기준으로 다시 시도해 주세요."
            fail_code = "snapshot_not_found"
        elif initial_ref_state == "not_found":
            fail_message = "snapshot 내 ref를 찾을 수 없습니다. 최신 snapshot 기준으로 다시 시도해 주세요."
            fail_code = "not_found"
        else:
            fail_message = "snapshot/ref가 stale 상태입니다. 최신 snapshot 기준으로 다시 시도해 주세요."
            fail_code = "stale_snapshot"

        return {
            "requested_snapshot": requested_snapshot,
            "requested_meta": requested_meta,
            "snapshot_id": snapshot_id,
            "ref_id": ref_id,
            "stale_recovered": stale_recovered,
            "reason_code": fail_code,
            "response": {
                "success": False,
                "effective": False,
                "reason_code": fail_code,
                "reason": fail_message,
                "stale_recovered": stale_recovered,
                "retry_path": retry_path,
            },
        }

    return {
        "requested_snapshot": requested_snapshot,
        "requested_meta": requested_meta,
        "snapshot_id": snapshot_id,
        "ref_id": ref_id,
        "stale_recovered": stale_recovered,
        "reason_code": reason_code,
        "response": None,
    }
