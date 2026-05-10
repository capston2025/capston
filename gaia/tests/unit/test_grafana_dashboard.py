from __future__ import annotations

import json
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_dashboard_has_all_case_success_board_for_full_pack_runs() -> None:
    dashboard_path = _repo_root() / "monitoring/grafana/dashboards/gaia_kpi.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    panels = {panel.get("id"): panel for panel in dashboard.get("panels", [])}

    panel = panels[911]
    assert panel["title"] == "전체 케이스 성공률 보드"

    expressions = "\n".join(str(target.get("expr") or "") for target in panel.get("targets", []))
    assert 'instance=~"kpi_pack_.*"' in expressions
    assert "gaia_scenario_success_count" in expressions
    assert "gaia_scenario_runs_total" in expressions
    assert "gaia_scenario_fail_count" in expressions

    description = str(panel.get("description") or "")
    assert "success_count/runs_total" in description
