"""Runner for GAIA harness tasks using the terminal one-shot path."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from .graders.blocked_vs_fail import BlockedVsFailGrader
from .graders.expected_signals import ExpectedSignalsGrader
from .graders.membership import MembershipGrader
from .graders.reason_codes import ReasonCodesGrader
from .graders.status import StatusGrader
from .registry import HarnessTask, TaskRegistry, load_builtin_registry, load_registry
from .report_schema import GraderOutcome

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

ARTIFACT_ROOT = WORKSPACE_ROOT / "artifacts" / "harness"


def _normalize_status(summary: Mapping[str, Any], exit_code: int) -> str:
    final_status = str(summary.get("final_status") or "").strip() or str(summary.get("status") or "").strip()
    if final_status:
        return final_status
    return "SUCCESS" if int(exit_code) == 0 else "FAIL"


def _task_payload(task: HarnessTask) -> dict[str, Any]:
    return task.as_dict()


def _build_child_code(task: Mapping[str, Any], session_id: str) -> str:
    payload = json.dumps({"task": task, "session_id": session_id}, ensure_ascii=False)
    return f"""
import contextlib, io, json, os
from gaia.terminal import _build_test_goal, run_chat_terminal_once
payload = json.loads({payload!r})
task = payload["task"]
session_id = payload["session_id"]
prepared_goal = _build_test_goal(url=task["url"], query=task["goal"])
constraints = task.get("constraints") if isinstance(task.get("constraints"), dict) else {{}}
expected_signals = task.get("expected_signals") if isinstance(task.get("expected_signals"), list) else []
goal_test_data = dict(getattr(prepared_goal, "test_data", {{}}) or {{}})
prepared_goal.expected_signals = [str(item) for item in expected_signals if str(item).strip()]
if prepared_goal.expected_signals:
    goal_test_data["harness_expected_signals"] = list(prepared_goal.expected_signals)
if constraints.get("requires_test_credentials"):
    username = (os.getenv("GAIA_TEST_USERNAME") or os.getenv("GAIA_AUTH_USERNAME") or "").strip()
    password = (os.getenv("GAIA_TEST_PASSWORD") or os.getenv("GAIA_AUTH_PASSWORD") or "").strip()
    email = (os.getenv("GAIA_TEST_EMAIL") or os.getenv("GAIA_AUTH_EMAIL") or "").strip()
    if username and password:
        goal_test_data["username"] = username
        goal_test_data["password"] = password
        goal_test_data.setdefault("auth_mode", "provided_credentials")
        goal_test_data.setdefault("return_credentials", True)
        if email:
            goal_test_data["email"] = email
prepared_goal.test_data = goal_test_data
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    code, summary = run_chat_terminal_once(
        url=task["url"],
        query=task["goal"],
        session_id=session_id,
        prepared_goal=prepared_goal,
    )
