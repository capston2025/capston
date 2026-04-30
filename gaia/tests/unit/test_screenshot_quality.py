from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from gaia.src.screenshot_quality import is_low_information_screenshot


def _png_base64(color: tuple[int, int, int], *, size: tuple[int, int] = (32, 32)) -> str:
    image = Image.new("RGB", size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def test_is_low_information_screenshot_flags_uniform_white_and_black() -> None:
    assert is_low_information_screenshot(_png_base64((255, 255, 255))) is True
    assert is_low_information_screenshot(_png_base64((0, 0, 0))) is True


def test_is_low_information_screenshot_keeps_normal_colored_capture() -> None:
    image = Image.new("RGB", (48, 48), (255, 255, 255))
    for x in range(12, 36):
        for y in range(12, 36):
            image.putpixel((x, y), (49, 130, 246))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode("utf-8")

    assert is_low_information_screenshot(payload) is False
