from __future__ import annotations

from typing import Any, Dict


async def trusted_click_point(
    page: Any,
    x: float,
    y: float,
    *,
    delay_ms: int = 30,
    move_first: bool = True,
    clamp_to_viewport: bool = True,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "input": "playwright_mouse",
        "x": float(x),
        "y": float(y),
        "clicked": False,
    }

    try:
        vw = None
        vh = None
        try:
            viewport = await page.evaluate(
                "() => ({ w: window.innerWidth || 0, h: window.innerHeight || 0 })"
            )
            if isinstance(viewport, dict):
                vw = int(viewport.get("w") or 0)
                vh = int(viewport.get("h") or 0)
        except Exception:
            vw = None
            vh = None

        cx = float(x)
        cy = float(y)
        if clamp_to_viewport and vw and vh:
            cx = max(1.0, min(float(vw - 1), cx))
            cy = max(1.0, min(float(vh - 1), cy))
            meta["x"] = cx
            meta["y"] = cy

        if move_first:
            await page.mouse.move(cx, cy)
        await page.mouse.click(cx, cy, delay=max(0, int(delay_ms)))
        meta["clicked"] = True
        return meta
    except Exception as exc:
        meta["error"] = str(exc)
        return meta

