"""Terminal-facing entrypoint for GAIA without GUI."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from gaia.common import (
    build_run_context,
    build_run_id,
    load_run_context,
    write_run_context,
)
from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase4.agent import AgentOrchestrator
from gaia.src.phase4.goal_driven import ExplorationConfig, ExploratoryAgent, GoalDrivenAgent, TestGoal
from gaia.src.phase4.mcp_host_runtime import ensure_mcp_host_running
from gaia.src.phase4.validation_rail import run_validation_rail
from gaia.src.phase4.session import WORKSPACE_DEFAULT
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.config import CONFIG
from gaia.src.utils.models import TestScenario
from gaia.src.utils.plan_repository import PlanRepository


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia terminal",
        description="Run GAIA from terminal mode.",
    )
    parser.add_argument("--plan", type=Path, help="Path to saved plan JSON.")
    parser.add_argument("--spec", type=Path, help="Path to PDF spec to regenerate plan.")
    parser.add_argument("--url", help="Target URL.")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format for terminal mode.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output file path.")
    parser.add_argument(
        "--resume",
        help="Resume with terminal run context ID or path (plan/url metadata).",
    )
    return parser


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _create_parser()
    return parser.parse_args(list(argv or []))


def _prompt_for_non_empty(prompt: str, required: str) -> str:
    while True:
        value = input(prompt).strip().strip('"').strip("'")
        if value:
            return value
        print(f"{required} is required.")


def _prompt_existing_file(prompt: str) -> Path:
    while True:
        value = _prompt_for_non_empty(prompt, f"{prompt} path")
        path = _resolve_path(Path(value))
        if path.exists():
            return path
        print(f"File not found: {path}")


def _prompt_source_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.plan or args.spec or args.resume:
        return args

    print("터미널 실행을 시작하려면 실행 소스가 필요합니다.")
    print("1) 계획 파일(plan.json)")
    print("2) 사양 파일(spec pdf)")
    print("3) 이전 실행 컨텍스트(run-id 또는 파일 경로)")

    while True:
        mode = input("선택 [1/2/3]: ").strip()
        if mode == "1":
            args.plan = _prompt_existing_file("Plan JSON 경로를 입력하세요: ")
            break
        if mode == "2":
            args.spec = _prompt_existing_file("Spec PDF 경로를 입력하세요: ")
            break
        if mode == "3":
            args.resume = _prompt_for_non_empty(
                "run-id 또는 run-context 경로를 입력하세요: ",
                "Resume target",
            )
            break
        print("1, 2, 3 중 하나를 입력해주세요.")

    return args


def _prompt_url_if_missing(args: argparse.Namespace, detected_url: str | None = None) -> None:
    if args.url:
        return
    if detected_url and detected_url.strip():
        return
    args.url = _prompt_for_non_empty("실행할 URL을 입력하세요: ", "URL")


def _resolve_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path.expanduser()


def _load_plan_from_source(
    plan_path: Path | None = None,
    spec_path: Path | None = None,
    resume_plan_path: Path | None = None,
    resume_spec_path: Path | None = None,
) -> tuple[list[TestScenario], str | None, str | None, str]:
    if plan_path is not None:
        path = _resolve_path(plan_path)
        if not path.exists():
            raise FileNotFoundError(f"Plan file not found: {path}")

        repository = PlanRepository()
        scenarios, metadata = repository.load_plan_file(path)
        discovered_url = metadata.get("url")
        resolved_url = discovered_url if isinstance(discovered_url, str) else None
        return scenarios, str(path), resolved_url, "plan"

    if spec_path is not None:
        path = _resolve_path(spec_path)
        if not path.exists():
            raise FileNotFoundError(f"Spec file not found: {path}")

        extractor = PDFLoader()
        parser_result = extractor.extract(path)
        analyzer = SpecAnalyzer()
        scenarios = analyzer.generate_from_spec(parser_result.text)
        if not scenarios:
            raise RuntimeError("No scenarios were generated from the provided spec.")
        return scenarios, None, parser_result.suggested_url, "spec"

    if resume_plan_path is not None:
        path = _resolve_path(resume_plan_path)
        if not path.exists():
            raise FileNotFoundError(f"Resume plan file not found: {path}")

        repository = PlanRepository()
        scenarios, metadata = repository.load_plan_file(path)
        discovered_url = metadata.get("url")
        resolved_url = discovered_url if isinstance(discovered_url, str) else None
        return scenarios, str(path), resolved_url, "plan"

    if resume_spec_path is not None:
        path = _resolve_path(resume_spec_path)
        if not path.exists():
            raise FileNotFoundError(f"Resume spec file not found: {path}")

        extractor = PDFLoader()
        parser_result = extractor.extract(path)
        analyzer = SpecAnalyzer()
        scenarios = analyzer.generate_from_spec(parser_result.text)
        if not scenarios:
            raise RuntimeError("No scenarios were generated from the resume spec.")
        return scenarios, None, parser_result.suggested_url, "spec"

    raise ValueError(
        "Either --plan, --spec, or --resume with a valid context is required."
    )


def _resolve_url(url: str | None, discovered_url: str | None) -> str:
    resolved = url or (discovered_url.strip() if discovered_url else "")
    if not resolved:
        raise ValueError(
            "URL is required. Set --url or provide it in plan metadata."
        )
    return resolved


def _run_id_dir(run_id: str) -> Path:
    return Path.home() / ".gaia" / "runs" / run_id


def _build_summary(
    *,
    run_id: str,
    scenarios: Sequence[TestScenario],
    tracker: ChecklistTracker,
    results: list[dict[str, Any]],
    status_counter: Counter[str],
    target_url: str,
    plan_path: str | None,
    spec_path: str | None,
    start_time: str,
) -> Dict[str, Any]:
    artifacts_dir = _run_id_dir(run_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if plan_path:
        plan_source = "plan"
    elif spec_path:
        plan_source = "spec"
    else:
        plan_source = "terminal"

    if plan_source == "spec" and plan_path is None and spec_path:
        generated_plan = artifacts_dir / "plan.json"
        with generated_plan.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "profile": "terminal",
                    "url": target_url,
                    "plan_source": "spec",
                    "spec_path": spec_path,
                    "test_scenarios": [scenario.model_dump() for scenario in scenarios],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        plan_path = str(generated_plan)

    status_counts = {
        "success": status_counter.get("success", 0),
        "failed": status_counter.get("failed", 0),
        "partial": status_counter.get("partial", 0),
        "skipped": status_counter.get("skipped", 0),
    }
    completed = (
        status_counts["success"]
        + status_counts["failed"]
        + status_counts["partial"]
        + status_counts["skipped"]
    )
    coverage = tracker.coverage() * 100

    if status_counts["failed"] > 0:
        status = "failed"
    elif status_counts["partial"] > 0:
        status = "partial"
    else:
        status = "success"

    return {
        "run_id": run_id,
        "mode": "terminal",
        "url": target_url,
        "plan_source": plan_source,
        "plan_path": plan_path,
        "spec_path": spec_path,
        "artifacts_path": str(artifacts_dir),
        "summary": {
            "total": len(scenarios),
            "completed": completed,
            "coverage": coverage,
            "coverage_percent": float(f"{coverage:.4f}"),
        },
        "status_counts": status_counts,
        "started_at": start_time,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "status": status,
    }


def run_terminal(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    args = _prompt_source_args(args)
    resume_context = None

    if args.resume:
        try:
            resume_context = load_run_context(args.resume)
        except Exception as exc:
            print(f"Failed to load run context: {exc}", file=sys.stderr)
            return 1

    if args.plan and args.spec:
        print("Cannot use both --plan and --spec together.", file=sys.stderr)
        return 2

    try:
        source_plan_path = args.plan or (
            Path(resume_context.plan_path) if resume_context and resume_context.plan_path else None
        )
        source_spec_path = args.spec or (
            Path(resume_context.spec_path) if resume_context and resume_context.spec_path else None
        )
        resume_url = resume_context.url if resume_context else None
        scenarios, plan_path, discovered_url, plan_source = _load_plan_from_source(
            plan_path=source_plan_path,
            spec_path=source_spec_path,
        )

        _prompt_url_if_missing(args, discovered_url or resume_url)
        target_url = _resolve_url(args.url, discovered_url or resume_url)
        start_time = datetime.now(timezone.utc).isoformat()

        tracker = ChecklistTracker()
        orchestrator = AgentOrchestrator(tracker=tracker)

        status_counter: Counter[str] = Counter()
        run_results: list[dict[str, Any]] = []
        if not scenarios:
            raise RuntimeError("No executable scenarios were loaded from the selected source.")

        for index, scenario in enumerate(scenarios, start=1):
            print(f"[{index}/{len(scenarios)}] 실행: {scenario.id} - {scenario.scenario}")
            result = orchestrator.execute_scenario(target_url, scenario)
            status = str(result.get("status", "failed"))
            status_counter[status] += 1
            run_results.append(
                {
                    "scenario_id": scenario.id,
                    "scenario": scenario.scenario,
                    "status": status,
                    "result": result,
                }
            )
            if status == "success":
                print(f"  ✅ success")
            else:
                print(f"  ⚠️ {status}: {result.get('error', 'no details')}")

        summary = _build_summary(
            run_id=build_run_id(),
            scenarios=scenarios,
            tracker=orchestrator.tracker,
            results=run_results,
            status_counter=status_counter,
            target_url=target_url,
            plan_path=plan_path,
            spec_path=str(source_spec_path) if source_spec_path else None,
            start_time=start_time,
        )

        context = build_run_context(
            mode="terminal",
            run_id=summary["run_id"],
            url=target_url,
            plan_source=plan_source,
            plan_path=summary["plan_path"],
            spec_path=summary["spec_path"],
            artifacts_path=summary["artifacts_path"],
            output_format=args.format,
            status=summary["status"],
            summary=summary,
        )
        context_path = write_run_context(context)

        summary["run_context_path"] = str(context_path)
        summary["resume_cli"] = f"gaia plan --resume {context.run_id}"

        if args.format == "json":
            payload = {
                "run_id": summary["run_id"],
                "mode": summary["mode"],
                "status": summary["status"],
                "run_context_path": str(context_path),
                "url": summary["url"],
                "plan_source": summary["plan_source"],
                "plan_path": summary["plan_path"],
                "spec_path": summary["spec_path"],
                "artifacts_path": summary["artifacts_path"],
                "started_at": summary["started_at"],
                "finished_at": summary["finished_at"],
                "summary": summary["summary"],
                "status_counts": summary["status_counts"],
                "results": run_results,
                "output_format": args.format,
            }
            raw = json.dumps(payload, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(raw, encoding="utf-8")
            print(raw)
        else:
            print("\n요약")
            print(f"총 시나리오: {summary['summary']['total']}")
            print(f"성공: {summary['status_counts']['success']}")
            print(f"실패: {summary['status_counts']['failed']}")
            print(f"커버리지: {summary['summary']['coverage_percent']:.2f}%")
            print(f"컨텍스트: {context_path}")
            print("GUI 이어서 보기: gaia plan --resume", context.run_id)

        if args.output and args.format == "json":
            print(f"json 저장됨: {args.output}")

        return 0 if summary["status"] in {"success", "partial"} else 1

    except Exception as exc:
        print(f"Terminal execution failed: {exc}", file=sys.stderr)
        return 1


def _extract_inline_test_data(query: str) -> tuple[str, Dict[str, str]]:
    tokens = [t for t in str(query or "").split() if t.strip()]
    data: Dict[str, str] = {}
    keep: list[str] = []
    aliases = {
        "id": "username",
        "user": "username",
        "username": "username",
        "email": "email",
        "pw": "password",
        "password": "password",
    }
    for tok in tokens:
        if "=" not in tok:
            keep.append(tok)
            continue
        k, v = tok.split("=", 1)
        key = aliases.get(k.strip().lower())
        if not key:
            keep.append(tok)
            continue
        value = v.strip().strip('"').strip("'")
        if value:
            data[key] = value
    return " ".join(keep).strip(), data


def _build_test_goal(url: str, query: str) -> TestGoal:
    clean_query, inline_data = _extract_inline_test_data(query)
    query_text = clean_query or query
    ts = int(time.time())
    words = [w for w in query_text.replace("/", " ").split() if w.strip()]
    keywords = words[:5]
    return TestGoal(
        id=f"CHAT_{ts}",
        name=(query_text[:40] or "chat test").strip(),
        description=query_text,
        priority="MUST",
        keywords=keywords,
        success_criteria=[query_text],
        max_steps=20,
        start_url=url,
        test_data=inline_data,
    )


def _augment_goal_with_env_credentials(goal: TestGoal, query_text: str) -> None:
    if not isinstance(goal.test_data, dict):
        goal.test_data = {}
    text = str(query_text or "")
    lowered = text.lower()
    needs_auth_context = any(
        token in lowered
        for token in (
            "로그인",
            "login",
            "auth",
            "인증",
            "회원가입",
            "signup",
            "register",
            "위시리스트",
            "wishlist",
            "조합",
            "combination",
            "시간표",
            "timetable",
        )
    )
    if not needs_auth_context:
        return
    username = (os.getenv("GAIA_TEST_USERNAME") or os.getenv("GAIA_AUTH_USERNAME") or "").strip()
    password = (os.getenv("GAIA_TEST_PASSWORD") or os.getenv("GAIA_AUTH_PASSWORD") or "").strip()
    email = (os.getenv("GAIA_TEST_EMAIL") or os.getenv("GAIA_AUTH_EMAIL") or "").strip()
    if username and password:
        goal.test_data.setdefault("username", username)
        goal.test_data.setdefault("password", password)
        goal.test_data.setdefault("auth_mode", "provided_credentials")
        goal.test_data.setdefault("return_credentials", True)
        if email:
            goal.test_data.setdefault("email", email)


def _infer_goal_type(query_text: str) -> str:
    text = str(query_text or "").lower()
    explicit_filter_tokens = ("필터", "filter", "학점", "credit", "정렬", "sort")
    category_like_tokens = ("category", "분류")
    readonly_tokens = ("현재", "이미", "보이는지", "확인", "추가 조작 없이", "visible", "already")
    if any(token in text for token in explicit_filter_tokens):
        return "filter_validation"
    if any(token in text for token in category_like_tokens) and not any(
        token in text for token in readonly_tokens
    ):
        return "filter_validation"
    if any(token in text for token in ("로그인", "login", "auth", "인증")):
        return "auth_validation"
    if any(token in text for token in ("회원가입", "signup", "register")):
        return "signup_validation"
    return "goal_execution"


def _action_label(action_name: str, goal_type: str, reasoning: str) -> str:
    action = str(action_name or "").lower()
    reasoning_low = str(reasoning or "").lower()
    if goal_type == "filter_validation":
        if action == "select":
            return "필터 값 변경"
        if action == "fill":
            return "필터 검색어 입력"
        if action == "wait":
            return "필터 반영 대기"
        if action == "click":
            if any(t in reasoning_low for t in ("적용", "apply", "search", "검색", "필터")):
                return "필터 적용 요청"
            return "필터 관련 클릭"
    mapping = {
        "click": "요소 클릭 검증",
        "fill": "입력 동작 검증",
        "select": "선택 동작 검증",
        "press": "키 입력 동작 검증",
        "scroll": "스크롤 동작 검증",
        "wait": "대기/반응 검증",
        "navigate": "페이지 이동 검증",
        "hover": "호버 동작 검증",
    }
    return mapping.get(action, f"{action or 'unknown'} 동작 검증")


def _build_validation_report(
    query_text: str,
    result: Any,
    *,
    semantic_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(semantic_report, dict) and str(semantic_report.get("mode") or "").strip():
        return semantic_report

    goal_type = _infer_goal_type(query_text)
    filter_mode = goal_type == "filter_validation"
    filter_tokens = ("필터", "filter", "학점", "credit", "category", "분류", "정렬", "sort")
    steps = list(getattr(result, "steps_taken", []) or [])
    checks: list[Dict[str, Any]] = []

    for step in steps:
        action_obj = getattr(step, "action", None)
        action_raw = getattr(action_obj, "action", "")
        action_name = (
            str(getattr(action_raw, "value", "") or action_raw or "").strip().lower()
        )
        reasoning = str(getattr(action_obj, "reasoning", "") or "").strip()
        reasoning_low = reasoning.lower()
        is_relevant = True
        if filter_mode:
            is_relevant = action_name in {"select", "fill", "click", "wait"} and (
                any(token in reasoning_low for token in filter_tokens) or action_name in {"select", "fill"}
            )
        if not is_relevant:
            continue

        success = bool(getattr(step, "success", False))
        status = "pass" if success else "fail"
        element_id = getattr(action_obj, "element_id", None)
        input_value = getattr(action_obj, "value", None)
        error_message = str(getattr(step, "error_message", "") or "").strip()

        checks.append(
            {
                "check_id": f"step_{int(getattr(step, 'step_number', len(checks) + 1) or (len(checks) + 1))}",
                "name": _action_label(action_name, goal_type, reasoning),
                "status": status,
                "step": int(getattr(step, "step_number", len(checks) + 1) or (len(checks) + 1)),
                "action": action_name or "unknown",
                "element_id": element_id,
                "input_value": input_value,
                "reasoning": reasoning,
                "error": error_message,
            }
        )

    if not checks:
        for step in steps[:10]:
            action_obj = getattr(step, "action", None)
            action_raw = getattr(action_obj, "action", "")
            action_name = (
                str(getattr(action_raw, "value", "") or action_raw or "").strip().lower()
            )
            reasoning = str(getattr(action_obj, "reasoning", "") or "").strip()
            success = bool(getattr(step, "success", False))
            checks.append(
                {
                    "check_id": f"step_{int(getattr(step, 'step_number', len(checks) + 1) or (len(checks) + 1))}",
                    "name": _action_label(action_name, "goal_execution", reasoning),
                    "status": "passed" if success else "failed",
                    "step": int(getattr(step, "step_number", len(checks) + 1) or (len(checks) + 1)),
                    "action": action_name or "unknown",
                    "element_id": getattr(action_obj, "element_id", None),
                    "input_value": getattr(action_obj, "value", None),
                    "reasoning": reasoning,
                    "error": str(getattr(step, "error_message", "") or "").strip(),
                }
            )

    total = len(checks)
    passed = sum(1 for c in checks if str(c.get("status")).lower() in {"pass", "passed"})
    failed = sum(1 for c in checks if str(c.get("status")).lower() in {"fail", "failed"})
    skipped = max(0, total - passed - failed)
    success_rate = round((passed / total) * 100, 1) if total > 0 else 0.0

    summary = {
        "goal_type": goal_type,
        "total_checks": total,
        "passed_checks": passed,
        "failed_checks": failed,
        "skipped_checks": skipped,
        "success_rate": success_rate,
    }
    return {
        "summary": summary,
        "checks": checks,
    }


def _is_strict_validation_failed(report: Dict[str, Any]) -> bool:
    if not isinstance(report, dict):
        return False
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return False
    if bool(summary.get("strict_failed")):
        return True
    try:
        failed_mandatory = int(summary.get("failed_mandatory_checks") or 0)
        skipped_mandatory = int(summary.get("skipped_mandatory_checks") or 0)
        return (failed_mandatory + skipped_mandatory) > 0
    except Exception:
        return False


def _is_goal_satisfaction_failed(report: Dict[str, Any]) -> bool:
    if not isinstance(report, dict):
        return False
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return False
    goal_type = str(summary.get("goal_type") or "").strip().lower()
    if goal_type != "filter_validation_semantic":
        return False
    if "goal_satisfied" not in summary:
        return False
    return not bool(summary.get("goal_satisfied"))


def _detect_blocked_user_action(
    reason: str,
    reason_summary: Dict[str, int],
) -> bool:
    reason_text = str(reason or "").strip().lower()
    blocked_codes = {
        "auth_required",
        "login_required",
        "captcha_detected",
        "2fa_required",
        "permission_prompt_detected",
        "blocked_timeout",
        "blocked_user_action",
        "steering_infeasible",
        "user_intervention_missing",
        "clarification_required",
        "clarification_timeout",
        "intervention_timeout",
    }
    for code in blocked_codes:
        if int(reason_summary.get(code) or 0) > 0:
            return True
    blocked_terms = (
        "captcha",
        "2fa",
        "권한 허용",
        "사용자 요청으로 실행을 중단",
        "추가 입력",
        "사용자 개입",
        "resume",
        "목표 명확화",
        "명확화가 필요",
        "사용자 입력이 제공되지 않아 중단",
    )
    return any(term in reason_text for term in blocked_terms)


def _derive_final_status(
    *,
    result_success: bool,
    reason: str,
    validation_report: Dict[str, Any],
    reason_summary: Dict[str, int],
) -> str:
    if result_success:
        return "SUCCESS"
    if _detect_blocked_user_action(reason, reason_summary):
        return "BLOCKED_USER_ACTION"
    if _is_strict_validation_failed(validation_report):
        return "FAIL"
    if _is_goal_satisfaction_failed(validation_report):
        return "FAIL"
    return "FAIL"


def _build_step_timeline(result: Any, *, limit: int = 12) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    steps = list(getattr(result, "steps_taken", []) or [])
    for step in steps[: max(1, int(limit))]:
        try:
            step_no = int(getattr(step, "step_number", len(rows) + 1) or (len(rows) + 1))
        except Exception:
            step_no = len(rows) + 1
        action_obj = getattr(step, "action", None)
        action_raw = getattr(action_obj, "action", "")
        action_name = str(getattr(action_raw, "value", "") or action_raw or "").strip().lower()
        reasoning = str(getattr(action_obj, "reasoning", "") or "").strip()
        duration_ms = getattr(step, "duration_ms", None)
        duration_seconds: float = 0.0
        try:
            if duration_ms is not None:
                duration_seconds = round(float(duration_ms) / 1000.0, 2)
        except Exception:
            duration_seconds = 0.0
        rows.append(
            {
                "step": step_no,
                "action": action_name or "unknown",
                "reasoning": reasoning,
                "duration_seconds": duration_seconds,
                "success": bool(getattr(step, "success", False)),
                "error": str(getattr(step, "error_message", "") or "").strip(),
            }
        )
    return rows


def _limit_attachments_for_status(
    attachments: Any,
    *,
    final_status: str,
) -> List[Dict[str, Any]]:
    if not isinstance(attachments, list):
        return []
    max_images_per_run = 3
    status_key = str(final_status or "").strip().upper()
    per_status_limit = {
        "SUCCESS": 1,
        "FAIL": 1,
        "BLOCKED_USER_ACTION": 1,
    }
    keep_limit = int(per_status_limit.get(status_key, 1))
    keep_limit = max(1, min(max_images_per_run, keep_limit))
    images: List[Dict[str, Any]] = []
    for row in attachments:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "").strip().lower() != "image_base64":
            continue
        data = row.get("data")
        if not isinstance(data, str) or not data.strip():
            continue
        images.append(row)
        if len(images) >= keep_limit:
            break
    return images


def _merge_reason_code_summary(
    base: Dict[str, Any],
    extra: Dict[str, Any],
) -> Dict[str, int]:
    merged: Dict[str, int] = {}
    for source in (base, extra):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            name = str(key or "").strip()
            if not name:
                continue
            try:
                count = int(value)
            except Exception:
                continue
            merged[name] = int(merged.get(name, 0)) + count
    return merged


def _print_llm_failure_help(reason: str) -> None:
    text = (reason or "").lower()
    if "insufficient_quota" not in text:
        return
    print("\n실행 안내")
    print("- 원인: OpenAI API quota/billing 부족 (429 insufficient_quota)")
    print("- 현재 경로는 OpenAI Platform API 호출이라 API 크레딧이 필요합니다.")
    print("- 해결 방법:")
    print("  1) OpenAI API 결제/크레딧 설정 후 다시 실행")
    print("  2) provider를 gemini로 전환해서 실행")
    print("  3) OpenAI는 manual(API key)로 재인증")


def _run_single_chat_goal(
    url: str,
    query: str,
    session_id: str = WORKSPACE_DEFAULT,
    steering_policy: Optional[Dict[str, Any]] = None,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    prepared_goal: Optional[TestGoal] = None,
) -> Tuple[int, Dict[str, Any]]:
    def _default_intervention_callback(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = str((payload or {}).get("kind") or "").strip().lower()
        if kind in {"clarification", "no_progress"}:
            question = str((payload or {}).get("question") or "").strip()
            return {
                "action": "continue",
                "proceed": True,
                "instruction": question or "현재 목표 범위에서 계속 진행하세요.",
            }
        if kind == "auth":
            username = (
                os.getenv("GAIA_TEST_USERNAME")
                or os.getenv("GAIA_AUTH_USERNAME")
                or ""
            ).strip()
            password = (
                os.getenv("GAIA_TEST_PASSWORD")
                or os.getenv("GAIA_AUTH_PASSWORD")
                or ""
            ).strip()
            email = (
                os.getenv("GAIA_TEST_EMAIL")
                or os.getenv("GAIA_AUTH_EMAIL")
                or ""
            ).strip()
            if username and password:
                response: Dict[str, Any] = {
                    "action": "continue",
                    "proceed": True,
                    "username": username,
                    "password": password,
                }
                if email:
                    response["email"] = email
                return response
            return {"action": "cancel", "proceed": False}
        return {"action": "cancel", "proceed": False}

    if intervention_callback is None:
        intervention_callback = _default_intervention_callback

    goal = prepared_goal or _build_test_goal(url=url, query=query)
    _augment_goal_with_env_credentials(goal, query)
    if isinstance(steering_policy, dict) and steering_policy:
        if not isinstance(goal.test_data, dict):
            goal.test_data = {}
        goal.test_data["steering_policy"] = dict(steering_policy)
    captured_shots: list[str] = []
    captured_hashes: set[str] = set()

    def _on_screenshot(base64_image: str) -> None:
        if not isinstance(base64_image, str):
            return
        payload = base64_image.strip()
        if not payload:
            return
        marker = payload[:96]
        if marker in captured_hashes:
            return
        captured_hashes.add(marker)
        captured_shots.append(payload)
        if len(captured_shots) > 8:
            removed = captured_shots.pop(0)
            try:
                captured_hashes.discard(removed[:96])
            except Exception:
                pass

    agent = GoalDrivenAgent(
        mcp_host_url=CONFIG.mcp.host_url,
        session_id=session_id or WORKSPACE_DEFAULT,
        screenshot_callback=_on_screenshot,
        intervention_callback=intervention_callback,
    )
    print(f"목표 실행: {goal.description}")
    result = agent.execute_goal(goal)
    goal_type = _infer_goal_type(goal.description)
    semantic_report: Optional[Dict[str, Any]] = None
    if goal_type == "filter_validation":
        cached_report = getattr(agent, "_last_filter_semantic_report", None)
        if isinstance(cached_report, dict) and cached_report.get("summary"):
            semantic_report = cached_report
        else:
            semantic_report = agent.run_filter_semantic_validation(
                goal_text=goal.description,
                max_pages=2,
                max_cases=3,
            )
    validation_report = _build_validation_report(
        goal.description,
        result,
        semantic_report=semantic_report,
    )
    validation_failed = _is_strict_validation_failed(validation_report)
    goal_unsatisfied = _is_goal_satisfaction_failed(validation_report)
    effective_success = bool(result.success) and not validation_failed and not goal_unsatisfied
    effective_reason = str(result.final_reason or "")
    if validation_failed:
        effective_reason = (
            "필터 의미 검증에서 필수 항목 실패가 발생했습니다. "
            + (effective_reason or "검증 리포트를 확인하세요.")
        ).strip()
    elif goal_unsatisfied:
        effective_reason = (
            "필터 의미 검증에서 목표 커버리지가 충족되지 않았습니다. "
            + (effective_reason or "검증 리포트를 확인하세요.")
        ).strip()
    rail_result = run_validation_rail(
        target_url=url,
        run_id=session_id or WORKSPACE_DEFAULT,
    )
    rail_summary = rail_result.get("summary") if isinstance(rail_result, dict) else {}
    rail_cases = rail_result.get("cases") if isinstance(rail_result, dict) else []
    rail_artifacts = rail_result.get("artifacts") if isinstance(rail_result, dict) else {}
    if not isinstance(rail_summary, dict):
        rail_summary = {}
    if not isinstance(rail_cases, list):
        rail_cases = []
    if not isinstance(rail_artifacts, dict):
        rail_artifacts = {}
    rail_mode = str(rail_summary.get("mode") or "soft").strip().lower()
    rail_status = str(rail_summary.get("status") or "").strip().lower()
    if rail_mode == "hard" and rail_status in {"failed", "timeout", "error"}:
        effective_success = False
        effective_reason = (
            "검증 레일 실패가 감지되었습니다. "
            + (effective_reason or str(rail_summary.get("reason") or "validation rail failed"))
        ).strip()

    report_reason_summary = (
        validation_report.get("reason_code_summary")
        if isinstance(validation_report.get("reason_code_summary"), dict)
        else {}
    )
    reason_summary = _merge_reason_code_summary(
        dict(getattr(agent, "_reason_code_counts", {}) or {}),
        report_reason_summary,
    )
    rail_reason_code = str(rail_summary.get("reason_code") or "").strip()
    if rail_reason_code:
        try:
            reason_summary[rail_reason_code] = int(reason_summary.get(rail_reason_code) or 0) + 1
        except Exception:
            reason_summary[rail_reason_code] = 1
    final_status = _derive_final_status(
        result_success=bool(effective_success),
        reason=effective_reason,
        validation_report=validation_report,
        reason_summary=reason_summary,
    )
    effective_success = final_status == "SUCCESS"

    print("\n실행 결과")
    print(f"goal: {result.goal_name}")
    print(f"status: {'success' if effective_success else 'failed'}")
    print(f"final_status: {final_status}")
    print(f"steps: {result.total_steps}")
    print(f"reason: {effective_reason}")
    print(f"duration: {result.duration_seconds:.2f}s")
    if not effective_success:
        _print_llm_failure_help(effective_reason)

    summary = {
        "goal": result.goal_name,
        "status": "success" if effective_success else "failed",
        "final_status": final_status,
        "steps": result.total_steps,
        "reason": effective_reason,
        "duration_seconds": round(float(result.duration_seconds), 2),
        "step_timeline": _build_step_timeline(result),
        "reason_code_summary": reason_summary,
        "validation_summary": validation_report.get("summary", {}),
        "validation_checks": validation_report.get("checks", []),
        "verification_report": validation_report,
        "validation_rail_summary": rail_summary,
        "validation_rail_cases": rail_cases,
        "validation_rail_artifacts": rail_artifacts,
        "attachments": (
            validation_report.get("attachments")
            if isinstance(validation_report.get("attachments"), list)
            else []
        ),
    }
    if not summary["attachments"] and captured_shots:
        sample = captured_shots[-3:]
        summary["attachments"] = [
            {
                "kind": "image_base64",
                "mime": "image/png",
                "data": shot,
                "label": f"실행 스냅샷 {idx + 1}/{len(sample)}",
            }
            for idx, shot in enumerate(sample)
            if isinstance(shot, str) and shot.strip()
        ]
    summary["attachments"] = _limit_attachments_for_status(
        summary.get("attachments"),
        final_status=final_status,
    )
    if isinstance(goal.test_data, dict):
        auth_payload = {}
        for key in (
            "auth_mode",
            "username",
            "email",
            "password",
            "department",
            "grade_year",
            "return_credentials",
        ):
            value = goal.test_data.get(key)
            if value not in (None, "", False):
                auth_payload[key] = value
        if auth_payload:
            summary["auth"] = auth_payload
    return (0 if effective_success else 1), summary


def run_chat_terminal_once(
    *,
    url: str,
    query: str,
    session_id: str = WORKSPACE_DEFAULT,
    steering_policy: Optional[Dict[str, Any]] = None,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    prepared_goal: Optional[TestGoal] = None,
) -> Tuple[int, Dict[str, Any]]:
    ensure_mcp_host_running(CONFIG.mcp.host_url, startup_timeout=10.0)
    return _run_single_chat_goal(
        url=url,
        query=query,
        session_id=session_id,
        steering_policy=steering_policy,
        intervention_callback=intervention_callback,
        prepared_goal=prepared_goal,
    )


def run_chat_terminal(
    *,
    url: str,
    initial_query: str | None = None,
    repl: bool = True,
    session_id: str = WORKSPACE_DEFAULT,
) -> int:
    if not url:
        print("URL is required for terminal chat mode.", file=sys.stderr)
        return 2

    if not repl:
        if not initial_query:
            print("A query is required when repl=False.", file=sys.stderr)
            return 2
        try:
            code, _ = _run_single_chat_goal(url, initial_query, session_id=session_id)
            return code
        except Exception as exc:
            print(f"Terminal chat failed: {exc}", file=sys.stderr)
            return 1

    print("GAIA Terminal Chat")
    print(f"- url: {url}")
    print("명령: /help, /url <new-url>, /exit")
    print("자연어로 테스트 목표를 입력하면 1회 실행합니다.")

    current_url = url
    while True:
        try:
            line = input("chat> ").strip()
        except KeyboardInterrupt:
            print("\n중단되었습니다.")
            return 130
        except EOFError:
            print()
            return 0

        if not line:
            continue
        if line == "/exit":
            return 0
        if line == "/help":
            print("/help")
            print("/url <new-url>")
            print("/exit")
            print("<자연어 목표>")
            continue
        if line.startswith("/url "):
            new_url = line[5:].strip()
            if not new_url:
                print("URL을 입력해주세요.")
                continue
            current_url = new_url
            print(f"url 변경됨: {current_url}")
            continue
        if line.startswith("/"):
            print("알 수 없는 명령입니다. /help를 확인하세요.")
            continue

        try:
            code, _ = _run_single_chat_goal(current_url, line, session_id=session_id)
            if code != 0:
                print(f"실행 종료 코드: {code}")
        except Exception as exc:
            print(f"Terminal chat failed: {exc}", file=sys.stderr)


def _run_ai_terminal_impl(
    *,
    url: str,
    max_actions: int = 50,
    session_id: str = WORKSPACE_DEFAULT,
    time_budget_seconds: int | None = None,
    intervention_callback: Optional[Callable[[str, str], Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    if not url:
        print("URL is required for terminal ai mode.", file=sys.stderr)
        return 2, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": "url_required",
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }

    actions = max(1, int(max_actions))
    budget = int(time_budget_seconds or 0)
    if budget < 0:
        budget = 0
    if budget > 0:
        config = ExplorationConfig(
            loop_mode="time",
            time_budget_seconds=budget,
            max_actions=max(actions, 1),
            non_stop_mode=True,
        )
    else:
        config = ExplorationConfig(max_actions=actions, non_stop_mode=True)
    try:
        if intervention_callback is None:
            def _default_ai_intervention_callback(reason: str, current_url: str) -> Dict[str, Any]:
                _ = reason
                _ = current_url
                username = (
                    os.getenv("GAIA_TEST_USERNAME")
                    or os.getenv("GAIA_AUTH_USERNAME")
                    or ""
                ).strip()
                password = (
                    os.getenv("GAIA_TEST_PASSWORD")
                    or os.getenv("GAIA_AUTH_PASSWORD")
                    or ""
                ).strip()
                email = (
                    os.getenv("GAIA_TEST_EMAIL")
                    or os.getenv("GAIA_AUTH_EMAIL")
                    or ""
                ).strip()
                if username and password:
                    payload: Dict[str, Any] = {
                        "proceed": True,
                        "username": username,
                        "password": password,
                    }
                    if email:
                        payload["email"] = email
                    return payload
                return {"action": "cancel", "proceed": False, "reason_code": "user_intervention_missing"}

            intervention_callback = _default_ai_intervention_callback
        agent = ExploratoryAgent(
            mcp_host_url=CONFIG.mcp.host_url,
            session_id=session_id or WORKSPACE_DEFAULT,
            config=config,
            user_intervention_callback=intervention_callback,
        )
        if budget > 0:
            print(f"AI 자율 탐색 시작: {url} (time_budget={budget}s)")
        else:
            print(f"AI 탐색 시작: {url} (max_actions={actions})")
        result = agent.explore(url)
        print("\n탐색 결과")
        print(f"actions: {result.total_actions}")
        print(f"pages: {result.total_pages_visited}")
        print(f"issues: {len(result.issues_found)}")
        print(f"reason: {result.completion_reason}")
        if result.screenshots_dir:
            print(f"screenshots_dir: {result.screenshots_dir}")
        if result.recording_gif_path:
            print(f"gif: {result.recording_gif_path}")
        validation_summary = (
            dict(result.validation_summary)
            if isinstance(result.validation_summary, dict)
            else {}
        )
        validation_checks = (
            list(result.validation_checks)
            if isinstance(result.validation_checks, list)
            else []
        )
        verification_report = (
            dict(result.verification_report)
            if isinstance(result.verification_report, dict)
            else {}
        )
        strict_failed = False
        try:
            strict_failed = bool(validation_summary.get("strict_failed")) or int(
                validation_summary.get("failed_mandatory_checks") or 0
            ) > 0
        except Exception:
            strict_failed = False

        completion_reason = str(result.completion_reason or "")
        _print_llm_failure_help(completion_reason)
        code = 0
        if "insufficient_quota" in completion_reason.lower() or strict_failed:
            code = 1
        reason = completion_reason
        if strict_failed:
            reason = (
                "필터 의미 검증 필수 항목 실패가 감지되었습니다. "
                + (completion_reason or "")
            ).strip()
        summary = {
            "goal": "autonomous_exploration",
            "status": "success" if code == 0 else "failed",
            "steps": int(result.total_actions or 0),
            "reason": reason,
            "duration_seconds": round(float(result.duration_seconds), 2),
            "reason_code_summary": (
                verification_report.get("reason_code_summary")
                if isinstance(verification_report.get("reason_code_summary"), dict)
                else {}
            ),
            "validation_summary": validation_summary,
            "validation_checks": validation_checks,
            "verification_report": verification_report,
        }
        return code, summary
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": "keyboard_interrupt",
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }
    except Exception as exc:
        print(f"Terminal AI failed: {exc}", file=sys.stderr)
        return 1, {
            "goal": "autonomous_exploration",
            "status": "failed",
            "steps": 0,
            "reason": str(exc),
            "duration_seconds": 0.0,
            "reason_code_summary": {},
            "validation_summary": {},
            "validation_checks": [],
            "verification_report": {},
        }


def run_ai_terminal(
    *,
    url: str,
    max_actions: int = 50,
    session_id: str = WORKSPACE_DEFAULT,
    time_budget_seconds: int | None = None,
    intervention_callback: Optional[Callable[[str, str], Any]] = None,
) -> int:
    code, _ = _run_ai_terminal_impl(
        url=url,
        max_actions=max_actions,
        session_id=session_id,
        time_budget_seconds=time_budget_seconds,
        intervention_callback=intervention_callback,
    )
    return code


def run_ai_terminal_with_summary(
    *,
    url: str,
    max_actions: int = 50,
    session_id: str = WORKSPACE_DEFAULT,
    time_budget_seconds: int | None = None,
    intervention_callback: Optional[Callable[[str, str], Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    return _run_ai_terminal_impl(
        url=url,
        max_actions=max_actions,
        session_id=session_id,
        time_budget_seconds=time_budget_seconds,
        intervention_callback=intervention_callback,
    )


__all__ = [
    "run_terminal",
    "run_chat_terminal",
    "run_chat_terminal_once",
    "run_ai_terminal",
    "run_ai_terminal_with_summary",
    "build_run_context",
    "_build_summary",
]
