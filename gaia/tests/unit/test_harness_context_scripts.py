from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_context_pack_repo_entry_renders() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/context_pack.py", "--area", "repo-entry"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "# Context Pack: repo-entry" in completed.stdout
    assert "AGENTS.md" in completed.stdout


def test_harness_docs_lint_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/lint_harness_docs.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "harness docs lint passed" in completed.stdout


def test_dev_harness_audit_renders() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/dev_harness.py", "audit"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "# Development Harness Audit" in completed.stdout
    assert "multi-user-interaction" in completed.stdout


def test_dev_harness_plan_renders_team_pattern() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/dev_harness.py", "plan", "--lane", "development-harness"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "Team pattern:" in completed.stdout
    assert "Eval contract:" in completed.stdout


def test_dev_harness_detect_maps_paths_to_lanes() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/dev_harness.py",
            "detect",
            "docs/harness/DEVELOPMENT_HARNESS.md",
            "gaia/src/phase4/participants/models.py",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "development-harness" in completed.stdout
    assert "multi-user-interaction" in completed.stdout
    assert "Recommended lane:" in completed.stdout


def test_dev_harness_dry_run_prints_commands() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/dev_harness.py",
            "run",
            "--lane",
            "cleanup-gc",
            "--tier",
            "full",
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "$ git diff --check" in completed.stdout
