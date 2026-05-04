#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "development_harness_manifest.json"


class HarnessError(RuntimeError):
    pass


def load_manifest() -> Dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HarnessError(f"missing manifest: {MANIFEST_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise HarnessError(f"invalid JSON in {MANIFEST_PATH}: {exc}") from exc


def _lanes(manifest: Dict[str, Any]) -> Dict[str, Any]:
    lanes = manifest.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        raise HarnessError("development harness manifest has no lanes")
    return lanes


def get_lane(manifest: Dict[str, Any], lane: str | None) -> tuple[str, Dict[str, Any]]:
    lanes = _lanes(manifest)
    lane_id = lane or str(manifest.get("default_lane") or "")
    if lane_id not in lanes:
        known = ", ".join(sorted(lanes))
        raise HarnessError(f"unknown lane: {lane_id}. known lanes: {known}")
    payload = lanes[lane_id]
    if not isinstance(payload, dict):
        raise HarnessError(f"lane must be an object: {lane_id}")
    return lane_id, payload


def list_lanes(manifest: Dict[str, Any]) -> str:
    lines = ["# Development Harness Lanes"]
    for lane_id, payload in sorted(_lanes(manifest).items()):
        summary = str(payload.get("summary") or "").strip()
        context_area = str(payload.get("context_area") or "").strip()
        suffix = f" [{context_area}]" if context_area else ""
        lines.append(f"- {lane_id}{suffix}: {summary}")
    return "\n".join(lines)


def changed_paths_from_git() -> List[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise HarnessError(completed.stderr.strip() or "git status failed")

    paths: List[str] = []
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1].strip()
        normalized = normalize_repo_path(raw)
        if normalized:
            paths.append(normalized)
    return list(dict.fromkeys(paths))


def normalize_repo_path(raw: str) -> str:
    text = str(raw or "").strip().strip('"').replace("\\", "/")
    if not text:
        return ""
    path = Path(text)
    try:
        if path.is_absolute():
            text = str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except Exception:
        pass
    if text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def detect_lanes(manifest: Dict[str, Any], paths: Iterable[str]) -> List[Dict[str, Any]]:
    normalized_paths = [path for path in (normalize_repo_path(item) for item in paths) if path]
    results: List[Dict[str, Any]] = []
    for lane_id, payload in sorted(_lanes(manifest).items()):
        score = 0
        matches: List[str] = []
        owned_paths = [normalize_repo_path(item) for item in payload.get("owned_paths") or []]
        prefixes = [normalize_repo_path(item) for item in payload.get("path_prefixes") or []]
        for changed in normalized_paths:
            lane_score = 0
            if changed in owned_paths:
                lane_score = max(lane_score, 4)
            for prefix in prefixes:
                if prefix and (changed == prefix or changed.startswith(prefix.rstrip("/") + "/")):
                    lane_score = max(lane_score, 3)
            for owned in owned_paths:
                if owned and (changed == owned or changed.startswith(owned.rstrip("/") + "/")):
                    lane_score = max(lane_score, 2)
            if lane_score:
                score += lane_score
                matches.append(changed)
        if score:
            results.append(
                {
                    "lane": lane_id,
                    "score": score,
                    "context_area": str(payload.get("context_area") or ""),
                    "team_pattern": str(payload.get("team_pattern") or ""),
                    "matches": list(dict.fromkeys(matches)),
                }
            )
    return sorted(results, key=lambda item: (-int(item["score"]), str(item["lane"])))


def render_detection(manifest: Dict[str, Any], paths: List[str], *, as_json: bool = False) -> str:
    normalized_paths = [path for path in (normalize_repo_path(item) for item in paths) if path]
    results = detect_lanes(manifest, normalized_paths)
    payload = {
        "paths": normalized_paths,
        "recommended_lane": results[0]["lane"] if results else None,
        "candidates": results,
    }
    if as_json:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    lines = ["# Development Harness Lane Detection", "", "Changed paths:"]
    if normalized_paths:
        for path in normalized_paths:
            lines.append(f"- {path}")
    else:
        lines.append("- <none>")
    lines.extend(["", "Candidate lanes:"])
    if results:
        for result in results:
            matches = ", ".join(result["matches"][:5])
            if len(result["matches"]) > 5:
                matches += ", ..."
            lines.append(
                f"- {result['lane']} "
                f"(score={result['score']}, context={result['context_area']}, pattern={result['team_pattern']}): {matches}"
            )
        lines.extend(["", f"Recommended lane: {results[0]['lane']}"])
        lines.append(f"Next: python scripts/dev_harness.py plan --lane {results[0]['lane']}")
    else:
        lines.append("- <none>")
        lines.extend(["", "Recommended lane: repo-entry"])
        lines.append("Next: python scripts/dev_harness.py plan --lane repo-entry")
    return "\n".join(lines)


def audit_manifest(manifest: Dict[str, Any]) -> str:
    lanes = _lanes(manifest)
    team = manifest.get("team_architecture") if isinstance(manifest.get("team_architecture"), dict) else {}
    revfactory = manifest.get("revfactory_mapping") if isinstance(manifest.get("revfactory_mapping"), dict) else {}
    lines = [
        "# Development Harness Audit",
        f"Manifest: {MANIFEST_PATH.relative_to(REPO_ROOT)}",
        f"Default lane: {manifest.get('default_lane')}",
        f"Workflow phases: {', '.join(str(item) for item in manifest.get('workflow', []))}",
        f"RevFactory source: {revfactory.get('source_repo', '<none>')}",
        f"Default team pattern: {team.get('default_pattern', '<none>')}",
        "",
        "Lanes:",
    ]
    for lane_id, payload in sorted(lanes.items()):
        context_area = str(payload.get("context_area") or "").strip()
        pattern = str(payload.get("team_pattern") or "").strip()
        lines.append(f"- {lane_id}: context={context_area}, pattern={pattern}")
    lines.extend(["", "Status: audit data loaded"])
    return "\n".join(lines)


def render_plan(lane_id: str, payload: Dict[str, Any]) -> str:
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    lines = [
        f"# Development Harness Plan: {lane_id}",
        f"Summary: {str(payload.get('summary') or '').strip()}",
        f"Context area: {str(payload.get('context_area') or '').strip()}",
        f"Team pattern: {str(payload.get('team_pattern') or '').strip()}",
        f"Recommended agents: {', '.join(str(item) for item in payload.get('recommended_agents') or [])}",
        "",
        "Owned paths:",
    ]
    for path in payload.get("owned_paths") or []:
        lines.append(f"- {path}")
    lines.extend(["", "Eval contract:"])
    for item in payload.get("eval_contract") or []:
        lines.append(f"- {item}")
    lines.extend(["", "Risk flags:"])
    for item in payload.get("risk_flags") or []:
        lines.append(f"- {item}")
    lines.extend(["", "Checks:"])
    for tier in ("smoke", "unit", "full"):
        commands = checks.get(tier) or []
        lines.append(f"{tier}:")
        if commands:
            for command in commands:
                lines.append(f"- {command}")
        else:
            lines.append("- <none>")
    return "\n".join(lines)


def commands_for_tier(payload: Dict[str, Any], tier: str) -> List[str]:
    checks = payload.get("checks") if isinstance(payload.get("checks"), dict) else {}
    if tier == "all":
        commands: List[str] = []
        for name in ("smoke", "unit", "full"):
            commands.extend(str(command) for command in checks.get(name, []) if str(command).strip())
        return commands
    if tier not in {"smoke", "unit", "full"}:
        raise HarnessError(f"unknown tier: {tier}")
    return [str(command) for command in checks.get(tier, []) if str(command).strip()]


def run_commands(commands: Iterable[str], *, dry_run: bool, keep_going: bool) -> int:
    status = 0
    for command in commands:
        print(f"$ {command}", flush=True)
        if dry_run:
            continue
        completed = subprocess.run(command, cwd=REPO_ROOT, shell=True, check=False)
        if completed.returncode != 0:
            status = completed.returncode
            if not keep_going:
                return status
    return status


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Development harness lane planner and runner.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List development harness lanes.")
    subparsers.add_parser("audit", help="Audit development harness metadata.")

    detect_parser = subparsers.add_parser("detect", help="Detect likely lane(s) from changed paths.")
    detect_parser.add_argument("paths", nargs="*", help="Changed paths. Defaults to git status when omitted.")
    detect_parser.add_argument("--changed", action="store_true", help="Use git status instead of explicit paths.")
    detect_parser.add_argument("--json", action="store_true", help="Emit JSON detection output.")

    plan_parser = subparsers.add_parser("plan", help="Render a lane plan.")
    plan_parser.add_argument("--lane", help="Lane id. Defaults to manifest.default_lane.")

    run_parser = subparsers.add_parser("run", help="Run checks for a lane and tier.")
    run_parser.add_argument("--lane", help="Lane id. Defaults to manifest.default_lane.")
    run_parser.add_argument("--tier", choices=["smoke", "unit", "full", "all"], default="smoke")
    run_parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    run_parser.add_argument("--keep-going", action="store_true", help="Continue after a failed command.")

    args = parser.parse_args(argv)
    try:
        manifest = load_manifest()
        if args.command == "list":
            print(list_lanes(manifest))
            return 0
        if args.command == "audit":
            print(audit_manifest(manifest))
            return 0
        if args.command == "detect":
            paths = changed_paths_from_git() if args.changed or not args.paths else args.paths
            print(render_detection(manifest, paths, as_json=args.json))
            return 0
        lane_id, lane_payload = get_lane(manifest, getattr(args, "lane", None))
        if args.command == "plan":
            print(render_plan(lane_id, lane_payload))
            return 0
        if args.command == "run":
            commands = commands_for_tier(lane_payload, args.tier)
            if not commands:
                print(f"no commands for lane={lane_id} tier={args.tier}")
                return 0
            return run_commands(commands, dry_run=args.dry_run, keep_going=args.keep_going)
    except HarnessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
