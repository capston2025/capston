"""Shared benchmark catalog, persistence, suite, and reporting helpers."""

from __future__ import annotations

import html
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse


@dataclass(frozen=True)
class BenchmarkPreset:
    key: str
    label: str
    default_url: str
    suite_path: str | None
    host_aliases: tuple[str, ...]


BENCHMARK_PRESETS: tuple[BenchmarkPreset, ...] = (
    BenchmarkPreset(
        key="inu_timetable",
        label="INU TIMETABLE",
        default_url="https://inuu-timetable.vercel.app/",
        suite_path="gaia/tests/scenarios/inuu_service_suite.json",
        host_aliases=("inuu-timetable.vercel.app",),
    ),
    BenchmarkPreset(
        key="wikipedia",
        label="위키피디아",
        default_url="https://ko.wikipedia.org/",
        suite_path="gaia/tests/scenarios/wikipedia_public_suite.json",
        host_aliases=("wikipedia.org", "ko.wikipedia.org"),
    ),
    BenchmarkPreset(
        key="fow_kr",
        label="Fow.kr",
        default_url="https://www.fow.lol/",
        suite_path="gaia/tests/scenarios/fow_public_suite.json",
        host_aliases=("fow.kr", "www.fow.lol", "fow.lol"),
    ),
    BenchmarkPreset(
        key="youtube",
        label="유튜브",
        default_url="https://www.youtube.com/",
        suite_path="gaia/tests/scenarios/youtube_public_suite.json",
        host_aliases=("youtube.com", "www.youtube.com"),
    ),
    BenchmarkPreset(
        key="github",
        label="깃허브",
        default_url="https://github.com/",
        suite_path="gaia/tests/scenarios/github_public_suite.json",
        host_aliases=("github.com", "www.github.com"),
    ),
    BenchmarkPreset(
        key="hacker_news",
        label="Hacker News",
        default_url="https://news.ycombinator.com/",
        suite_path="gaia/tests/scenarios/hacker_news_public_suite.json",
        host_aliases=("news.ycombinator.com", "ycombinator.com"),
    ),
    BenchmarkPreset(
        key="pypi",
        label="PyPI",
        default_url="https://pypi.org/",
        suite_path="gaia/tests/scenarios/pypi_public_suite.json",
        host_aliases=("pypi.org", "www.pypi.org"),
    ),
    BenchmarkPreset(
        key="npm",
        label="npm",
        default_url="https://www.npmjs.com/",
        suite_path="gaia/tests/scenarios/npm_public_suite.json",
        host_aliases=("npmjs.com", "www.npmjs.com"),
    ),
    BenchmarkPreset(
        key="moneytoring",
        label="머니터링",
        default_url="https://www.moneytoring.ai/",
        suite_path="gaia/tests/scenarios/moneytoring_public_suite.json",
        host_aliases=("moneytoring.ai", "www.moneytoring.ai"),
    ),
    BenchmarkPreset(
        key="apple_store",
        label="애플스토어",
        default_url="https://www.apple.com/kr/",
        suite_path="gaia/tests/scenarios/apple_store_public_suite.json",
        host_aliases=("apple.com", "www.apple.com"),
    ),
    BenchmarkPreset(
        key="spell_checker",
        label="맞춤법 검사기",
        default_url="https://nara-speller.co.kr/speller/",
        suite_path="gaia/tests/scenarios/spell_checker_public_suite.json",
        host_aliases=("nara-speller.co.kr",),
    ),
    BenchmarkPreset(
        key="dcinside",
        label="디시인사이드",
        default_url="https://www.dcinside.com/",
        suite_path="gaia/tests/scenarios/dcinside_public_suite.json",
        host_aliases=("dcinside.com", "www.dcinside.com"),
    ),
)


def benchmark_registry_path() -> Path:
    return Path.home() / ".gaia" / "benchmark_mode_targets.json"


