from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .models import ActionDecision, DOMElement, TestGoal


_DEFAULT_HISTORY_ENABLED = "1"
_DEFAULT_PROMPT_CHAR_LIMIT = 12000
_DEFAULT_COMPACT_CHAR_LIMIT = 6000
_DEFAULT_REPLAY_CHAR_LIMIT = 3200
_DEFAULT_BACKGROUND_LOCK_LEASE_SEC = 90.0
_REPO_ROOT = Path(__file__).resolve().parents[4]
_BACKGROUND_INLINE_TRIGGERS = {"goal_start", "goal_end", "state_refresh"}
_BACKGROUND_DEFERRED_TRIGGERS = {"decision", "step_outcome"}


def _history_root() -> Path:
    configured = str(os.getenv("GAIA_RUN_HISTORY_DIR", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _REPO_ROOT / ".gaia" / "run_history"


def _history_enabled(agent: Any) -> bool:
    cached = getattr(agent, "_run_history_enabled", None)
    if isinstance(cached, bool):
        return cached
    raw = str(os.getenv("GAIA_RUN_HISTORY_ENABLED", _DEFAULT_HISTORY_ENABLED) or _DEFAULT_HISTORY_ENABLED)
    enabled = raw.strip().lower() not in {"0", "false", "off", "no"}
    agent._run_history_enabled = enabled
    return enabled


def _safe_slug(value: object, *, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    return slug or fallback


def _json_default(value: object) -> object:
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    return str(value)


def _append_event(agent: Any, payload: Dict[str, Any]) -> None:
    if not _history_enabled(agent):
        return
    raw_paths = [
        str(getattr(agent, "_run_history_events_path", "") or "").strip(),
        str(getattr(agent, "_run_history_session_events_path", "") or "").strip(),
    ]
    seen: set[str] = set()
    for raw_path in raw_paths:
        if not raw_path or raw_path in seen:
            continue
        seen.add(raw_path)
        events_path = Path(raw_path)
        try:
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=_json_default))
                fh.write("\n")
        except Exception:
            continue


def _append_transcript(agent: Any, payload: Dict[str, Any]) -> None:
    if not _history_enabled(agent):
        return
    raw_paths = [
        str(getattr(agent, "_run_history_transcript_path", "") or "").strip(),
        str(getattr(agent, "_run_history_session_transcript_path", "") or "").strip(),
    ]
    seen: set[str] = set()
    for raw_path in raw_paths:
        if not raw_path or raw_path in seen:
            continue
        seen.add(raw_path)
        transcript_path = Path(raw_path)
        try:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            with transcript_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, default=_json_default))
                fh.write("\n")
        except Exception:
            continue


def _decision_action_value(decision: ActionDecision) -> str:
    action = getattr(decision, "action", "")
    return str(getattr(action, "value", action) or "").strip()


def _load_events(agent: Any) -> List[Dict[str, Any]]:
    raw_path = str(getattr(agent, "_run_history_session_events_path", "") or "").strip()
    if not raw_path:
        raw_path = str(getattr(agent, "_run_history_events_path", "") or "").strip()
    events_path = Path(raw_path)
    if not events_path or not events_path.exists():
        return []
    events: List[Dict[str, Any]] = []
    try:
        with events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    events.append(item)
    except Exception:
        return []
    return events


def _load_transcript_rows(agent: Any) -> List[Dict[str, Any]]:
    raw_path = str(getattr(agent, "_run_history_session_transcript_path", "") or "").strip()
    if not raw_path:
        raw_path = str(getattr(agent, "_run_history_transcript_path", "") or "").strip()
    transcript_path = Path(raw_path)
    if not transcript_path or not transcript_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with transcript_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return []
    return rows


