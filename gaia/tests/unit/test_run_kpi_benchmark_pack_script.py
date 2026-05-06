from __future__ import annotations

import json
from pathlib import Path

from scripts.run_kpi_benchmark_pack import (
    MIN_BENCHMARK_TIMEOUT_SEC,
    _build_run_suite_command,
    _effective_timeout_cap,
    _load_suite_manifest,
    _resolve_suite_paths,
)


def test_effective_timeout_cap_enforces_minimum_budget() -> None:
    assert _effective_timeout_cap(180) == MIN_BENCHMARK_TIMEOUT_SEC
    assert _effective_timeout_cap(600) == MIN_BENCHMARK_TIMEOUT_SEC
    assert _effective_timeout_cap(900) == 900


def test_load_suite_manifest_resolves_suite_paths_from_manifest_dir(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"suites": [{"suite_path": "suite.json"}]}),
        encoding="utf-8",
    )

    assert _load_suite_manifest(manifest_path) == [suite_path.resolve()]


def test_resolve_suite_paths_accepts_explicit_suites_and_manifest(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.json"
    manifest_suite = tmp_path / "manifest_suite.json"
    explicit.write_text("{}", encoding="utf-8")
    manifest_suite.write_text("{}", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"suites": [{"suite_path": "manifest_suite.json"}]}),
        encoding="utf-8",
    )

    assert _resolve_suite_paths(
        suite_args=[str(explicit)],
        suite_manifest=str(manifest_path),
    ) == [explicit.resolve(), manifest_suite.resolve()]


def test_build_run_suite_command_forwards_push_metrics(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"

    without_push = _build_run_suite_command(
        suite_path,
        repeats=1,
        timeout_cap=600,
        session_prefix="external-public",
        push_metrics=False,
    )
    with_push = _build_run_suite_command(
        suite_path,
        repeats=1,
        timeout_cap=600,
        session_prefix="external-public",
        push_metrics=True,
    )

    assert "--push-metrics" not in without_push
    assert with_push[-1] == "--push-metrics"
