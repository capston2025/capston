"""GAIA package root exposing high-level orchestration helpers."""

from gaia.src.phase1.analyzer import SpecAnalyzer
from gaia.src.phase1.pdf_loader import PDFLoader
from gaia.src.phase4.agent import AgentOrchestrator, MCPClient
from gaia.src.phase5.report import build_summary
from gaia.src.tracker.checklist import ChecklistTracker

try:  # Optional GUI dependency (PySide6)
    from gaia.src.gui import AppController, MainWindow
except ModuleNotFoundError:  # pragma: no cover - GUI optional during testing
    AppController = None  # type: ignore[assignment]
    MainWindow = None  # type: ignore[assignment]

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
