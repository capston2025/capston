"""Worker thread for Agent Builder analysis"""
from PySide6.QtCore import QObject, Signal

from gaia.src.phase1.agent_client import AgentServiceClient


class AnalysisWorker(QObject):
    """Worker to analyze PDF with Agent Builder in background thread"""

    # Signals
    progress = Signal(str)  # Log messages
    finished = Signal(object)  # AnalysisResult
    error = Signal(str)  # Error message

    def __init__(self, pdf_text: str):
        super().__init__()
        self.pdf_text = pdf_text
        self._agent_client = AgentServiceClient()

    def run(self) -> None:
        """Run the analysis (executed in worker thread)"""
        try:
            self.progress.emit("🤖 Analyzing with AI Agent Builder (GPT-5)...")
            self.progress.emit("⏱️  Large documents may take 3-5 minutes...")

            # Call Agent Builder with extended timeout for GPT-5
            # GPT-5 can take 10-15 minutes for large documents (50+ pages)
            analysis_result = self._agent_client.analyze_document(
                self.pdf_text,
                timeout=1500  # 25 minutes
            )

            # Emit success
            self.finished.emit(analysis_result)

        except Exception as exc:
            # Emit error
            self.error.emit(str(exc))
