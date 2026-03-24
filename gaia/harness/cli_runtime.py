from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from gaia.harness.registry import HarnessTask, TaskRegistry, load_builtin_registry, load_registry
from gaia.harness.runner import (
    ARTIFACT_ROOT,
    _grade_task_result,
    _latest_report_path,
    _summarize_grades,
    _summarize_results,
    _write_markdown,
    run_task,
)

_GATE_SUMMARY_HIGHER_KEYS = (
    "pass_at_1",
    "pass_at_k",
    "pass_all_k",
    "pass_rate",
    "task_pass_at_1",
    "task_pass_at_k",
    "task_pass_all_k",
)
_GATE_SUMMARY_EQUAL_KEYS = ("task_count",)
_GATE_TASK_KEYS = ("pass_at_k", "pass_all_k")
_TASK_SUMMARY_METRIC_MAP = {
    "pass_at_1": "pass_at_1",
    "pass_at_k": "pass_at_k",
    "pass_all_k": "pass_all_k",
    "pass_rate": "pass_at_k",
    "task_pass_at_1": "pass_at_1",
    "task_pass_at_k": "pass_at_k",
    "task_pass_all_k": "pass_all_k",
}


def build_harness_parser(prog: str = "gaia harness") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Run GAIA evaluation harness.")
    subparsers = parser.add_subparsers(dest="harness_command", required=True)

    ls_parser = subparsers.add_parser("ls", help="List available harness tasks.")
    ls_parser.add_argument("--registry", help="Optional custom task registry JSON path.")
    ls_parser.add_argument("--suite-id", action="append", dest="suite_ids", help="Only include tasks from matching suite IDs.")
    ls_parser.add_argument("--tag", action="append", dest="tags", help="Only include tasks with matching tags.")
    ls_parser.add_argument(
        "--contains",
        action="append",
        dest="contains",
        help="Only include tasks whose id, suite, URL, goal, tags, or metadata contains the text.",
    )
    ls_parser.add_argument("--json", action="store_true", help="Emit JSON instead of tabular text.")

    run_parser = subparsers.add_parser("run", help="Run one or more harness tasks.")
    run_parser.add_argument("--registry", help="Optional custom task registry JSON path.")
    run_parser.add_argument("--suite-id", action="append", dest="suite_ids", help="Only run tasks from matching suite IDs.")
    run_parser.add_argument("--tag", action="append", dest="tags", help="Only run tasks with matching tags.")
    run_parser.add_argument(
        "--contains",
        action="append",
        dest="contains",
        help="Only run tasks whose id, suite, URL, goal, tags, or metadata contains the text.",
    )
    run_parser.add_argument("--task-id")
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--repeats", type=int, default=1, help="Run the selected tasks multiple times.")
    run_parser.add_argument("--timeout-sec", type=int, default=180)
    run_parser.add_argument("--session-prefix", default="harness")
    run_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human-readable summary.")

    report_parser = subparsers.add_parser("report", help="Show a harness report.")
    report_parser.add_argument("--path", help="Path to report.json")
    report_parser.add_argument("--latest", action="store_true")
    report_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human-readable summary.")

    gate_parser = subparsers.add_parser("gate", help="Compare a report against a baseline and fail on regressions.")
    gate_parser.add_argument("--baseline", required=True, help="Path to the baseline report.json.")
    current_group = gate_parser.add_mutually_exclusive_group()
    current_group.add_argument("--path", help="Path to the current report.json.")
    current_group.add_argument("--latest", action="store_true", help="Use the latest harness report.")
    gate_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human-readable summary.")
    return parser


def _resolve_registry(path: str | None) -> TaskRegistry:
    if path:
        return load_registry(Path(path).expanduser().resolve())
    return load_builtin_registry()


def _load_report(path: str | Path, *, label: str) -> tuple[Path, dict[str, Any]]:
    report_path = Path(path).expanduser().resolve()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} report must be a JSON object")
    return report_path, payload


def _resolve_report_path(path: str | None, latest: bool) -> Path:
    if path and latest:
        raise ValueError("Use either --path or --latest, not both")
    if path:
        return Path(path).expanduser().resolve()
    return _latest_report_path()


