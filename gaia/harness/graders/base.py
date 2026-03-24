from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..report_schema import GraderOutcome


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    aliases = {
        "success": "passed",
        "succeeded": "passed",
        "pass": "passed",
        "passed": "passed",
        "ok": "passed",
        "done": "passed",
        "completed": "passed",
        "failure": "failed",
        "failed": "failed",
        "fail": "failed",
        "error": "failed",
        "skipped": "skipped",
        "skip": "skipped",
        "unknown": "unknown",
    }
    return aliases.get(text, text)


def _coerce_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _extract_path(data: Any, path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return default
        if part not in current:
            return default
        current = current[part]
    return default if current is None else current


def _first_present(data: Any, paths: Sequence[str]) -> Any:
    for path in paths:
        value = _extract_path(data, path, default=None)
        if value is not None:
            return value
    return None


@dataclass(frozen=True, slots=True)
class GraderConfig:
    name: str
    payload_paths: tuple[str, ...] = ()


class BaseGrader(ABC):
    """Base class for deterministic GAIA graders."""

    config: GraderConfig

    def __init__(self, name: str, payload_paths: Iterable[str] = ()) -> None:
        self.config = GraderConfig(name=name, payload_paths=tuple(payload_paths))

    @property
    def name(self) -> str:
        return self.config.name

    def _resolve_payload(self, payload: Any) -> Mapping[str, Any]:
        if not self.config.payload_paths:
            mapping = _coerce_mapping(payload)
            return mapping or {}

        for path in self.config.payload_paths:
            resolved = _extract_path(payload, path, default=None)
            mapping = _coerce_mapping(resolved)
            if mapping is not None:
                return mapping
        mapping = _coerce_mapping(payload)
        return mapping or {}

    @abstractmethod
    def grade(self, payload: Any) -> GraderOutcome:
        raise NotImplementedError

    @staticmethod
    def normalize_text(value: Any) -> str:
        return _normalize_text(value)

    @staticmethod
    def extract_path(data: Any, path: str, default: Any = None) -> Any:
        return _extract_path(data, path, default)

    @staticmethod
    def first_present(data: Any, paths: Sequence[str]) -> Any:
        return _first_present(data, paths)

