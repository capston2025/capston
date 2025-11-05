#!/usr/bin/env python3
"""GAIA GUI를 실행합니다"""
import sys
from pathlib import Path

# 임포트를 위해 상위 디렉터리를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

# .env 파일에서 환경 변수를 로드
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

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
