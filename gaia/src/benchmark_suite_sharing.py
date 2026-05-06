"""Helpers for sharing benchmark suite definitions through the monitoring server."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import requests

SHARE_USER = "gaia"
SHARED_SUITES_PATH = "/shared/suites"
_SENSITIVE_KEY_RE = re.compile(r"(password|passwd|token|secret|api[_-]?key|cookie|authorization)", re.IGNORECASE)


class SharedSuiteError(RuntimeError):
    """Raised when suite sharing fails."""


class SharedSuiteNotFound(SharedSuiteError):
    """Raised when the remote shared suite does not exist yet."""


@dataclass(frozen=True)
class SharedSuiteMergeStats:
    added: int
    updated: int
    local_only: int
    remote_total: int


def build_shared_suite_url(server: str, suite_key: str) -> str:
    base = _monitoring_base_url(server)
    safe_key = quote(str(suite_key or "").strip(), safe="")
    if not safe_key:
        raise SharedSuiteError("suite_key is required")
    return f"{base}{SHARED_SUITES_PATH}/{safe_key}.json"


def build_shared_suite_index_url(server: str) -> str:
    return f"{_monitoring_base_url(server)}{SHARED_SUITES_PATH}/"


def sanitize_suite_for_sharing(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(dict(payload), ensure_ascii=False))
    return _scrub_sensitive_values(normalized)


def merge_shared_suite_payload(
    local_payload: Mapping[str, Any],
    remote_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], SharedSuiteMergeStats]:
    local = dict(local_payload)
    remote = dict(remote_payload)
    remote_scenarios = [dict(row) for row in list(remote.get("scenarios") or []) if isinstance(row, Mapping)]
    local_scenarios = [dict(row) for row in list(local.get("scenarios") or []) if isinstance(row, Mapping)]

    local_by_id = {_scenario_id(row): row for row in local_scenarios if _scenario_id(row)}
    remote_by_id = {_scenario_id(row): row for row in remote_scenarios if _scenario_id(row)}

    merged_scenarios: list[dict[str, Any]] = []
    added = 0
    updated = 0
    for row in remote_scenarios:
        scenario_id = _scenario_id(row)
        if scenario_id:
            local_row = local_by_id.get(scenario_id)
            if local_row is None:
                added += 1
            elif _json_key(local_row) != _json_key(row):
                updated += 1
        merged_scenarios.append(dict(row))

    local_only = 0
    for row in local_scenarios:
        scenario_id = _scenario_id(row)
        if scenario_id and scenario_id in remote_by_id:
            continue
        local_only += 1
        merged_scenarios.append(dict(row))

    merged = dict(local)
    if isinstance(remote.get("site"), Mapping):
        merged["site"] = {**dict(remote.get("site") or {}), **dict(local.get("site") or {})}
    if isinstance(remote.get("grader_configs"), Mapping) or isinstance(local.get("grader_configs"), Mapping):
        merged["grader_configs"] = {**dict(remote.get("grader_configs") or {}), **dict(local.get("grader_configs") or {})}
    if remote.get("suite_id") and not merged.get("suite_id"):
        merged["suite_id"] = remote.get("suite_id")
    merged["scenarios"] = merged_scenarios

    return merged, SharedSuiteMergeStats(
        added=added,
        updated=updated,
        local_only=local_only,
        remote_total=len(remote_scenarios),
    )


def upload_shared_suite(
    *,
    server: str,
    token: str | None,
    suite_key: str,
    suite_payload: Mapping[str, Any],
    timeout: int = 10,
) -> str:
    url = build_shared_suite_url(server, suite_key)
    body = json.dumps(sanitize_suite_for_sharing(suite_payload), ensure_ascii=False, indent=2) + "\n"
    response = requests.put(
        url,
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        auth=(SHARE_USER, token) if token else None,
        timeout=timeout,
    )
    _raise_for_share_response(response, "upload")
    return url


def download_shared_suite(
    *,
    server: str,
    token: str | None,
    suite_key: str,
    timeout: int = 10,
) -> dict[str, Any]:
    url = build_shared_suite_url(server, suite_key)
    response = requests.get(
        url,
        auth=(SHARE_USER, token) if token else None,
        timeout=timeout,
    )
    if response.status_code == 404:
        raise SharedSuiteNotFound(f"shared suite not found: {suite_key}")
    _raise_for_share_response(response, "download")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SharedSuiteError("shared suite response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SharedSuiteError("shared suite must be a JSON object")
    payload.setdefault("scenarios", [])
    if not isinstance(payload.get("scenarios"), list):
        raise SharedSuiteError("shared suite scenarios must be a list")
    return payload


def list_shared_suites(*, server: str, token: str | None, timeout: int = 10) -> list[str]:
    response = requests.get(
        build_shared_suite_index_url(server),
        auth=(SHARE_USER, token) if token else None,
        timeout=timeout,
    )
    if response.status_code == 404:
        return []
    _raise_for_share_response(response, "list")
    try:
        entries = response.json()
    except ValueError:
        return []
    if not isinstance(entries, list):
        return []
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "").strip()
        if name.endswith(".json"):
            names.append(name.removesuffix(".json"))
    return sorted(names)


def _monitoring_base_url(server: str) -> str:
    parsed = urlsplit(str(server or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise SharedSuiteError("valid monitoring server URL is required")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _scenario_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("scenario_id") or "").strip()


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _scrub_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for key, child in value.items():
            if _SENSITIVE_KEY_RE.search(str(key)):
                continue
            scrubbed[key] = _scrub_sensitive_values(child)
        return scrubbed
    if isinstance(value, list):
        return [_scrub_sensitive_values(item) for item in value]
    return value


def _raise_for_share_response(response: requests.Response, operation: str) -> None:
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        status = getattr(response, "status_code", "?")
        if status == 401:
            raise SharedSuiteError("shared suite authentication failed; reconnect monitoring token") from exc
        raise SharedSuiteError(f"shared suite {operation} failed: HTTP {status}") from exc
