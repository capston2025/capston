"""Terminal-facing entrypoint for GAIA without GUI."""
from __future__ import annotations

import argparse
import json
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
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> Tuple[int, Dict[str, Any]]:
    goal = _build_test_goal(url=url, query=query)
    agent = GoalDrivenAgent(
        mcp_host_url=CONFIG.mcp.host_url,
        session_id=f"chat_{int(time.time())}",
        intervention_callback=intervention_callback,
    )
    print(f"목표 실행: {goal.description}")
    result = agent.execute_goal(goal)
    print("\n실행 결과")
    print(f"goal: {result.goal_name}")
    print(f"status: {'success' if result.success else 'failed'}")
    print(f"steps: {result.total_steps}")
    print(f"reason: {result.final_reason}")
    print(f"duration: {result.duration_seconds:.2f}s")
    if not result.success:
        _print_llm_failure_help(result.final_reason)
    summary = {
        "goal": result.goal_name,
        "status": "success" if result.success else "failed",
        "steps": result.total_steps,
        "reason": result.final_reason,
        "duration_seconds": round(float(result.duration_seconds), 2),
    }
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
    return (0 if result.success else 1), summary


def run_chat_terminal_once(
    *,
    url: str,
    query: str,
    intervention_callback: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> Tuple[int, Dict[str, Any]]:
    return _run_single_chat_goal(url=url, query=query, intervention_callback=intervention_callback)


def run_chat_terminal(
    *,
    url: str,
    initial_query: str | None = None,
    repl: bool = True,
) -> int:
    if not url:
        print("URL is required for terminal chat mode.", file=sys.stderr)
        return 2

    if not repl:
        if not initial_query:
            print("A query is required when repl=False.", file=sys.stderr)
            return 2
        try:
            code, _ = _run_single_chat_goal(url, initial_query)
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
            code, _ = _run_single_chat_goal(current_url, line)
            if code != 0:
                print(f"실행 종료 코드: {code}")
        except Exception as exc:
            print(f"Terminal chat failed: {exc}", file=sys.stderr)


def run_ai_terminal(*, url: str, max_actions: int = 50) -> int:
    if not url:
        print("URL is required for terminal ai mode.", file=sys.stderr)
        return 2

    actions = max(1, int(max_actions))
    try:
        agent = ExploratoryAgent(
            mcp_host_url=CONFIG.mcp.host_url,
            session_id=f"ai_{int(time.time())}",
            config=ExplorationConfig(max_actions=actions),
        )
        print(f"AI 탐색 시작: {url} (max_actions={actions})")
        result = agent.explore(url)
        print("\n탐색 결과")
        print(f"actions: {result.total_actions}")
        print(f"pages: {result.total_pages_visited}")
        print(f"issues: {len(result.issues_found)}")
        print(f"reason: {result.completion_reason}")
        _print_llm_failure_help(result.completion_reason)
        if "insufficient_quota" in (result.completion_reason or "").lower():
            return 1
        return 0
    except KeyboardInterrupt:
        print("\n중단되었습니다.")
        return 130
    except Exception as exc:
        print(f"Terminal AI failed: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "run_terminal",
    "run_chat_terminal",
    "run_chat_terminal_once",
    "run_ai_terminal",
    "build_run_context",
    "_build_summary",
]
