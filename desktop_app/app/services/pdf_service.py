"""PDF parsing helpers that reuse server-side checklist extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


@dataclass(slots=True)
class PDFServiceResult:
    """Structured result from parsing a checklist PDF."""

    items: Sequence[str] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)
    suggested_url: str | None = None


class PDFService:
    """Facade for checklist extraction logic.

    In Phase 2 this will call into the FastAPI checklist parsing functions.
    For now it returns a stub so the UI can be exercised without backend wiring.
    """

    def extract_checklist(self, pdf_path: Path) -> PDFServiceResult:
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        placeholder = f"TODO: parse '{pdf_path.name}' via shared FastAPI logic"
        return PDFServiceResult(
            items=(placeholder,),
            notes=("Backend integration pending",),
            suggested_url="https://example.com",
        )
