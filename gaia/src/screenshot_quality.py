from __future__ import annotations

import base64
from io import BytesIO

try:
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover
    Image = None
    ImageStat = None


def _normalize_base64_image(payload: str) -> str:
    text = str(payload or "").strip()
    if not text:
        return ""
    if "," in text and text.lower().startswith("data:image"):
        return text.split(",", 1)[1].strip()
    return text


def is_low_information_screenshot(
    screenshot_base64: str,
    *,
    dark_mean_threshold: float = 8.0,
    light_mean_threshold: float = 247.0,
    spread_threshold: float = 6.0,
    stddev_threshold: float = 3.0,
) -> bool:
    normalized = _normalize_base64_image(screenshot_base64)
    if not normalized or Image is None or ImageStat is None:
        return False
    try:
        image_bytes = base64.b64decode(normalized)
        with Image.open(BytesIO(image_bytes)) as image:
            sample = image.convert("RGB")
            if max(sample.size) > 128:
                sample.thumbnail((128, 128))
            stat = ImageStat.Stat(sample)
            means = [float(value) for value in stat.mean[:3]]
            extrema = stat.extrema[:3]
            stddev = [float(value) for value in stat.stddev[:3]]
    except Exception:
        return False

    if not means or not extrema:
        return False

    avg_mean = sum(means) / len(means)
    max_spread = max(float(high) - float(low) for low, high in extrema)
    max_stddev = max(stddev or [0.0])

    uniformly_dark = avg_mean <= dark_mean_threshold
    uniformly_light = avg_mean >= light_mean_threshold
    visually_flat = max_spread <= spread_threshold and max_stddev <= stddev_threshold
    return visually_flat and (uniformly_dark or uniformly_light)


__all__ = ["is_low_information_screenshot"]
