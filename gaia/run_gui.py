#!/usr/bin/env python3
"""Launch GAIA GUI"""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication
from gaia.src.gui.main_window import MainWindow
from gaia.src.gui.controller import AppController

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GAIA - QA Automation")

    window = MainWindow()
    controller = AppController(window)

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
