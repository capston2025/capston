from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "gaia" / "tests" / "scenarios" / "external_public_manifest.json"
REQUIRED_SCENARIO_FIELDS = {
    "id",
    "url",
    "goal",
    "constraints",
    "expected_signals",
    "time_budget_sec",
}
FORBIDDEN_GOAL_KEYWORDS = {
    "로그인",
    "회원가입",
    "결제",
    "구매",
    "장바구니",
    "삭제",
    "댓글",
    "글쓰기",
    "게시",
    "업로드",
    "다운로드",
    "예약",
    "captcha",
    "password",
    "비밀번호",
}
INTERNAL_HOST_MARKERS = {
    "inuu-timetable.vercel.app",
    "localhost",
    "127.0.0.1",
}


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _load_suite(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_external_public_manifest_has_30_sites_and_150_scenarios() -> None:
    manifest = _load_manifest()

    assert manifest["site_count"] == 30
    assert manifest["scenario_count"] == 150
    assert len(manifest["suites"]) == 30

    total = sum(len(_load_suite(item["suite_path"])["scenarios"]) for item in manifest["suites"])
    assert total == 150


def test_external_public_suites_exist_and_have_five_scenarios() -> None:
    manifest = _load_manifest()

    for item in manifest["suites"]:
        suite_path = ROOT / item["suite_path"]
        assert suite_path.exists(), item["suite_path"]
        suite = _load_suite(item["suite_path"])
        assert len(suite["scenarios"]) == 5, item["suite_path"]


def test_external_public_scenario_contract_and_uniqueness() -> None:
    manifest = _load_manifest()
    scenario_ids: set[str] = set()

    for item in manifest["suites"]:
        suite = _load_suite(item["suite_path"])
        for scenario in suite["scenarios"]:
            assert REQUIRED_SCENARIO_FIELDS <= scenario.keys(), scenario.get("id")
            scenario_id = str(scenario["id"])
            assert scenario_id not in scenario_ids
            scenario_ids.add(scenario_id)
            assert isinstance(scenario["constraints"], dict), scenario_id
            assert isinstance(scenario["expected_signals"], list), scenario_id
            assert int(scenario["time_budget_sec"]) > 0, scenario_id


def test_external_public_manifest_excludes_internal_hosts_and_destructive_goals() -> None:
    manifest = _load_manifest()

    for item in manifest["suites"]:
        suite = _load_suite(item["suite_path"])
        urls = [str(item.get("base_url") or ""), str(suite.get("site", {}).get("base_url") or "")]
        urls.extend(str(scenario.get("url") or "") for scenario in suite["scenarios"])
        for url in urls:
            host = urlparse(url).netloc.lower()
            assert not any(marker in host for marker in INTERNAL_HOST_MARKERS), url
        for scenario in suite["scenarios"]:
            goal = str(scenario.get("goal") or "").lower()
            assert not any(keyword.lower() in goal for keyword in FORBIDDEN_GOAL_KEYWORDS), scenario["id"]