def load_benchmark_registry(path: Path | None = None) -> dict[str, Any]:
    target = path or benchmark_registry_path()
    if not target.exists():
        return {"sites": {}, "custom_sites": {}}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"sites": {}, "custom_sites": {}}
    if not isinstance(payload, dict):
        return {"sites": {}, "custom_sites": {}}
    if not isinstance(payload.get("sites"), dict):
        payload["sites"] = {}
    if not isinstance(payload.get("custom_sites"), dict):
        payload["custom_sites"] = {}
    return payload


def save_benchmark_registry(payload: Mapping[str, Any], path: Path | None = None) -> Path:
    target = path or benchmark_registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    if not isinstance(normalized.get("sites"), dict):
        normalized["sites"] = {}
    if not isinstance(normalized.get("custom_sites"), dict):
        normalized["custom_sites"] = {}
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def find_preset(site_key: str) -> BenchmarkPreset | None:
    for preset in BENCHMARK_PRESETS:
        if preset.key == site_key:
            return preset
    return None


def extract_url_host(url: str) -> str:
    try:
        return str(urlparse(str(url or "").strip()).netloc or "").strip().lower()
    except Exception:
        return ""


def build_url_history(site_entry: Mapping[str, Any]) -> list[str]:
    urls = [str(item).strip() for item in list(site_entry.get("urls") or []) if str(item).strip()]
    default_url = str(site_entry.get("default_url") or "").strip()
    if default_url:
        urls = [default_url] + [item for item in urls if item != default_url]
    return urls


def upsert_benchmark_site_url(payload: Mapping[str, Any], site_key: str, url: str) -> dict[str, Any]:
    normalized = dict(payload)
    sites = dict(normalized.get("sites") or {})
    current = dict(sites.get(site_key) or {})
    clean_url = str(url or "").strip()
    urls = [str(item).strip() for item in list(current.get("urls") or []) if str(item).strip()]
    if clean_url:
        urls = [clean_url] + [item for item in urls if item != clean_url]
    current["default_url"] = clean_url or str(current.get("default_url") or "").strip()
    current["urls"] = urls[:8]
    sites[site_key] = current
    normalized["sites"] = sites
    return normalized


def create_custom_suite_payload(*, site_key: str, label: str, default_url: str) -> dict[str, Any]:
    return {
        "suite_id": f"{site_key}_public_v1",
        "site": {
            "name": label,
            "base_url": default_url,
            "mode": "public_browse",
        },
        "grader_configs": {},
        "scenarios": [],
    }


def create_custom_site_definition(
    *,
    site_key: str,
    label: str,
    default_url: str,
) -> dict[str, Any]:
    host = extract_url_host(default_url)
    host_aliases = tuple(
        dict.fromkeys(
            alias
            for alias in (
                host,
                host.removeprefix("www.") if host.startswith("www.") else "",
                f"www.{host}" if host and not host.startswith("www.") else "",
            )
            if alias
        )
    )
    return {
        "label": label,
        "default_url": default_url,
        "suite_path": f"gaia/tests/scenarios/custom_{site_key}_suite.json",
        "host_aliases": list(host_aliases),
    }


