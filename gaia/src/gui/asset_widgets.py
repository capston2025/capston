"""Shared GUI asset widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


GUI_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class GuiAssetLabel(QLabel):
    """QLabel that loads a bundled GUI asset and keeps it scaled on resize."""

    def __init__(
        self,
        asset_name: str,
        *,
        parent: QWidget | None = None,
        min_height: int = 120,
        max_height: int | None = None,
        fit: str = "contain",
    ) -> None:
        super().__init__(parent)
        self._source_pixmap = QPixmap(str(GUI_ASSETS_DIR / asset_name))
        self._fit = "cover" if fit == "cover" else "contain"
        self._preferred_height = max(1, int(max_height or min_height))
        self._last_render_size = QSize()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(min_height)
        if max_height is not None:
            self.setMaximumHeight(max_height)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        if self._source_pixmap.isNull():
            self.hide()

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(320, self._preferred_height)

    def minimumSizeHint(self) -> QSize:  # noqa: D401
        return QSize(160, self.minimumHeight())

    def resizeEvent(self, event) -> None:  # noqa: D401
        super().resizeEvent(event)
        self._refresh_pixmap()

    def showEvent(self, event) -> None:  # noqa: D401
        super().showEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._source_pixmap.isNull() or self.width() <= 1 or self.height() <= 1:
            return
        current_size = self.size()
        if current_size == self._last_render_size:
            return
        self._last_render_size = QSize(current_size)
        aspect = (
            Qt.AspectRatioMode.KeepAspectRatioByExpanding
            if self._fit == "cover"
            else Qt.AspectRatioMode.KeepAspectRatio
        )
        self.setPixmap(
            self._source_pixmap.scaled(
                self.size(),
                aspect,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


__all__ = ["GUI_ASSETS_DIR", "GuiAssetLabel"]
