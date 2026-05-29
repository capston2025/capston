"""Human-vs-GAIA benchmark board artifact writer."""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

BOARD_SCHEMA_VERSION = "gaia.human_vs_gaia.board.v1"


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _status_label(value: Any) -> str:
    status = _text(value, "PENDING").upper()
    if status in {"SUCCESS", "PASS", "PASSED"}:
        return "SUCCESS"
    if status in {"BLOCKED_USER_ACTION", "BLOCKED"}:
        return "BLOCKED"
    if status in {"PENDING", "WAITING"}:
        return "PENDING"
    return status or "UNKNOWN"


def _duration(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except Exception:
        return None


def _build_entries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        scenario_id = _text(row.get("scenario_id") or row.get("id"), f"scenario-{index}")
        repeat = row.get("repeat")
        try:
            repeat_num = int(repeat)
        except Exception:
            repeat_num = 1
        gaia_status = _status_label(row.get("status"))
        entries.append(
            {
                "scenario_id": scenario_id,
                "repeat": repeat_num,
                "goal": _text(row.get("goal")),
                "human": {
                    "status": "PENDING",
                    "duration_seconds": None,
                    "note": "사람 기록 대기",
                },
                "gaia": {
                    "status": gaia_status,
                    "duration_seconds": _duration(row.get("duration_seconds")),
                    "runner_id": _text(row.get("runner_id")),
                    "provider": _text(row.get("provider")),
                    "model": _text(row.get("model")),
                    "reason": _text(row.get("reason")),
                },
                "winner": "PENDING" if gaia_status != "SUCCESS" else "GAIA_WAITING_HUMAN",
            }
        )
    return entries


def build_battle_board_payload(
    *,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    entries = _build_entries(rows)
    gaia_counts = Counter(entry["gaia"]["status"] for entry in entries)
    return {
        "schema_version": BOARD_SCHEMA_VERSION,
        "title": "Human vs GAIA Battle Board",
        "generated_at": generated.isoformat(),
        "suite_id": _text(summary.get("suite_id")),
        "site": dict(summary.get("site") or {}) if isinstance(summary.get("site"), Mapping) else {},
        "runner_id": _text(summary.get("runner_id")),
        "provider": _text(summary.get("provider")),
        "model": _text(summary.get("model")),
        "qa_mode": _text(summary.get("qa_mode"), "off"),
        "benchmark_mode": _text(summary.get("benchmark_mode"), "standard"),
        "started_at": _text(summary.get("started_at")),
        "scenario_count": int(summary.get("scenario_count") or len(entries)),
        "repeats": int(summary.get("repeats") or 1),
        "gaia_counts": dict(gaia_counts),
        "entries": entries,
    }


def _json_for_script(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def render_battle_board_html(payload: Mapping[str, Any]) -> str:
    title = html.escape(_text(payload.get("title"), "Human vs GAIA Battle Board"))
    data = _json_for_script(payload)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --panel: #171b22;
      --line: #2a313c;
      --text: #eef2f7;
      --muted: #9aa5b5;
      --good: #4cc38a;
      --bad: #f06d6d;
      --wait: #f5bd4f;
      --info: #65a8ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 44px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; margin-bottom: 22px; }}
    h1 {{ margin: 0; font-size: clamp(26px, 4vw, 44px); letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 14px; text-align: right; line-height: 1.55; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .card {{ border: 1px solid var(--line); background: var(--panel); border-radius: 8px; padding: 14px; }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0; }}
    .value {{ font-size: 26px; font-weight: 760; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 8px; border: 1px solid var(--line); }}
    th, td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; }}
    th {{ background: #1d232c; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0; }}
    tr:last-child td {{ border-bottom: 0; }}
    .scenario {{ min-width: 180px; }}
    .goal {{ color: var(--muted); font-size: 13px; margin-top: 4px; line-height: 1.4; }}
    .pill {{ display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 650; }}
    .SUCCESS {{ color: var(--good); border-color: color-mix(in srgb, var(--good) 55%, var(--line)); }}
    .FAIL, .ERROR, .UNKNOWN {{ color: var(--bad); border-color: color-mix(in srgb, var(--bad) 55%, var(--line)); }}
    .BLOCKED, .PENDING {{ color: var(--wait); border-color: color-mix(in srgb, var(--wait) 55%, var(--line)); }}
    .runner {{ color: var(--muted); font-size: 12px; margin-top: 6px; }}
    .reason {{ color: var(--muted); font-size: 12px; line-height: 1.4; max-width: 360px; overflow-wrap: anywhere; }}
    @media (max-width: 820px) {{
      header {{ display: block; }}
      .meta {{ text-align: left; margin-top: 10px; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ display: none; }}
      tr {{ border-bottom: 1px solid var(--line); }}
      td {{ border-bottom: 0; padding: 10px; }}
      td::before {{ content: attr(data-label); display: block; color: var(--muted); font-size: 11px; margin-bottom: 4px; text-transform: uppercase; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Human vs GAIA</h1>
        <div class="goal">실행 중인 GAIA 결과는 이 페이지에 계속 반영됩니다. 사람 기록은 같은 시나리오 기록이 붙으면 비교됩니다.</div>
      </div>
      <div class="meta" id="meta"></div>
    </header>
    <section class="grid" id="cards"></section>
    <table>
      <thead>
        <tr>
          <th>Scenario</th>
          <th>Human</th>
          <th>GAIA</th>
          <th>Winner</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </main>
  <script>
    const board = {data};
    const entries = board.entries || [];
    const counts = board.gaia_counts || {{}};
    const success = counts.SUCCESS || 0;
    const blocked = counts.BLOCKED || 0;
    const failed = entries.length - success - blocked;
    const avg = entries.length
      ? (entries.reduce((sum, row) => sum + Number(row.gaia.duration_seconds || 0), 0) / entries.length).toFixed(1)
      : "0.0";
    document.getElementById("meta").innerHTML = [
      `suite: ${{board.suite_id || "-"}}`,
      `runner: ${{board.runner_id || "-"}}`,
      `updated: ${{board.generated_at || "-"}}`
    ].join("<br>");
    document.getElementById("cards").innerHTML = [
      ["Total", entries.length],
      ["GAIA Success", success],
      ["Blocked/Fail", blocked + failed],
      ["Avg Seconds", avg]
    ].map(([label, value]) => `<article class="card"><div class="label">${{label}}</div><div class="value">${{value}}</div></article>`).join("");
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}}[char]));
    document.getElementById("rows").innerHTML = entries.map((entry) => {{
      const human = entry.human || {{}};
      const gaia = entry.gaia || {{}};
      const gaiaStatus = gaia.status || "UNKNOWN";
      const humanStatus = human.status || "PENDING";
      return `<tr>
        <td data-label="Scenario" class="scenario"><strong>${{esc(entry.scenario_id)}}</strong><div class="goal">${{esc(entry.goal || "")}}</div></td>
        <td data-label="Human"><span class="pill ${{esc(humanStatus)}}">${{esc(humanStatus)}}</span><div class="runner">${{esc(human.note || "")}}</div></td>
        <td data-label="GAIA"><span class="pill ${{esc(gaiaStatus)}}">${{esc(gaiaStatus)}}</span><div class="runner">${{esc(gaia.duration_seconds ?? "-")}}s · ${{esc(gaia.model || "-")}}</div></td>
        <td data-label="Winner">${{esc(entry.winner || "PENDING")}}</td>
        <td data-label="Evidence" class="reason">${{esc(gaia.reason || "-")}}</td>
      </tr>`;
    }}).join("");
  </script>
</body>
</html>
"""


def write_battle_board(
    output_dir: Path | str,
    *,
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    target_dir = Path(output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    payload = build_battle_board_payload(summary=summary, rows=rows, generated_at=generated_at)
    json_path = target_dir / "battle_board.json"
    html_path = target_dir / "battle_board.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(render_battle_board_html(payload), encoding="utf-8")
    return {
        "enabled": True,
        "mode": "human_vs_gaia",
        "json_path": str(json_path),
        "html_path": str(html_path),
        "url": html_path.as_uri(),
    }
