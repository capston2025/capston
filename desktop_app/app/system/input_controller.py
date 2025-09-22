"""Thin wrapper around PyAutoGUI for system-level interactions."""
from __future__ import annotations

from typing import Tuple

try:
    import pyautogui
except ModuleNotFoundError:  # pragma: no cover - optional dependency during scaffolding
    pyautogui = None  # type: ignore[assignment]


class InputController:
    """Provides high-level helpers for mouse and keyboard automation."""

    def __init__(self) -> None:
        self._ensure_available()

    # ------------------------------------------------------------------
    def move_and_click(self, position: Tuple[int, int]) -> None:
        self._ensure_available()
        pyautogui.moveTo(*position)
        pyautogui.click()

    def type_text(self, text: str, interval: float = 0.0) -> None:
        self._ensure_available()
        pyautogui.write(text, interval=interval)

    def press(self, key: str) -> None:
        self._ensure_available()
        pyautogui.press(key)

    # ------------------------------------------------------------------
    def _ensure_available(self) -> None:
        if pyautogui is None:
            raise RuntimeError("PyAutoGUI is not installed. Install it to enable automation features.")
