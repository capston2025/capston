"""원시 기획 컨텍스트를 추출하기 위한 PDF 유틸리티입니다."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

try:  # 가능하면 pypdf를 우선 사용
    from pypdf import PdfReader  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - 선택적 폴백
    try:
        from PyPDF2 import PdfReader  # type: ignore
    except ModuleNotFoundError:
        PdfReader = None  # type: ignore[assignment]


@dataclass(slots=True)
class ChecklistExtractionResult:
    """제품 명세 PDF를 파싱한 구조화된 결과입니다."""

    text: str
    checklist_items: Sequence[str] = field(default_factory=tuple)
    notes: Sequence[str] = field(default_factory=tuple)
    suggested_url: str | None = None


class PDFLoader:
    """PDF 파일을 텍스트로 변환하고 간단한 체크리스트 힌트를 도출합니다."""

    def extract(self, pdf_path: Path | str) -> ChecklistExtractionResult:
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(path)

        raw_text = self._read_pdf(path)
        checklist = self._infer_checklist(raw_text)

        return ChecklistExtractionResult(
            text=raw_text,
            checklist_items=tuple(checklist),
            notes=(
                "Checklist items inferred heuristically; review before execution.",
            ),
        )

    # ------------------------------------------------------------------
    def _read_pdf(self, path: Path) -> str:
        if PdfReader is None:
            raise RuntimeError(
                "Neither pypdf nor PyPDF2 is installed. Install one of them to parse PDFs."
            )

        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception as exc:  # pragma: no cover - 방어적 처리
                pages.append(f"[Extraction error: {exc}]")
        return "\n".join(page.strip() for page in pages if page.strip())

    def _infer_checklist(self, text: str) -> Iterable[str]:
        if not text:
            return []

        items: list[str] = []
        for line in text.splitlines():
            normalized = line.strip(" •\t")
            if not normalized:
                continue
            if len(normalized) < 200 and any(keyword in normalized.lower() for keyword in ("로그인", "signup", "결제", "dashboard", "report")):
                items.append(normalized)
        if not items:
            head = text.splitlines()[:5]
            items = [f"Spec summary: {fragment[:90]}" for fragment in head if fragment]
        return items
