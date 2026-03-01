#!/usr/bin/env python3
"""Run GAIA autonomous hardening scenario with time budget and persist result."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run GAIA hardening test.")
    parser.add_argument("--minutes", type=int, default=5, help="Time budget in minutes.")
    parser.add_argument("--url", required=True, help="Target URL.")
    parser.add_argument("--provider", default="openai", choices=("openai", "gemini"))
    parser.add_argument("--model", default="gpt-5.3-codex")
    parser.add_argument("--auth", default="reuse", choices=("reuse", "fresh"))
    parser.add_argument("--runtime", default="terminal", choices=("terminal", "gui"))
    parser.add_argument("--session", default="workspace_default")
    parser.add_argument("--max-actions", type=int, default=10_000_000)
    args = parser.parse_args()

    seconds = max(60, int(args.minutes) * 60)
    cmd = [
        "python",
        "-m",
        "gaia.cli",
        "autonomous",
        "--llm-provider",
        args.provider,
        "--llm-model",
        args.model,
        "--auth",
        args.auth,
        "--runtime",
        args.runtime,
        "--session",
        args.session,
        "--url",
        args.url,
        "--time-budget-seconds",
        str(seconds),
        "--max-actions",
        str(max(1, int(args.max_actions))),
    ]

    started = time.time()
    proc = subprocess.run(cmd, check=False)
    elapsed = round(time.time() - started, 3)

    report = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "command": cmd,
        "exit_code": int(proc.returncode),
        "duration_sec": elapsed,
        "time_budget_sec": seconds,
        "url": args.url,
        "provider": args.provider,
        "model": args.model,
        "runtime": args.runtime,
        "session": args.session,
    }

    output_dir = Path("artifacts") / "hardening"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"hardening_{args.minutes}m_{ts}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[hardening] report saved: {output_path}")
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
