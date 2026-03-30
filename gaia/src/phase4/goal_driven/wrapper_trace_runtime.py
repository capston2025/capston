from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable, Optional


def wrapper_mode_name(agent: Any) -> str:
    explicit = str(
        getattr(agent, "_goal_wrapper_mode", "")
        or os.getenv("GAIA_GOAL_WRAPPER_MODE", "")
        or ""
    ).strip().lower()
    if explicit in {"thin", "openclaw", "openclaw-thin", "openclaw_thin"}:
        return "thin"
    if explicit in {"classic", "legacy", "full", "off", "false", "0"}:
        return "classic"
    backend_name = str(
        getattr(agent, "_browser_backend_name", "")
        or os.getenv("GAIA_BROWSER_BACKEND", "")
        or ""
    ).strip().lower()
    return "thin" if backend_name == "openclaw" else "classic"


def thin_wrapper_enabled(agent: Any) -> bool:
    return wrapper_mode_name(agent) == "thin"


def wrapper_trace_enabled(agent: Any) -> bool:
    explicit = getattr(agent, "_wrapper_trace_enabled", None)
    if explicit is not None:
        return bool(explicit)
    raw = str(os.getenv("GAIA_WRAPPER_TRACE", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def dump_wrapper_trace(agent: Any, *, kind: str, payload: dict[str, Any]) -> Optional[str]:
    if not wrapper_trace_enabled(agent):
        return None
    root = Path(str(os.getenv("GAIA_WRAPPER_TRACE_DIR", "artifacts/wrapper_trace") or "artifacts/wrapper_trace"))
    run_id = str(getattr(agent, "_wrapper_trace_run_id", "") or "").strip()
    if not run_id:
        run_id = time.strftime("%Y%m%d-%H%M%S")
        setattr(agent, "_wrapper_trace_run_id", run_id)
    trace_dir = root / run_id
    trace_dir.mkdir(parents=True, exist_ok=True)
    counters = getattr(agent, "_wrapper_trace_counters", None)
    if not isinstance(counters, dict):
        counters = {}
        setattr(agent, "_wrapper_trace_counters", counters)
    counter_key = str(kind or "trace")
    counters[counter_key] = int(counters.get(counter_key, 0) or 0) + 1
    action_history = getattr(agent, "_action_history", None)
    step_guess = len(action_history) + 1 if isinstance(action_history, list) else counters[counter_key]
    path = trace_dir / f"step-{step_guess:02d}-{counter_key}-{counters[counter_key]:02d}.json"
    record = dict(payload or {})
    record.setdefault("kind", counter_key)
    record.setdefault("run_id", run_id)
    record.setdefault("step_guess", step_guess)
    record.setdefault("generated_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    return str(path)


def serialize_dom_elements(elements: Iterable[Any], *, limit: int = 80, agent: Any = None) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for idx, element in enumerate(elements or []):
        if idx >= max(1, int(limit)):
            break
        item = {
            "id": getattr(element, "id", None),
            "ref_id": getattr(element, "ref_id", None),
            "tag": getattr(element, "tag", None),
            "role": getattr(element, "role", None),
            "text": getattr(element, "text", None),
            "aria_label": getattr(element, "aria_label", None),
            "title": getattr(element, "title", None),
            "container_ref_id": getattr(element, "container_ref_id", None),
            "container_name": getattr(element, "container_name", None),
            "container_role": getattr(element, "container_role", None),
            "container_source": getattr(element, "container_source", None),
            "context_text": getattr(element, "context_text", None),
            "group_action_labels": list(getattr(element, "group_action_labels", None) or []),
            "role_ref_role": getattr(element, "role_ref_role", None),
            "role_ref_name": getattr(element, "role_ref_name", None),
            "role_ref_nth": getattr(element, "role_ref_nth", None),
            "options": list(getattr(element, "options", None) or []),
            "selected_value": getattr(element, "selected_value", None),
            "is_visible": getattr(element, "is_visible", None),
            "is_enabled": getattr(element, "is_enabled", None),
        }
        if agent is not None:
            try:
                from .dom_prompt_formatting import context_match_tokens, semantic_tags_for_element

                semantic_tags = semantic_tags_for_element(agent, element)
            except Exception:
                semantic_tags = []
            if semantic_tags:
                item["semantic_tags"] = semantic_tags
            try:
                matched_tokens = list(context_match_tokens(agent, element) or [])
            except Exception:
                matched_tokens = []
            if matched_tokens:
                item["context_match_tokens"] = matched_tokens
        serialized.append(item)
    return serialized


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return str(value)
