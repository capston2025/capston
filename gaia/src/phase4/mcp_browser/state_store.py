"""Browser state/env helpers for MCP Host."""
from __future__ import annotations

from typing import Any, Dict


class BrowserStateStore:
    @staticmethod
    async def get_state(page: Any) -> Dict[str, Any]:
        cookies = await page.context.cookies()
        storage = await page.evaluate(
            """
            () => {
              const local = {};
              const session = {};
              for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                local[k] = localStorage.getItem(k);
              }
              for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                session[k] = sessionStorage.getItem(k);
              }
              return { local_storage: local, session_storage: session };
            }
            """
        )
        return {
            "cookies": cookies,
            "local_storage": storage.get("local_storage", {}),
            "session_storage": storage.get("session_storage", {}),
            "url": page.url,
        }

    @staticmethod
    async def set_state(page: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        cookies = payload.get("cookies") or []
        if cookies:
            await page.context.add_cookies(cookies)
        local_storage = payload.get("local_storage") or {}
        session_storage = payload.get("session_storage") or {}
        await page.evaluate(
            """
            ({ local_storage, session_storage }) => {
              for (const [k, v] of Object.entries(local_storage || {})) {
                localStorage.setItem(k, String(v));
              }
              for (const [k, v] of Object.entries(session_storage || {})) {
                sessionStorage.setItem(k, String(v));
              }
              return true;
            }
            """,
            {"local_storage": local_storage, "session_storage": session_storage},
        )
        return {"cookies_set": len(cookies), "local_keys": len(local_storage), "session_keys": len(session_storage)}

    @staticmethod
    async def clear_state(page: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        clear_cookies = bool(payload.get("cookies", True))
        clear_local = bool(payload.get("local_storage", True))
        clear_session = bool(payload.get("session_storage", True))
        if clear_cookies:
            await page.context.clear_cookies()
        await page.evaluate(
            """
            ({ clear_local, clear_session }) => {
              if (clear_local) localStorage.clear();
              if (clear_session) sessionStorage.clear();
              return true;
            }
            """,
            {"clear_local": clear_local, "clear_session": clear_session},
        )
        return {
            "cookies_cleared": clear_cookies,
            "local_storage_cleared": clear_local,
            "session_storage_cleared": clear_session,
        }

    @staticmethod
    async def apply_env(page: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = page.context
        applied: Dict[str, Any] = {}
        warnings: list[str] = []

        if "offline" in payload:
            offline = bool(payload.get("offline"))
            await context.set_offline(offline)
            applied["offline"] = offline

        headers = payload.get("headers")
        if isinstance(headers, dict):
            await context.set_extra_http_headers({str(k): str(v) for k, v in headers.items()})
            applied["headers"] = headers

        geo = payload.get("geolocation")
        if isinstance(geo, dict) and "latitude" in geo and "longitude" in geo:
            await context.grant_permissions(["geolocation"])
            await context.set_geolocation(
                {
                    "latitude": float(geo["latitude"]),
                    "longitude": float(geo["longitude"]),
                    "accuracy": float(geo.get("accuracy", 10)),
                }
            )
            applied["geolocation"] = geo

        viewport = payload.get("viewport")
        if isinstance(viewport, dict) and "width" in viewport and "height" in viewport:
            await page.set_viewport_size({"width": int(viewport["width"]), "height": int(viewport["height"])})
            applied["viewport"] = {"width": int(viewport["width"]), "height": int(viewport["height"])}

        media = payload.get("media")
        if isinstance(media, dict):
            await page.emulate_media(
                media=media.get("media"),
                color_scheme=media.get("color_scheme"),
                reduced_motion=media.get("reduced_motion"),
                forced_colors=media.get("forced_colors"),
            )
            applied["media"] = media

        for key in ("auth", "timezone", "locale", "device"):
            if key in payload:
                applied[key] = payload.get(key)
                warnings.append(f"{key} will fully apply on next context recreation")

        return {"applied": applied, "warnings": warnings}