def upsert_custom_benchmark_site(
    payload: Mapping[str, Any],
    *,
    site_key: str,
    site_definition: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(payload)
    custom_sites = dict(normalized.get("custom_sites") or {})
    custom_sites[site_key] = dict(site_definition)
    normalized["custom_sites"] = custom_sites
    normalized = upsert_benchmark_site_url(normalized, site_key, str(site_definition.get("default_url") or ""))
    return normalized


def delete_custom_benchmark_site(payload: Mapping[str, Any], site_key: str) -> dict[str, Any]:
    normalized = dict(payload)
    custom_sites = dict(normalized.get("custom_sites") or {})
    custom_sites.pop(site_key, None)
    normalized["custom_sites"] = custom_sites
    sites = dict(normalized.get("sites") or {})
    sites.pop(site_key, None)
    normalized["sites"] = sites
    return normalized


def build_benchmark_site_catalog(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, BenchmarkPreset]]:
    sites = payload.get("sites") if isinstance(payload.get("sites"), Mapping) else {}
    catalog: list[dict[str, Any]] = []
    preset_map: dict[str, BenchmarkPreset] = {}
    for preset in BENCHMARK_PRESETS:
        preset_map[preset.key] = preset
        current = sites.get(preset.key) if isinstance(sites, Mapping) else {}
        current = current if isinstance(current, Mapping) else {}
        urls = build_url_history(
            {
                "default_url": str(current.get("default_url") or preset.default_url).strip() or preset.default_url,
                "urls": list(current.get("urls") or []),
            }
        )
        status = "준비됨" if preset.suite_path else "suite 미등록"
        catalog.append(
            {
                "key": preset.key,
                "label": preset.label,
                "default_url": urls[0] if urls else preset.default_url,
                "urls": urls[:8],
                "suite_path": preset.suite_path,
                "suite_available": bool(preset.suite_path),
                "status_text": status,
                "is_custom": False,
            }
        )

    custom_sites = payload.get("custom_sites") if isinstance(payload.get("custom_sites"), Mapping) else {}
    for raw_key in sorted(custom_sites):
        raw_entry = custom_sites.get(raw_key)
        if not isinstance(raw_entry, Mapping):
            continue
        site_key = str(raw_key or "").strip()
        label = str(raw_entry.get("label") or "").strip()
        default_url = str(raw_entry.get("default_url") or "").strip()
        suite_path = str(raw_entry.get("suite_path") or "").strip()
        host_aliases = tuple(
            str(item).strip().lower()
            for item in list(raw_entry.get("host_aliases") or [])
            if str(item).strip()
        )
        if not site_key or not label or not default_url or not suite_path:
            continue
        site_state = sites.get(site_key) if isinstance(sites, Mapping) else {}
        site_state = site_state if isinstance(site_state, Mapping) else {}
        urls = build_url_history(
            {
                "default_url": str(site_state.get("default_url") or default_url).strip() or default_url,
                "urls": list(site_state.get("urls") or []),
            }
        )
        preset = BenchmarkPreset(
            key=site_key,
            label=label,
            default_url=default_url,
            suite_path=suite_path,
            host_aliases=host_aliases or tuple(filter(None, (extract_url_host(default_url),))),
        )
        preset_map[site_key] = preset
        catalog.append(
            {
                "key": site_key,
                "label": label,
                "default_url": urls[0] if urls else default_url,
                "urls": urls[:8],
                "suite_path": suite_path,
                "suite_available": bool(suite_path),
                "status_text": "커스텀",
                "is_custom": True,
            }
        )
    return catalog, preset_map


