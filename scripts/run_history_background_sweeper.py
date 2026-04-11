#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gaia.src.phase4.goal_driven.run_history_runtime import drain_pending_run_history_updates


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep pending GAIA run history background updates.")
    parser.add_argument(
        "--history-root",
        default="",
        help="Optional run history root. Defaults to GAIA_RUN_HISTORY_DIR or .gaia/run_history.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of pending runs to drain in this sweep.",
    )
    parser.add_argument(
        "--drain-reason",
        default="background_sweeper",
        help="Reason prefix recorded in updater artifacts.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = drain_pending_run_history_updates(
            history_root=str(args.history_root or "").strip(),
            limit=max(1, int(args.limit)),
            drain_reason=str(args.drain_reason or "").strip() or "background_sweeper",
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
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
