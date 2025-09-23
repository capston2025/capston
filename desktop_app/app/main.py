"""Entry point for the QA automation desktop application."""
from __future__ import annotations

import sys
from PyQt6.QtWidgets import QApplication

from app.core.controller import AppController
from app.ui.main_window import MainWindow


def _bootstrap_controller(window: MainWindow) -> AppController:
    """Create the application controller bound to *window*."""
    return AppController(window)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow(controller_factory=_bootstrap_controller)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
