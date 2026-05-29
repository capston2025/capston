from __future__ import annotations

import json
from datetime import datetime, timezone

from gaia.src.battle_board import build_battle_board_payload, write_battle_board


def test_battle_board_payload_marks_human_side_pending() -> None:
    payload = build_battle_board_payload(
        summary={
            "suite_id": "human_vs_gaia_demo",
            "runner_id": "runner-1",
            "provider": "openai",
            "model": "gpt-5.5",
            "qa_mode": "off",
            "benchmark_mode": "standard",
            "scenario_count": 1,
            "repeats": 1,
        },
        rows=[
            {
                "scenario_id": "DEMO_001",
                "goal": "홈 화면 CTA 확인",
                "status": "SUCCESS",
                "duration_seconds": 12.34,
                "runner_id": "runner-1",
                "provider": "openai",
                "model": "gpt-5.5",
            }
        ],
        generated_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
    )

    assert payload["schema_version"] == "gaia.human_vs_gaia.board.v1"
    assert payload["gaia_counts"] == {"SUCCESS": 1}
    assert payload["entries"][0]["human"]["status"] == "PENDING"
    assert payload["entries"][0]["gaia"]["status"] == "SUCCESS"


def test_write_battle_board_creates_html_and_json(tmp_path) -> None:
    info = write_battle_board(
        tmp_path,
        summary={"suite_id": "demo", "scenario_count": 1, "repeats": 1},
        rows=[
            {
                "scenario_id": "DEMO_001",
                "goal": "홈 화면 CTA 확인",
                "status": "FAIL",
                "reason": "button not found",
                "duration_seconds": 3.2,
            }
        ],
    )

    html_path = tmp_path / "battle_board.html"
    json_path = tmp_path / "battle_board.json"
    assert info["enabled"] is True
    assert html_path.exists()
    assert json_path.exists()
    assert "Human vs GAIA" in html_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["entries"][0]["scenario_id"] == "DEMO_001"