def _coerce_metric_value(value: Any, *, label: str, metric: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} report metric '{metric}' must be numeric")
    return float(value)


def _coerce_task_metric_value(value: Any, *, label: str, task_id: str, metric: str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} report task '{task_id}' metric '{metric}' must be numeric")
    numeric = float(value)
    if numeric < 0.0 or numeric > 1.0:
        raise ValueError(f"{label} report task '{task_id}' metric '{metric}' must be between 0 and 1")
    return numeric


def _task_id_from_row(row: Mapping[str, Any], *, label: str, index: int) -> str:
    task_id = str(row.get("task_id") or row.get("id") or "").strip()
    if not task_id:
        raise ValueError(f"{label} report row {index} is missing task_id")
    return task_id


def _normalize_gate_tasks(payload: Mapping[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    tasks = payload.get("tasks")
    if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes)):
        normalized: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(tasks, start=1):
            if not isinstance(row, Mapping):
                raise ValueError(f"{label} report task row {index} must be an object")
            task_id = _task_id_from_row(row, label=label, index=index)
            normalized_row = dict(row)
            normalized_row["task_id"] = task_id
            normalized[task_id] = normalized_row
        return normalized

    results = payload.get("results")
    if isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for index, row in enumerate(results, start=1):
            if not isinstance(row, Mapping):
                raise ValueError(f"{label} report result row {index} must be an object")
            task_id = _task_id_from_row(row, label=label, index=index)
            grouped.setdefault(task_id, []).append(row)

        normalized: dict[str, dict[str, Any]] = {}
        for task_id, rows in grouped.items():
            overall_passes = [
                bool(_coerce_task_metric_value(row.get("overall_pass"), label=label, task_id=task_id, metric="overall_pass"))
                for row in rows
            ]
            first_row = rows[0]
            normalized[task_id] = {
                "task_id": task_id,
                "pass_at_k": 1.0 if any(overall_passes) else 0.0,
                "pass_all_k": 1.0 if (overall_passes and all(overall_passes)) else 0.0,
                "pass_at_1": _coerce_task_metric_value(first_row.get("overall_pass"), label=label, task_id=task_id, metric="overall_pass"),
                "attempt_count": len(rows),
            }
        return normalized

    raise ValueError(f"{label} report must contain either a 'tasks' or 'results' array")


def _derive_summary_metric(
    payload: Mapping[str, Any],
    *,
    summary: Mapping[str, Any],
    normalized_tasks: Mapping[str, Mapping[str, Any]],
    metric: str,
    label: str,
) -> float:
    if metric in summary:
        return _coerce_metric_value(summary.get(metric), label=label, metric=metric)

    if metric in _GATE_SUMMARY_EQUAL_KEYS:
        payload_count = payload.get("task_count")
        if isinstance(payload_count, (int, float)) and not isinstance(payload_count, bool):
            return float(payload_count)
        return float(len(normalized_tasks))

    task_metric = _TASK_SUMMARY_METRIC_MAP.get(metric)
    if task_metric:
        if not normalized_tasks:
            return 0.0
        values = [
            _coerce_task_metric_value(task_row.get(task_metric), label=label, task_id=task_id, metric=task_metric)
            for task_id, task_row in normalized_tasks.items()
        ]
        return round(sum(values) / len(values), 4) if values else 0.0

    raise ValueError(f"{label} report is missing summary metric '{metric}'")


