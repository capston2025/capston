#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "context_manifest.json"
DEV_HARNESS_MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "development_harness_manifest.json"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def load_dev_harness_manifest() -> Dict[str, Any]:
    return json.loads(DEV_HARNESS_MANIFEST_PATH.read_text(encoding="utf-8"))


def lint_manifest() -> List[str]:
    errors: List[str] = []
    manifest = load_manifest()
    areas = manifest.get("areas")
    if not isinstance(areas, dict) or not areas:
        errors.append("manifest.areas must be a non-empty object")
        return errors

    default_area = manifest.get("default_area")
    if not isinstance(default_area, str) or default_area not in areas:
        errors.append("manifest.default_area must point to a defined area")

    if not AGENTS_PATH.exists():
        errors.append("AGENTS.md is missing at repository root")
    else:
        agents_text = AGENTS_PATH.read_text(encoding="utf-8")
        if "scripts/context_pack.py" not in agents_text:
            errors.append("AGENTS.md must reference scripts/context_pack.py")
        if "docs/harness/context_manifest.json" not in agents_text:
            errors.append("AGENTS.md must reference docs/harness/context_manifest.json")
        if "scripts/dev_harness.py" not in agents_text:
            errors.append("AGENTS.md must reference scripts/dev_harness.py")
        if "docs/harness/development_harness_manifest.json" not in agents_text:
            errors.append("AGENTS.md must reference docs/harness/development_harness_manifest.json")

    for area, payload in areas.items():
        if not isinstance(payload, dict):
            errors.append(f"area {area} must be an object")
            continue
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append(f"area {area} is missing summary")
        for key in ("docs", "files", "checks"):
            items = payload.get(key)
            if not isinstance(items, list):
                errors.append(f"area {area}.{key} must be a list")
                continue
            if key in {"docs", "checks"} and not items:
                errors.append(f"area {area}.{key} must not be empty")
            if key in {"docs", "files"}:
                for rel in items:
                    path = REPO_ROOT / str(rel)
                    if not path.exists():
                        errors.append(f"area {area}.{key} path does not exist: {rel}")
    errors.extend(lint_dev_harness_manifest(manifest))
    return errors


def lint_dev_harness_manifest(context_manifest: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not DEV_HARNESS_MANIFEST_PATH.exists():
        return ["docs/harness/development_harness_manifest.json is missing"]

    try:
        manifest = load_dev_harness_manifest()
    except json.JSONDecodeError as exc:
        return [f"development_harness_manifest.json is invalid JSON: {exc}"]

    lanes = manifest.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        errors.append("development_harness_manifest.lanes must be a non-empty object")
        return errors

    default_lane = manifest.get("default_lane")
    if not isinstance(default_lane, str) or default_lane not in lanes:
        errors.append("development_harness_manifest.default_lane must point to a defined lane")

    context_areas = set((context_manifest.get("areas") or {}).keys())
    team_architecture = manifest.get("team_architecture")
    if not isinstance(team_architecture, dict):
        errors.append("development_harness_manifest.team_architecture must be an object")
        team_architecture = {}
    known_patterns = set((team_architecture.get("patterns") or {}).keys())
    known_agents = set((team_architecture.get("agents") or {}).keys())

    revfactory = manifest.get("revfactory_mapping")
    if not isinstance(revfactory, dict):
        errors.append("development_harness_manifest.revfactory_mapping must be an object")
    else:
        phases = revfactory.get("phases")
        if not isinstance(phases, list):
            errors.append("development_harness_manifest.revfactory_mapping.phases must be a list")
        else:
            phase_ids = sorted(item.get("phase") for item in phases if isinstance(item, dict))
            if phase_ids != list(range(8)):
                errors.append("development_harness_manifest.revfactory_mapping.phases must define phases 0..7")

    for lane, payload in lanes.items():
        if not isinstance(payload, dict):
            errors.append(f"development lane {lane} must be an object")
            continue
        context_area = payload.get("context_area")
        if not isinstance(context_area, str) or context_area not in context_areas:
            errors.append(f"development lane {lane}.context_area must point to a context_manifest area")
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            errors.append(f"development lane {lane} is missing summary")
        for key in ("owned_paths", "eval_contract", "risk_flags"):
            items = payload.get(key)
            if not isinstance(items, list):
                errors.append(f"development lane {lane}.{key} must be a list")
                continue
            if key == "owned_paths":
                for rel in items:
                    path = REPO_ROOT / str(rel)
                    if not path.exists():
                        errors.append(f"development lane {lane}.owned_paths path does not exist: {rel}")
        prefixes = payload.get("path_prefixes", [])
        if prefixes is not None and not isinstance(prefixes, list):
            errors.append(f"development lane {lane}.path_prefixes must be a list")
        elif isinstance(prefixes, list):
            for prefix in prefixes:
                if not isinstance(prefix, str) or not prefix.strip():
                    errors.append(f"development lane {lane}.path_prefixes contains an empty prefix")
        team_pattern = payload.get("team_pattern")
        if not isinstance(team_pattern, str) or not team_pattern.strip():
            errors.append(f"development lane {lane}.team_pattern must be a non-empty string")
        else:
            for part in [item.strip() for item in team_pattern.split("+") if item.strip()]:
                if known_patterns and part not in known_patterns:
                    errors.append(f"development lane {lane}.team_pattern references unknown pattern: {part}")
        recommended_agents = payload.get("recommended_agents")
        if not isinstance(recommended_agents, list) or not recommended_agents:
            errors.append(f"development lane {lane}.recommended_agents must be a non-empty list")
        else:
            for agent in recommended_agents:
                if not isinstance(agent, str) or not agent.strip():
                    errors.append(f"development lane {lane}.recommended_agents contains an empty agent")
                elif known_agents and agent not in known_agents:
                    errors.append(f"development lane {lane}.recommended_agents references unknown agent: {agent}")
        checks = payload.get("checks")
        if not isinstance(checks, dict):
            errors.append(f"development lane {lane}.checks must be an object")
            continue
        for tier in ("smoke", "unit", "full"):
            commands = checks.get(tier)
            if not isinstance(commands, list):
                errors.append(f"development lane {lane}.checks.{tier} must be a list")
                continue
            for command in commands:
                if not isinstance(command, str) or not command.strip():
                    errors.append(f"development lane {lane}.checks.{tier} contains an empty command")
    return errors


def main() -> int:
    errors = lint_manifest()
    if errors:
        for item in errors:
            print(f"ERROR: {item}", file=sys.stderr)
        return 1
    print("harness docs lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
