#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "context_manifest.json"


def load_manifest() -> Dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def build_area_payload(area: str) -> Dict[str, Any]:
    manifest = load_manifest()
    areas = manifest.get("areas") or {}
    if area not in areas:
        raise KeyError(area)
    payload = dict(areas[area])
    payload["area"] = area
    payload["default_area"] = manifest.get("default_area")
    return payload


def _render_text(payload: Dict[str, Any]) -> str:
    lines = [
        f"# Context Pack: {payload['area']}",
        f"Summary: {payload.get('summary', '').strip()}",
        "",
        "Read first:",
    ]
    for path in payload.get("docs") or []:
        lines.append(f"- {path}")
    files = payload.get("files") or []
    if files:
        lines.append("")
        lines.append("Open code only if the task touches this area:")
        for path in files:
            lines.append(f"- {path}")
    checks = payload.get("checks") or []
    if checks:
        lines.append("")
        lines.append("Checks:")
        for cmd in checks:
            lines.append(f"- {cmd}")
    lines.append("")
    lines.append("Expansion rule:")
    lines.append("- Do not read files outside this pack until a symbol, trace, or failing path points there.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Print a minimal context pack for an area.")
    parser.add_argument("--area", help="Area id from docs/harness/context_manifest.json")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--list-areas", action="store_true", help="List known area ids.")
    args = parser.parse_args(argv)

    manifest = load_manifest()
    areas = manifest.get("areas") or {}

    if args.list_areas:
        for area in sorted(areas):
            print(area)
        return 0

    area = args.area or manifest.get("default_area")
    if area not in areas:
        print(f"unknown area: {area}", file=sys.stderr)
        return 2

    payload = build_area_payload(area)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