result = {{
    "exit_code": int(code),
    "summary": summary,
    "captured_log": buf.getvalue(),
}}
print(json.dumps(result, ensure_ascii=False))
"""


def run_task(
    task: HarnessTask,
    *,
    python_executable: str = sys.executable,
    timeout_sec: int = 1800,
    env: Mapping[str, str] | None = None,
    session_id: str | None = None,
) -> Dict[str, Any]:
    task_payload = _task_payload(task)
    code = _build_child_code(task_payload, session_id or task.id)
    started = time.monotonic()
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    try:
        proc = subprocess.run(
            [python_executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=run_env,
            cwd=str(WORKSPACE_ROOT),
            check=False,
        )
        duration = round(time.monotonic() - started, 2)
    except subprocess.TimeoutExpired as exc:
        return {
            "task_id": task.id,
            "suite_id": task.suite_id,
            "goal": task.goal,
            "url": task.url,
            "constraints": dict(task.constraints),
            "status": "FAIL",
            "final_status": "FAIL",
            "reason": f"harness_timeout({timeout_sec}s)",
            "exit_code": 124,
            "duration_seconds": round(time.monotonic() - started, 2),
            "summary": {},
            "captured_log": str(exc.stdout or "") + str(exc.stderr or ""),
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    payload: Dict[str, Any] = {}
    if stdout:
        last_line = stdout.splitlines()[-1]
        try:
            parsed = json.loads(last_line)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    exit_code = int(payload.get("exit_code") if isinstance(payload.get("exit_code"), int) else proc.returncode)
    status = _normalize_status(summary, exit_code)
    reason = str(summary.get("reason") or stderr or "")
    return {
        "task_id": task.id,
        "suite_id": task.suite_id,
        "goal": task.goal,
        "url": task.url,
        "constraints": dict(task.constraints),
        "status": status,
        "final_status": str(summary.get("final_status") or status),
        "reason": reason,
        "exit_code": exit_code,
        "duration_seconds": duration,
        "summary": summary,
        "captured_log": payload.get("captured_log") if isinstance(payload.get("captured_log"), str) else stderr,
    }


def _grade_task_result(task: HarnessTask, row: Mapping[str, Any]) -> list[GraderOutcome]:
    grades: list[GraderOutcome] = []
    status_cfg = task.grader_configs.get("status", {}) if isinstance(task.grader_configs.get("status"), dict) else {}
    grades.append(StatusGrader(expected_statuses=status_cfg.get("expected_statuses", ("passed",))).grade(row))
    reason_cfg = task.grader_configs.get("reason_codes", {}) if isinstance(task.grader_configs.get("reason_codes"), dict) else {}
    grades.append(
        ReasonCodesGrader(
            required_reason_codes=reason_cfg.get("required_reason_codes", ()),
            forbidden_reason_codes=reason_cfg.get("forbidden_reason_codes", ()),
            minimum_counts=reason_cfg.get("minimum_counts", {}),
        ).grade(row)
    )
    if isinstance(task.expected_signals, list) and task.expected_signals:
        grades.append(
            ExpectedSignalsGrader(required_signals=task.expected_signals).grade(row)
        )
    membership_cfg = task.grader_configs.get("membership", {}) if isinstance(task.grader_configs.get("membership"), dict) else {}
    if membership_cfg:
        grades.append(
            MembershipGrader(
                expected_present=bool(membership_cfg.get("expected_present", True)),
                destination_terms=membership_cfg.get("destination_terms", ()),
                target_terms=membership_cfg.get("target_terms", ()),
            ).grade(row)
        )
    blocked_cfg_raw = task.grader_configs.get("blocked_vs_fail")
    if blocked_cfg_raw not in (None, False):
        blocked_cfg = blocked_cfg_raw if isinstance(blocked_cfg_raw, dict) else {}
        blocked_kwargs = {
            key: blocked_cfg[key]
            for key in (
                "allowed_blocked_statuses",
                "forbidden_fail_markers",
                "allowed_blocked_markers",
                "forbidden_fail_statuses",
                "payload_paths",
                "final_status_paths",
                "reason_paths",
                "reason_code_summary_paths",
            )
            if key in blocked_cfg
        }
        grades.append(
            BlockedVsFailGrader(**blocked_kwargs).grade(row)
        )
    return grades


def _select_tasks(
    registry: TaskRegistry,
    *,
    task_id: str | None = None,
    limit: int | None = None,
) -> list[HarnessTask]:
    tasks = list(registry.tasks)
    if task_id is not None:
        selected = registry.get(task_id)
        if selected is None:
            raise KeyError(f"Task not found: {task_id}")
        tasks = [selected]
    if limit is not None:
        tasks = tasks[: max(int(limit), 0)]
    return tasks


def _summarize_results(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    task_count = len(rows)
    attempts_total = 0
    task_status_counts: Counter[str] = Counter()
    attempt_status_counts: Counter[str] = Counter()
    pass_at_1_count = 0
    pass_at_k_count = 0
    pass_all_k_count = 0
    attempt_success_count = 0

    for row in rows:
        attempts = row.get("rows")
        if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
            attempts = row.get("attempts")
        if isinstance(attempts, Sequence) and not isinstance(attempts, (str, bytes)):
            attempts_list = [attempt for attempt in attempts if isinstance(attempt, Mapping)]
        else:
            attempts_list = [row]

        attempts_total += len(attempts_list)
        task_status_counts[str(row.get("status") or "FAIL")] += 1
        pass_at_1_count += 1 if bool(row.get("pass_at_1")) else 0
        pass_at_k_count += 1 if bool(row.get("pass_at_k")) else 0
        pass_all_k_count += 1 if bool(row.get("pass_all_k")) else 0
        for attempt in attempts_list:
            attempt_status_counts[str(attempt.get("status") or "FAIL")] += 1
            attempt_success_count += 1 if bool(attempt.get("overall_pass")) else 0

    reason_code_counts = _summarize_reason_codes(rows)
    top_reason_codes = [
        {"reason_code": code, "count": count}
        for code, count in sorted(reason_code_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
    ]
    summary = {
        "runs_total": task_count,
        "task_count": task_count,
        "attempts_total": attempts_total,
        "success_count": pass_at_k_count,
        "failed_count": task_count - pass_at_k_count,
        "attempt_success_count": attempt_success_count,
        "attempt_failed_count": attempts_total - attempt_success_count,
        "pass_at_1": round(pass_at_1_count / task_count, 4) if task_count else 0.0,
        "pass_at_k": round(pass_at_k_count / task_count, 4) if task_count else 0.0,
        "pass_all_k": round(pass_all_k_count / task_count, 4) if task_count else 0.0,
        "pass_rate": round(pass_at_k_count / task_count, 4) if task_count else 0.0,
        "task_pass_at_1": round(pass_at_1_count / task_count, 4) if task_count else 0.0,
        "task_pass_at_k": round(pass_at_k_count / task_count, 4) if task_count else 0.0,
        "task_pass_all_k": round(pass_all_k_count / task_count, 4) if task_count else 0.0,
        "task_status_counts": dict(task_status_counts),
        "attempt_status_counts": dict(attempt_status_counts),
        "other_status_counts": {key: value for key, value in task_status_counts.items() if key not in {"SUCCESS", "FAIL"}},
        "reason_code_counts": reason_code_counts,
        "reason_code_total": sum(int(value) for value in reason_code_counts.values()),
        "top_reason_codes": top_reason_codes,
    }
    return summary


def _summarize_grades(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        attempt_rows = row.get("rows")
        if not isinstance(attempt_rows, Sequence) or isinstance(attempt_rows, (str, bytes)):
            attempt_rows = row.get("attempts")
        if isinstance(attempt_rows, Sequence) and not isinstance(attempt_rows, (str, bytes)):
            row_iter = [attempt for attempt in attempt_rows if isinstance(attempt, Mapping)]
        else:
            row_iter = [row]
        for attempt in row_iter:
            grades = attempt.get("grades", [])
            if not isinstance(grades, Sequence) or isinstance(grades, (str, bytes)):
                continue
            for grade in grades:
                if not isinstance(grade, Mapping):
                    continue
                grader = str(grade.get("grader") or "unknown")
                bucket = summary.setdefault(grader, {"pass": 0, "fail": 0})
                bucket["pass" if bool(grade.get("passed")) else "fail"] += 1
    return summary


def _summarize_reason_codes(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        attempt_rows = row.get("rows")
        if not isinstance(attempt_rows, Sequence) or isinstance(attempt_rows, (str, bytes)):
            attempt_rows = row.get("attempts")
        if isinstance(attempt_rows, Sequence) and not isinstance(attempt_rows, (str, bytes)):
            row_iter = [attempt for attempt in attempt_rows if isinstance(attempt, Mapping)]
        else:
            row_iter = [row]
        for attempt in row_iter:
            summary = attempt.get("summary")
            if not isinstance(summary, Mapping):
                summary = attempt
            reason_codes = summary.get("reason_code_summary")
            if not isinstance(reason_codes, Mapping):
                reason_codes = summary.get("validation_reason_counts")
            if not isinstance(reason_codes, Mapping):
                continue
            for key, value in reason_codes.items():
                code = str(key).strip()
                if not code:
                    continue
                try:
                    counter[code] += int(value or 0)
                except Exception:
                    counter[code] += 0
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def _latest_report_path() -> Path:
    if not ARTIFACT_ROOT.exists():
        raise FileNotFoundError("No harness artifacts found")
    candidates = [path / "report.json" for path in ARTIFACT_ROOT.iterdir() if path.is_dir() and (path / "report.json").exists()]
    if not candidates:
        raise FileNotFoundError("No harness reports found")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    lines = [
        f"# GAIA Harness Report: {payload.get('run_id')}",
        "",
        f"- generated_at: {payload.get('generated_at')}",
        f"- task_count: {payload.get('task_count')}",
        f"- repeats: {payload.get('repeats')}",
        "",
        "## Summary",
        "",
    ]
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Grade Summary")
    lines.append("")
    grade_summary = payload.get("grade_summary") if isinstance(payload.get("grade_summary"), Mapping) else {}
    for grader, counts in grade_summary.items():
        if not isinstance(counts, Mapping):
            continue
        lines.append(f"- {grader}: pass={counts.get('pass', 0)} fail={counts.get('fail', 0)}")
    reason_summary = payload.get("reason_code_summary") if isinstance(payload.get("reason_code_summary"), Mapping) else {}
    if reason_summary:
        lines.append("")
        lines.append("## Top Reason Codes")
        lines.append("")
        for code, count in list(sorted(reason_summary.items(), key=lambda item: (-int(item[1]), str(item[0]))))[:10]:
            lines.append(f"- {code}: {count}")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def run_registry(
    registry: TaskRegistry,
    *,
    task_id: str | None = None,
    limit: int | None = None,
    suite_id: str | None = None,
    python_executable: str = sys.executable,
    timeout_sec: int = 1800,
    env: Mapping[str, str] | None = None,
    session_prefix: str = "harness",
    repeats: int = 1,
) -> dict[str, Any]:
    tasks = _select_tasks(registry, task_id=task_id, limit=limit)
    if suite_id is not None:
        target_suite = str(suite_id).strip()
        tasks = [task for task in tasks if task.suite_id == target_suite]
    results = []
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    task_reports = []
    repeats = max(1, int(repeats))
    for index, task in enumerate(tasks, start=1):
        task_rows = []
        for attempt in range(1, repeats + 1):
            session_id = f"{session_prefix}:{task.id}:{index}:{attempt}"
            row = run_task(
                task,
                python_executable=python_executable,
                timeout_sec=timeout_sec,
                env=env,
                session_id=session_id,
            )
            row["attempt"] = attempt
            row["attempt_index"] = attempt
            row["attempt_count"] = repeats
            grades = _grade_task_result(task, row)
            row["grades"] = [grade.to_dict() for grade in grades]
            row["overall_pass"] = all(bool(grade.passed) for grade in grades)
            row["reason_code_counts"] = _summarize_reason_codes([row])
            row["top_reason_codes"] = [
                {"reason_code": code, "count": count}
                for code, count in sorted(row["reason_code_counts"].items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
            ]
            task_rows.append(row)
            results.append(row)
        pass_count = sum(1 for row in task_rows if bool(row.get("overall_pass")))
        reason_code_counts = _summarize_reason_codes(task_rows)
        task_reports.append(
            {
                "task_id": task.id,
                "suite_id": task.suite_id,
                "goal": task.goal,
                "url": task.url,
                "repeats": repeats,
                "rows": task_rows,
                "attempts": task_rows,
                "attempt_count": len(task_rows),
                "attempt_success_count": pass_count,
                "attempt_failure_count": len(task_rows) - pass_count,
                "overall_pass": bool(pass_count),
                "status": "SUCCESS" if pass_count else "FAIL",
                "best_attempt_index": next((row.get("attempt_index") for row in task_rows if bool(row.get("overall_pass"))), task_rows[0].get("attempt_index") if task_rows else 0),
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
    summary["pass_rate"] = summary.get("pass_at_k", 0.0)
    summary["avg_duration_seconds"] = round(statistics.mean(durations), 2) if durations else 0.0
    summary["task_pass_at_1"] = round(sum(1 for task in task_reports if float(task.get("pass_at_1") or 0.0) == 1.0) / len(task_reports), 4) if task_reports else 0.0
    summary["task_pass_at_k"] = round(sum(1 for task in task_reports if float(task.get("pass_at_k") or 0.0) > 0.0) / len(task_reports), 4) if task_reports else 0.0
    summary["task_pass_all_k"] = round(sum(1 for task in task_reports if float(task.get("pass_all_k") or 0.0) == 1.0) / len(task_reports), 4) if task_reports else 0.0
    reason_code_summary = _summarize_reason_codes(results)
    top_reason_codes = [
        {"reason_code": code, "count": count}
        for code, count in sorted(reason_code_summary.items(), key=lambda item: (-int(item[1]), str(item[0])))[:10]
    ]
    payload = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "registry": str(registry.source) if registry.source else None,
        "task_count": len(tasks),
        "repeats": repeats,
        "results": results,
        "tasks": task_reports,
        "summary": summary,
        "grade_summary": _summarize_grades(results),
        "reason_code_summary": reason_code_summary,
        "reason_code_counts": reason_code_summary,
        "top_reason_codes": top_reason_codes,
        "artifact_dir": str(artifact_dir),
    }
    (artifact_dir / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(artifact_dir / "report.md", payload)
    return payload

__all__ = [
    "ARTIFACT_ROOT",
    "_latest_report_path",
    "load_builtin_registry",
    "load_registry",
    "run_registry",
    "run_task",
]
