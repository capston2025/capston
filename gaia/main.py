"""Entry point for the GAIA desktop orchestrator."""
from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication

from gaia.src.gui.controller import AppController
from gaia.src.gui.main_window import MainWindow


def _bootstrap_controller(window: MainWindow) -> AppController:
    return AppController(window)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(controller_factory=_bootstrap_controller)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
