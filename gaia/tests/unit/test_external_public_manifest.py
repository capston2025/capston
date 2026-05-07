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
EXCLUDED_SITE_KEYS = {
    "boj",
    "cgv",
    "coupang",
    "gmarket",
    "naver_shopping",
    "npm",
    "oliveyoung",
    "spell_checker",
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
    site_keys = {str(item.get("site_key") or "") for item in manifest["suites"]}

    assert not (site_keys & EXCLUDED_SITE_KEYS)

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


def test_kakao_map_route_scenario_uses_public_route_deep_link() -> None:
    suite = _load_suite("gaia/tests/scenarios/kakao_map_public_suite.json")
    route = next(item for item in suite["scenarios"] if item["id"] == "KAKAOMAP_004_ROUTE_PANEL")

    assert str(route["url"]).startswith("https://map.kakao.com/link/by/")
    assert "서울역" in route["goal"]
    assert "경복궁" in route["goal"]


def test_failed_public_scenarios_use_playwright_verified_readonly_surfaces() -> None:
    fow = _load_suite("gaia/tests/scenarios/fow_public_suite.json")
    fow_by_id = {item["id"]: item for item in fow["scenarios"]}
    assert fow_by_id["FOW_002_CHAMPION_STATS"]["url"] == "https://www.fow.lol/stats"
    assert fow_by_id["FOW_003_RANKING_LIST"]["url"] == "https://www.fow.lol/ranking"
    assert fow_by_id["FOW_004_REGION_QUEUE_FILTER"]["url"] == "https://www.fow.lol/stats"
    assert "승률" in fow_by_id["FOW_004_REGION_QUEUE_FILTER"]["goal"]

    moneytoring = _load_suite("gaia/tests/scenarios/moneytoring_public_suite.json")
    moneytoring_002 = next(item for item in moneytoring["scenarios"] if item["id"] == "MONEYTORING_002_STOCK_SEARCH")
    assert "삼성전자" in moneytoring_002["goal"]
    assert "검색해" not in moneytoring_002["goal"]

    elevenst = _load_suite("gaia/tests/scenarios/elevenst_public_suite.json")
    elevenst_005 = next(item for item in elevenst["scenarios"] if item["id"] == "ELEVENST_005_SORT_CHANGE")
    assert elevenst_005["url"].startswith("https://search.11st.co.kr/pc/total-search")
    assert "선택해" not in elevenst_005["goal"]

    government24 = _load_suite("gaia/tests/scenarios/government24_public_suite.json")
    gov24_005 = next(item for item in government24["scenarios"] if item["id"] == "GOV24_005_RESULT_FILTER")
    assert "검색필터" in gov24_005["goal"]
    assert "선택해" not in gov24_005["goal"]

    seoul_culture = _load_suite("gaia/tests/scenarios/seoul_culture_public_suite.json")
    facility = next(item for item in seoul_culture["scenarios"] if item["id"] == "SEOULCULTURE_004_FACILITY_LIST")
    assert facility["url"] == "https://culture.seoul.go.kr/night/sub/nightFac/list.do"
    assert "야간 운영시설" in facility["goal"]

    pypi = _load_suite("gaia/tests/scenarios/pypi_public_suite.json")
    pypi_002 = next(item for item in pypi["scenarios"] if item["id"] == "PYPI_002_PACKAGE_SEARCH")
    assert pypi_002["url"] == "https://pypi.org/project/requests/#files"
    assert "/search/" not in pypi_002["url"]

    musinsa = _load_suite("gaia/tests/scenarios/musinsa_public_suite.json")
    musinsa_005 = next(item for item in musinsa["scenarios"] if item["id"] == "MUSINSA_005_SORT_CHANGE")
    assert "목록 표시가 바뀌는지" not in musinsa_005["goal"]
    assert "현재 선택값" in musinsa_005["goal"]

    seoul_open_data = _load_suite("gaia/tests/scenarios/seoul_open_data_public_suite.json")
    seoul_data_003 = next(item for item in seoul_open_data["scenarios"] if item["id"] == "SEOULDATA_003_DATASET_DETAIL")
    seoul_data_005 = next(item for item in seoul_open_data["scenarios"] if item["id"] == "SEOULDATA_005_SORT_OR_FILTER")
    assert "검색 결과 또는 상세 카드" in seoul_data_003["goal"]
    assert "조회 버튼" in seoul_data_005["goal"]
    assert "목록 표시가 바뀌는지" not in seoul_data_005["goal"]

    museum = _load_suite("gaia/tests/scenarios/national_museum_public_suite.json")
    assert museum["site"]["name"] == "국립중앙박물관"
    assert len(museum["scenarios"]) == 5