def _goal_domain_slug(goal: Optional[TestGoal], agent: Any) -> str:
    goal_context = _goal_contract_context(agent, goal)
    snapshot = _load_context_snapshot_payload(agent)
    candidates = [
        str(goal_context.get("start_url") or "").strip(),
        str(getattr(agent, "_active_url", "") or snapshot.get("active_url") or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        parsed = urlparse(candidate)
        host = str(parsed.netloc or "").strip().lower()
        if host:
            return _safe_slug(host, fallback="site")
    return "site"


def _session_key_for(agent: Any, goal: Optional[TestGoal]) -> str:
    override = str(os.getenv("GAIA_RUN_HISTORY_SESSION_KEY", "") or "").strip()
    if override:
        return _safe_slug(override, fallback="session")
    session_id = _safe_slug(getattr(agent, "session_id", "") or "goal-driven", fallback="goal-driven")
    goal_slug = _safe_slug(
        getattr(goal, "id", "") or getattr(goal, "name", "") or "goal",
        fallback="goal",
    )
    domain_slug = _goal_domain_slug(goal, agent)
    return f"{domain_slug}--{goal_slug}--{session_id}"


def _truncate_text(value: object, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _truncate_large_text(value: object, limit: int = 4000) -> str:
    return _truncate_text(value, limit)


def _tokenize_for_retrieval(value: object) -> List[str]:
    text = str(value or "").lower()
    tokens = re.findall(r"[a-z0-9가-힣_./:-]{2,}", text)
    unique: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique


def _render_decision_line(event: Dict[str, Any]) -> str:
    action = str(event.get("action") or "").strip() or "unknown"
    ref_id = str(event.get("ref_id") or "").strip()
    element_id = event.get("element_id")
    reason = _truncate_text(event.get("reasoning") or "", 140)
    parts = [f"Step {int(event.get('step') or 0)} | plan | {action}"]
    if ref_id:
        parts.append(f"ref={ref_id}")
    if element_id is not None:
        parts.append(f"element_id={element_id}")
    if reason:
        parts.append(f'reason="{reason}"')
    return " | ".join(parts)


def _render_outcome_line(event: Dict[str, Any]) -> str:
    action = str(event.get("action") or "").strip() or "unknown"
    status = str(event.get("status") or "").strip() or "unknown"
    parts = [f"Step {int(event.get('step') or 0)} | outcome | {action} | {status}"]
    changed = event.get("changed")
    if changed is not None:
        parts.append(f"changed={bool(changed)}")
    success = event.get("success")
    if success is not None:
        parts.append(f"success={bool(success)}")
    reason_code = str(event.get("reason_code") or "").strip()
    if reason_code:
        parts.append(f"reason_code={reason_code}")
    state_change = event.get("state_change")
    if isinstance(state_change, dict) and state_change:
        positives = [str(key) for key, value in state_change.items() if value]
        if positives:
            parts.append("signals=" + ",".join(positives[:6]))
    error = _truncate_text(event.get("error") or "", 120)
    if error and error != "none":
        parts.append(f'error="{error}"')
    return " | ".join(parts)


def _render_signal_lines(agent: Any) -> List[str]:
    lines: List[str] = []
    recent_signals = list(getattr(agent, "_recent_signal_history", []) or [])[-6:]
    if not recent_signals:
        recent_signals = _snapshot_dict_list(
            _load_context_snapshot_payload(agent).get("recent_signal_history", []),
            limit=6,
        )
    for item in recent_signals:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        pieces: List[str] = []
        if action:
            pieces.append(f"action={action}")
        if item.get("pagination_candidate"):
            pieces.append("pagination_candidate=true")
        state_change = item.get("state_change")
        if isinstance(state_change, dict):
            positives = [str(key) for key, value in state_change.items() if value]
            if positives:
                pieces.append("signals=" + ",".join(positives[:6]))
        if pieces:
            lines.append("- " + " | ".join(pieces))
    return lines


def _render_fill_memory_lines(agent: Any) -> List[str]:
    lines: List[str] = []
    items = list(getattr(agent, "_persistent_state_memory", []) or [])[-6:]
    if not items:
        items = _snapshot_dict_list(
            _load_context_snapshot_payload(agent).get("persistent_state_memory", []),
            limit=6,
        )
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        value = _truncate_text(item.get("expected_value") or item.get("value") or "", 80)
        context = _truncate_text(item.get("context_text") or "", 100)
        container = _truncate_text(item.get("container_name") or "", 80)
        line = f"- kind={kind}"
        if value:
            line += f' | value="{value}"'
        if container:
            line += f' | container="{container}"'
        if context:
            line += f' | context="{context}"'
        lines.append(line)
    return lines


def _goal_context(agent: Any, events: List[Dict[str, Any]], goal: Optional[TestGoal]) -> Dict[str, str]:
    goal_start = next(
        (
            event
            for event in reversed(events)
            if str(event.get("kind") or "").strip() == "goal_start"
        ),
        {},
    )
    goal_contract = _goal_contract_context(agent, goal)
    return {
        "name": str(
            goal_contract.get("name")
            or goal_start.get("goal_name")
            or getattr(agent, "_active_goal_text", "")
            or _load_context_snapshot_payload(agent).get("active_goal_text")
            or ""
        ).strip(),
        "description": str(
            goal_contract.get("description")
            or goal_start.get("goal_description")
            or ""
        ).strip(),
    }


def _dedupe_lines(lines: List[str]) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _latest_terminal_event(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for event in reversed(events):
        if str(event.get("kind") or "").strip() == "goal_end":
            return event
    return None


def _render_summary(agent: Any, events: List[Dict[str, Any]], goal: Optional[TestGoal]) -> str:
    goal_context = _goal_context(agent, events, goal)
    goal_name = goal_context["name"]
    goal_description = goal_context["description"]
    run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown"
    session_key = str(getattr(agent, "_run_history_session_key", "") or "").strip() or "unknown"
    goal_start = next((event for event in events if str(event.get("kind") or "") == "goal_start"), {})
    terminal = next(
        (
            event
            for event in reversed(events)
            if str(event.get("kind") or "") == "goal_end"
        ),
        {},
    )
    decision_lines = [
        _render_decision_line(event)
        for event in events
        if str(event.get("kind") or "") == "decision"
    ]
    outcome_lines = [
        _render_outcome_line(event)
        for event in events
        if str(event.get("kind") or "") == "step_outcome"
    ]
    run_ids = [
        str(event.get("run_id") or "").strip()
        for event in events
        if str(event.get("run_id") or "").strip()
    ]
    unique_run_ids: List[str] = []
    seen_run_ids: set[str] = set()
    for item in run_ids:
        if item in seen_run_ids:
            continue
        seen_run_ids.add(item)
        unique_run_ids.append(item)
    prior_run_ids = [item for item in unique_run_ids if item != run_id]
    prior_terminal_events = [
        event
        for event in events
        if str(event.get("kind") or "") == "goal_end"
        and str(event.get("run_id") or "").strip() != run_id
    ]

    sections: List[str] = ["## 누적 실행 상태 원장"]
    sections.append(f"- session_key: `{session_key}`")
    sections.append(f"- run_id: `{run_id}`")
    if goal_name:
        sections.append(f"- goal: {goal_name}")
    if goal_description:
        sections.append(f"- goal_description: {goal_description}")
    if goal_start:
        started_at = float(goal_start.get("timestamp") or 0.0)
        if started_at > 0:
            sections.append(f"- started_at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started_at))}")
    sections.append(f"- recorded_events: {len(events)}")
    sections.append(f"- observed_runs: {len(unique_run_ids)}")
    if prior_run_ids:
        sections.append(f"- continued_from_previous_session: true ({len(prior_run_ids)} prior runs)")
    if terminal:
        sections.append(
            "- terminal: "
            f"status={str(terminal.get('status') or '').strip() or 'unknown'}"
            f", reason={_truncate_text(terminal.get('reason') or '', 160) or 'none'}"
        )

    if prior_terminal_events:
        sections.append("")
        sections.append("### 이전 실행 요약")
        for event in prior_terminal_events[-4:]:
            previous_run_id = str(event.get("run_id") or "").strip() or "unknown"
            status = str(event.get("status") or "").strip() or "unknown"
            reason = _truncate_text(event.get("reason") or "", 120) or "none"
            sections.append(f"- run={previous_run_id} | status={status} | reason=\"{reason}\"")

    if outcome_lines:
        sections.append("")
        sections.append("### 실행 타임라인")
        sections.extend(f"- {line}" for line in outcome_lines[-16:])

    if decision_lines:
        sections.append("")
        sections.append("### 최근 계획된 액션")
        sections.extend(f"- {line}" for line in decision_lines[-12:])

    signal_lines = _render_signal_lines(agent)
    if signal_lines:
        sections.append("")
        sections.append("### 최근 상태 신호")
        sections.extend(signal_lines)

    fill_memory_lines = _render_fill_memory_lines(agent)
    if fill_memory_lines:
        sections.append("")
        sections.append("### 최근 fill/select 기억")
        sections.extend(fill_memory_lines)

    summary = "\n".join(section for section in sections if section is not None).strip()
    max_chars = int(
        str(
            os.getenv(
                "GAIA_RUN_HISTORY_PROMPT_CHAR_LIMIT",
                str(_DEFAULT_PROMPT_CHAR_LIMIT),
            )
        ).strip()
        or str(_DEFAULT_PROMPT_CHAR_LIMIT)
    )
    if len(summary) > max_chars:
        summary = summary[: max_chars - 24].rstrip() + "\n... (truncated history)"
    return summary


def _current_run_events(agent: Any, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    if not current_run_id:
        return list(events)
    return [
        event
        for event in events
        if str(event.get("run_id") or "").strip() == current_run_id
    ]


def _last_failed_outcome(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for event in reversed(events):
        if str(event.get("kind") or "").strip() != "step_outcome":
            continue
        if bool(event.get("success")):
            continue
        return event
    return None


def _build_failure_warning_lines(current_events: List[Dict[str, Any]]) -> List[str]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for event in current_events:
        if str(event.get("kind") or "") != "step_outcome":
            continue
        if bool(event.get("success")):
            continue
        action = str(event.get("action") or "").strip() or "unknown"
        ref_id = str(event.get("ref_id") or "").strip() or "-"
        reason_code = str(event.get("reason_code") or "").strip() or "unknown"
        key = f"{action}|{ref_id}|{reason_code}"
        bucket = grouped.setdefault(
            key,
            {"action": action, "ref_id": ref_id, "reason_code": reason_code, "count": 0},
        )
        bucket["count"] = int(bucket.get("count", 0)) + 1
    lines: List[str] = []
    for item in grouped.values():
        if int(item.get("count", 0)) < 2:
            continue
        lines.append(
            "- "
            f"action={item['action']} | ref={item['ref_id']} | "
            f"reason_code={item['reason_code']} | repeat_failures={item['count']}"
        )
    return sorted(lines)


def _objective_lines(agent: Any, events: List[Dict[str, Any]], goal: Optional[TestGoal]) -> List[str]:
    goal_context = _goal_context(agent, events, goal)
    goal_contract = _goal_contract_context(agent, goal)
    lines: List[str] = []
    if goal_context["name"]:
        lines.append(f"- goal: {goal_context['name']}")
    if goal_context["description"]:
        lines.append(f"- description: {_truncate_text(goal_context['description'], 220)}")
    expected_signals = list(goal_contract.get("expected_signals") or [])
    success_criteria = list(goal_contract.get("success_criteria") or [])
    success_signal_lines = expected_signals or success_criteria
    if success_signal_lines:
        lines.append(f"- success_signals: {_truncate_text('; '.join(success_signal_lines[:4]), 220)}")
    if not lines:
        lines.append("- goal: (missing)")
    return lines


def _completed_progress_lines(
    events: List[Dict[str, Any]],
    *,
    fallback_events: Optional[List[Dict[str, Any]]] = None,
    limit: int = 3,
) -> List[str]:
    lines = [
        f"- {_render_outcome_line(event)}"
        for event in events
        if str(event.get("kind") or "").strip() == "step_outcome"
        and (bool(event.get("success")) or bool(event.get("changed")))
    ]
    lines = _dedupe_lines(lines)
    if lines:
        return lines[-limit:]
    if fallback_events is not None:
        last_progress = _last_progress_event(fallback_events)
        if last_progress is not None:
            return [f"- carry_over_progress: {_render_outcome_line(last_progress)}"]
    return ["- no_confirmed_progress_yet"]


def _active_blocker_lines(
    events: List[Dict[str, Any]],
    *,
    terminal_event: Optional[Dict[str, Any]] = None,
    repeated_blockers: Optional[List[Dict[str, Any]]] = None,
    limit: int = 4,
) -> List[str]:
    lines: List[str] = []
    latest_failed = _last_failed_outcome(events)
    if latest_failed is not None:
        lines.append(f"- latest_failure: {_render_outcome_line(latest_failed)}")
    if terminal_event is not None:
        status = str(terminal_event.get("status") or "").strip() or "unknown"
        if status != "success":
            reason = _truncate_text(terminal_event.get("reason") or "", 160) or "none"
            lines.append(f'- terminal_blocker: status={status} | reason="{reason}"')
    for item in list(repeated_blockers or [])[:3]:
        lines.append(
            "- "
            f"recent_repeat_history: action={item['action']} | ref={item['ref_id']} | "
            f"reason_code={item['reason_code']} | count={item['count']}"
        )
    lines = _dedupe_lines(lines)
    if lines:
        return lines[:limit]
    return ["- none"]


def _next_best_action_lines(
    *,
    last_progress: Optional[Dict[str, Any]],
    failed_event: Optional[Dict[str, Any]],
    terminal_event: Optional[Dict[str, Any]],
) -> List[str]:
    lines: List[str] = []
    if last_progress is not None:
        lines.append(f"- resume_from_progress: {_truncate_text(_render_outcome_line(last_progress), 180)}")
    if failed_event is not None:
        ref_id = str(failed_event.get("ref_id") or "").strip()
        reason_code = str(failed_event.get("reason_code") or "").strip()
        if ref_id:
            lines.append(f"- inspect_alternate_surface_around_ref: {ref_id}")
        if reason_code and reason_code not in {"ok", "unknown"}:
            lines.append(f"- avoid_repeating_reason_code: {reason_code}")
    elif terminal_event is not None:
        status = str(terminal_event.get("status") or "").strip() or "unknown"
        if status != "success":
            lines.append("- collect_missing_completion_evidence_before_terminal_retry")
    lines = _dedupe_lines(lines)
    if lines:
        return lines[:3]
    return ["- capture_next_success_signal_from_current_surface"]


def _open_question_lines(
    *,
    failed_event: Optional[Dict[str, Any]],
    terminal_event: Optional[Dict[str, Any]],
) -> List[str]:
    lines: List[str] = []
    if failed_event is not None:
        ref_id = str(failed_event.get("ref_id") or "").strip()
        reason_code = str(failed_event.get("reason_code") or "").strip()
        if ref_id:
            lines.append(f"- is_ref_still_valid: {ref_id}")
        if reason_code and reason_code not in {"ok", "unknown"}:
            lines.append(f"- what_evidence_clears_reason_code: {reason_code}")
    if not lines and terminal_event is not None:
        status = str(terminal_event.get("status") or "").strip() or "unknown"
        if status != "success":
            reason = _truncate_text(terminal_event.get("reason") or "", 160) or "none"
            lines.append(f'- what_was_missing_at_last_terminal: "{reason}"')
    if not lines:
        lines.append("- which_success_criterion_is_still_missing")
    return _dedupe_lines(lines)[:3]


def _render_compact_summary(agent: Any, events: List[Dict[str, Any]], goal: Optional[TestGoal]) -> str:
    current_events = _current_run_events(agent, events)
    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown"
    session_key = str(getattr(agent, "_run_history_session_key", "") or "").strip() or "unknown"
    goal_context = _goal_context(agent, events, goal)
    goal_name = goal_context["name"]
    prior_terminal_events = [
        event
        for event in events
        if str(event.get("kind") or "") == "goal_end"
        and str(event.get("run_id") or "").strip() != current_run_id
    ]
    progress_lines = [
        _render_outcome_line(event)
        for event in current_events
        if str(event.get("kind") or "") == "step_outcome"
        and (bool(event.get("success")) or bool(event.get("changed")))
    ]
    decision_lines = [
        _render_decision_line(event)
        for event in current_events
        if str(event.get("kind") or "") == "decision"
    ]
    warning_lines = _build_failure_warning_lines(current_events)
    current_failure_buckets = [
        item
        for item in _session_failure_buckets(current_events)
        if int(item.get("count", 0)) >= 2
    ]
    signal_lines = _render_signal_lines(agent)
    fill_memory_lines = _render_fill_memory_lines(agent)
    last_progress = _last_progress_event(events)
    latest_failed = _last_failed_outcome(current_events)
    carryover_terminal = prior_terminal_events[-1] if prior_terminal_events else None
    continuity_progress_lines = _completed_progress_lines(current_events, fallback_events=events, limit=3)
    continuity_blocker_lines = _active_blocker_lines(
        current_events,
        terminal_event=carryover_terminal,
        repeated_blockers=current_failure_buckets,
        limit=4,
    )
    next_best_action_lines = _next_best_action_lines(
        last_progress=last_progress,
        failed_event=latest_failed,
        terminal_event=carryover_terminal,
    )
    open_question_lines = _open_question_lines(
        failed_event=latest_failed,
        terminal_event=carryover_terminal,
    )

    sections: List[str] = ["## 누적 실행 상태 기록(압축)"]
    sections.append(f"- session_key: `{session_key}`")
    sections.append(f"- current_run: `{current_run_id}`")
    if goal_name:
        sections.append(f"- goal: {goal_name}")
    if prior_terminal_events:
        sections.append(f"- prior_runs: {len(prior_terminal_events)}")

    sections.append("")
    sections.append("### Current Objective")
    sections.extend(_objective_lines(agent, events, goal))

    sections.append("")
    sections.append("### Completed Progress")
    sections.extend(continuity_progress_lines)

    sections.append("")
    sections.append("### Active Blockers")
    sections.extend(continuity_blocker_lines)

    sections.append("")
    sections.append("### Next Best Action")
    sections.extend(next_best_action_lines)

    sections.append("")
    sections.append("### Open Questions")
    sections.extend(open_question_lines)

    if prior_terminal_events:
        sections.append("")
        sections.append("### 이전 실행 carry-over")
        for event in prior_terminal_events[-3:]:
            sections.append(
                "- "
                f"run={str(event.get('run_id') or '').strip() or 'unknown'} | "
                f"status={str(event.get('status') or '').strip() or 'unknown'} | "
                f"reason=\"{_truncate_text(event.get('reason') or '', 120) or 'none'}\""
            )

    if progress_lines:
        sections.append("")
        sections.append("### 현재 run 최근 진전")
        sections.extend(f"- {line}" for line in progress_lines[-8:])

    if decision_lines:
        sections.append("")
        sections.append("### 현재 run 최근 계획")
        sections.extend(f"- {line}" for line in decision_lines[-6:])

    if warning_lines:
        sections.append("")
        sections.append("### 반복/실패 주의")
        sections.extend(warning_lines)

    if signal_lines:
        sections.append("")
        sections.append("### 최근 상태 신호")
        sections.extend(signal_lines[-4:])

    if fill_memory_lines:
        sections.append("")
        sections.append("### 최근 fill/select 기억")
        sections.extend(fill_memory_lines[-4:])

    compact = "\n".join(section for section in sections if section is not None).strip()
    max_chars = int(
        str(
            os.getenv(
                "GAIA_RUN_HISTORY_COMPACT_CHAR_LIMIT",
                str(_DEFAULT_COMPACT_CHAR_LIMIT),
            )
        ).strip()
        or str(_DEFAULT_COMPACT_CHAR_LIMIT)
    )
    if len(compact) > max_chars:
        compact = compact[: max_chars - 25].rstrip() + "\n... (truncated compact)"
    return compact


def _session_failure_buckets(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for event in events:
        if str(event.get("kind") or "") != "step_outcome":
            continue
        if bool(event.get("success")):
            continue
        action = str(event.get("action") or "").strip() or "unknown"
        ref_id = str(event.get("ref_id") or "").strip() or "-"
        reason_code = str(event.get("reason_code") or "").strip() or "unknown"
        key = f"{action}|{ref_id}|{reason_code}"
        bucket = grouped.setdefault(
            key,
            {
                "action": action,
                "ref_id": ref_id,
                "reason_code": reason_code,
                "count": 0,
                "last_run_id": str(event.get("run_id") or "").strip(),
            },
        )
        bucket["count"] = int(bucket.get("count", 0)) + 1
        bucket["last_run_id"] = str(event.get("run_id") or "").strip()
    items = sorted(grouped.values(), key=lambda item: int(item.get("count", 0)), reverse=True)
    return items


def _last_progress_event(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for event in reversed(events):
        if str(event.get("kind") or "") != "step_outcome":
            continue
        if bool(event.get("success")) or bool(event.get("changed")):
            return event
    return None


def _recent_attempt_lines(
    events: List[Dict[str, Any]],
    *,
    current_run_id: str,
    limit: int,
) -> List[str]:
    prior_terminal_events = [
        event
        for event in events
        if str(event.get("kind") or "").strip() == "goal_end"
        and str(event.get("run_id") or "").strip() != current_run_id
    ]
    if not prior_terminal_events:
        return []

    lines: List[str] = []
    for terminal in reversed(prior_terminal_events[-limit:]):
        run_id = str(terminal.get("run_id") or "").strip()
        latest_outcome = next(
            (
                event
                for event in reversed(events)
                if str(event.get("run_id") or "").strip() == run_id
                and str(event.get("kind") or "").strip() == "step_outcome"
            ),
            None,
        )
        parts = [
            f"run={run_id or 'unknown'}",
            f"terminal={str(terminal.get('status') or '').strip() or 'unknown'}",
        ]
        if latest_outcome is not None:
            reason_code = str(latest_outcome.get("reason_code") or "").strip()
            if reason_code and reason_code not in {"ok", "unknown"}:
                parts.append(f"reason_code={reason_code}")
            parts.append(f"last_outcome={_truncate_text(_render_outcome_line(latest_outcome), 140)}")
        reason = _truncate_text(terminal.get("reason") or "", 120) or "none"
        parts.append(f'reason="{reason}"')
        lines.append("- " + " | ".join(parts))
    return lines


def _render_memory_summary(agent: Any, events: List[Dict[str, Any]], goal: Optional[TestGoal]) -> str:
    session_key = str(getattr(agent, "_run_history_session_key", "") or "").strip() or "unknown"
    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown"
    goal_context = _goal_context(agent, events, goal)
    goal_name = goal_context["name"]
    prior_terminal_events = [
        event
        for event in events
        if str(event.get("kind") or "") == "goal_end"
        and str(event.get("run_id") or "").strip() != current_run_id
    ]
    failure_buckets = _session_failure_buckets(events)
    last_progress = _last_progress_event(events)
    latest_failed = _last_failed_outcome(events)
    latest_terminal = _latest_terminal_event(events)
    repeated_blockers = [item for item in failure_buckets if int(item.get("count", 0)) >= 2]
    recent_attempt_lines = _recent_attempt_lines(events, current_run_id=current_run_id, limit=4)

    sections: List[str] = ["# Session Memory"]
    sections.append(f"- session_key: `{session_key}`")
    if goal_name:
        sections.append(f"- goal: {goal_name}")
    sections.append(f"- current_run: `{current_run_id}`")

    sections.append("")
    sections.append("## Current Objective")
    sections.extend(_objective_lines(agent, events, goal))

    sections.append("")
    sections.append("## Completed Progress")
    sections.extend(_completed_progress_lines(events, limit=4))

    sections.append("")
    sections.append("## Active Blockers")
    sections.extend(
        _active_blocker_lines(
            events,
            terminal_event=latest_terminal,
            repeated_blockers=repeated_blockers,
            limit=5,
        )
    )

    sections.append("")
    sections.append("## Next Best Action")
    sections.extend(
        _next_best_action_lines(
            last_progress=last_progress,
            failed_event=latest_failed,
            terminal_event=latest_terminal,
        )
    )

    sections.append("")
    sections.append("## Open Questions")
    sections.extend(
        _open_question_lines(
            failed_event=latest_failed,
            terminal_event=latest_terminal,
        )
    )

    if recent_attempt_lines:
        sections.append("")
        sections.append("## Recent Attempts")
        sections.extend(recent_attempt_lines)

    if prior_terminal_events:
        sections.append("")
        sections.append("## Prior Outcomes")
        for event in prior_terminal_events[-5:]:
            sections.append(
                "- "
                f"run={str(event.get('run_id') or '').strip() or 'unknown'} | "
                f"status={str(event.get('status') or '').strip() or 'unknown'} | "
                f"reason=\"{_truncate_text(event.get('reason') or '', 120) or 'none'}\""
            )

    if prior_terminal_events:
        latest_terminal = prior_terminal_events[-1]
        sections.append("")
        sections.append("## Resume Hints")
        sections.append(
            "- "
            f"last_terminal_status={str(latest_terminal.get('status') or '').strip() or 'unknown'} | "
            f"reason=\"{_truncate_text(latest_terminal.get('reason') or '', 140) or 'none'}\""
        )
        if last_progress is not None:
            sections.append(
                "- "
                f"resume_from_progress=\"{_truncate_text(_render_outcome_line(last_progress), 180)}\""
            )

    memory_summary = "\n".join(section for section in sections if section is not None).strip()
    max_chars = int(
        str(
            os.getenv(
                "GAIA_RUN_HISTORY_MEMORY_CHAR_LIMIT",
                str(_DEFAULT_COMPACT_CHAR_LIMIT),
            )
        ).strip()
        or str(_DEFAULT_COMPACT_CHAR_LIMIT)
    )
    if len(memory_summary) > max_chars:
        memory_summary = memory_summary[: max_chars - 24].rstrip() + "\n... (truncated memory)"
    return memory_summary


def _render_session_summary(agent: Any, events: List[Dict[str, Any]], goal: Optional[TestGoal]) -> str:
    memory_summary = _render_memory_summary(agent, events, goal)
    if not memory_summary.strip():
        return ""

    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown"
    prior_terminal_events = [
        event
        for event in events
        if str(event.get("kind") or "").strip() == "goal_end"
        and str(event.get("run_id") or "").strip() != current_run_id
    ]
    failure_buckets = _session_failure_buckets(events)
    repeated_blockers = [item for item in failure_buckets if int(item.get("count", 0)) >= 2]
    latest_prior_terminal = prior_terminal_events[-1] if prior_terminal_events else None

    audit_lines = [
        "- re_anchor_on_current_dom_before_reusing_prior_plan",
    ]
    if latest_prior_terminal is None:
        audit_lines.append("- fresh_session_history_use_current_dom_as_source_of_truth")
    else:
        audit_lines.append("- apply_resume_hints_only_if_current_surface_matches_last_goal_surface")
        audit_lines.append(
            "- "
            f"last_terminal_checkpoint: status={str(latest_prior_terminal.get('status') or '').strip() or 'unknown'} | "
            f"reason=\"{_truncate_text(latest_prior_terminal.get('reason') or '', 140) or 'none'}\""
        )
    if repeated_blockers:
        blocker = repeated_blockers[0]
        audit_lines.append(
            "- "
            f"avoid_repeat: action={str(blocker.get('action') or '').strip() or 'unknown'} | "
            f"ref={str(blocker.get('ref_id') or '').strip() or '-'} | "
            f"reason_code={str(blocker.get('reason_code') or '').strip() or 'unknown'} | "
            "require_new_state_change_or_new_goal_evidence"
        )

    goal_contract = _goal_contract_context(agent, goal)
    goal_text_blob = " ".join(
        [
            str(goal_contract.get("name") or "").strip(),
            str(goal_contract.get("description") or "").strip(),
            " ".join(str(item or "").strip() for item in list(goal_contract.get("success_criteria") or []) if str(item or "").strip()),
        ]
    ).lower()
    expected_signals = list(goal_contract.get("expected_signals") or [])
    preconditions = list(goal_contract.get("preconditions") or [])
    test_data = dict(goal_contract.get("test_data") or {})
    start_url = str(goal_contract.get("start_url") or "").strip()
    readonly_visibility_goal = bool(
        {"text_visible", "link_visible", "cta_visible"} & {item.lower() for item in expected_signals}
        or any(token in goal_text_blob for token in ("이미 보이", "already visible", "추가 조작 없이", "without interaction"))
    )
    start_rule_lines = [
        "- reread_goal_contract_before_first_action",
    ]
    if start_url:
        start_rule_lines.append(f"- prefer_current_surface_from_start_url: {start_url}")
    if expected_signals:
        start_rule_lines.append(
            "- "
            f"verify_success_contract_against_current_surface: {', '.join(expected_signals[:4])}"
        )
    if readonly_visibility_goal:
        start_rule_lines.append("- readonly_visibility_first: inspect current surface before navigation or repeated clicking")
    if preconditions:
        start_rule_lines.append(
            "- "
            f"respect_preconditions: {_truncate_text('; '.join(preconditions[:3]), 180)}"
        )
    if test_data:
        start_rule_lines.append("- only_apply_test_data_when_matching_input_surface_is_visible")
    goal_constraints = goal_contract.get("goal_constraints") or {}
    if isinstance(goal_constraints, dict):
        contract_rules: List[str] = []
        if bool(goal_constraints.get("require_no_navigation")):
            contract_rules.append("no_navigation")
        if bool(goal_constraints.get("current_view_only")):
            contract_rules.append("current_view_only")
        if bool(goal_constraints.get("require_state_change")):
            contract_rules.append("require_state_change")
        if bool(goal_constraints.get("forbid_search_action")):
            contract_rules.append("forbid_search_action")
        mutation_direction = str(goal_constraints.get("mutation_direction") or "").strip().lower()
        if mutation_direction:
            contract_rules.append(f"mutation_direction={mutation_direction}")
        if contract_rules:
            start_rule_lines.append("- respect_harness_contract: " + ", ".join(contract_rules))

        collect_min = goal_constraints.get("collect_min")
        apply_target = goal_constraints.get("apply_target")
        metric_label = str(goal_constraints.get("metric_label") or "").strip() or "count"
        collect_parts: List[str] = []
        if collect_min is not None:
            collect_parts.append(f"collect_min={int(collect_min)}{metric_label}")
        if apply_target is not None:
            collect_parts.append(f"apply_target={int(apply_target)}{metric_label}")
        if collect_parts:
            start_rule_lines.append("- enforce_goal_thresholds: " + ", ".join(collect_parts))

    memory_lines = memory_summary.splitlines()
    if memory_lines and memory_lines[0].startswith("# Session Memory"):
        memory_lines[0] = "# Session Summary"
    else:
        memory_lines.insert(0, "# Session Summary")
        memory_lines.insert(1, "")
    insert_at = len(memory_lines)
    for index, line in enumerate(memory_lines[1:], start=1):
        if line.startswith("## "):
            insert_at = index
            break
    session_lines = list(memory_lines[:insert_at])
    if session_lines and session_lines[-1].strip():
        session_lines.append("")
    refresh_trigger = str(getattr(agent, "_run_history_last_refresh_trigger", "") or "").strip() or "unknown"
    refresh_label = _refresh_label(getattr(agent, "_run_history_last_refresh_at", 0.0))
    session_lines.append("## Summary Updater")
    session_lines.append("- updater_path: side_pass")
    session_lines.append(f"- last_refresh_trigger: {refresh_trigger}")
    session_lines.append(f"- last_refresh_at: {refresh_label}")
    session_lines.append(
        "- "
        f"retrieval_included: {'true' if bool(getattr(agent, '_run_history_last_refresh_include_retrieval', False)) else 'false'}"
    )
    session_lines.append(f"- queue_state: {_background_queue_state(agent)}")
    session_lines.append(
        f"- drain_count: {int(getattr(agent, '_run_history_background_drain_count', 0) or 0)}"
    )
    last_drain_reason = str(getattr(agent, "_run_history_background_last_drain_reason", "") or "").strip()
    if last_drain_reason:
        session_lines.append(f"- last_drain_reason: {last_drain_reason}")
    startup_recovery_drained = int(getattr(agent, "_run_history_startup_recovery_drained", 0) or 0)
    startup_recovery_failed = int(getattr(agent, "_run_history_startup_recovery_failed", 0) or 0)
    startup_recovery_at = _refresh_label(getattr(agent, "_run_history_startup_recovery_at", 0.0))
    if startup_recovery_drained > 0 or startup_recovery_failed > 0:
        session_lines.append(f"- startup_recovery_drained: {startup_recovery_drained}")
        session_lines.append(f"- startup_recovery_failed: {startup_recovery_failed}")
        session_lines.append(f"- startup_recovery_at: {startup_recovery_at}")
    session_lines.append("")
    session_lines.append("## Startup Continuity Audit")
    session_lines.extend(audit_lines)
    if startup_recovery_drained > 0:
        session_lines.append(
            f"- startup_recovery_replayed_pending_updates: {startup_recovery_drained}"
        )
    if startup_recovery_failed > 0:
        session_lines.append(
            f"- startup_recovery_left_failed_updates: {startup_recovery_failed}"
        )
    session_lines.append("")
    session_lines.append("## Session Start Rules")
    session_lines.extend(start_rule_lines)
    session_lines.append("")
    session_lines.extend(memory_lines[insert_at:])
    session_summary = "\n".join(session_lines).strip()

    replay_guidance = [
        "## Replay Guidance",
        "- prioritize: Startup Continuity Audit -> Session Start Rules -> Current Objective -> Recent Attempts -> Active Blockers -> Next Best Action -> Completed Progress",
        "- use_targeted_retrieval_for: Recent Attempts, Prior Outcomes, Resume Hints, matching reason_code, matching goal_end",
    ]
    return session_summary.rstrip() + "\n\n" + "\n".join(replay_guidance)


def _write_history_document(raw_path: object, content: str) -> None:
    path_text = str(raw_path or "").strip()
    if not path_text:
        return
    path = Path(path_text)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_history_document(raw_path: object) -> str:
    path_text = str(raw_path or "").strip()
    if not path_text:
        return ""
    try:
        return Path(path_text).read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_history_json(raw_path: object) -> Dict[str, Any]:
    text = _load_history_document(raw_path)
    if not text.strip():
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _refresh_label(timestamp: object) -> str:
    refresh_timestamp = float(timestamp or 0.0)
    if refresh_timestamp <= 0:
        return "unknown"
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(refresh_timestamp))


def _invalidate_run_history_caches(agent: Any) -> None:
    agent._run_history_session_summary = ""
    agent._run_history_replay_packet_summary = ""
    agent._run_history_prompt_summary = ""
    agent._run_history_memory_summary = ""
    agent._run_history_retrieval_summary = ""
    agent._run_history_context_snapshot_cache = {}


def _snapshot_string_list(value: object, *, limit: int) -> List[str]:
    items: List[str] = []
    for raw_item in list(value or []):
        text = str(raw_item or "").strip()
        if text:
            items.append(text)
    return items[-limit:]


def _snapshot_dict_list(value: object, *, limit: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for raw_item in list(value or []):
        if isinstance(raw_item, dict):
            items.append(dict(raw_item))
    return items[-limit:]


def _load_context_snapshot_payload(agent: Any, *, expected_run_id: str = "") -> Dict[str, Any]:
    run_id = str(expected_run_id or getattr(agent, "_run_history_run_id", "") or "").strip()
    cached = getattr(agent, "_run_history_context_snapshot_cache", None)
    if isinstance(cached, dict) and cached:
        cached_run_id = str(cached.get("run_id") or "").strip()
        if not run_id or not cached_run_id or cached_run_id == run_id:
            return dict(cached)

    documents = [
        _load_history_json(getattr(agent, "_run_history_context_snapshot_path", "")),
        _load_history_json(getattr(agent, "_run_history_session_context_snapshot_path", "")),
    ]
    for payload in documents:
        if not payload:
            continue
        artifact_run_id = str(payload.get("run_id") or "").strip()
        if run_id and artifact_run_id and artifact_run_id != run_id:
            continue
        agent._run_history_context_snapshot_cache = dict(payload)
        return dict(payload)
    return {}


def _goal_contract_context(agent: Any, goal: Optional[TestGoal]) -> Dict[str, Any]:
    snapshot = _load_context_snapshot_payload(agent)
    goal_snapshot = dict(snapshot.get("goal") or {}) if isinstance(snapshot.get("goal"), dict) else {}

    def string_list_from_goal(attr_name: str) -> List[str]:
        if goal is not None:
            return [
                str(item or "").strip()
                for item in list(getattr(goal, attr_name, []) or [])
                if str(item or "").strip()
            ]
        return _snapshot_string_list(goal_snapshot.get(attr_name, []), limit=8)

    test_data = {}
    if goal is not None:
        test_data = dict(getattr(goal, "test_data", {}) or {})
    elif isinstance(goal_snapshot.get("test_data"), dict):
        test_data = dict(goal_snapshot.get("test_data") or {})

    goal_id = str(getattr(goal, "id", "") or goal_snapshot.get("id") or "").strip()
    goal_name = str(getattr(goal, "name", "") or goal_snapshot.get("name") or "").strip()
    goal_description = str(
        getattr(goal, "description", "")
        or goal_snapshot.get("description")
        or getattr(agent, "_active_goal_text", "")
        or snapshot.get("active_goal_text")
        or ""
    ).strip()
    start_url = str(getattr(goal, "start_url", "") or goal_snapshot.get("start_url") or "").strip()
    goal_constraints = getattr(agent, "_goal_constraints", {}) or {}
    if not isinstance(goal_constraints, dict) or not goal_constraints:
        goal_constraints = dict(snapshot.get("goal_constraints") or {}) if isinstance(snapshot.get("goal_constraints"), dict) else {}

    return {
        "id": goal_id,
        "name": goal_name,
        "description": goal_description,
        "success_criteria": string_list_from_goal("success_criteria"),
        "expected_signals": string_list_from_goal("expected_signals"),
        "preconditions": string_list_from_goal("preconditions"),
        "test_data": test_data,
        "start_url": start_url,
        "goal_constraints": dict(goal_constraints or {}),
    }


def _render_context_snapshot_artifact(
    agent: Any,
    goal: Optional[TestGoal],
    *,
    trigger: str,
) -> str:
    existing = _load_context_snapshot_payload(agent)
    goal_context = _goal_contract_context(agent, goal)

    recent_signal_history = _snapshot_dict_list(getattr(agent, "_recent_signal_history", []) or [], limit=6)
    if not recent_signal_history:
        recent_signal_history = _snapshot_dict_list(existing.get("recent_signal_history", []), limit=6)

    persistent_state_memory = _snapshot_dict_list(getattr(agent, "_persistent_state_memory", []) or [], limit=6)
    if not persistent_state_memory:
        persistent_state_memory = _snapshot_dict_list(existing.get("persistent_state_memory", []), limit=6)

    action_history = _snapshot_string_list(getattr(agent, "_action_history", []) or [], limit=6)
    if not action_history:
        action_history = _snapshot_string_list(existing.get("action_history", []), limit=6)

    action_feedback = _snapshot_string_list(getattr(agent, "_action_feedback", []) or [], limit=6)
    if not action_feedback:
        action_feedback = _snapshot_string_list(existing.get("action_feedback", []), limit=6)

    active_goal_text = str(
        getattr(agent, "_active_goal_text", "")
        or existing.get("active_goal_text")
        or goal_context.get("name")
        or ""
    ).strip()
    active_url = str(getattr(agent, "_active_url", "") or existing.get("active_url") or "").strip()
    active_snapshot_id = str(
        getattr(agent, "_active_snapshot_id", "") or existing.get("active_snapshot_id") or ""
    ).strip()

    payload = {
        "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown",
        "session_key": str(getattr(agent, "_run_history_session_key", "") or "").strip() or "unknown",
        "trigger": str(trigger or "").strip() or "unspecified",
        "updated_at": _refresh_label(time.time()),
        "goal": {
            "id": str(goal_context.get("id") or "").strip(),
            "name": str(goal_context.get("name") or "").strip(),
            "description": str(goal_context.get("description") or "").strip(),
            "success_criteria": list(goal_context.get("success_criteria") or []),
            "expected_signals": list(goal_context.get("expected_signals") or []),
            "preconditions": list(goal_context.get("preconditions") or []),
            "test_data": dict(goal_context.get("test_data") or {}),
            "start_url": str(goal_context.get("start_url") or "").strip(),
        },
        "goal_constraints": dict(goal_context.get("goal_constraints") or {}),
        "active_goal_text": active_goal_text,
        "active_url": active_url,
        "active_snapshot_id": active_snapshot_id,
        "recent_signal_history": recent_signal_history,
        "persistent_state_memory": persistent_state_memory,
        "action_history": action_history,
        "action_feedback": action_feedback,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def refresh_run_history_context_snapshot_artifacts(
    agent: Any,
    goal: Optional[TestGoal] = None,
    *,
    trigger: str = "unspecified",
) -> Dict[str, str]:
    if not _history_enabled(agent):
        return {}
    artifact = _render_context_snapshot_artifact(agent, goal, trigger=trigger)
    _write_history_document(getattr(agent, "_run_history_context_snapshot_path", ""), artifact)
    _write_history_document(getattr(agent, "_run_history_session_context_snapshot_path", ""), artifact)
    agent._run_history_context_snapshot_cache = _load_history_json(
        getattr(agent, "_run_history_context_snapshot_path", "")
    ) or _load_history_json(getattr(agent, "_run_history_session_context_snapshot_path", ""))
    return {"context_snapshot": artifact}


def _restore_context_snapshot_state_from_artifact(agent: Any) -> None:
    payload = _load_context_snapshot_payload(agent)
    if not payload:
        return
    goal_constraints = payload.get("goal_constraints")
    if isinstance(goal_constraints, dict):
        agent._goal_constraints = dict(goal_constraints)
    agent._active_goal_text = str(payload.get("active_goal_text") or "").strip()
    agent._active_url = str(payload.get("active_url") or "").strip()
    agent._active_snapshot_id = str(payload.get("active_snapshot_id") or "").strip()
    agent._recent_signal_history = _snapshot_dict_list(payload.get("recent_signal_history", []), limit=6)
    agent._persistent_state_memory = _snapshot_dict_list(payload.get("persistent_state_memory", []), limit=6)
    agent._action_history = _snapshot_string_list(payload.get("action_history", []), limit=6)
    agent._action_feedback = _snapshot_string_list(payload.get("action_feedback", []), limit=6)


def _background_pending_artifacts(include_retrieval: bool) -> List[str]:
    artifacts = ["context_snapshot", "summary", "compact", "memory", "session_summary", "replay", "updater"]
    if include_retrieval:
        artifacts.extend(["retrieval", "retrieval_index"])
    return artifacts


def _background_subprocess_enabled() -> bool:
    raw = str(os.getenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "auto") or "auto").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return False
    if raw in {"1", "true", "on", "yes", "enabled"}:
        return True
    return not bool(os.getenv("PYTEST_CURRENT_TEST"))


def _background_updater_script_path() -> Path:
    return _REPO_ROOT / "scripts" / "run_history_background_updater.py"


def _background_updater_popen_kwargs() -> Dict[str, Any]:
    if os.name == "nt":
        return {}
    return {"start_new_session": True}


def _background_lock_lease_seconds() -> float:
    raw = str(
        os.getenv(
            "GAIA_RUN_HISTORY_BACKGROUND_LOCK_LEASE_SEC",
            str(_DEFAULT_BACKGROUND_LOCK_LEASE_SEC),
        )
        or str(_DEFAULT_BACKGROUND_LOCK_LEASE_SEC)
    ).strip()
    try:
        return max(1.0, float(raw))
    except Exception:
        return _DEFAULT_BACKGROUND_LOCK_LEASE_SEC


def _load_updater_lock_payload(agent: Any) -> Dict[str, Any]:
    run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    documents = [
        _load_history_json(getattr(agent, "_run_history_updater_lock_path", "")),
        _load_history_json(getattr(agent, "_run_history_session_updater_lock_path", "")),
    ]
    for payload in documents:
        if not payload:
            continue
        artifact_run_id = str(payload.get("run_id") or "").strip()
        if run_id and artifact_run_id and artifact_run_id != run_id:
            continue
        return payload
    return {}


def _is_active_background_lock(payload: Dict[str, Any], *, current_pid: int = 0) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"launching", "running"}:
        return False
    lock_pid = int(payload.get("pid") or 0)
    if current_pid > 0 and lock_pid == current_pid:
        return False
    lease_expires_at = float(payload.get("lease_expires_at_ts") or 0.0)
    return lease_expires_at > float(time.time())


def _background_lock_state(agent: Any) -> str:
    payload = _load_updater_lock_payload(agent)
    if not payload:
        return "none"
    if _is_active_background_lock(payload):
        return str(payload.get("status") or "").strip() or "active"
    status = str(payload.get("status") or "").strip().lower()
    if status in {"launching", "running"}:
        return "stale"
    return status or "idle"


def _write_updater_lock_artifact(
    agent: Any,
    *,
    status: str,
    owner: str,
    trigger: str,
    pid: int = 0,
    reason: str = "",
    lease_seconds: float = 0.0,
) -> None:
    now = float(time.time())
    normalized_status = str(status or "").strip() or "idle"
    normalized_owner = str(owner or "").strip() or "unknown"
    normalized_trigger = str(trigger or "").strip() or "unspecified"
    normalized_reason = str(reason or "").strip()
    normalized_pid = int(pid or 0)
    lease = max(0.0, float(lease_seconds or 0.0))
    payload = {
        "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown",
        "status": normalized_status,
        "owner": normalized_owner,
        "trigger": normalized_trigger,
        "reason": normalized_reason,
        "pid": normalized_pid,
        "updated_at": _refresh_label(now),
        "updated_at_ts": now,
        "lease_expires_at": _refresh_label(now + lease) if lease > 0 else "expired",
        "lease_expires_at_ts": now + lease if lease > 0 else 0.0,
    }
    artifact = json.dumps(payload, ensure_ascii=False, indent=2)
    _write_history_document(getattr(agent, "_run_history_updater_lock_path", ""), artifact)
    _write_history_document(getattr(agent, "_run_history_session_updater_lock_path", ""), artifact)


def _launch_run_history_background_subprocess(agent: Any, *, trigger: str) -> None:
    if not _history_enabled(agent) or not _background_subprocess_enabled():
        return
    run_dir = str(getattr(agent, "_run_history_dir", "") or "").strip()
    if not run_dir:
        return
    if bool(getattr(agent, "_run_history_background_active", False)):
        return
    if not list(getattr(agent, "_run_history_background_queue_triggers", []) or []):
        return
    now = float(time.time())
    last_launch_at = float(getattr(agent, "_run_history_background_last_launch_at", 0.0) or 0.0)
    if last_launch_at > 0 and now - last_launch_at < 0.5:
        return
    existing_lock = _load_updater_lock_payload(agent)
    if _is_active_background_lock(existing_lock):
        agent._run_history_background_last_launch_status = "skipped_active_lock"
        agent._run_history_background_last_launch_trigger = str(trigger or "").strip() or "unspecified"
        agent._run_history_background_last_launch_at = now
        agent._run_history_background_last_launch_pid = int(existing_lock.get("pid") or 0)
        return
    script_path = _background_updater_script_path()
    if not script_path.exists():
        agent._run_history_background_last_launch_status = "missing_script"
        agent._run_history_background_last_launch_trigger = str(trigger or "").strip() or "unspecified"
        agent._run_history_background_last_launch_at = now
        return
    command = [
        sys.executable,
        str(script_path),
        "--run-dir",
        run_dir,
        "--drain-reason",
        f"background_subprocess:{str(trigger or '').strip() or 'unspecified'}",
    ]
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(_REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_background_updater_popen_kwargs(),
        )
    except Exception as exc:
        agent._run_history_background_last_launch_status = f"error:{exc.__class__.__name__}"
        agent._run_history_background_last_launch_trigger = str(trigger or "").strip() or "unspecified"
        agent._run_history_background_last_launch_at = now
        _write_updater_lock_artifact(
            agent,
            status="error",
            owner="background_launcher",
            trigger=trigger,
            reason=f"spawn_error:{exc.__class__.__name__}",
            lease_seconds=0.0,
        )
        return
    agent._run_history_background_last_launch_status = "spawned"
    agent._run_history_background_last_launch_trigger = str(trigger or "").strip() or "unspecified"
    agent._run_history_background_last_launch_at = now
    agent._run_history_background_last_launch_pid = int(getattr(proc, "pid", 0) or 0)
    agent._run_history_background_launch_count = int(
        getattr(agent, "_run_history_background_launch_count", 0) or 0
    ) + 1
    _write_updater_lock_artifact(
        agent,
        status="launching",
        owner="background_launcher",
        trigger=trigger,
        pid=agent._run_history_background_last_launch_pid,
        reason="spawned",
        lease_seconds=_background_lock_lease_seconds(),
    )


def _render_updater_queue_artifact(agent: Any) -> str:
    payload = {
        "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown",
        "queue_state": _background_queue_state(agent),
        "queued_triggers": list(getattr(agent, "_run_history_background_queue_triggers", []) or []),
        "pending_include_retrieval": bool(
            getattr(agent, "_run_history_background_pending_include_retrieval", False)
        ),
        "pending_artifacts": list(getattr(agent, "_run_history_background_pending_artifacts", []) or []),
        "queue_since": _refresh_label(getattr(agent, "_run_history_background_queue_since", 0.0)),
        "queue_since_ts": float(getattr(agent, "_run_history_background_queue_since", 0.0) or 0.0),
        "last_queued_at": _refresh_label(getattr(agent, "_run_history_background_last_queued_at", 0.0)),
        "last_queued_at_ts": float(getattr(agent, "_run_history_background_last_queued_at", 0.0) or 0.0),
        "last_drained_at": _refresh_label(getattr(agent, "_run_history_background_last_drained_at", 0.0)),
        "last_drained_at_ts": float(getattr(agent, "_run_history_background_last_drained_at", 0.0) or 0.0),
        "drain_count": int(getattr(agent, "_run_history_background_drain_count", 0) or 0),
        "last_drain_reason": str(getattr(agent, "_run_history_background_last_drain_reason", "") or "").strip(),
        "last_updated_artifacts": list(getattr(agent, "_run_history_background_last_updated_artifacts", []) or []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _write_updater_queue_artifact(agent: Any) -> None:
    artifact = _render_updater_queue_artifact(agent)
    _write_history_document(getattr(agent, "_run_history_updater_queue_path", ""), artifact)
    _write_history_document(getattr(agent, "_run_history_session_updater_queue_path", ""), artifact)


def _restore_background_queue_state_from_artifact(agent: Any) -> None:
    if bool(getattr(agent, "_run_history_background_active", False)):
        return
    if list(getattr(agent, "_run_history_background_queue_triggers", []) or []):
        return
    run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    documents = [
        _load_history_document(getattr(agent, "_run_history_updater_queue_path", "")),
        _load_history_document(getattr(agent, "_run_history_session_updater_queue_path", "")),
    ]
    for document in documents:
        text = str(document or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        artifact_run_id = str(payload.get("run_id") or "").strip()
        if run_id and artifact_run_id and artifact_run_id != run_id:
            continue
        queued_triggers = payload.get("queued_triggers")
        pending_artifacts = payload.get("pending_artifacts")
        if not isinstance(queued_triggers, list) or not queued_triggers:
            continue
        agent._run_history_background_queue_triggers = [
            str(item or "").strip() for item in queued_triggers if str(item or "").strip()
        ][-8:]
        agent._run_history_background_pending_include_retrieval = bool(
            payload.get("pending_include_retrieval", False)
        )
        agent._run_history_background_pending_artifacts = [
            str(item or "").strip()
            for item in list(pending_artifacts or [])
            if str(item or "").strip()
        ]
        if float(getattr(agent, "_run_history_background_queue_since", 0.0) or 0.0) <= 0:
            agent._run_history_background_queue_since = float(getattr(agent, "_run_history_last_refresh_at", 0.0) or 0.0)
        return


def _background_queue_state(agent: Any) -> str:
    pending = list(getattr(agent, "_run_history_background_queue_triggers", []) or [])
    if bool(getattr(agent, "_run_history_background_active", False)):
        return "draining" if pending else "idle"
    if pending:
        return "pending"
    if float(getattr(agent, "_run_history_background_last_drained_at", 0.0) or 0.0) > 0:
        return "idle"
    return "empty"


def _schedule_run_history_background_update(
    agent: Any,
    *,
    trigger: str,
    include_retrieval: bool,
) -> None:
    if not _history_enabled(agent):
        return
    now = float(time.time())
    normalized_trigger = str(trigger or "").strip() or "unspecified"
    queue = list(getattr(agent, "_run_history_background_queue_triggers", []) or [])
    queue.append(normalized_trigger)
    agent._run_history_background_queue_triggers = queue[-8:]
    agent._run_history_background_last_queued_at = now
    if float(getattr(agent, "_run_history_background_queue_since", 0.0) or 0.0) <= 0:
        agent._run_history_background_queue_since = now
    agent._run_history_background_pending_include_retrieval = bool(
        bool(getattr(agent, "_run_history_background_pending_include_retrieval", False)) or include_retrieval
    )
    pending_artifacts = set(getattr(agent, "_run_history_background_pending_artifacts", []) or [])
    pending_artifacts.update(_background_pending_artifacts(include_retrieval))
    agent._run_history_background_pending_artifacts = sorted(str(item) for item in pending_artifacts if str(item).strip())
    _write_updater_queue_artifact(agent)
    _invalidate_run_history_caches(agent)


def _should_drain_background_update(trigger: str) -> bool:
    normalized = str(trigger or "").strip() or "unspecified"
    if normalized in _BACKGROUND_INLINE_TRIGGERS:
        return True
    if normalized in _BACKGROUND_DEFERRED_TRIGGERS:
        return False
    return True


def refresh_run_history_artifacts(agent: Any, goal: Optional[TestGoal] = None) -> Dict[str, str]:
    if not _history_enabled(agent):
        return {}
    events = _load_events(agent)
    summary = _render_summary(agent, events, goal)
    compact_summary = _render_compact_summary(agent, events, goal)
    memory_summary = _render_memory_summary(agent, events, goal)
    session_summary = _render_session_summary(agent, events, goal)
    agent._run_history_replay_packet_summary = ""
    agent._run_history_retrieval_summary = ""
    _write_history_document(getattr(agent, "_run_history_state_path", ""), summary)
    _write_history_document(getattr(agent, "_run_history_session_state_path", ""), summary)
    _write_history_document(getattr(agent, "_run_history_prompt_path", ""), compact_summary)
    _write_history_document(getattr(agent, "_run_history_session_prompt_path", ""), compact_summary)
    _write_history_document(getattr(agent, "_run_history_memory_path", ""), memory_summary)
    _write_history_document(getattr(agent, "_run_history_session_memory_path", ""), memory_summary)
    _write_history_document(getattr(agent, "_run_history_summary_path", ""), session_summary)
    _write_history_document(getattr(agent, "_run_history_session_summary_path", ""), session_summary)
    agent._run_history_session_summary = session_summary
    agent._run_history_prompt_summary = compact_summary
    agent._run_history_memory_summary = memory_summary
    return {
        "state": summary,
        "compact": compact_summary,
        "memory": memory_summary,
        "session_summary": session_summary,
    }


def _render_replay_artifact(agent: Any, replay_packet: str) -> str:
    refresh_trigger = str(getattr(agent, "_run_history_last_replay_refresh_trigger", "") or "").strip() or "unspecified"
    refresh_label = _refresh_label(getattr(agent, "_run_history_last_replay_refresh_at", 0.0))
    include_retrieval = bool(getattr(agent, "_run_history_last_replay_refresh_include_retrieval", False))
    run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown"
    boundary_mode = "unknown"
    for raw_line in str(replay_packet or "").splitlines():
        line = str(raw_line or "").strip()
        if line.startswith("- mode: "):
            boundary_mode = line.split(": ", 1)[-1].strip() or "unknown"
            break

    lines = [
        "# Replay Artifact",
        "",
        "## Replay Updater",
        "- updater_path: replay_side_pass",
        f"- run_id: {run_id}",
        f"- last_refresh_trigger: {refresh_trigger}",
        f"- last_refresh_at: {refresh_label}",
        f"- retrieval_included: {'true' if include_retrieval else 'false'}",
        f"- boundary_mode: {boundary_mode}",
        "- replay_priority: replay boundary -> resume checklist -> recent attempt digest -> session summary -> memory replay -> retrieval -> current run tail",
    ]
    packet_text = str(replay_packet or "").strip()
    if packet_text:
        lines.extend(["", packet_text])
    return "\n".join(lines).strip()


def _render_retrieval_artifact(agent: Any, retrieval_summary: str) -> str:
    refresh_trigger = str(getattr(agent, "_run_history_last_retrieval_refresh_trigger", "") or "").strip() or "unspecified"
    refresh_label = _refresh_label(getattr(agent, "_run_history_last_retrieval_refresh_at", 0.0))
    run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown"
    hit_count = len([line for line in str(retrieval_summary or "").splitlines() if str(line or "").startswith("- ")])
    lines = [
        "# Retrieval Artifact",
        "",
        "## Retrieval Updater",
        "- updater_path: retrieval_side_pass",
        f"- run_id: {run_id}",
        f"- last_refresh_trigger: {refresh_trigger}",
        f"- last_refresh_at: {refresh_label}",
        f"- hit_count: {hit_count}",
        "- retrieval_priority: Recent Attempts -> Resume Hints -> goal_end/step_outcome -> assistant transcript",
    ]
    summary_text = str(retrieval_summary or "").strip()
    if summary_text:
        lines.extend(["", summary_text])
    else:
        lines.extend(["", "## 관련 세션 기억 검색 결과", "- none"])
    return "\n".join(lines).strip()


def _render_updater_artifact(agent: Any, *, trigger: str, include_retrieval: bool, updated_artifacts: List[str]) -> str:
    queue_state = _background_queue_state(agent)
    pending_triggers = list(getattr(agent, "_run_history_background_queue_triggers", []) or [])
    pending_artifacts = list(getattr(agent, "_run_history_background_pending_artifacts", []) or [])
    drain_count = int(getattr(agent, "_run_history_background_drain_count", 0) or 0)
    last_drain_reason = str(getattr(agent, "_run_history_background_last_drain_reason", "") or "").strip() or "unknown"
    lines = [
        "# Run History Updater",
        "",
        "## Updater Mode",
        "- mode: queued_background_simulation",
        "- updater_path: background_updater_pass",
        f"- trigger: {str(trigger or '').strip() or 'unspecified'}",
        f"- include_retrieval: {'true' if include_retrieval else 'false'}",
        f"- deferred_flush: {'true' if queue_state == 'pending' else 'false'}",
        f"- updated_artifacts: {', '.join(updated_artifacts) if updated_artifacts else 'none'}",
        "",
        "## Updater Queue",
        f"- queue_state: {queue_state}",
        f"- queue_depth: {len(pending_triggers)}",
        f"- queued_triggers: {', '.join(pending_triggers) if pending_triggers else 'none'}",
        f"- pending_artifacts: {', '.join(pending_artifacts) if pending_artifacts else 'none'}",
        f"- queue_since: {_refresh_label(getattr(agent, '_run_history_background_queue_since', 0.0))}",
        f"- last_queued_at: {_refresh_label(getattr(agent, '_run_history_background_last_queued_at', 0.0))}",
        f"- last_drained_at: {_refresh_label(getattr(agent, '_run_history_background_last_drained_at', 0.0))}",
        f"- drain_count: {drain_count}",
        f"- last_drain_reason: {last_drain_reason}",
        f"- background_launch_status: {str(getattr(agent, '_run_history_background_last_launch_status', '') or '').strip() or 'none'}",
        f"- background_launch_trigger: {str(getattr(agent, '_run_history_background_last_launch_trigger', '') or '').strip() or 'none'}",
        f"- background_launch_at: {_refresh_label(getattr(agent, '_run_history_background_last_launch_at', 0.0))}",
        f"- background_launch_pid: {int(getattr(agent, '_run_history_background_last_launch_pid', 0) or 0)}",
        f"- background_launch_count: {int(getattr(agent, '_run_history_background_launch_count', 0) or 0)}",
        f"- background_lock_state: {_background_lock_state(agent)}",
        "",
        "## Artifact Refresh Status",
        "- "
        f"context_snapshot: run_id={str(_load_context_snapshot_payload(agent).get('run_id') or '').strip() or 'unknown'}",
        "- "
        f"summary: trigger={str(getattr(agent, '_run_history_last_refresh_trigger', '') or '').strip() or 'unknown'} | "
        f"at={_refresh_label(getattr(agent, '_run_history_last_refresh_at', 0.0))}",
        "- "
        f"retrieval: trigger={str(getattr(agent, '_run_history_last_retrieval_refresh_trigger', '') or '').strip() or 'unknown'} | "
        f"at={_refresh_label(getattr(agent, '_run_history_last_retrieval_refresh_at', 0.0))}",
        "- "
        f"replay: trigger={str(getattr(agent, '_run_history_last_replay_refresh_trigger', '') or '').strip() or 'unknown'} | "
        f"at={_refresh_label(getattr(agent, '_run_history_last_replay_refresh_at', 0.0))}",
    ]
    return "\n".join(lines).strip()


def _extract_retrieval_summary_from_artifact(document: str, *, expected_run_id: str = "") -> str:
    text = str(document or "").strip()
    if not text:
        return ""
    expected = str(expected_run_id or "").strip()
    if expected:
        run_id_line = next(
            (
                str(raw_line or "").strip()
                for raw_line in text.splitlines()
                if str(raw_line or "").strip().startswith("- run_id: ")
            ),
            "",
        )
        artifact_run_id = run_id_line.split(": ", 1)[-1].strip() if run_id_line else ""
        if artifact_run_id != expected:
            return ""
    marker = "## 관련 세션 기억 검색 결과"
    marker_index = text.find(marker)
    if marker_index < 0:
        return ""
    summary = text[marker_index:].strip()
    if summary == f"{marker}\n- none" or summary == marker:
        return ""
    return summary


def _extract_replay_packet_from_artifact(document: str, *, expected_run_id: str = "") -> str:
    text = str(document or "").strip()
    if not text:
        return ""
    expected = str(expected_run_id or "").strip()
    if expected:
        run_id_line = next(
            (
                str(raw_line or "").strip()
                for raw_line in text.splitlines()
                if str(raw_line or "").strip().startswith("- run_id: ")
            ),
            "",
        )
        artifact_run_id = run_id_line.split(": ", 1)[-1].strip() if run_id_line else ""
        if artifact_run_id != expected:
            return ""
    marker = "## 세션 continuity replay packet"
    marker_index = text.find(marker)
    if marker_index < 0:
        return ""
    return text[marker_index:].strip()


def refresh_run_history_retrieval_artifacts(
    agent: Any,
    goal: Optional[TestGoal] = None,
    *,
    trigger: str = "unspecified",
) -> Dict[str, str]:
    if not _history_enabled(agent):
        return {}
    agent._run_history_last_retrieval_refresh_trigger = str(trigger or "").strip() or "unspecified"
    agent._run_history_last_retrieval_refresh_at = float(time.time())
    agent._run_history_retrieval_summary = ""
    index_entries = _build_retrieval_index_entries(agent, goal=goal)
    index_artifact = _render_retrieval_index_artifact(agent, index_entries, trigger=trigger)
    _write_history_document(getattr(agent, "_run_history_retrieval_index_path", ""), index_artifact)
    _write_history_document(getattr(agent, "_run_history_session_retrieval_index_path", ""), index_artifact)
    retrieval_summary = _score_retrieval_entries(agent, goal, index_entries)
    retrieval_artifact = _render_retrieval_artifact(agent, retrieval_summary)
    _write_history_document(getattr(agent, "_run_history_retrieval_path", ""), retrieval_artifact)
    _write_history_document(getattr(agent, "_run_history_session_retrieval_path", ""), retrieval_artifact)
    return {
        "retrieval": retrieval_summary,
        "retrieval_artifact": retrieval_artifact,
        "retrieval_index_artifact": index_artifact,
    }


def refresh_run_history_replay_artifacts(
    agent: Any,
    goal: Optional[TestGoal] = None,
    *,
    include_retrieval: bool = True,
    trigger: str = "unspecified",
) -> Dict[str, str]:
    if not _history_enabled(agent):
        return {}
    agent._run_history_last_replay_refresh_trigger = str(trigger or "").strip() or "unspecified"
    agent._run_history_last_replay_refresh_at = float(time.time())
    agent._run_history_last_replay_refresh_include_retrieval = bool(include_retrieval)
    agent._run_history_replay_packet_summary = ""
    replay_packet = build_run_history_replay_packet_context(agent, goal=goal)
    replay_artifact = _render_replay_artifact(agent, replay_packet)
    _write_history_document(getattr(agent, "_run_history_replay_path", ""), replay_artifact)
    _write_history_document(getattr(agent, "_run_history_session_replay_path", ""), replay_artifact)
    return {"replay": replay_artifact, "replay_packet": replay_packet}


def refresh_run_history_updater_artifacts(
    agent: Any,
    *,
    trigger: str,
    include_retrieval: bool,
    updated_artifacts: List[str],
) -> Dict[str, str]:
    if not _history_enabled(agent):
        return {}
    updater_artifact = _render_updater_artifact(
        agent,
        trigger=trigger,
        include_retrieval=include_retrieval,
        updated_artifacts=updated_artifacts,
    )
    _write_history_document(getattr(agent, "_run_history_updater_path", ""), updater_artifact)
    _write_history_document(getattr(agent, "_run_history_session_updater_path", ""), updater_artifact)
    _write_updater_queue_artifact(agent)
    return {"updater": updater_artifact}


def _drain_run_history_background_update(
    agent: Any,
    *,
    goal: Optional[TestGoal] = None,
    drain_reason: str,
) -> Dict[str, str]:
    if not _history_enabled(agent):
        return {}
    if bool(getattr(agent, "_run_history_background_active", False)):
        return {}
    pending_triggers = list(getattr(agent, "_run_history_background_queue_triggers", []) or [])
    if not pending_triggers:
        agent._run_history_background_pending_artifacts = []
        _write_updater_queue_artifact(agent)
        return refresh_run_history_updater_artifacts(
            agent,
            trigger=str(drain_reason or "").strip() or "unspecified",
            include_retrieval=bool(getattr(agent, "_run_history_background_pending_include_retrieval", False)),
            updated_artifacts=[],
        )

    include_retrieval = bool(getattr(agent, "_run_history_background_pending_include_retrieval", False))
    trigger = pending_triggers[-1]
    agent._run_history_background_active = True
    agent._run_history_last_refresh_trigger = str(trigger or "").strip() or "unspecified"
    agent._run_history_last_refresh_at = float(time.time())
    agent._run_history_last_refresh_include_retrieval = include_retrieval
    agent._run_history_background_queue_triggers = []
    agent._run_history_background_queue_since = 0.0
    agent._run_history_background_pending_artifacts = []
    agent._run_history_background_pending_include_retrieval = False
    try:
        refresh_run_history_context_snapshot_artifacts(agent, goal=goal, trigger=trigger)
        artifacts = refresh_run_history_artifacts(agent, goal=goal)
        if not artifacts:
            return {}
        updated_artifacts = ["context_snapshot", "summary", "compact", "memory", "session_summary"]
        if include_retrieval:
            artifacts.update(
                refresh_run_history_retrieval_artifacts(
                    agent,
                    goal=goal,
                    trigger=trigger,
                )
            )
            updated_artifacts.extend(["retrieval", "retrieval_index"])
        artifacts.update(
            refresh_run_history_replay_artifacts(
                agent,
                goal=goal,
                include_retrieval=include_retrieval,
                trigger=trigger,
            )
        )
        updated_artifacts.append("replay")
        agent._run_history_background_last_drained_at = float(time.time())
        agent._run_history_background_drain_count = int(
            getattr(agent, "_run_history_background_drain_count", 0) or 0
        ) + 1
        agent._run_history_background_last_drain_reason = str(drain_reason or "").strip() or "unspecified"
        agent._run_history_background_last_updated_artifacts = list(updated_artifacts)
        agent._run_history_background_active = False
        _write_updater_queue_artifact(agent)
        artifacts.update(
            refresh_run_history_updater_artifacts(
                agent,
                trigger=trigger,
                include_retrieval=include_retrieval,
                updated_artifacts=updated_artifacts,
            )
        )
        return artifacts
    finally:
        agent._run_history_background_active = False


def _flush_pending_run_history_background_update(
    agent: Any,
    *,
    goal: Optional[TestGoal] = None,
    drain_reason: str,
) -> None:
    if not _history_enabled(agent):
        return
    _restore_background_queue_state_from_artifact(agent)
    if bool(getattr(agent, "_run_history_background_active", False)):
        return
    if not list(getattr(agent, "_run_history_background_queue_triggers", []) or []):
        return
    _drain_run_history_background_update(agent, goal=goal, drain_reason=drain_reason)


def _build_run_history_artifact_only_agent(run_dir: str) -> Any:
    run_path = Path(str(run_dir or "").strip())
    if not str(run_path):
        raise ValueError("run_dir is required")
    session_dir = run_path.parent.parent if run_path.parent.name == "runs" else run_path.parent
    if not session_dir.name:
        raise ValueError(f"invalid run_dir: {run_dir}")

    agent = SimpleNamespace()
    agent.session_id = "artifact-only-run-history"
    agent._run_history_enabled = True
    agent._run_history_run_id = str(run_path.name or "").strip()
    agent._run_history_dir = str(run_path)
    agent._run_history_events_path = str(run_path / "events.jsonl")
    agent._run_history_state_path = str(run_path / "state.md")
    agent._run_history_summary_path = str(run_path / "summary.md")
    agent._run_history_updater_path = str(run_path / "updater.md")
    agent._run_history_updater_queue_path = str(run_path / "updater_queue.json")
    agent._run_history_updater_lock_path = str(run_path / "updater_lock.json")
    agent._run_history_replay_path = str(run_path / "replay.md")
    agent._run_history_retrieval_path = str(run_path / "retrieval.md")
    agent._run_history_retrieval_index_path = str(run_path / "retrieval_index.json")
    agent._run_history_context_snapshot_path = str(run_path / "context_snapshot.json")
    agent._run_history_prompt_path = str(run_path / "compact.md")
    agent._run_history_memory_path = str(run_path / "MEMORY.md")
    agent._run_history_transcript_path = str(run_path / "transcript.jsonl")
    agent._run_history_session_key = str(session_dir.name or "").strip()
    agent._run_history_session_dir = str(session_dir)
    agent._run_history_session_events_path = str(session_dir / "events.jsonl")
    agent._run_history_session_state_path = str(session_dir / "state.md")
    agent._run_history_session_summary_path = str(session_dir / "summary.md")
    agent._run_history_session_updater_path = str(session_dir / "updater.md")
    agent._run_history_session_updater_queue_path = str(session_dir / "updater_queue.json")
    agent._run_history_session_updater_lock_path = str(session_dir / "updater_lock.json")
    agent._run_history_session_replay_path = str(session_dir / "replay.md")
    agent._run_history_session_retrieval_path = str(session_dir / "retrieval.md")
    agent._run_history_session_retrieval_index_path = str(session_dir / "retrieval_index.json")
    agent._run_history_session_context_snapshot_path = str(session_dir / "context_snapshot.json")
    agent._run_history_session_prompt_path = str(session_dir / "compact.md")
    agent._run_history_session_memory_path = str(session_dir / "MEMORY.md")
    agent._run_history_session_transcript_path = str(session_dir / "transcript.jsonl")
    agent._run_history_last_refresh_trigger = ""
    agent._run_history_last_refresh_at = 0.0
    agent._run_history_last_refresh_include_retrieval = False
    agent._run_history_last_retrieval_refresh_trigger = ""
    agent._run_history_last_retrieval_refresh_at = 0.0
    agent._run_history_last_replay_refresh_trigger = ""
    agent._run_history_last_replay_refresh_at = 0.0
    agent._run_history_last_replay_refresh_include_retrieval = False
    agent._run_history_session_summary = ""
    agent._run_history_replay_packet_summary = ""
    agent._run_history_prompt_summary = ""
    agent._run_history_memory_summary = ""
    agent._run_history_retrieval_summary = ""
    agent._run_history_context_snapshot_cache = {}
    agent._run_history_background_queue_triggers = []
    agent._run_history_background_queue_since = 0.0
    agent._run_history_background_last_queued_at = 0.0
    agent._run_history_background_last_drained_at = 0.0
    agent._run_history_background_drain_count = 0
    agent._run_history_background_last_drain_reason = ""
    agent._run_history_background_last_launch_status = ""
    agent._run_history_background_last_launch_trigger = ""
    agent._run_history_background_last_launch_at = 0.0
    agent._run_history_background_last_launch_pid = 0
    agent._run_history_background_launch_count = 0
    agent._run_history_startup_recovery_drained = 0
    agent._run_history_startup_recovery_failed = 0
    agent._run_history_startup_recovery_at = 0.0
    agent._run_history_background_pending_include_retrieval = False
    agent._run_history_background_pending_artifacts = []
    agent._run_history_background_last_updated_artifacts = []
    agent._run_history_background_active = False
    agent._recent_signal_history = []
    agent._persistent_state_memory = []
    agent._goal_constraints = {}
    agent._active_goal_text = ""
    agent._active_url = ""
    agent._active_snapshot_id = ""
    agent._action_history = []
    agent._action_feedback = []
    return agent


def run_history_artifact_only_updater_pass(
    run_dir: str,
    *,
    drain_reason: str = "artifact_only_queue_drain",
    worker_pid: int = 0,
) -> Dict[str, str]:
    agent = _build_run_history_artifact_only_agent(run_dir)
    _restore_context_snapshot_state_from_artifact(agent)
    _restore_background_queue_state_from_artifact(agent)
    current_pid = int(worker_pid or 0)
    existing_lock = _load_updater_lock_payload(agent)
    if _is_active_background_lock(existing_lock, current_pid=current_pid):
        return {"skipped": "active_lock"}
    _write_updater_lock_artifact(
        agent,
        status="running",
        owner="artifact_only_worker",
        trigger=drain_reason,
        pid=current_pid,
        reason="worker_started",
        lease_seconds=_background_lock_lease_seconds(),
    )
    try:
        artifacts = _drain_run_history_background_update(
            agent,
            goal=None,
            drain_reason=str(drain_reason or "").strip() or "artifact_only_queue_drain",
        )
        _write_updater_lock_artifact(
            agent,
            status="idle",
            owner="artifact_only_worker",
            trigger=drain_reason,
            pid=current_pid,
            reason="worker_completed",
            lease_seconds=0.0,
        )
        refresh_run_history_updater_artifacts(
            agent,
            trigger=str(drain_reason or "").strip() or "artifact_only_queue_drain",
            include_retrieval=bool(getattr(agent, "_run_history_last_refresh_include_retrieval", False)),
            updated_artifacts=list(getattr(agent, "_run_history_background_last_updated_artifacts", []) or []),
        )
        return artifacts
    except Exception as exc:
        _write_updater_lock_artifact(
            agent,
            status="failed",
            owner="artifact_only_worker",
            trigger=drain_reason,
            pid=current_pid,
            reason=exc.__class__.__name__,
            lease_seconds=0.0,
        )
        refresh_run_history_updater_artifacts(
            agent,
            trigger=str(drain_reason or "").strip() or "artifact_only_queue_drain",
            include_retrieval=bool(getattr(agent, "_run_history_last_refresh_include_retrieval", False)),
            updated_artifacts=list(getattr(agent, "_run_history_background_last_updated_artifacts", []) or []),
        )
        raise


def list_run_history_pending_updates(
    *,
    history_root: str = "",
    session_key: str = "",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    root = Path(str(history_root or "").strip()).expanduser() if str(history_root or "").strip() else _history_root()
    sessions_root = root / "sessions"
    if not sessions_root.exists():
        return []
    session_filter = str(session_key or "").strip()

    items: List[Dict[str, Any]] = []
    for queue_path in sessions_root.glob("*/runs/*/updater_queue.json"):
        payload = _load_history_json(queue_path)
        if not payload:
            continue
        queued_triggers = [
            str(item or "").strip()
            for item in list(payload.get("queued_triggers") or [])
            if str(item or "").strip()
        ]
        queue_state = str(payload.get("queue_state") or "").strip() or "unknown"
        if not queued_triggers and queue_state != "pending":
            continue
        run_dir = queue_path.parent
        session_dir = run_dir.parent.parent if run_dir.parent.name == "runs" else run_dir.parent
        current_session_key = str(session_dir.name or "").strip()
        if session_filter and current_session_key != session_filter:
            continue
        run_id = str(payload.get("run_id") or run_dir.name or "").strip() or "unknown"
        lock_payload = {}
        try:
            lock_payload = json.loads((run_dir / "updater_lock.json").read_text(encoding="utf-8"))
            if not isinstance(lock_payload, dict):
                lock_payload = {}
        except Exception:
            lock_payload = {}
        sort_ts = float(payload.get("queue_since_ts") or 0.0) or float(payload.get("last_queued_at_ts") or 0.0)
        if sort_ts <= 0:
            try:
                sort_ts = float(queue_path.stat().st_mtime)
            except Exception:
                sort_ts = 0.0
        lock_state = "none"
        if lock_payload:
            if _is_active_background_lock(lock_payload):
                lock_state = str(lock_payload.get("status") or "").strip() or "active"
            else:
                lock_state = str(lock_payload.get("status") or "").strip().lower() or "stale"
                if lock_state in {"launching", "running"}:
                    lock_state = "stale"
        items.append(
            {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "session_key": current_session_key,
                "queue_state": queue_state,
                "queued_triggers": queued_triggers,
                "pending_artifacts": [
                    str(item or "").strip()
                    for item in list(payload.get("pending_artifacts") or [])
                    if str(item or "").strip()
                ],
                "lock_state": lock_state,
                "lock_pid": int(lock_payload.get("pid") or 0) if lock_payload else 0,
                "queue_since_ts": float(payload.get("queue_since_ts") or 0.0),
                "last_queued_at_ts": float(payload.get("last_queued_at_ts") or 0.0),
                "_sort_ts": sort_ts,
            }
        )
    items.sort(key=lambda item: (float(item.get("_sort_ts") or 0.0), str(item.get("run_id") or "")))
    return [
        {key: value for key, value in item.items() if key != "_sort_ts"}
        for item in items[: max(1, int(limit))]
    ]


def drain_pending_run_history_updates(
    *,
    history_root: str = "",
    session_key: str = "",
    limit: int = 20,
    drain_reason: str = "background_sweeper",
) -> Dict[str, Any]:
    pending = list_run_history_pending_updates(
        history_root=history_root,
        session_key=session_key,
        limit=limit,
    )
    results: List[Dict[str, Any]] = []
    drained = 0
    skipped_locked = 0
    failed = 0
    for item in pending:
        run_dir = str(item.get("run_dir") or "").strip()
        run_id = str(item.get("run_id") or "").strip() or "unknown"
        try:
            artifacts = run_history_artifact_only_updater_pass(
                run_dir,
                drain_reason=f"{str(drain_reason or '').strip() or 'background_sweeper'}:{run_id}",
                worker_pid=0,
            )
        except Exception as exc:
            failed += 1
            results.append(
                {
                    "run_id": run_id,
                    "run_dir": run_dir,
                    "status": "failed",
                    "error": exc.__class__.__name__,
                }
            )
            continue
        if "skipped" in artifacts:
            skipped_locked += 1
            results.append(
                {
                    "run_id": run_id,
                    "run_dir": run_dir,
                    "status": str(artifacts.get("skipped") or "skipped"),
                }
            )
            continue
        drained += 1
        results.append(
            {
                "run_id": run_id,
                "run_dir": run_dir,
                "status": "drained",
                "updated_artifacts": sorted(str(key) for key in artifacts.keys()),
            }
        )
    return {
        "history_root": str(root.expanduser().resolve()) if (root := (Path(str(history_root or "").strip()).expanduser() if str(history_root or "").strip() else _history_root())) else str(_history_root()),
        "session_key": str(session_key or "").strip(),
        "discovered": len(pending),
        "drained": drained,
        "skipped_locked": skipped_locked,
        "failed": failed,
        "results": results,
    }


def run_history_background_updater_pass(
    agent: Any,
    *,
    goal: Optional[TestGoal] = None,
    include_retrieval: bool = True,
    trigger: str = "unspecified",
) -> Dict[str, str]:
    refresh_run_history_context_snapshot_artifacts(agent, goal=goal, trigger=trigger)
    _schedule_run_history_background_update(
        agent,
        trigger=trigger,
        include_retrieval=include_retrieval,
    )
    if not _should_drain_background_update(trigger):
        _launch_run_history_background_subprocess(agent, trigger=trigger)
        return refresh_run_history_updater_artifacts(
            agent,
            trigger=trigger,
            include_retrieval=include_retrieval,
            updated_artifacts=[],
        )
    return _drain_run_history_background_update(
        agent,
        goal=goal,
        drain_reason="inline_terminal_boundary",
    )


def run_history_summary_side_pass(
    agent: Any,
    *,
    goal: Optional[TestGoal] = None,
    include_retrieval: bool = True,
    trigger: str = "unspecified",
) -> Dict[str, str]:
    return run_history_background_updater_pass(
        agent,
        goal=goal,
        include_retrieval=include_retrieval,
        trigger=trigger,
    )


def refresh_run_history_state(agent: Any, goal: Optional[TestGoal] = None) -> str:
    artifacts = run_history_summary_side_pass(
        agent,
        goal=goal,
        include_retrieval=True,
        trigger="state_refresh",
    )
    if not artifacts:
        return ""
    return str(artifacts.get("state") or "")


def build_run_history_prompt_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    _flush_pending_run_history_background_update(
        agent,
        goal=goal,
        drain_reason="context_read_flush:compact",
    )
    cached = str(getattr(agent, "_run_history_prompt_summary", "") or "").strip()
    if cached:
        return cached
    artifact_text = _load_history_document(getattr(agent, "_run_history_prompt_path", "")) or _load_history_document(
        getattr(agent, "_run_history_session_prompt_path", "")
    )
    if artifact_text.strip():
        agent._run_history_prompt_summary = artifact_text.strip()
        return agent._run_history_prompt_summary
    artifacts = refresh_run_history_artifacts(agent, goal=goal)
    return str(artifacts.get("compact") or "")


def build_run_history_session_summary_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    _flush_pending_run_history_background_update(
        agent,
        goal=goal,
        drain_reason="context_read_flush:session_summary",
    )
    cached = str(getattr(agent, "_run_history_session_summary", "") or "").strip()
    if cached:
        return cached
    artifact_text = _load_history_document(
        getattr(agent, "_run_history_session_summary_path", "")
    ) or _load_history_document(getattr(agent, "_run_history_summary_path", ""))
    if artifact_text.strip():
        agent._run_history_session_summary = artifact_text.strip()
        return agent._run_history_session_summary
    artifacts = refresh_run_history_artifacts(agent, goal=goal)
    return str(artifacts.get("session_summary") or "")


def _markdown_section_map(document: str, *, header_prefix: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    current_header = ""
    current_lines: List[str] = []

    def flush_section() -> None:
        nonlocal current_header, current_lines
        body = "\n".join(line.rstrip() for line in current_lines).strip()
        if current_header and body:
            sections[current_header] = body
        current_header = ""
        current_lines = []

    for raw_line in str(document or "").splitlines():
        line = str(raw_line or "").rstrip()
        if line.startswith(header_prefix):
            flush_section()
            current_header = line[len(header_prefix) :].strip()
            continue
        if current_header:
            current_lines.append(line)
    flush_section()
    return sections


def _condense_replay_body(body: str) -> str:
    parts: List[str] = []
    for raw_line in str(body or "").splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        parts.append(line)
    return " ; ".join(parts).strip()


def _bullet_field_map(block: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for raw_line in str(block or "").splitlines():
        line = str(raw_line or "").strip()
        if not line.startswith("- ") or ": " not in line:
            continue
        key, value = line[2:].split(": ", 1)
        key = str(key or "").strip()
        value = str(value or "").strip()
        if key and value:
            fields[key] = value
    return fields


def _section_bullet_lines(document: str, *, header_prefix: str, section_name: str, limit: int) -> List[str]:
    sections = _markdown_section_map(document, header_prefix=header_prefix)
    body = str(sections.get(section_name) or "").strip()
    if not body:
        return []
    lines: List[str] = []
    for raw_line in body.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def build_run_history_session_replay_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    session_summary = build_run_history_session_summary_context(agent, goal=goal)
    if not session_summary.strip():
        return ""
    sections = _markdown_section_map(session_summary, header_prefix="## ")
    replay_order = [
        "Startup Continuity Audit",
        "Session Start Rules",
        "Current Objective",
        "Active Blockers",
        "Next Best Action",
        "Completed Progress",
    ]
    lines: List[str] = ["## 세션 연속성 replay packet(summary.md)"]
    included = 0
    for header in replay_order:
        body = str(sections.get(header) or "").strip()
        if not body:
            continue
        if header == "Completed Progress" and "no_confirmed_progress_yet" in body:
            continue
        if header == "Active Blockers" and body.strip() == "- none":
            continue
        condensed = _condense_replay_body(body)
        if not condensed:
            continue
        lines.append(f"- {header}: {condensed}")
        included += 1
    if included <= 0:
        return ""
    return "\n".join(lines).strip()


def build_run_history_attempt_digest_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    memory_summary = build_run_history_memory_context(agent, goal=goal)
    if not memory_summary.strip():
        return ""
    attempt_lines = _section_bullet_lines(
        memory_summary,
        header_prefix="## ",
        section_name="Recent Attempts",
        limit=2,
    )
    if not attempt_lines:
        return ""
    lines = ["## recent attempt digest"]
    for index, line in enumerate(attempt_lines, start=1):
        lines.append(f"- attempt_{index}: {line}")
    return "\n".join(lines).strip()


def build_run_history_memory_replay_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    memory_summary = build_run_history_memory_context(agent, goal=goal)
    if not memory_summary.strip():
        return ""
    sections = _markdown_section_map(memory_summary, header_prefix="## ")
    replay_order = [
        "Recent Attempts",
        "Prior Outcomes",
        "Resume Hints",
    ]
    lines: List[str] = ["## 세션 carry-over 기억(MEMORY replay)"]
    included = 0
    for header in replay_order:
        body = str(sections.get(header) or "").strip()
        if not body:
            continue
        condensed = _condense_replay_body(body)
        if not condensed:
            continue
        lines.append(f"- {header}: {condensed}")
        included += 1
    if included <= 0:
        return ""
    return "\n".join(lines).strip()


def build_run_history_progress_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    compact_summary = build_run_history_prompt_context(agent, goal=goal)
    if not compact_summary.strip():
        return ""
    sections = _markdown_section_map(compact_summary, header_prefix="### ")
    replay_order = [
        "현재 run 최근 진전",
        "현재 run 최근 계획",
        "반복/실패 주의",
        "최근 상태 신호",
        "최근 fill/select 기억",
    ]
    lines: List[str] = ["## 현재 run replay tail"]
    included = 0
    for header in replay_order:
        body = str(sections.get(header) or "").strip()
        if not body:
            continue
        condensed = _condense_replay_body(body)
        if not condensed:
            continue
        lines.append(f"- {header}: {condensed}")
        included += 1
    if included <= 0:
        return ""
    return "\n".join(lines).strip()


def build_run_history_replay_boundary_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    events = _load_events(agent)
    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    prior_terminal_events = [
        event
        for event in events
        if str(event.get("kind") or "").strip() == "goal_end"
        and str(event.get("run_id") or "").strip() != current_run_id
    ]
    prior_progress_events = [
        event
        for event in events
        if str(event.get("kind") or "").strip() == "step_outcome"
        and str(event.get("run_id") or "").strip() != current_run_id
        and (bool(event.get("success")) or bool(event.get("changed")))
    ]
    prior_outcome_events = [
        event
        for event in events
        if str(event.get("kind") or "").strip() == "step_outcome"
        and str(event.get("run_id") or "").strip() != current_run_id
    ]
    failure_buckets = _session_failure_buckets(events)
    repeated_blockers = [item for item in failure_buckets if int(item.get("count", 0)) >= 2]

    lines: List[str] = ["## replay boundary"]
    if prior_terminal_events:
        latest = prior_terminal_events[-1]
        latest_progress = prior_progress_events[-1] if prior_progress_events else None
        latest_resume_source = latest_progress or (prior_outcome_events[-1] if prior_outcome_events else None)
        lines.append("- mode: carry_over_resume")
        lines.append(f"- prior_runs: {len(prior_terminal_events)}")
        lines.append(f"- resume_from_run: {str(latest.get('run_id') or '').strip() or 'unknown'}")
        lines.append(f"- last_terminal_status: {str(latest.get('status') or '').strip() or 'unknown'}")
        reason = _truncate_text(str(latest.get("reason") or "").strip(), 140) or "none"
        lines.append(f'- last_terminal_reason: "{reason}"')
        if latest_resume_source is not None:
            lines.append(
                '- '
                f'resume_hint: "{_truncate_text(_render_outcome_line(latest_resume_source), 180)}"'
            )
    else:
        lines.append("- mode: fresh_session_start")
        lines.append("- prior_runs: 0")
        lines.append("- carry_over_available: no")

    if repeated_blockers:
        blocker = repeated_blockers[0]
        lines.append(
            "- "
            f"repeat_guard: action={str(blocker.get('action') or '').strip() or 'unknown'} | "
            f"ref={str(blocker.get('ref_id') or '').strip() or '-'} | "
            f"reason_code={str(blocker.get('reason_code') or '').strip() or 'unknown'}"
        )

    return "\n".join(lines).strip()


def build_run_history_resume_checklist_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    boundary = build_run_history_replay_boundary_context(agent, goal=goal)
    session_summary = build_run_history_session_summary_context(agent, goal=goal)
    memory_summary = build_run_history_memory_context(agent, goal=goal)
    if not boundary.strip() and not session_summary.strip() and not memory_summary.strip():
        return ""

    boundary_fields = _bullet_field_map(boundary)
    summary_sections = _markdown_section_map(session_summary, header_prefix="## ")
    memory_sections = _markdown_section_map(memory_summary, header_prefix="## ")

    objective = _condense_replay_body(str(summary_sections.get("Current Objective") or "").strip())
    start_rules = _condense_replay_body(str(summary_sections.get("Session Start Rules") or "").strip())
    recent_attempts = _condense_replay_body(
        str(summary_sections.get("Recent Attempts") or memory_sections.get("Recent Attempts") or "").strip()
    )
    blockers = _condense_replay_body(str(summary_sections.get("Active Blockers") or "").strip())
    next_action = _condense_replay_body(str(summary_sections.get("Next Best Action") or "").strip())
    audit = _condense_replay_body(str(summary_sections.get("Startup Continuity Audit") or "").strip())
    resume_memory = _condense_replay_body(str(memory_sections.get("Resume Hints") or "").strip())

    lines: List[str] = ["## resume checklist"]
    included = 0

    mode = str(boundary_fields.get("mode") or "").strip()
    if mode:
        lines.append(f"- mode: {mode}")
        included += 1

    if objective:
        lines.append(f"- objective: {objective}")
        included += 1

    if start_rules:
        lines.append(f"- start_rules: {start_rules}")
        included += 1

    if audit:
        lines.append(f"- verify_first: {audit}")
        included += 1

    if recent_attempts:
        lines.append(f"- recent_attempts: {recent_attempts}")
        included += 1

    last_terminal_reason = str(boundary_fields.get("last_terminal_reason") or "").strip()
    if last_terminal_reason and last_terminal_reason != '"none"':
        lines.append(f"- last_terminal_reason: {last_terminal_reason}")
        included += 1

    resume_hint = str(boundary_fields.get("resume_hint") or "").strip() or resume_memory
    if resume_hint:
        lines.append(f"- resume_hint: {resume_hint}")
        included += 1

    if blockers and blockers != "none":
        lines.append(f"- blockers_now: {blockers}")
        included += 1

    if next_action:
        lines.append(f"- next_best_action: {next_action}")
        included += 1

    repeat_guard = str(boundary_fields.get("repeat_guard") or "").strip()
    if repeat_guard:
        lines.append(f"- avoid_repeat: {repeat_guard}")
        included += 1

    if included <= 0:
        return ""
    return "\n".join(lines).strip()


def _fit_replay_block_to_budget(block_text: str, max_chars: int) -> str:
    text = str(block_text or "").strip()
    if not text or max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    raw_lines = [str(line or "").rstrip() for line in text.splitlines() if str(line or "").strip()]
    if not raw_lines:
        return ""

    selected: List[str] = []
    used = 0
    for line in raw_lines:
        addition = len(line) + (1 if selected else 0)
        if used + addition > max_chars:
            break
        selected.append(line)
        used += addition

    if not selected:
        return ""

    truncated_line = "- truncated_for_packet: true"
    truncated_addition = len(truncated_line) + 1
    if used + truncated_addition <= max_chars:
        selected.append(truncated_line)

    return "\n".join(selected).strip()


def _is_fresh_session_boundary(boundary_block: str) -> bool:
    fields = _bullet_field_map(boundary_block)
    return str(fields.get("mode") or "").strip() == "fresh_session_start"


def build_run_history_replay_packet_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    _flush_pending_run_history_background_update(
        agent,
        goal=goal,
        drain_reason="context_read_flush:replay",
    )
    cached = str(getattr(agent, "_run_history_replay_packet_summary", "") or "").strip()
    if cached:
        return cached
    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    artifact_packet = _extract_replay_packet_from_artifact(
        _load_history_document(getattr(agent, "_run_history_replay_path", "")),
        expected_run_id=current_run_id,
    ) or _extract_replay_packet_from_artifact(
        _load_history_document(getattr(agent, "_run_history_session_replay_path", "")),
        expected_run_id=current_run_id,
    )
    if artifact_packet:
        agent._run_history_replay_packet_summary = artifact_packet
        return artifact_packet
    blocks = [
        ("boundary", build_run_history_replay_boundary_context(agent, goal=goal)),
        ("resume_checklist", build_run_history_resume_checklist_context(agent, goal=goal)),
        ("attempt_digest", build_run_history_attempt_digest_context(agent, goal=goal)),
        ("summary_replay", build_run_history_session_replay_context(agent, goal=goal)),
        ("memory_replay", build_run_history_memory_replay_context(agent, goal=goal)),
        ("retrieval", build_run_history_retrieval_context(agent, goal=goal)),
        ("current_run_tail", build_run_history_progress_context(agent, goal=goal)),
    ]
    if _is_fresh_session_boundary(str(blocks[0][1] or "")):
        blocks = [item for item in blocks if item[0] != "summary_replay"]
    selected_blocks = [(name, block.strip()) for name, block in blocks if str(block or "").strip()]
    if not selected_blocks:
        return ""

    limit = int(
        str(
            os.getenv(
                "GAIA_RUN_HISTORY_REPLAY_CHAR_LIMIT",
                str(_DEFAULT_REPLAY_CHAR_LIMIT),
            )
        ).strip()
        or str(_DEFAULT_REPLAY_CHAR_LIMIT)
    )

    lines: List[str] = ["## 세션 continuity replay packet"]
    used = len(lines[0]) + 1
    omitted: List[str] = []
    truncated: List[str] = []
    protected_blocks = {"boundary", "resume_checklist", "attempt_digest"}
    for name, block in selected_blocks:
        block_text = block.strip()
        available = limit - used - 2
        if available <= 0:
            omitted.append(name)
            continue
        block_len = len(block_text) + 2
        if block_len <= limit - used:
            lines.append("")
            lines.append(block_text)
            used += block_len
            continue
        if name in protected_blocks:
            fitted = _fit_replay_block_to_budget(block_text, available)
            if fitted:
                lines.append("")
                lines.append(fitted)
                used += len(fitted) + 2
                truncated.append(name)
                continue
        omitted.append(name)

    if omitted or truncated:
        lines.append("")
        lines.append("## Replay Packet Omitted")
        if omitted:
            lines.append("- omitted_due_to_char_limit: " + ", ".join(omitted))
        if truncated:
            lines.append("- truncated_due_to_char_limit: " + ", ".join(truncated))

    summary = "\n".join(lines).strip()
    agent._run_history_replay_packet_summary = summary
    return summary


def build_run_history_memory_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    _flush_pending_run_history_background_update(
        agent,
        goal=goal,
        drain_reason="context_read_flush:memory",
    )
    cached = str(getattr(agent, "_run_history_memory_summary", "") or "").strip()
    if cached:
        return cached
    artifact_text = _load_history_document(getattr(agent, "_run_history_memory_path", "")) or _load_history_document(
        getattr(agent, "_run_history_session_memory_path", "")
    )
    if artifact_text.strip():
        agent._run_history_memory_summary = artifact_text.strip()
        return agent._run_history_memory_summary
    refresh_run_history_artifacts(agent, goal=goal)
    return str(getattr(agent, "_run_history_memory_summary", "") or "").strip()


def _merge_query_tokens(token_weights: Dict[str, int], value: object, weight: int) -> None:
    for token in _tokenize_for_retrieval(value):
        token_weights[token] = int(token_weights.get(token, 0)) + int(weight)


def _query_overlap_score(text: object, token_weights: Dict[str, int]) -> tuple[int, List[str]]:
    if not token_weights:
        return 0, []
    tokens = set(_tokenize_for_retrieval(text))
    overlap = sorted(token for token in tokens if token in token_weights)
    if not overlap:
        return 0, []
    return sum(int(token_weights.get(token, 0)) for token in overlap), overlap


def _memory_section_candidates(memory_summary: str) -> List[Dict[str, str]]:
    if not memory_summary.strip():
        return []

    sections: List[Dict[str, str]] = []
    current_header = ""
    current_lines: List[str] = []

    def flush_section() -> None:
        nonlocal current_header, current_lines
        body = " ".join(line.strip() for line in current_lines if line.strip()).strip()
        if current_header and body:
            sections.append({"header": current_header, "body": body})
        current_header = ""
        current_lines = []

    for raw_line in memory_summary.splitlines():
        line = str(raw_line or "").rstrip()
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            flush_section()
            current_header = line[3:].strip()
            continue
        if current_header:
            current_lines.append(line)
    flush_section()
    return sections


def _render_goal_end_retrieval_text(event: Dict[str, Any], latest_outcome_by_run: Dict[str, Dict[str, Any]]) -> str:
    run_id = str(event.get("run_id") or "").strip()
    latest_outcome = latest_outcome_by_run.get(run_id)
    outcome_suffix = ""
    if latest_outcome is not None:
        latest_reason_code = str(latest_outcome.get("reason_code") or "").strip()
        if latest_reason_code and latest_reason_code not in {"ok", "unknown"}:
            outcome_suffix += f" | last_reason_code={latest_reason_code}"
        outcome_suffix += (
            " | "
            f"last_outcome=\"{_truncate_text(_render_outcome_line(latest_outcome), 160)}\""
        )
    return (
        "goal_end | "
        f"status={str(event.get('status') or '').strip() or 'unknown'} | "
        f"reason=\"{_truncate_text(event.get('reason') or '', 220) or 'none'}\""
        f"{outcome_suffix}"
    )


def _retrieval_entry_digest(text: object) -> str:
    normalized = str(text or "").strip().encode("utf-8", errors="replace")
    return hashlib.sha1(normalized).hexdigest()[:10]


def _build_retrieval_entry_id(entry: Dict[str, Any]) -> str:
    source = str(entry.get("source") or "").strip().lower() or "entry"
    text_digest = _retrieval_entry_digest(entry.get("text") or "")
    if source == "event":
        run_id = _safe_slug(str(entry.get("run_id") or "").strip(), fallback="run")
        kind = _safe_slug(str(entry.get("kind") or "").strip(), fallback="event")
        reason_code = _safe_slug(str(entry.get("reason_code") or "").strip(), fallback="none")
        return f"event:{run_id}:{kind}:{reason_code}:{text_digest}"
    if source == "memory":
        header = _safe_slug(str(entry.get("header") or "").strip(), fallback="memory")
        return f"memory:{header}:{text_digest}"
    stage = _safe_slug(str(entry.get("stage") or "").strip(), fallback="transcript")
    role = _safe_slug(str(entry.get("role") or "").strip(), fallback="assistant")
    return f"transcript:{stage}:{role}:{text_digest}"


def _normalize_retrieval_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(entry or {})
    entry_id = str(normalized.get("entry_id") or "").strip()
    if not entry_id:
        entry_id = _build_retrieval_entry_id(normalized)
    normalized["entry_id"] = entry_id
    return normalized


def _build_retrieval_index_entries(agent: Any, goal: Optional[TestGoal] = None) -> List[Dict[str, Any]]:
    events = _load_events(agent)
    transcript_rows = _load_transcript_rows(agent)
    memory_summary = build_run_history_memory_context(agent, goal=goal)
    memory_sections = _memory_section_candidates(memory_summary)
    if not events and not transcript_rows and not memory_sections:
        return []

    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    latest_outcome_by_run: Dict[str, Dict[str, Any]] = {}
    for event in reversed(events):
        if str(event.get("kind") or "").strip() != "step_outcome":
            continue
        run_id = str(event.get("run_id") or "").strip()
        if run_id and run_id not in latest_outcome_by_run:
            latest_outcome_by_run[run_id] = event

    prior_run_history_exists = any(
        str(event.get("run_id") or "").strip() != current_run_id
        and str(event.get("kind") or "").strip() in {"goal_end", "step_outcome", "decision"}
        for event in events
    )

    entries: List[Dict[str, Any]] = []
    for event in events:
        kind = str(event.get("kind") or "").strip()
        if kind not in {"goal_end", "step_outcome", "decision"}:
            continue
        if kind == "goal_end":
            text = _render_goal_end_retrieval_text(event, latest_outcome_by_run)
        elif kind == "step_outcome":
            text = f"step_outcome | {_render_outcome_line(event)}"
        else:
            text = f"decision | {_render_decision_line(event)}"
        entries.append(
            _normalize_retrieval_entry(
                {
                "source": "event",
                "kind": kind,
                "run_id": str(event.get("run_id") or "").strip(),
                "reason_code": str(event.get("reason_code") or "").strip(),
                "text": text,
                }
            )
        )

    for section in memory_sections:
        header = str(section.get("header") or "").strip()
        body = str(section.get("body") or "").strip()
        if not header or not body or not prior_run_history_exists or header == "Current Objective":
            continue
        entries.append(
            _normalize_retrieval_entry(
                {
                "source": "memory",
                "header": header,
                "text": f"MEMORY | {header}: {_truncate_text(body, 240)}",
                }
            )
        )

    for row in transcript_rows:
        content = str(row.get("content") or "").strip()
        stage = str(row.get("stage") or "").strip()
        role = str(row.get("role") or "").strip().lower()
        if not content or role == "user" or stage.endswith("_prompt"):
            continue
        entries.append(
            _normalize_retrieval_entry(
                {
                "source": "transcript",
                "stage": stage,
                "role": role,
                "text": f"transcript:{stage} | {_truncate_text(content, 220)}",
                }
            )
        )
    return entries


def _render_retrieval_index_artifact(agent: Any, entries: List[Dict[str, Any]], *, trigger: str) -> str:
    payload = {
        "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip() or "unknown",
        "trigger": str(trigger or "").strip() or "unspecified",
        "updated_at": _refresh_label(time.time()),
        "entries": list(entries),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _load_retrieval_index_entries_from_artifact(document: str, *, expected_run_id: str = "") -> List[Dict[str, Any]]:
    text = str(document or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    expected = str(expected_run_id or "").strip()
    artifact_run_id = str(payload.get("run_id") or "").strip()
    if expected and artifact_run_id != expected:
        return []
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []
    return [_normalize_retrieval_entry(entry) for entry in entries if isinstance(entry, dict)]


def _rank_retrieval_entries(agent: Any, goal: Optional[TestGoal], entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    events = _load_events(agent)
    current_run_events = _current_run_events(agent, events)
    goal_contract = _goal_contract_context(agent, goal)
    snapshot = _load_context_snapshot_payload(agent)
    recent_reason_codes: List[str] = []
    for event in reversed(current_run_events):
        if str(event.get("kind") or "").strip() != "step_outcome":
            continue
        reason_code = str(event.get("reason_code") or "").strip()
        if not reason_code or reason_code in recent_reason_codes:
            continue
        recent_reason_codes.append(reason_code)
        if len(recent_reason_codes) >= 4:
            break

    query_token_weights: Dict[str, int] = {}
    _merge_query_tokens(query_token_weights, goal_contract.get("name", ""), 6)
    _merge_query_tokens(query_token_weights, goal_contract.get("description", ""), 4)
    _merge_query_tokens(
        query_token_weights,
        " ".join(goal_contract.get("success_criteria", []) or []),
        4,
    )
    _merge_query_tokens(
        query_token_weights,
        " ".join(goal_contract.get("expected_signals", []) or []),
        5,
    )
    action_history = list(getattr(agent, "_action_history", []) or [])[-3:]
    if not action_history:
        action_history = _snapshot_string_list(snapshot.get("action_history", []), limit=3)
    for part in action_history:
        _merge_query_tokens(query_token_weights, part, 2)
    action_feedback = list(getattr(agent, "_action_feedback", []) or [])[-4:]
    if not action_feedback:
        action_feedback = _snapshot_string_list(snapshot.get("action_feedback", []), limit=4)
    for part in action_feedback:
        _merge_query_tokens(query_token_weights, part, 5)
    for reason_code in recent_reason_codes:
        _merge_query_tokens(query_token_weights, reason_code, 7)
    for event in current_run_events[-4:]:
        if str(event.get("kind") or "").strip() != "step_outcome":
            continue
        _merge_query_tokens(query_token_weights, event.get("error") or "", 4)
        _merge_query_tokens(query_token_weights, _render_outcome_line(event), 3)

    if not query_token_weights:
        return []

    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    latest_prior_run_id = ""
    for event in reversed(events):
        if str(event.get("kind") or "").strip() != "goal_end":
            continue
        run_id = str(event.get("run_id") or "").strip()
        if run_id and run_id != current_run_id:
            latest_prior_run_id = run_id
            break

    memory_section_bonus = {
        "Completed Progress": 8,
        "Active Blockers": 10,
        "Next Best Action": 8,
        "Open Questions": 6,
        "Recent Attempts": 11,
        "Prior Outcomes": 7,
        "Resume Hints": 10,
    }
    candidates: List[Dict[str, Any]] = []
    for raw_entry in entries:
        entry = _normalize_retrieval_entry(raw_entry)
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        overlap_score, overlap_tokens = _query_overlap_score(text, query_token_weights)
        if overlap_score <= 0:
            continue
        source = str(entry.get("source") or "").strip()
        score = overlap_score
        if source == "event":
            kind = str(entry.get("kind") or "").strip()
            if kind == "goal_end":
                score += 9
            elif kind == "step_outcome":
                score += 7
            else:
                score += 2
            run_id = str(entry.get("run_id") or "").strip()
            if run_id and run_id != current_run_id:
                score += 2
            if latest_prior_run_id and run_id == latest_prior_run_id:
                score += 4
            reason_code = str(entry.get("reason_code") or "").strip()
            if reason_code and reason_code in recent_reason_codes:
                score += 6
        elif source == "memory":
            score += int(memory_section_bonus.get(str(entry.get("header") or "").strip(), 5))
        elif source == "transcript":
            stage = str(entry.get("stage") or "").strip()
            role = str(entry.get("role") or "").strip().lower()
            score += (2 if "response" in stage else 0) + (1 if role == "assistant" else 0)
        candidates.append(
            {
                "entry_id": str(entry.get("entry_id") or "").strip(),
                "entry": entry,
                "score": score,
                "overlap": overlap_tokens,
            }
        )

    candidates.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return candidates


def search_run_history_retrieval_index(
    agent: Any,
    goal: Optional[TestGoal] = None,
    *,
    limit: int = 6,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    search_entries = list(entries or [])
    if not search_entries:
        current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
        search_entries = _load_retrieval_index_entries_from_artifact(
            _load_history_document(getattr(agent, "_run_history_retrieval_index_path", "")),
            expected_run_id=current_run_id,
        ) or _load_retrieval_index_entries_from_artifact(
            _load_history_document(getattr(agent, "_run_history_session_retrieval_index_path", "")),
            expected_run_id=current_run_id,
        )
        if not search_entries:
            search_entries = _build_retrieval_index_entries(agent, goal=goal)

    ranked = _rank_retrieval_entries(agent, goal, search_entries)
    results: List[Dict[str, Any]] = []
    seen_entry_ids: set[str] = set()
    for item in ranked:
        entry_id = str(item.get("entry_id") or "").strip()
        if not entry_id or entry_id in seen_entry_ids:
            continue
        seen_entry_ids.add(entry_id)
        results.append(
            {
                "entry_id": entry_id,
                "score": int(item.get("score", 0) or 0),
                "overlap": list(item.get("overlap") or []),
                "entry": dict(item.get("entry") or {}),
            }
        )
        if len(results) >= max(1, int(limit)):
            break
    return results


def get_run_history_retrieval_entry(
    agent: Any,
    entry_id: str,
    goal: Optional[TestGoal] = None,
    *,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    target_id = str(entry_id or "").strip()
    if not target_id:
        return {}
    search_entries = list(entries or [])
    if not search_entries:
        current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
        search_entries = _load_retrieval_index_entries_from_artifact(
            _load_history_document(getattr(agent, "_run_history_retrieval_index_path", "")),
            expected_run_id=current_run_id,
        ) or _load_retrieval_index_entries_from_artifact(
            _load_history_document(getattr(agent, "_run_history_session_retrieval_index_path", "")),
            expected_run_id=current_run_id,
        )
        if not search_entries:
            search_entries = _build_retrieval_index_entries(agent, goal=goal)
    for raw_entry in search_entries:
        entry = _normalize_retrieval_entry(raw_entry)
        if str(entry.get("entry_id") or "").strip() == target_id:
            return entry
    return {}


def _score_retrieval_entries(agent: Any, goal: Optional[TestGoal], entries: List[Dict[str, Any]]) -> str:
    candidates = search_run_history_retrieval_index(agent, goal=goal, limit=6, entries=entries)
    if not candidates:
        agent._run_history_retrieval_summary = ""
        return ""
    lines: List[str] = []
    seen_texts: set[str] = set()
    for item in candidates:
        entry = dict(item.get("entry") or {})
        entry_id = str(item.get("entry_id") or "").strip()
        text = str(entry.get("text") or "").strip()
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        prefix = f"[{entry_id}] " if entry_id else ""
        lines.append(f"- {prefix}{text}")
    summary = "## 관련 세션 기억 검색 결과\n" + "\n".join(lines)
    agent._run_history_retrieval_summary = summary
    return summary


def build_run_history_retrieval_context(agent: Any, goal: Optional[TestGoal] = None) -> str:
    _flush_pending_run_history_background_update(
        agent,
        goal=goal,
        drain_reason="context_read_flush:retrieval",
    )
    cached = str(getattr(agent, "_run_history_retrieval_summary", "") or "").strip()
    if cached:
        return cached
    current_run_id = str(getattr(agent, "_run_history_run_id", "") or "").strip()
    artifact_summary = _extract_retrieval_summary_from_artifact(
        _load_history_document(getattr(agent, "_run_history_retrieval_path", "")),
        expected_run_id=current_run_id,
    ) or _extract_retrieval_summary_from_artifact(
        _load_history_document(getattr(agent, "_run_history_session_retrieval_path", "")),
        expected_run_id=current_run_id,
    )
    if artifact_summary:
        agent._run_history_retrieval_summary = artifact_summary
        return artifact_summary
    index_entries = _load_retrieval_index_entries_from_artifact(
        _load_history_document(getattr(agent, "_run_history_retrieval_index_path", "")),
        expected_run_id=current_run_id,
    ) or _load_retrieval_index_entries_from_artifact(
        _load_history_document(getattr(agent, "_run_history_session_retrieval_index_path", "")),
        expected_run_id=current_run_id,
    )
    if not index_entries:
        index_entries = _build_retrieval_index_entries(agent, goal=goal)
    return _score_retrieval_entries(agent, goal, index_entries)


def record_run_history_transcript(
    agent: Any,
    *,
    stage: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not _history_enabled(agent):
        return
    text = str(content or "")
    _append_transcript(
        agent,
        {
            "kind": "transcript",
            "timestamp": time.time(),
            "session_key": str(getattr(agent, "_run_history_session_key", "") or "").strip(),
            "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip(),
            "stage": str(stage or "").strip(),
            "role": str(role or "").strip(),
            "char_count": len(text),
            "content": _truncate_large_text(text, 5000),
            "metadata": dict(metadata or {}),
        },
    )


def initialize_run_history(agent: Any, goal: TestGoal) -> None:
    enabled = _history_enabled(agent)
    agent._run_history_last_refresh_trigger = ""
    agent._run_history_last_refresh_at = 0.0
    agent._run_history_last_refresh_include_retrieval = False
    agent._run_history_last_retrieval_refresh_trigger = ""
    agent._run_history_last_retrieval_refresh_at = 0.0
    agent._run_history_last_replay_refresh_trigger = ""
    agent._run_history_last_replay_refresh_at = 0.0
    agent._run_history_last_replay_refresh_include_retrieval = False
    agent._run_history_session_summary = ""
    agent._run_history_replay_packet_summary = ""
    agent._run_history_prompt_summary = ""
    agent._run_history_memory_summary = ""
    agent._run_history_retrieval_summary = ""
    agent._run_history_background_queue_triggers = []
    agent._run_history_background_queue_since = 0.0
    agent._run_history_background_last_queued_at = 0.0
    agent._run_history_background_last_drained_at = 0.0
    agent._run_history_background_drain_count = 0
    agent._run_history_background_last_drain_reason = ""
    agent._run_history_background_last_launch_status = ""
    agent._run_history_background_last_launch_trigger = ""
    agent._run_history_background_last_launch_at = 0.0
    agent._run_history_background_last_launch_pid = 0
    agent._run_history_background_launch_count = 0
    agent._run_history_background_pending_include_retrieval = False
    agent._run_history_background_pending_artifacts = []
    agent._run_history_background_last_updated_artifacts = []
    agent._run_history_background_active = False
    if not enabled:
        return
    session_key = _session_key_for(agent, goal)
    session_dir = _history_root() / "sessions" / session_key
    startup_recovery = drain_pending_run_history_updates(
        history_root=str(_history_root()),
        session_key=session_key,
        limit=8,
        drain_reason=f"session_startup_recovery:{session_key}",
    )
    agent._run_history_startup_recovery_drained = int(startup_recovery.get("drained", 0) or 0)
    agent._run_history_startup_recovery_failed = int(startup_recovery.get("failed", 0) or 0)
    if agent._run_history_startup_recovery_drained > 0 or agent._run_history_startup_recovery_failed > 0:
        agent._run_history_startup_recovery_at = float(time.time())
    goal_slug = _safe_slug(getattr(goal, "id", "") or getattr(goal, "name", ""), fallback="goal")
    run_id = f"{int(time.time() * 1000)}-{goal_slug}"
    run_dir = session_dir / "runs" / run_id
    events_path = run_dir / "events.jsonl"
    state_path = run_dir / "state.md"
    summary_path = run_dir / "summary.md"
    updater_path = run_dir / "updater.md"
    updater_queue_path = run_dir / "updater_queue.json"
    updater_lock_path = run_dir / "updater_lock.json"
    replay_path = run_dir / "replay.md"
    retrieval_path = run_dir / "retrieval.md"
    retrieval_index_path = run_dir / "retrieval_index.json"
    context_snapshot_path = run_dir / "context_snapshot.json"
    prompt_path = run_dir / "compact.md"
    memory_path = run_dir / "MEMORY.md"
    transcript_path = run_dir / "transcript.jsonl"
    session_events_path = session_dir / "events.jsonl"
    session_state_path = session_dir / "state.md"
    session_summary_path = session_dir / "summary.md"
    session_updater_path = session_dir / "updater.md"
    session_updater_queue_path = session_dir / "updater_queue.json"
    session_updater_lock_path = session_dir / "updater_lock.json"
    session_replay_path = session_dir / "replay.md"
    session_retrieval_path = session_dir / "retrieval.md"
    session_retrieval_index_path = session_dir / "retrieval_index.json"
    session_context_snapshot_path = session_dir / "context_snapshot.json"
    session_prompt_path = session_dir / "compact.md"
    session_memory_path = session_dir / "MEMORY.md"
    session_transcript_path = session_dir / "transcript.jsonl"
    agent._run_history_run_id = run_id
    agent._run_history_dir = str(run_dir)
    agent._run_history_events_path = str(events_path)
    agent._run_history_state_path = str(state_path)
    agent._run_history_summary_path = str(summary_path)
    agent._run_history_updater_path = str(updater_path)
    agent._run_history_updater_queue_path = str(updater_queue_path)
    agent._run_history_updater_lock_path = str(updater_lock_path)
    agent._run_history_replay_path = str(replay_path)
    agent._run_history_retrieval_path = str(retrieval_path)
    agent._run_history_retrieval_index_path = str(retrieval_index_path)
    agent._run_history_context_snapshot_path = str(context_snapshot_path)
    agent._run_history_prompt_path = str(prompt_path)
    agent._run_history_memory_path = str(memory_path)
    agent._run_history_transcript_path = str(transcript_path)
    agent._run_history_session_key = session_key
    agent._run_history_session_dir = str(session_dir)
    agent._run_history_session_events_path = str(session_events_path)
    agent._run_history_session_state_path = str(session_state_path)
    agent._run_history_session_summary_path = str(session_summary_path)
    agent._run_history_session_updater_path = str(session_updater_path)
    agent._run_history_session_updater_queue_path = str(session_updater_queue_path)
    agent._run_history_session_updater_lock_path = str(session_updater_lock_path)
    agent._run_history_session_replay_path = str(session_replay_path)
    agent._run_history_session_retrieval_path = str(session_retrieval_path)
    agent._run_history_session_retrieval_index_path = str(session_retrieval_index_path)
    agent._run_history_session_context_snapshot_path = str(session_context_snapshot_path)
    agent._run_history_session_prompt_path = str(session_prompt_path)
    agent._run_history_session_memory_path = str(session_memory_path)
    agent._run_history_session_transcript_path = str(session_transcript_path)
    prior_events_exist = session_events_path.exists()
    refresh_run_history_context_snapshot_artifacts(agent, goal=goal, trigger="goal_start")
    _append_event(
        agent,
        {
            "kind": "goal_start",
            "timestamp": time.time(),
            "session_key": session_key,
            "run_id": run_id,
            "goal_id": str(getattr(goal, "id", "") or "").strip(),
            "goal_name": str(getattr(goal, "name", "") or "").strip(),
            "goal_description": str(getattr(goal, "description", "") or "").strip(),
            "start_url": str(getattr(goal, "start_url", "") or "").strip(),
            "continued_from_previous_session": bool(prior_events_exist),
        },
    )
    run_history_summary_side_pass(agent, goal=goal, include_retrieval=True, trigger="goal_start")


def record_run_history_decision(
    agent: Any,
    *,
    step_number: int,
    decision: ActionDecision,
    selected_element: Optional[DOMElement] = None,
) -> None:
    if not _history_enabled(agent):
        return
    _append_event(
        agent,
        {
            "kind": "decision",
            "timestamp": time.time(),
            "session_key": str(getattr(agent, "_run_history_session_key", "") or "").strip(),
            "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip(),
            "step": int(step_number),
            "action": _decision_action_value(decision),
            "ref_id": str(getattr(decision, "ref_id", "") or "").strip(),
            "element_id": getattr(decision, "element_id", None),
            "value": str(getattr(decision, "value", "") or "").strip(),
            "reasoning": str(getattr(decision, "reasoning", "") or "").strip(),
            "confidence": float(getattr(decision, "confidence", 0.0) or 0.0),
            "selected_element": (
                {
                    "id": getattr(selected_element, "id", None),
                    "tag": str(getattr(selected_element, "tag", "") or "").strip(),
                    "role": str(getattr(selected_element, "role", "") or "").strip(),
                    "text": _truncate_text(getattr(selected_element, "text", "") or "", 120),
                    "aria_label": _truncate_text(getattr(selected_element, "aria_label", "") or "", 120),
                    "container_name": _truncate_text(getattr(selected_element, "container_name", "") or "", 120),
                    "context_text": _truncate_text(getattr(selected_element, "context_text", "") or "", 160),
                    "ref_id": str(getattr(selected_element, "ref_id", "") or "").strip(),
                }
                if selected_element is not None
                else None
            ),
        },
    )
    run_history_summary_side_pass(agent, include_retrieval=True, trigger="decision")


def record_run_history_feedback(
    agent: Any,
    *,
    step_number: int,
    decision: ActionDecision,
    success: bool,
    changed: bool,
    error: Optional[str],
    reason_code: Optional[str] = None,
    state_change: Optional[Dict[str, Any]] = None,
) -> None:
    if not _history_enabled(agent):
        return
    _append_event(
        agent,
        {
            "kind": "step_outcome",
            "timestamp": time.time(),
            "session_key": str(getattr(agent, "_run_history_session_key", "") or "").strip(),
            "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip(),
            "step": int(step_number),
            "action": _decision_action_value(decision),
            "ref_id": str(getattr(decision, "ref_id", "") or "").strip(),
            "element_id": getattr(decision, "element_id", None),
            "status": "success" if success else "failed",
            "success": bool(success),
            "changed": bool(changed),
            "reason_code": str(reason_code or "").strip(),
            "error": str(error or "").strip(),
            "state_change": dict(state_change or {}),
            "active_url": str(getattr(agent, "_active_url", "") or "").strip(),
            "snapshot_id": str(getattr(agent, "_active_snapshot_id", "") or "").strip(),
        },
    )
    run_history_summary_side_pass(agent, include_retrieval=True, trigger="step_outcome")


def record_run_history_goal_outcome(
    agent: Any,
    *,
    goal: TestGoal,
    status: str,
    reason: str,
    step_count: int,
    duration_seconds: float,
) -> None:
    if not _history_enabled(agent):
        return
    _append_event(
        agent,
        {
            "kind": "goal_end",
            "timestamp": time.time(),
            "session_key": str(getattr(agent, "_run_history_session_key", "") or "").strip(),
            "run_id": str(getattr(agent, "_run_history_run_id", "") or "").strip(),
            "goal_id": str(getattr(goal, "id", "") or "").strip(),
            "goal_name": str(getattr(goal, "name", "") or "").strip(),
            "status": str(status or "").strip(),
            "reason": str(reason or "").strip(),
            "step_count": int(step_count),
            "duration_seconds": float(duration_seconds),
        },
    )
    run_history_summary_side_pass(agent, goal=goal, include_retrieval=True, trigger="goal_end")
