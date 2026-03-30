from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional


ResolveSessionPageFn = Callable[..., Awaitable[Any]]
GetTabIndexFn = Callable[[Any], int]
BuildErrorFn = Callable[[str, str], Dict[str, Any]]


def _pick_value(params: Dict[str, Any], payload: Dict[str, Any], key: str, default: Any = None) -> Any:
    if key in params:
        return params.get(key)
    if key in payload:
        return payload.get(key)
    return default


def build_interaction_handlers(
    *,
    resolve_session_page_fn: ResolveSessionPageFn,
    get_tab_index_fn: GetTabIndexFn,
    build_error_fn: BuildErrorFn,
    browser_state_store_cls: Any,
) -> Dict[str, Any]:
    async def browser_dialog_arm(params: Dict[str, Any]) -> Dict[str, Any]:
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        session_id = str(_pick_value(params, payload, "session_id", "default"))
        mode = str(_pick_value(params, payload, "mode") or "dismiss").strip().lower()
        if mode not in {"accept", "dismiss"}:
            return build_error_fn("not_actionable", "mode must be accept|dismiss")
        prompt_text = str(
            _pick_value(params, payload, "prompt_text")
            or _pick_value(params, payload, "promptText")
            or ""
        )
        session, _ = await resolve_session_page_fn(
            session_id,
            tab_id=_pick_value(params, payload, "tab_id", _pick_value(params, payload, "targetId")),
        )
        session.dialog_mode = mode
        session.dialog_prompt_text = prompt_text
        session._ensure_dialog_listener()
        return {"success": True, "reason_code": "ok", "mode": mode}

    async def browser_file_chooser_arm(params: Dict[str, Any]) -> Dict[str, Any]:
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        session_id = str(_pick_value(params, payload, "session_id", "default"))
        files = _pick_value(params, payload, "files")
        if isinstance(files, str):
            file_list = [files]
        elif isinstance(files, list):
            file_list = [str(p) for p in files if str(p).strip()]
        else:
            file_list = []
        session, _ = await resolve_session_page_fn(
            session_id,
            tab_id=_pick_value(params, payload, "tab_id", _pick_value(params, payload, "targetId")),
        )
        session.file_chooser_files = file_list
        session._ensure_file_chooser_listener()
        return {"success": True, "reason_code": "ok", "files": file_list}

    async def browser_download_wait(params: Dict[str, Any]) -> Dict[str, Any]:
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        session_id = str(_pick_value(params, payload, "session_id", "default"))
        timeout_ms = int(_pick_value(params, payload, "timeout_ms") or _pick_value(params, payload, "timeoutMs") or 20000)
        path = str(_pick_value(params, payload, "path") or "")
        tab_id = _pick_value(params, payload, "tab_id", _pick_value(params, payload, "targetId"))
        session, page = await resolve_session_page_fn(session_id, tab_id=tab_id)

        download = await page.wait_for_event("download", timeout=timeout_ms)
        suggested_name = download.suggested_filename
        base_download_dir = (Path.home() / ".gaia" / "downloads").resolve()
        base_download_dir.mkdir(parents=True, exist_ok=True)
        if path:
            save_target = Path(path).expanduser().resolve()
            if not save_target.is_relative_to(base_download_dir):
                return build_error_fn(
                    "not_actionable",
                    f"download path must be under {base_download_dir}",
                )
        else:
            save_target = (base_download_dir / f"{int(time.time())}_{suggested_name}").resolve()
        save_target.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(str(save_target))
        item = {
            "url": download.url,
            "suggested_filename": suggested_name,
            "saved_path": str(save_target),
        }
        session.observability.add_download_event(item)
        tab_idx = get_tab_index_fn(page)
        return {
            "success": True,
            "reason_code": "ok",
            "tab_id": tab_idx,
            "targetId": tab_idx,
            "item": item,
        }

    async def browser_state(params: Dict[str, Any]) -> Dict[str, Any]:
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        session_id = str(_pick_value(params, payload, "session_id", "default"))
        tab_id = _pick_value(params, payload, "tab_id", _pick_value(params, payload, "targetId"))
        profile = str(_pick_value(params, payload, "profile") or "default")
        kind = str(_pick_value(params, payload, "kind") or "").strip().lower()
        op = str(_pick_value(params, payload, "op") or "get").strip().lower()
        _session, page = await resolve_session_page_fn(session_id, tab_id=tab_id)

        if op == "get":
            tab_idx = get_tab_index_fn(page)
            state = await browser_state_store_cls.get_state(page)
            if kind in {"local", "local_storage"}:
                state = {"local_storage": dict(state.get("local_storage") or {}), "url": state.get("url", "")}
            elif kind in {"session", "session_storage"}:
                state = {"session_storage": dict(state.get("session_storage") or {}), "url": state.get("url", "")}
            return {
                "success": True,
                "reason_code": "ok",
                "tab_id": tab_idx,
                "targetId": tab_idx,
                "state": state,
                "meta": {"profile": profile, "kind": kind or "all", "op": op},
            }

        if op == "set":
            state_payload = _pick_value(params, payload, "state") if isinstance(_pick_value(params, payload, "state"), dict) else {}
            if kind in {"local", "local_storage"}:
                local_payload = state_payload.get("local_storage", state_payload.get("local", state_payload))
                if not isinstance(local_payload, dict):
                    return build_error_fn("invalid_input", "local_storage payload must be an object")
                state_payload = {"local_storage": local_payload}
            elif kind in {"session", "session_storage"}:
                session_payload = state_payload.get("session_storage", state_payload.get("session", state_payload))
                if not isinstance(session_payload, dict):
                    return build_error_fn("invalid_input", "session_storage payload must be an object")
                state_payload = {"session_storage": session_payload}
            elif kind == "cookies":
                cookies_payload = state_payload.get("cookies", state_payload)
                if not isinstance(cookies_payload, list):
                    return build_error_fn("invalid_input", "cookies payload must be an array")
                state_payload = {"cookies": cookies_payload}
            meta = await browser_state_store_cls.set_state(page, state_payload)
            meta["profile"] = profile
            meta["kind"] = kind or "all"
            tab_idx = get_tab_index_fn(page)
            return {
                "success": True,
                "reason_code": "ok",
                "tab_id": tab_idx,
                "targetId": tab_idx,
                "meta": meta,
            }

        if op == "clear":
            clear_payload = _pick_value(params, payload, "state") if isinstance(_pick_value(params, payload, "state"), dict) else {}
            if kind in {"local", "local_storage"}:
                clear_payload = {"local_storage": clear_payload.get("local_storage", clear_payload.get("local", True))}
            elif kind in {"session", "session_storage"}:
                clear_payload = {"session_storage": clear_payload.get("session_storage", clear_payload.get("session", True))}
            elif kind == "cookies":
                clear_payload = {"cookies": clear_payload.get("cookies", True)}
            meta = await browser_state_store_cls.clear_state(page, clear_payload)
            meta["profile"] = profile
            meta["kind"] = kind or "all"
            tab_idx = get_tab_index_fn(page)
            return {
                "success": True,
                "reason_code": "ok",
                "tab_id": tab_idx,
                "targetId": tab_idx,
                "meta": meta,
            }

        return build_error_fn("not_actionable", "state op must be get|set|clear")

    async def browser_env(params: Dict[str, Any]) -> Dict[str, Any]:
        payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
        session_id = str(_pick_value(params, payload, "session_id", "default"))
        tab_id = _pick_value(params, payload, "tab_id", _pick_value(params, payload, "targetId"))
        profile = str(_pick_value(params, payload, "profile") or "default")
        op = str(_pick_value(params, payload, "op") or "get").strip().lower()
        session, page = await resolve_session_page_fn(session_id, tab_id=tab_id)
        if op == "get":
            tab_idx = get_tab_index_fn(page)
            return {
                "success": True,
                "reason_code": "ok",
                "tab_id": tab_idx,
                "targetId": tab_idx,
                "state": dict(session.env_overrides),
                "meta": {"profile": profile, "op": op},
            }
        if op == "set":
            env_payload = _pick_value(params, payload, "env") if isinstance(_pick_value(params, payload, "env"), dict) else {}
            result = await browser_state_store_cls.apply_env(page, env_payload)
            session.env_overrides.update(result.get("applied", {}))
            tab_idx = get_tab_index_fn(page)
            return {
                "success": True,
                "reason_code": "ok",
                "tab_id": tab_idx,
                "targetId": tab_idx,
                "state": dict(session.env_overrides),
                "meta": dict(result, profile=profile),
            }
        return build_error_fn("not_actionable", "env op must be get|set")

    return {
        "dialog_arm": browser_dialog_arm,
        "file_chooser_arm": browser_file_chooser_arm,
        "download_wait": browser_download_wait,
        "state": browser_state,
        "env": browser_env,
    }