def build_benchmark_catalog(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    catalog, _ = build_benchmark_site_catalog(payload)
    return catalog


def resolve_benchmark_site(payload: Mapping[str, Any], site_key: str) -> BenchmarkPreset | None:
    _, preset_map = build_benchmark_site_catalog(payload)
    return preset_map.get(str(site_key or "").strip())


def load_suite_payload(workspace_root: Path, suite_path: str) -> dict[str, Any]:
    target = (workspace_root / str(suite_path)).resolve()
    if not target.exists():
        raise FileNotFoundError(f"benchmark suite not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark suite must be a JSON object")
    payload.setdefault("scenarios", [])
    if not isinstance(payload.get("scenarios"), list):
        raise ValueError("benchmark suite scenarios must be a list")
    return payload


def save_suite_payload(target: Path, payload: Mapping[str, Any]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(payload)
    normalized.setdefault("scenarios", [])
    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reloaded = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(reloaded, dict):
        raise ValueError("saved suite is not a JSON object")
    return target


def build_scenario_labels(suite_payload: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for raw in list(suite_payload.get("scenarios") or []):
        if not isinstance(raw, Mapping):
            continue
        scenario_id = str(raw.get("id") or "").strip() or "UNKNOWN"
        name = str(raw.get("name") or "").strip()
        goal = str(raw.get("goal") or "").strip() or "-"
        suffix = name or goal
        labels.append(f"{scenario_id} | {suffix}")
    return labels


def build_single_scenario_suite_payload(
    suite_payload: Mapping[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    target_id = str(scenario_id or "").strip()
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    selected = [row for row in scenarios if str(row.get("id") or "").strip() == target_id]
    if not selected:
        raise KeyError(f"scenario not found: {target_id}")
    payload["scenarios"] = selected
    return payload


def append_scenario_to_suite(
    suite_payload: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_id = str(scenario.get("id") or "").strip()
    if not scenario_id:
        raise ValueError("scenario id is required")
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    existing_ids = {str(row.get("id") or "").strip() for row in scenarios}
    if scenario_id in existing_ids:
        raise ValueError(f"duplicate scenario id: {scenario_id}")
    scenarios.append(dict(scenario))
    payload["scenarios"] = scenarios
    return payload


def replace_scenario_in_suite(
    suite_payload: Mapping[str, Any],
    original_id: str,
    updated_scenario: Mapping[str, Any],
) -> dict[str, Any]:
    target_id = str(original_id or "").strip()
    updated_id = str(updated_scenario.get("id") or "").strip()
    if not target_id or not updated_id:
        raise ValueError("scenario id is required")
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    replaced = False
    for index, row in enumerate(scenarios):
        row_id = str(row.get("id") or "").strip()
        if row_id == target_id:
            scenarios[index] = dict(updated_scenario)
            replaced = True
            continue
        if row_id == updated_id and updated_id != target_id:
            raise ValueError(f"duplicate scenario id: {updated_id}")
    if not replaced:
        raise KeyError(f"scenario not found: {target_id}")
    payload["scenarios"] = scenarios
    return payload


def delete_scenario_from_suite(
    suite_payload: Mapping[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    target_id = str(scenario_id or "").strip()
    payload = dict(suite_payload)
    scenarios = [dict(row) for row in list(payload.get("scenarios") or []) if isinstance(row, Mapping)]
    filtered = [row for row in scenarios if str(row.get("id") or "").strip() != target_id]
    if len(filtered) == len(scenarios):
        raise KeyError(f"scenario not found: {target_id}")
    payload["scenarios"] = filtered
    return payload


def default_scenario_name(current: Mapping[str, Any]) -> str:
    name = str(current.get("name") or "").strip()
    if name:
        return name
    scenario_id = str(current.get("id") or "").strip()
    if scenario_id:
        return scenario_id
    return ""


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def infer_scenario_prefix(existing_ids: set[str], default_url: str) -> str:
    prefixes: list[str] = []
    for item in sorted(existing_ids):
        token = str(item or "").strip().split("_", 1)[0].strip().upper()
        if token:
            prefixes.append(token)
    if prefixes:
        counts: dict[str, int] = {}
        for token in prefixes:
            counts[token] = counts.get(token, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    host = extract_url_host(default_url).removeprefix("www.")
    host_token = host.split(".", 1)[0].strip().upper()
    if host_token:
        return re.sub(r"[^A-Z0-9]+", "", host_token) or "SCN"
    return "SCN"


def generate_scenario_id(*, test_name: str, existing_ids: set[str], default_url: str) -> str:
    prefix = infer_scenario_prefix(existing_ids, default_url)
    max_index = 0
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)(?:_|$)")
    for item in existing_ids:
        matched = pattern.match(str(item or "").strip().upper())
        if matched:
            try:
                max_index = max(max_index, int(matched.group(1)))
            except Exception:
                continue
    next_index = max_index + 1
    suffix = _slugify(test_name).replace("-", "_").replace(".", "_").upper()
    suffix = re.sub(r"_+", "_", suffix).strip("_") or "BENCHMARK"
    return f"{prefix}_{next_index:03d}_{suffix}"


def build_scenario_payload(
    *,
    current: Mapping[str, Any],
    test_name: str,
    url: str,
    goal: str,
    time_budget_sec: int,
    existing_ids: set[str],
) -> dict[str, Any]:
    constraints = dict(current.get("constraints") or {})
    current_id = str(current.get("id") or "").strip()
    reserved_ids = {str(item).strip() for item in existing_ids if str(item).strip()}
    if current_id:
        reserved_ids.discard(current_id)
    scenario_id = current_id or generate_scenario_id(
        test_name=test_name,
        existing_ids=reserved_ids,
        default_url=url,
    )
    scenario = dict(current)
    scenario["id"] = scenario_id
    if str(current.get("name") or "").strip() or not current_id or test_name != current_id:
        scenario["name"] = test_name
    else:
        scenario.pop("name", None)
    scenario["url"] = url
    scenario["goal"] = goal
    scenario["constraints"] = dict(constraints) if constraints else {
        "allow_navigation": True,
        "require_ref_only": True,
        "require_state_change": False,
    }
    scenario["time_budget_sec"] = max(1, int(time_budget_sec))
    if "expected_signals" in current and not isinstance(current.get("expected_signals"), list):
        scenario.pop("expected_signals", None)
    elif "expected_signals" not in scenario and not current:
        scenario["expected_signals"] = []
    return scenario


def _remap_suite_url(original_url: str, old_base_url: str, target_url: str) -> str:
    source = str(original_url or "").strip()
    old_base = str(old_base_url or "").strip()
    target = str(target_url or "").strip()
    if not source or not target:
        return source or target
    if not old_base:
        return target
    normalized_base = old_base.rstrip("/")
    if source == old_base or source == normalized_base:
        return target
    prefix = normalized_base + "/"
    if source.startswith(prefix):
        suffix = source[len(prefix) :]
        return target.rstrip("/") + "/" + suffix
    return target


def override_suite_urls(suite_payload: Mapping[str, Any], target_url: str) -> dict[str, Any]:
    clean_url = str(target_url or "").strip()
    payload = dict(suite_payload)
    if not clean_url:
        return payload
    site = dict(payload.get("site") or {})
    old_base_url = str(site.get("base_url") or "").strip()
    if site:
        site["base_url"] = clean_url
        payload["site"] = site
    scenarios = []
    for raw in list(payload.get("scenarios") or []):
        if not isinstance(raw, Mapping):
            continue
        scenario = dict(raw)
        scenario["url"] = _remap_suite_url(str(scenario.get("url") or "").strip(), old_base_url, clean_url)
        scenarios.append(scenario)
    if scenarios:
        payload["scenarios"] = scenarios
    return payload


def _summary_matches_site(summary: Mapping[str, Any], preset: BenchmarkPreset, selected_url: str) -> bool:
    site = summary.get("site") if isinstance(summary.get("site"), Mapping) else {}
    base_url = str(site.get("base_url") or "").strip()
    host = extract_url_host(base_url)
    selected_host = extract_url_host(selected_url)
    if selected_host and host == selected_host:
        return True
    if host and any(alias in host for alias in preset.host_aliases):
        return True
    return False


def scan_benchmark_reports(
    *,
    workspace_root: Path,
    site_key: str,
    selected_url: str = "",
    limit: int = 12,
    registry_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    preset = resolve_benchmark_site(registry_payload or {}, site_key) or find_preset(site_key)
    if preset is None:
        return []
    root = workspace_root / "artifacts" / "benchmarks"
    if not root.exists():
        return []
    reports: list[dict[str, Any]] = []
    for summary_path in sorted(root.glob("*/summary.json"), reverse=True):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(summary, Mapping):
            continue
        if not _summary_matches_site(summary, preset, selected_url):
            continue
        result_path = summary_path.with_name("results.json")
        results: list[dict[str, Any]] = []
        if result_path.exists():
            try:
                parsed = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(parsed, list):
                    results = [row for row in parsed if isinstance(row, dict)]
            except Exception:
                results = []
        reports.append(
            {
                "artifact_dir": str(summary_path.parent),
                "summary_path": str(summary_path),
                "results_path": str(result_path),
                "summary": dict(summary),
                "results": results,
            }
        )
        if len(reports) >= max(1, int(limit)):
            break
    return reports


def render_benchmark_reports_html(
    *,
    site_label: str,
    selected_url: str,
    reports: Iterable[Mapping[str, Any]],
) -> str:
    scenario_cards: list[str] = []
    scenario_groups: dict[str, list[dict[str, Any]]] = {}
    reports_list = list(reports)
    for report in reports_list:
        summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
        results = report.get("results") if isinstance(report.get("results"), list) else []
        started_at = str(summary.get("started_at") or "-")
        artifact_dir = str(report.get("artifact_dir") or "-")
        report_provider = str(summary.get("provider") or "").strip() or _infer_provider_from_model(str(summary.get("model") or ""))
        report_model = str(summary.get("model") or "").strip() or "-"
        for row in results:
            if not isinstance(row, Mapping):
                continue
            scenario_id = str(row.get("scenario_id") or "-").strip() or "-"
            row_model = str(row.get("model") or "").strip() or report_model
            row_provider = str(row.get("provider") or "").strip() or report_provider or _infer_provider_from_model(row_model)
            entry = {
                "scenario_id": scenario_id,
                "goal": str(row.get("goal") or "").strip(),
                "status": str(row.get("status") or "-").strip() or "-",
                "reason": str(row.get("reason") or "-").strip() or "-",
                "duration_seconds": row.get("duration_seconds"),
                "started_at": started_at,
                "artifact_dir": artifact_dir,
                "provider": row_provider or "-",
                "model": row_model or "-",
                "completion_source": (
                    str(
                        (row.get("summary") or {}).get("goal_completion_source")
                        if isinstance(row.get("summary"), Mapping)
                        else ""
                    ).strip()
                    or "-"
                ),
            }
            scenario_groups.setdefault(scenario_id, []).append(entry)

    for scenario_id in sorted(scenario_groups):
        entries = sorted(
            scenario_groups[scenario_id],
            key=lambda item: str(item.get("started_at") or ""),
            reverse=True,
        )
        durations = [
            float(item["duration_seconds"])
            for item in entries
            if isinstance(item.get("duration_seconds"), (int, float))
        ]
        run_count = len(entries)
        success_count = sum(1 for item in entries if str(item.get("status") or "").upper() == "SUCCESS")
        fail_count = sum(1 for item in entries if str(item.get("status") or "").upper() == "FAIL")
        success_rate = (success_count / run_count) if run_count else 0.0
        latest_duration = durations[0] if durations else None
        avg_duration = statistics.mean(durations) if durations else None
        median_duration = statistics.median(durations) if durations else None
        min_duration = min(durations) if durations else None
        max_duration = max(durations) if durations else None
        models = sorted({f"{str(item.get('provider') or '-')} / {str(item.get('model') or '-')}" for item in entries})

        goal_text = html.escape(str(entries[0].get("goal") or "-"))
        metrics_html = "".join(
            [
                _render_benchmark_metric("Runs", str(run_count)),
                _render_benchmark_metric("Success", str(success_count)),
                _render_benchmark_metric("Fail", str(fail_count)),
                _render_benchmark_metric("Success Rate", f"{success_rate:.0%}"),
                _render_benchmark_metric("Latest Sec", _format_seconds(latest_duration)),
                _render_benchmark_metric("Avg Sec", _format_seconds(avg_duration)),
                _render_benchmark_metric("Median Sec", _format_seconds(median_duration)),
                _render_benchmark_metric("Min~Max", _format_range(min_duration, max_duration)),
            ]
        )
        model_html = html.escape(", ".join(models))

        rows = []
        for item in entries:
            status = html.escape(str(item.get("status") or "-"))
            reason = html.escape(str(item.get("reason") or "-"))
            started_at = html.escape(str(item.get("started_at") or "-"))
            completion_source = html.escape(str(item.get("completion_source") or "-"))
            artifact_dir = html.escape(str(item.get("artifact_dir") or "-"))
            duration = html.escape(_format_seconds(item.get("duration_seconds")))
            provider_model = html.escape(f"{str(item.get('provider') or '-')} / {str(item.get('model') or '-')}")
            rows.append(
                f"""
                <tr>
                  <td>{started_at}</td>
                  <td class="mono">{provider_model}</td>
                  <td class="mono">{duration}</td>
                  <td><span class='badge {status.lower()}'>{status}</span></td>
                  <td class="mono">{completion_source}</td>
                  <td>{reason}</td>
                  <td class="path-cell">{artifact_dir}</td>
                </tr>
                """
            )
        row_html = "".join(rows) or "<tr><td colspan='6'>상세 결과 없음</td></tr>"
        scenario_cards.append(
            f"""
            <section class="report-card">
              <div class="report-top">
                <div class="title-block">
                  <div class="eyebrow">SCENARIO</div>
                  <h3>{html.escape(scenario_id)}</h3>
                  <p class="goal">{goal_text}</p>
                  <p class="goal"><strong>Models:</strong> {model_html}</p>
                </div>
                <div class="metrics scenario-metrics">
                  {metrics_html}
                </div>
              </div>
              <table>
                <thead><tr><th>Run Started</th><th>Provider / Model</th><th>Duration</th><th>Status</th><th>Completion</th><th>Reason</th><th>Artifact</th></tr></thead>
                <tbody>{row_html}</tbody>
              </table>
            </section>
            """
        )

    if not scenario_cards:
        scenario_cards.append(
            """
            <section class="empty-card">
              <h3>아직 실행 이력이 없습니다</h3>
              <p>먼저 벤치를 한 번 실행하면 여기에 시각적인 결과 보드가 표시됩니다.</p>
            </section>
            """
        )

    safe_site_label = html.escape(site_label)
    safe_url = html.escape(selected_url or "-")
    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>{safe_site_label} Benchmark Board</title>
      <style>
        :root {{
          --bg1: #f6f8ff;
          --bg2: #e7efff;
          --card: rgba(255,255,255,0.82);
          --line: rgba(110,120,255,0.14);
          --ink: #18203a;
          --muted: #5d6785;
          --primary: #3563ff;
          --success: #16a34a;
          --fail: #dc2626;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          font-family: 'Pretendard', 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif;
          background: linear-gradient(135deg, var(--bg1), var(--bg2));
          color: var(--ink);
        }}
        .shell {{
          max-width: 1200px;
          margin: 0 auto;
          padding: 40px 24px 64px;
        }}
        .hero {{
          background: var(--card);
          border: 1px solid var(--line);
          border-radius: 28px;
          padding: 28px 30px;
          box-shadow: 0 18px 50px rgba(53, 99, 255, 0.10);
          margin-bottom: 24px;
        }}
        .eyebrow {{
          font-size: 12px;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          color: var(--primary);
          font-weight: 700;
          margin-bottom: 10px;
        }}
        h1 {{ margin: 0 0 8px; font-size: 32px; }}
        .sub {{ color: var(--muted); font-size: 14px; }}
        .stack {{ display: grid; gap: 18px; }}
        .report-card, .empty-card {{
          background: var(--card);
          border: 1px solid var(--line);
          border-radius: 24px;
          padding: 22px 24px;
          box-shadow: 0 16px 40px rgba(24, 32, 58, 0.06);
        }}
        .report-top {{
          display: flex;
          justify-content: space-between;
          gap: 16px;
          align-items: flex-start;
          margin-bottom: 14px;
        }}
        .report-top h3 {{ margin: 0; font-size: 20px; }}
        .title-block {{ min-width: 0; }}
        .goal {{ margin: 8px 0 0; color: var(--muted); font-size: 14px; line-height: 1.5; }}
        .metrics {{
          display: grid;
          grid-template-columns: repeat(4, minmax(80px, 1fr));
          gap: 10px;
          min-width: 360px;
        }}
        .scenario-metrics {{
          grid-template-columns: repeat(4, minmax(92px, 1fr));
          min-width: min(100%, 560px);
        }}
        .metric {{
          background: rgba(255,255,255,0.9);
          border: 1px solid rgba(53, 99, 255, 0.10);
          border-radius: 16px;
          padding: 12px 10px;
          text-align: center;
        }}
        .metric span {{ display: block; font-size: 20px; font-weight: 800; }}
        .metric label {{ display: block; color: var(--muted); font-size: 11px; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.08em; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ text-align: left; padding: 12px 10px; border-top: 1px solid rgba(24, 32, 58, 0.08); vertical-align: top; }}
        th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
        .mono {{ font-family: 'SFMono-Regular', 'Menlo', monospace; white-space: nowrap; }}
        .path-cell {{ font-size: 12px; color: var(--muted); word-break: break-all; min-width: 220px; }}
        .badge {{
          display: inline-flex;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 700;
        }}
        .badge.success {{ color: white; background: var(--success); }}
        .badge.fail {{ color: white; background: var(--fail); }}
        .badge.blocked {{ color: white; background: #f59e0b; }}
        .badge.skipped {{ color: white; background: #6b7280; }}
        @media (max-width: 960px) {{
          .report-top {{ flex-direction: column; }}
          .metrics, .scenario-metrics {{
            min-width: 100%;
            grid-template-columns: repeat(2, minmax(120px, 1fr));
          }}
        }}
      </style>
    </head>
    <body>
      <main class="shell">
        <section class="hero">
          <div class="eyebrow">Benchmark Results</div>
          <h1>{safe_site_label}</h1>
          <div class="sub">선택 URL: {safe_url}</div>
          <div class="sub">시나리오별로 묶어서 최신 이력과 정량 시간 지표를 보여줍니다.</div>
        </section>
        <section class="stack">
          {''.join(scenario_cards)}
        </section>
      </main>
    </body>
    </html>
    """


def _render_benchmark_metric(label: str, value: str) -> str:
    return (
        "<div class=\"metric\">"
        f"<span>{html.escape(value)}</span>"
        f"<label>{html.escape(label)}</label>"
        "</div>"
    )


def _format_seconds(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "-"
    return f"{float(value):.2f}s"


def _format_range(min_value: float | None, max_value: float | None) -> str:
    if min_value is None or max_value is None:
        return "-"
    return f"{min_value:.2f}s ~ {max_value:.2f}s"


def _infer_provider_from_model(model_name: str) -> str:
    normalized = str(model_name or "").strip().lower()
    if normalized.startswith("gpt-") or "codex" in normalized:
        return "openai"
    if normalized.startswith("gemini"):
        return "gemini"
    if normalized.startswith("gemma") or normalized.startswith("ollama:"):
        return "ollama"
    return ""


__all__ = [
    "BENCHMARK_PRESETS",
    "BenchmarkPreset",
    "append_scenario_to_suite",
    "benchmark_registry_path",
    "build_benchmark_catalog",
    "build_benchmark_site_catalog",
    "build_scenario_labels",
    "build_scenario_payload",
    "build_single_scenario_suite_payload",
    "build_url_history",
    "create_custom_site_definition",
    "create_custom_suite_payload",
    "default_scenario_name",
    "delete_custom_benchmark_site",
    "delete_scenario_from_suite",
    "extract_url_host",
    "find_preset",
    "generate_scenario_id",
    "infer_scenario_prefix",
    "load_benchmark_registry",
    "load_suite_payload",
    "override_suite_urls",
    "render_benchmark_reports_html",
    "replace_scenario_in_suite",
    "resolve_benchmark_site",
    "save_benchmark_registry",
    "save_suite_payload",
    "scan_benchmark_reports",
    "upsert_benchmark_site_url",
    "upsert_custom_benchmark_site",
]
