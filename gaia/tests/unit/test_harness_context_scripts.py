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
