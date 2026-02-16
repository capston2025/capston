"""Shared helpers for GAIA CLI execution context."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4


def _runs_root() -> Path:
    return Path.home() / ".gaia" / "runs"


def resolve_run_context_path(run_id: str | Path) -> Path:
    if not run_id:
        raise ValueError("run id/path is required")

    candidate = Path(run_id)
    if candidate.suffix == ".json" or candidate.is_absolute() or candidate.exists():
        return candidate

    return _runs_root() / f"{candidate}.json"


def build_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _normalize_json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _normalize_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_json_value(v) for v in value]
    return value


def write_run_context(context: "RunContext", path: Path | None = None) -> Path:
    target = path or resolve_run_context_path(context.run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_json_value(context.to_dict())
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return target


def load_run_context(run_id_or_path: str | Path) -> "RunContext":
    path = resolve_run_context_path(run_id_or_path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Run context not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid run context JSON: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Run context payload is invalid: {path}")

    return RunContext.from_dict(data)


@dataclass
class RunContext:
    """Persisted execution context shared by terminal and GUI."""

    run_id: str
    mode: str
    created_at: str
    status: str = "unknown"
    url: str | None = None
    plan_source: str | None = None
    plan_path: str | None = None
    spec_path: str | None = None
    artifacts_path: str | None = None
    output_format: str = "text"
    status_message: str | None = None
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "RunContext":
        return RunContext(
            run_id=str(payload.get("run_id") or ""),
            mode=str(payload.get("mode", "")),
            created_at=str(payload.get("created_at") or ""),
            status=str(payload.get("status", "unknown")),
            url=payload.get("url"),
            plan_source=payload.get("plan_source"),
            plan_path=payload.get("plan_path"),
            spec_path=payload.get("spec_path"),
            artifacts_path=payload.get("artifacts_path"),
            output_format=payload.get("output_format", "text"),
            status_message=payload.get("status_message"),
            summary=payload.get("summary", {}),
        )


def build_run_context(
    *,
    mode: str,
    run_id: str | None = None,
    url: str | None = None,
    plan_source: str | None = None,
    plan_path: str | None = None,
    spec_path: str | None = None,
    artifacts_path: str | None = None,
    output_format: str = "text",
    status: str = "unknown",
    status_message: str | None = None,
    summary: Dict[str, Any] | None = None,
) -> RunContext:
    return RunContext(
        run_id=run_id or build_run_id(),
        created_at=datetime.now(timezone.utc).isoformat(),
        mode=mode,
        status=status,
        url=url,
        plan_source=plan_source,
        plan_path=plan_path,
        spec_path=spec_path,
        artifacts_path=artifacts_path,
        output_format=output_format,
        status_message=status_message,
        summary=summary or {},
    )


__all__ = [
    "RunContext",
    "build_run_id",
    "build_run_context",
    "load_run_context",
    "write_run_context",
    "resolve_run_context_path",
]

