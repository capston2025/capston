#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gaia.src.benchmark_manager import prune_benchmark_reports


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete local benchmark record artifacts.")
    parser.add_argument("--site-key", required=True, help="Benchmark site key, for example wikipedia or inu_timetable.")
    parser.add_argument("--url", default="", help="Only prune records matching this selected URL host.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--all", action="store_true", help="Delete all matching records, not only failed records.")
    parser.add_argument("--confirm", action="store_true", help="Actually delete files. Without this, only previews.")
    args = parser.parse_args()

    result = prune_benchmark_reports(
        workspace_root=ROOT,
        site_key=str(args.site_key),
        selected_url=str(args.url or ""),
        limit=max(1, int(args.limit)),
        failed_only=not bool(args.all),
        dry_run=not bool(args.confirm),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
