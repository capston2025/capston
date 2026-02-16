"""GAIA package root exposing high-level orchestration helpers."""

from __future__ import annotations

from typing import Any

__all__ = [
    "AppController",
    "MainWindow",
    "SpecAnalyzer",
    "PDFLoader",
    "AgentOrchestrator",
    "MCPClient",
    "build_summary",
    "ChecklistTracker",
]


def __getattr__(name: str) -> Any:
    if name == "SpecAnalyzer":
        from gaia.src.phase1.analyzer import SpecAnalyzer

        return SpecAnalyzer
    if name == "PDFLoader":
        from gaia.src.phase1.pdf_loader import PDFLoader

        return PDFLoader
    if name == "AgentOrchestrator":
        from gaia.src.phase4.agent import AgentOrchestrator

        return AgentOrchestrator
    if name == "MCPClient":
        from gaia.src.phase4.agent import MCPClient

        return MCPClient
    if name == "build_summary":
        from gaia.src.phase5.report import build_summary

        return build_summary
    if name == "ChecklistTracker":
        from gaia.src.tracker.checklist import ChecklistTracker

        return ChecklistTracker
    if name == "AppController":
        from gaia.src.gui import AppController

        return AppController
    if name == "MainWindow":
        from gaia.src.gui import MainWindow

        return MainWindow
    raise AttributeError(name)
