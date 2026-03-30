#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "context_manifest.json"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


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
