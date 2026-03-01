#!/usr/bin/env python3
"""Generate reason_code distribution report from GAIA memory DB."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate reason_code report.")
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".gaia" / "memory" / "kb.sqlite3"),
        help="Path to kb sqlite3.",
    )
    parser.add_argument("--domain", default="", help="Filter by domain.")
    parser.add_argument(
        "--out-dir",
        default="artifacts/reports",
        help="Output directory.",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"[reason-code-report] db not found: {db_path}")
        return 1

    query = """
        SELECT reason_code, COUNT(*) AS n
        FROM action_records
        {where_clause}
        GROUP BY reason_code
        ORDER BY n DESC
    """
    params: tuple[str, ...] = ()
    where_clause = ""
    if args.domain.strip():
        where_clause = "WHERE domain = ?"
        params = (args.domain.strip().lower(),)

    with _connect(db_path) as conn:
        rows = conn.execute(query.format(where_clause=where_clause), params).fetchall()

    data = [{"reason_code": str(row["reason_code"]), "count": int(row["n"])} for row in rows]
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    md_lines = [
        "# GAIA Reason Code Report",
        "",
        f"- generated_at: {now}",
        f"- db_path: `{db_path}`",
        f"- domain: `{args.domain.strip().lower() or '*'}`",
        "",
        "| reason_code | count |",
        "| --- | ---: |",
    ]
    if data:
        for item in data:
            md_lines.append(f"| {item['reason_code']} | {item['count']} |")
    else:
        md_lines.append("| (none) | 0 |")
    md_text = "\n".join(md_lines) + "\n"

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"reason_code_report_{ts}.md"
    json_path = out_dir / f"reason_code_report_{ts}.json"
    md_path.write_text(md_text, encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[reason-code-report] markdown: {md_path}")
    print(f"[reason-code-report] json: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
