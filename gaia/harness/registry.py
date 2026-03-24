"""JSON task registry for GAIA harness runs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_DIR = ROOT / "tests" / "scenarios"
DESTINATION_HINTS = (
    "위시리스트",
    "장바구니",
    "시간표",
    "내 시간표",
    "선택 목록",
    "내 목록",
)


def _coerce_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _merge_mappings(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge_mappings(existing, value)
        else:
            merged[key] = value
    return merged


def _split_harness(value: Any) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    harness = _coerce_mapping(value, field_name="harness")
    tags = [str(item) for item in harness.get("tags", []) if str(item).strip()] if isinstance(harness.get("tags"), list) else []
    graders = _coerce_mapping(harness.get("graders"), field_name="harness.graders")
    graders = _merge_mappings(graders, _coerce_mapping(harness.get("grader_overrides"), field_name="harness.grader_overrides"))
    metadata = {key: value for key, value in harness.items() if key not in {"tags", "graders", "grader_overrides"}}
    return metadata, graders, tags


def _task_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _coerce_mapping(raw.get("metadata"), field_name="metadata")
    metadata = _merge_mappings(metadata, _coerce_mapping(raw.get("task_metadata"), field_name="task_metadata"))

    harness_metadata, _, _ = _split_harness(raw.get("harness"))
    metadata = _merge_mappings(metadata, harness_metadata)

    known_keys = {
        "id",
        "task_id",
        "name",
        "url",
        "start_url",
        "goal",
        "query",
        "scenario",
        "constraints",
        "suite_id",
        "suite_path",
        "suite_metadata",
        "expected_signals",
        "graders",
        "grader_configs",
        "grader_overrides",
        "metadata",
        "task_metadata",
        "harness",
        "tags",
    }
    for key, value in raw.items():
        if key not in known_keys:
            metadata[key] = value
    return metadata


def _registry_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _coerce_mapping(raw.get("metadata"), field_name="metadata")
    metadata = _merge_mappings(metadata, _coerce_mapping(raw.get("suite_metadata"), field_name="suite_metadata"))

    harness_metadata, _, _ = _split_harness(raw.get("harness"))
    metadata = _merge_mappings(metadata, harness_metadata)

    known_keys = {
        "tasks",
        "scenarios",
        "metadata",
        "suite_metadata",
        "grader_configs",
        "graders",
        "grader_overrides",
        "harness",
        "suite_id",
    }
    for key, value in raw.items():
        if key not in known_keys:
            metadata[key] = value
    return metadata


@dataclass(slots=True)
class HarnessTask:
    """A single runnable task in the harness registry."""

    id: str
    url: str
    goal: str
    suite_id: str = ""
    suite_path: str = ""
    suite_metadata: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    expected_signals: list[str] = field(default_factory=list)
    grader_configs: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.metadata)
        payload["id"] = self.id
        payload["url"] = self.url
        payload["goal"] = self.goal
        if self.suite_id:
            payload["suite_id"] = self.suite_id
        if self.suite_path:
            payload["suite_path"] = self.suite_path
        if self.suite_metadata:
            payload["suite_metadata"] = dict(self.suite_metadata)
        if self.constraints:
            payload["constraints"] = dict(self.constraints)
        if self.expected_signals:
            payload["expected_signals"] = list(self.expected_signals)
        if self.grader_configs:
            payload["grader_configs"] = dict(self.grader_configs)
            payload["graders"] = dict(self.grader_configs)
        if self.tags:
            payload["tags"] = list(self.tags)
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


def _coerce_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _coerce_constraints(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("constraints must be a JSON object")
    return dict(value)


def _parse_task(
    raw: Mapping[str, Any],
    *,
    suite_metadata: Mapping[str, Any] | None = None,
    grader_config_defaults: Mapping[str, Any] | None = None,
) -> HarnessTask:
    task_id = _coerce_text(
        raw.get("id") or raw.get("task_id") or raw.get("name"),
        field_name="task id",
    )
    url = _coerce_text(raw.get("url") or raw.get("start_url"), field_name="task url")
    goal = _coerce_text(
        raw.get("goal") or raw.get("query") or raw.get("scenario"),
        field_name="task goal",
    )
    constraints = _coerce_constraints(raw.get("constraints"))
    expected_signals = [str(item) for item in raw.get("expected_signals", []) if str(item).strip()] if isinstance(raw.get("expected_signals"), list) else []
    grader_configs: dict[str, Any] = {}
    if grader_config_defaults:
        grader_configs = _merge_mappings(grader_configs, grader_config_defaults)
    grader_configs = _merge_mappings(grader_configs, _coerce_mapping(raw.get("graders"), field_name="graders"))
    grader_configs = _merge_mappings(grader_configs, _coerce_mapping(raw.get("grader_configs"), field_name="grader_configs"))
    grader_configs = _merge_mappings(grader_configs, _coerce_mapping(raw.get("grader_overrides"), field_name="grader_overrides"))
    harness_metadata, harness_graders, harness_tags = _split_harness(raw.get("harness"))
    grader_configs = _merge_mappings(grader_configs, harness_graders)
    tags = [str(item) for item in raw.get("tags", []) if str(item).strip()] if isinstance(raw.get("tags"), list) else []
    for tag in harness_tags:
        if tag not in tags:
            tags.append(tag)
    suite_metadata_dict = _coerce_mapping(suite_metadata, field_name="suite_metadata")
    suite_metadata_dict = _merge_mappings(suite_metadata_dict, _coerce_mapping(raw.get("suite_metadata"), field_name="suite_metadata"))
    suite_metadata_dict = _merge_mappings(suite_metadata_dict, harness_metadata)
    metadata = _task_metadata(raw)
    return HarnessTask(
        id=task_id,
        url=url,
        goal=goal,
        suite_id=str(raw.get("suite_id") or "").strip(),
        suite_path=str(raw.get("suite_path") or "").strip(),
        suite_metadata=suite_metadata_dict,
        constraints=constraints,
        expected_signals=expected_signals,
        grader_configs=grader_configs,
        tags=tags,
        metadata=metadata,
    )


def _extract_quoted_terms(goal: str) -> list[str]:
    found: list[str] = []
    for pattern in (r"'([^']{2,80})'", r'"([^"]{2,80})"', r"“([^”]{2,80})”"):
        for match in re.finditer(pattern, goal):
            value = str(match.group(1) or "").strip()
            if value and value not in found:
                found.append(value)
    return found


def _infer_destination_terms(goal: str) -> list[str]:
    terms: list[str] = []
    for hint in DESTINATION_HINTS:
        if hint in goal and hint not in terms:
            terms.append(hint)
    return terms


def _default_grader_configs(goal: str, constraints: Mapping[str, Any], expected_signals: Sequence[str]) -> dict[str, Any]:
    grader_configs: dict[str, Any] = {
        "status": {"expected_statuses": ["passed"]},
        "reason_codes": {
            "forbidden_reason_codes": [
                "user_intervention_missing",
                "dom_snapshot_retry_exhausted",
            ]
        },
    }
    destination_terms = _infer_destination_terms(goal)
    target_terms = _extract_quoted_terms(goal)
    expected = {str(item).strip() for item in expected_signals if str(item).strip()}
    if destination_terms and (
        expected.intersection(
            {
                "wishlist_count_increased",
                "wishlist_count_decreased",
                "zero_state_visible",
                "combination_applied",
                "timetable_updated",
            }
        )
        or any(term in goal for term in destination_terms)
    ):
        grader_configs["membership"] = {
            "expected_present": True,
            "destination_terms": destination_terms,
            "target_terms": target_terms,
        }
    if bool(constraints.get("requires_test_credentials")):
        grader_configs["reason_codes"]["forbidden_reason_codes"] = [
            "user_intervention_missing",
            "dom_snapshot_retry_exhausted",
        ]
    return grader_configs


def _merge_grader_configs(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    return _merge_mappings(base, override)


@dataclass(slots=True)
class TaskRegistry:
    """In-memory registry for harness tasks."""

    tasks: tuple[HarnessTask, ...]
    suite_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    grader_configs: dict[str, Any] = field(default_factory=dict)
    source: Path | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, source: Path | None = None) -> "TaskRegistry":
        if not isinstance(payload, Mapping):
            raise ValueError("registry payload must be a JSON object")

        tasks_raw = payload.get("tasks")
        if tasks_raw is None:
            tasks_raw = payload.get("scenarios")
        if not isinstance(tasks_raw, Sequence) or isinstance(tasks_raw, (str, bytes)) or not tasks_raw:
            raise ValueError("registry.tasks must be a non-empty array")

        grader_configs = _coerce_mapping(payload.get("grader_configs"), field_name="grader_configs")
        grader_configs = _merge_mappings(grader_configs, _coerce_mapping(payload.get("graders"), field_name="graders"))
        grader_configs = _merge_mappings(grader_configs, _coerce_mapping(payload.get("grader_overrides"), field_name="grader_overrides"))
        tasks = []
        for item in tasks_raw:
            if not isinstance(item, Mapping):
                raise ValueError("registry tasks must be JSON objects")
            tasks.append(_parse_task(item, grader_config_defaults=grader_configs))

        metadata = _registry_metadata(payload)
        suite_id = str(payload.get("suite_id") or "").strip()
        return cls(
            tasks=tuple(tasks),
            suite_id=suite_id,
            metadata=dict(metadata),
            grader_configs=dict(grader_configs),
            source=source,
        )

    @classmethod
    def load(cls, path: Path | str) -> "TaskRegistry":
        resolved = Path(path).expanduser()
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        return cls.from_dict(payload, source=resolved)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.metadata)
        if self.suite_id:
            payload["suite_id"] = self.suite_id
        if self.grader_configs:
            payload["grader_configs"] = dict(self.grader_configs)
        payload["tasks"] = [task.as_dict() for task in self.tasks]
        return payload

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path or self.source or "tasks.json").expanduser()
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def __iter__(self) -> Iterable[HarnessTask]:
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    def get(self, task_id: str) -> HarnessTask | None:
        target = str(task_id).strip()
        for task in self.tasks:
            if task.id == target:
                return task
        return None

    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.tasks)


def load_registry(path: Path | str) -> TaskRegistry:
    return TaskRegistry.load(path)


def default_suite_paths() -> list[Path]:
    if not DEFAULT_SUITE_DIR.exists():
        return []
    return sorted(DEFAULT_SUITE_DIR.glob("*.json"))


def load_suite_registry(path: Path | str) -> TaskRegistry:
    resolved = Path(path).expanduser().resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("suite payload must be an object")
    suite_id = str(payload.get("suite_id") or resolved.stem).strip()
    suite_metadata = _registry_metadata(payload)
    suite_harness_metadata, suite_harness_graders, suite_harness_tags = _split_harness(payload.get("harness"))
    suite_metadata = _merge_mappings(suite_metadata, suite_harness_metadata)
    suite_grader_configs = _coerce_mapping(payload.get("grader_configs"), field_name="grader_configs")
    suite_grader_configs = _merge_mappings(suite_grader_configs, _coerce_mapping(payload.get("graders"), field_name="graders"))
    suite_grader_configs = _merge_mappings(suite_grader_configs, _coerce_mapping(payload.get("grader_overrides"), field_name="grader_overrides"))
    suite_grader_configs = _merge_mappings(suite_grader_configs, suite_harness_graders)
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, Sequence) or isinstance(scenarios, (str, bytes)) or not scenarios:
        if str(payload.get("id") or "").strip() and str(payload.get("url") or "").strip() and str(payload.get("goal") or "").strip():
            scenarios = [payload]
        else:
            raise ValueError("suite.scenarios must be a non-empty array")
    tasks: list[HarnessTask] = []
    for item in scenarios:
        if not isinstance(item, Mapping):
            continue
        goal = str(item.get("goal") or item.get("query") or item.get("scenario") or "")
        constraints = _coerce_constraints(item.get("constraints"))
        expected_signals = [str(v) for v in item.get("expected_signals", []) if str(v).strip()] if isinstance(item.get("expected_signals"), list) else []
        parsed = _parse_task(
            item,
            suite_metadata=suite_metadata,
            grader_config_defaults=_merge_grader_configs(
                _default_grader_configs(goal, constraints, expected_signals),
                suite_grader_configs,
            ),
        )
        parsed.suite_id = suite_id
        parsed.suite_path = str(resolved)
        difficulty = str(item.get("difficulty") or "").strip()
        if difficulty:
            parsed.tags.append(difficulty)
        for tag in suite_harness_tags:
            if tag not in parsed.tags:
                parsed.tags.append(tag)
        parsed.tags.append(suite_id)
        tasks.append(parsed)
    return TaskRegistry(tasks=tuple(tasks), suite_id=suite_id, metadata=dict(suite_metadata), grader_configs=dict(suite_grader_configs), source=resolved)


def load_builtin_registry(*, suite_paths: Sequence[Path] | None = None) -> TaskRegistry:
    registries = []
    for path in (list(suite_paths) if suite_paths else default_suite_paths()):
        try:
            registries.append(load_suite_registry(path))
        except Exception:
            continue
    tasks: list[HarnessTask] = []
    for registry in registries:
        tasks.extend(registry.tasks)
    return TaskRegistry(tasks=tuple(tasks), metadata={"kind": "builtin"}, source=None)
