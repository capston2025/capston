#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gaia.src.phase4.goal_driven.run_history_runtime import run_history_artifact_only_updater_pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drain queued GAIA run history updater artifacts.")
    parser.add_argument("--run-dir", required=True, help="Absolute or relative path to the run history run directory.")
    parser.add_argument(
        "--drain-reason",
        default="background_subprocess",
        help="Reason label recorded in updater artifacts.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = run_history_artifact_only_updater_pass(
            args.run_dir,
            drain_reason=str(args.drain_reason or "").strip() or "background_subprocess",
            worker_pid=os.getpid(),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "run_dir": str(Path(args.run_dir).resolve()),
                "updated_artifacts": sorted(str(key) for key in result.keys()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