def _compare_gate_reports(
    baseline_payload: Mapping[str, Any],
    current_payload: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_summary = baseline_payload.get("summary")
    current_summary = current_payload.get("summary")
    if not isinstance(baseline_summary, Mapping):
        raise ValueError("Baseline report is missing a summary object")
    if not isinstance(current_summary, Mapping):
        raise ValueError("Current report is missing a summary object")

    baseline_tasks = _normalize_gate_tasks(baseline_payload, label="Baseline")
    current_tasks = _normalize_gate_tasks(current_payload, label="Current")

    summary_diffs: list[dict[str, Any]] = []
    summary_metrics = list(_GATE_SUMMARY_EQUAL_KEYS) + list(_GATE_SUMMARY_HIGHER_KEYS)
    for metric in summary_metrics:
        baseline_value = _derive_summary_metric(
            baseline_payload,
            summary=baseline_summary,
            normalized_tasks=baseline_tasks,
            metric=metric,
            label="Baseline",
        )
        current_value = _derive_summary_metric(
            current_payload,
            summary=current_summary,
            normalized_tasks=current_tasks,
            metric=metric,
            label="Current",
        )
        if metric in _GATE_SUMMARY_EQUAL_KEYS:
            if abs(current_value - baseline_value) > 1e-9:
                summary_diffs.append(
                    {
                        "metric": metric,
                        "baseline": baseline_value,
                        "current": current_value,
                        "kind": "mismatch",
                    }
                )
        elif current_value + 1e-9 < baseline_value:
            summary_diffs.append(
                {
                    "metric": metric,
                    "baseline": baseline_value,
                    "current": current_value,
                    "kind": "regression",
                }
            )

    baseline_task_ids = set(baseline_tasks)
    current_task_ids = set(current_tasks)
    task_diffs: list[dict[str, Any]] = []

    missing_from_current = sorted(baseline_task_ids - current_task_ids)
    extra_in_current = sorted(current_task_ids - baseline_task_ids)
    if missing_from_current or extra_in_current:
        task_diffs.append(
            {
                "kind": "task_set_mismatch",
                "missing_from_current": missing_from_current,
                "extra_in_current": extra_in_current,
            }
        )

    for task_id in sorted(baseline_task_ids & current_task_ids):
        baseline_row = baseline_tasks[task_id]
        current_row = current_tasks[task_id]
        for metric in _GATE_TASK_KEYS:
            if metric not in baseline_row:
                raise ValueError(f"Baseline report task '{task_id}' is missing metric '{metric}'")
            if metric not in current_row:
                raise ValueError(f"Current report task '{task_id}' is missing metric '{metric}'")
            baseline_value = _coerce_task_metric_value(baseline_row.get(metric), label="Baseline", task_id=task_id, metric=metric)
            current_value = _coerce_task_metric_value(current_row.get(metric), label="Current", task_id=task_id, metric=metric)
            if current_value + 1e-9 < baseline_value:
                task_diffs.append(
                    {
                        "kind": "regression",
                        "task_id": task_id,
                        "metric": metric,
                        "baseline": baseline_value,
                        "current": current_value,
                    }
                )

    return {
        "summary_diffs": summary_diffs,
        "task_diffs": task_diffs,
        "regression_count": len(summary_diffs) + len(task_diffs),
    }


def _format_gate_report(result: Mapping[str, Any]) -> list[str]:
    lines = [
        f"baseline: {result.get('baseline_path')}",
        f"current: {result.get('current_path')}",
        f"status: {result.get('status')}",
    ]
    summary_diffs = result.get("summary_diffs")
    if isinstance(summary_diffs, Sequence) and not isinstance(summary_diffs, (str, bytes)) and summary_diffs:
        lines.append("summary:")
        for diff in summary_diffs:
            if not isinstance(diff, Mapping):
                continue
            lines.append(
                f"- {diff.get('metric')}: baseline={diff.get('baseline')} current={diff.get('current')} ({diff.get('kind')})"
            )
    task_diffs = result.get("task_diffs")
    if isinstance(task_diffs, Sequence) and not isinstance(task_diffs, (str, bytes)) and task_diffs:
        lines.append("tasks:")
        for diff in task_diffs:
            if not isinstance(diff, Mapping):
                continue
            kind = diff.get("kind")
            if kind == "task_set_mismatch":
                lines.append(
                    f"- task set mismatch: missing_from_current={diff.get('missing_from_current', [])} "
                    f"extra_in_current={diff.get('extra_in_current', [])}"
                )
                continue
            lines.append(
                f"- {diff.get('task_id')} {diff.get('metric')}: baseline={diff.get('baseline')} current={diff.get('current')}"
            )
    return lines


def _normalize_values(values: Sequence[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    normalized: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _task_matches(
    task: HarnessTask,
    *,
    suite_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    contains: Sequence[str] | None = None,
) -> bool:
    suite_ids = _normalize_values(suite_ids)
    tags = _normalize_values(tags)
    contains = _normalize_values(contains)

    if suite_ids and task.suite_id not in suite_ids:
        return False
    if tags and not set(tags).intersection(task.tags):
        return False
    if contains:
        metadata_blob = json.dumps(task.metadata, ensure_ascii=False, sort_keys=True) if task.metadata else ""
        haystack = " ".join(
            [
                task.id,
                task.suite_id,
                task.suite_path,
                task.url,
                task.goal,
                " ".join(task.tags),
                metadata_blob,
            ]
        ).lower()
        if not any(term.lower() in haystack for term in contains):
            return False
    return True


def _select_tasks(
    registry: TaskRegistry,
    *,
    task_id: str | None = None,
    limit: int | None = None,
    suite_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    contains: Sequence[str] | None = None,
) -> list[HarnessTask]:
    tasks = [task for task in registry.tasks if _task_matches(task, suite_ids=suite_ids, tags=tags, contains=contains)]
    if task_id is not None:
        selected = registry.get(task_id)
        if selected is None or selected not in tasks:
            return []
        tasks = [selected]
    if limit is not None:
        tasks = tasks[: max(int(limit), 0)]
    return tasks


def _run_registry(
    registry: TaskRegistry,
    *,
    task_id: str | None = None,
    limit: int | None = None,
    repeats: int = 1,
    suite_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    contains: Sequence[str] | None = None,
    python_executable: str = sys.executable,
    timeout_sec: int = 1800,
    env: Mapping[str, str] | None = None,
    session_prefix: str = "harness",
) -> dict[str, Any]:
    tasks = _select_tasks(
        registry,
        task_id=task_id,
        limit=limit,
        suite_ids=suite_ids,
        tags=tags,
        contains=contains,
    )
    repeat_count = max(int(repeats), 1)
    results: list[dict[str, Any]] = []
    task_groups: dict[str, list[dict[str, Any]]] = {}

    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    for repeat_index in range(1, repeat_count + 1):
        for index, task in enumerate(tasks, start=1):
            session_id = f"{session_prefix}:{task.id}:r{repeat_index}:i{index}"
            row = run_task(
                task,
                python_executable=python_executable,
                timeout_sec=timeout_sec,
                env=env,
                session_id=session_id,
            )
            grades = _grade_task_result(task, row)
            row["task_index"] = index
            row["repeat_index"] = repeat_index
            row["repeat_count"] = repeat_count
            row["grades"] = [grade.to_dict() for grade in grades]
            row["overall_pass"] = all(bool(grade.passed) for grade in grades)
            results.append(row)
            task_groups.setdefault(task.id, []).append(row)

    task_reports: list[dict[str, Any]] = []
    for task in tasks:
        task_rows = task_groups.get(task.id, [])
        pass_count = sum(1 for row in task_rows if bool(row.get("overall_pass")))
        reason_code_counts = _summarize_results(task_rows).get("reason_code_counts", {}) if task_rows else {}
        task_reports.append(
            {
                "task_id": task.id,
                "suite_id": task.suite_id,
                "goal": task.goal,
                "url": task.url,
                "repeats": repeat_count,
                "rows": task_rows,
                "attempts": task_rows,
                "attempt_count": len(task_rows),
                "attempt_success_count": pass_count,
                "attempt_failure_count": len(task_rows) - pass_count,
                "overall_pass": bool(pass_count),
                "status": "SUCCESS" if pass_count else "FAIL",
                "best_attempt_index": next((row.get("repeat_index") for row in task_rows if bool(row.get("overall_pass"))), task_rows[0].get("repeat_index") if task_rows else 0),
                "pass_at_1": bool(task_rows and bool(task_rows[0].get("overall_pass"))),
                "pass_at_k": bool(task_rows and any(bool(row.get("overall_pass")) for row in task_rows)),
                "pass_all_k": bool(task_rows and all(bool(row.get("overall_pass")) for row in task_rows)),
                "grade_summary": _summarize_grades(task_rows),
                "reason_code_summary": reason_code_counts,
                "reason_code_counts": reason_code_counts,
                "top_reason_codes": [
                    {"reason_code": code, "count": count}
                    for code, count in sorted(reason_code_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
                ],
            }
        )

    run_id = f"harness_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    artifact_dir = ARTIFACT_ROOT / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    durations = [float(row.get("duration_seconds") or 0.0) for row in results]
    summary = _summarize_results(task_reports)
    summary["pass_rate"] = round(sum(1 for row in results if bool(row.get("overall_pass"))) / len(results), 4) if results else 0.0
    summary["avg_duration_seconds"] = round(sum(durations) / len(durations), 2) if durations else 0.0
    reason_code_summary = _summarize_results(results).get("reason_code_counts", {})
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "registry": str(registry.source) if registry.source else None,
        "task_count": len(tasks),
        "repeats": repeat_count,
        "selection": {
            "task_id": task_id,
            "limit": limit,
            "suite_ids": list(_normalize_values(suite_ids)),
            "tags": list(_normalize_values(tags)),
            "contains": list(_normalize_values(contains)),
        },
        "results": results,
        "tasks": task_reports,
        "summary": summary,
        "grade_summary": _summarize_grades(results),
        "reason_code_summary": reason_code_summary,
        "reason_code_counts": reason_code_summary,
        "top_reason_codes": [
            {"reason_code": code, "count": count}
            for code, count in sorted(reason_code_summary.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
        ],
        "artifact_dir": str(artifact_dir),
    }
    (artifact_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(artifact_dir / "report.md", payload)
    return payload


def run_harness_cli(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    parser = build_harness_parser()
    if not args or args[0] in {"-h", "--help", "help"}:
        parser.print_help()
        return 0

    parsed = parser.parse_args(args)
    if parsed.harness_command == "ls":
        registry = _resolve_registry(getattr(parsed, "registry", None))
        registry = TaskRegistry(
            tasks=tuple(
                task
                for task in registry.tasks
                if _task_matches(
                    task,
                    suite_ids=getattr(parsed, "suite_ids", None),
                    tags=getattr(parsed, "tags", None),
                    contains=getattr(parsed, "contains", None),
                )
            ),
            metadata=dict(registry.metadata),
            source=registry.source,
        )
        rows = [task.as_dict() for task in registry.tasks]
        if parsed.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        for task in rows:
            print(f"{task.get('id')}\t{task.get('suite_id') or '-'}\t{task.get('url')}")
        return 0

    if parsed.harness_command == "run":
        registry = _resolve_registry(getattr(parsed, "registry", None))
        payload = _run_registry(
            registry,
            task_id=parsed.task_id,
            limit=parsed.limit,
            repeats=getattr(parsed, "repeats", 1),
            suite_ids=getattr(parsed, "suite_ids", None),
            tags=getattr(parsed, "tags", None),
            contains=getattr(parsed, "contains", None),
            timeout_sec=max(10, int(parsed.timeout_sec)),
            session_prefix=str(parsed.session_prefix or "harness"),
        )
        if parsed.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"run_id: {payload.get('run_id')}")
            print(f"artifact_dir: {payload.get('artifact_dir')}")
            print(f"task_count: {payload.get('task_count')}")
            print(f"repeats: {payload.get('repeats')}")
            selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
            for key, value in selection.items():
                if value not in (None, [], ""):
                    print(f"{key}: {value}")
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            for key, value in summary.items():
                print(f"{key}: {value}")
        return 0

    if parsed.harness_command == "report":
        report_path = Path(parsed.path).expanduser().resolve() if parsed.path else _latest_report_path()
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if parsed.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        print(f"report: {report_path}")
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0

    if parsed.harness_command == "gate":
        try:
            baseline_path, baseline_payload = _load_report(parsed.baseline, label="Baseline")
            current_path = _resolve_report_path(getattr(parsed, "path", None), bool(getattr(parsed, "latest", False)))
            current_path, current_payload = _load_report(current_path, label="Current")
            comparison = _compare_gate_reports(baseline_payload, current_payload)
            result = {
                "baseline_path": str(baseline_path),
                "current_path": str(current_path),
                "summary_diffs": comparison["summary_diffs"],
                "task_diffs": comparison["task_diffs"],
                "regression_count": comparison["regression_count"],
                "status": "FAIL" if comparison["regression_count"] else "PASS",
            }
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if parsed.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for line in _format_gate_report(result):
                print(line)
        return 1 if result["regression_count"] else 0

    return 0


def dispatch_harness_command(command: str, argv: Sequence[str] | None = None) -> int:
    return run_harness_cli([command, *(list(argv or []))])
