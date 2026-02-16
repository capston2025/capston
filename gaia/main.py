"""Entry point for the GAIA desktop orchestrator."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(Path(__file__).parent.parent / ".env")

from PySide6.QtWidgets import QApplication

from gaia.common import load_run_context
from gaia.src.gui.controller import AppController
from gaia.src.gui.main_window import MainWindow


def _bootstrap_controller(window: MainWindow) -> AppController:
    return AppController(window)


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia start gui",
        description="Launch GAIA GUI",
    )
    parser.add_argument("--resume", help="Resume GUI from terminal run context")
    parser.add_argument("--url", help="Pre-fill URL field")
    parser.add_argument("--plan", help="Load plan file in advance")
    parser.add_argument("--spec", help="Reserved for future use")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _create_parser()
    parsed = parser.parse_args(list(argv or []))

    source_root = Path(__file__).parent.parent
    sys.path.insert(0, str(source_root))

    app = QApplication(sys.argv)
    controller_holder: list[AppController] = []

    def create_controller(window_ref: MainWindow) -> AppController:
        controller = _bootstrap_controller(window_ref)
        controller_holder.append(controller)
        return controller

    window = MainWindow(controller_factory=create_controller)
    window.show()

    if not controller_holder:
        print("Failed to initialize AppController.")
        return 1

    controller = controller_holder[0]
    if parsed.resume:
        try:
            context = load_run_context(parsed.resume)
        except Exception as exc:
            print(f"Failed to load run context: {exc}", file=sys.stderr)
            return 1
        controller.apply_run_context(context=context, url=parsed.url, plan_path=parsed.plan)
    elif parsed.url or parsed.plan:
        controller.apply_run_context(context=None, url=parsed.url, plan_path=parsed.plan)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
