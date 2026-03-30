"""Minimal task harness for GAIA one-shot terminal runs."""
from __future__ import annotations

from .registry import HarnessTask, TaskRegistry, load_builtin_registry, load_registry
from .runner import run_registry, run_task

__all__ = [
    "HarnessTask",
    "TaskRegistry",
    "load_builtin_registry",
    "load_registry",
    "run_registry",
    "run_task",
]
