"""Terminal-facing entrypoint for GAIA without GUI."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Sequence

from gaia.common import (
    build_run_context,
    build_run_id,
    load_run_context,
    write_run_context,
)
from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase4.agent import AgentOrchestrator
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.models import TestScenario
from gaia.src.utils.plan_repository import PlanRepository


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia start terminal",
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
        summary["resume_cli"] = f"gaia start gui --resume {context.run_id}"

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
            print("GUI 이어서 보기: gaia start gui --resume", context.run_id)

        if args.output and args.format == "json":
            print(f"json 저장됨: {args.output}")

        return 0 if summary["status"] in {"success", "partial"} else 1

    except Exception as exc:
        print(f"Terminal execution failed: {exc}", file=sys.stderr)
        return 1


__all__ = ["run_terminal", "build_run_context", "_build_summary"]
