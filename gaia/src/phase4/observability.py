"""Per-session observability utilities for browser telemetry."""
from __future__ import annotations

import asyncio
import fnmatch
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


class RingBuffer:
    def __init__(self, maxlen: int = 500) -> None:
        self._buf: Deque[Dict[str, Any]] = deque(maxlen=maxlen)

    def add(self, item: Dict[str, Any]) -> None:
        self._buf.append(item)

    def clear(self) -> None:
        self._buf.clear()

    def list(self, limit: int = 100) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        return list(self._buf)[-limit:]


class SessionObservability:
    def __init__(self, *, maxlen: int = 800) -> None:
        self.console = RingBuffer(maxlen=maxlen)
        self.errors = RingBuffer(maxlen=maxlen)
        self.requests = RingBuffer(maxlen=maxlen)
        self.dialogs = RingBuffer(maxlen=maxlen)
        self.downloads = RingBuffer(maxlen=maxlen)
        self._attached_page = None
        self._request_seq = 0
        self._request_ids: Dict[Any, str] = {}
        self._responses: Dict[str, Any] = {}
        self._response_bodies: Dict[str, Dict[str, Any]] = {}

    def _next_request_id(self) -> str:
        self._request_seq += 1
        return f"req_{self._request_seq}"

    def attach_page(self, page: Any) -> None:
        if page is None or self._attached_page is page:
            return
        self._attached_page = page
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_console(self, msg: Any) -> None:
        try:
            self.console.add(
                {
                    "ts": int(time.time() * 1000),
                    "type": str(msg.type),
                    "text": str(msg.text),
                }
            )
        except Exception:
            return

    def _on_page_error(self, exc: Any) -> None:
        self.errors.add(
            {
                "ts": int(time.time() * 1000),
                "type": "pageerror",
                "text": str(exc),
            }
        )

    def _on_request(self, request: Any) -> None:
        req_id = self._next_request_id()
        self._request_ids[request] = req_id
        self.requests.add(
            {
                "request_id": req_id,
                "ts": int(time.time() * 1000),
                "stage": "request",
                "method": str(request.method),
                "url": str(request.url),
                "status": None,
                "resource_type": str(request.resource_type),
            }
        )

    def _on_response(self, response: Any) -> None:
        request = response.request
        req_id = self._request_ids.get(request) or self._next_request_id()
        self._request_ids[request] = req_id
        self._responses[req_id] = response
        self.requests.add(
            {
                "request_id": req_id,
                "ts": int(time.time() * 1000),
                "stage": "response",
                "method": str(request.method),
                "url": str(response.url),
                "status": int(response.status),
                "resource_type": str(request.resource_type),
            }
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._capture_body(req_id, response))
        except Exception:
            return

    async def _capture_body(self, req_id: str, response: Any) -> None:
        if req_id in self._response_bodies:
            return
        try:
            text = await response.text()
            content_type = str(response.headers.get("content-type", ""))
            self._response_bodies[req_id] = {
                "request_id": req_id,
                "content_type": content_type,
                "truncated": len(text) > 200_000,
                "text": text[:200_000],
            }
        except Exception as exc:
            self._response_bodies[req_id] = {
                "request_id": req_id,
                "error": str(exc),
            }

    def add_dialog_event(self, payload: Dict[str, Any]) -> None:
        row = {"ts": int(time.time() * 1000)}
        row.update(payload)
        self.dialogs.add(row)

    def add_download_event(self, payload: Dict[str, Any]) -> None:
        row = {"ts": int(time.time() * 1000)}
        row.update(payload)
        self.downloads.add(row)

    def get_console(self, *, limit: int = 100, level: str = "") -> List[Dict[str, Any]]:
        rows = self.console.list(limit=limit)
        lv = (level or "").strip().lower()
        if not lv:
            return rows
        return [row for row in rows if str(row.get("type", "")).lower() == lv]

    def get_errors(self, *, limit: int = 100) -> List[Dict[str, Any]]:
        return self.errors.list(limit=limit)

    def clear_requests(self) -> None:
        self.requests.clear()
        self._request_ids.clear()
        self._responses.clear()
        self._response_bodies.clear()

    def get_requests(
        self,
        *,
        limit: int = 100,
        url_contains: str = "",
        pattern: str = "",
        method: str = "",
        resource_type: str = "",
        status: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        rows = self.requests.list(limit=limit * 3)
        needle = (url_contains or "").strip().lower()
        glob_pattern = (pattern or "").strip()
        method_filter = (method or "").strip().lower()
        resource_filter = (resource_type or "").strip().lower()
        out: List[Dict[str, Any]] = []
        for row in rows:
            row_url = str(row.get("url", ""))
            if needle and needle not in row_url.lower():
                continue
            if glob_pattern and not fnmatch.fnmatch(row_url, glob_pattern):
                continue
            if method_filter and str(row.get("method", "")).lower() != method_filter:
                continue
            if resource_filter and str(row.get("resource_type", "")).lower() != resource_filter:
                continue
            if status is not None and row.get("status") != status:
                continue
            out.append(row)
        return out[-limit:]

    async def get_response_body(
        self,
        *,
        request_id: str = "",
        url: str = "",
        url_contains: str = "",
        pattern: str = "",
        method: str = "",
        max_chars: int = 200_000,
    ) -> Dict[str, Any]:
        rid = (request_id or "").strip()
        max_chars = max(1, int(max_chars or 200_000))
        if not rid and url:
            needle = url.strip()
            for row in reversed(self.requests.list(limit=500)):
                if row.get("url") == needle and row.get("request_id"):
                    rid = str(row["request_id"])
                    break
        if not rid and (url_contains or pattern or method):
            needle = (url_contains or "").strip().lower()
            glob_pattern = (pattern or "").strip()
            method_filter = (method or "").strip().lower()
            for row in reversed(self.requests.list(limit=500)):
                row_url = str(row.get("url", ""))
                if needle and needle not in row_url.lower():
                    continue
                if glob_pattern and not fnmatch.fnmatch(row_url, glob_pattern):
                    continue
                if method_filter and str(row.get("method", "")).lower() != method_filter:
                    continue
                if row.get("request_id"):
                    rid = str(row["request_id"])
                    break

        if not rid:
            return {
                "success": False,
                "reason_code": "not_found",
                "reason": "request_id or url/url_contains/pattern is required",
            }

        def _trim_body(raw: Dict[str, Any]) -> Dict[str, Any]:
            body = dict(raw)
            text = body.get("text")
            if isinstance(text, str) and len(text) > max_chars:
                body["text"] = text[:max_chars]
                body["truncated"] = True
            return body

        body = self._response_bodies.get(rid)
        if body:
            return {"success": True, "reason_code": "ok", "body": _trim_body(body)}

        response = self._responses.get(rid)
        if response is None:
            return {"success": False, "reason_code": "not_found", "reason": f"response not found: {rid}"}
        await self._capture_body(rid, response)
        body = self._response_bodies.get(rid)
        if body is None:
            return {"success": False, "reason_code": "not_found", "reason": f"response body not found: {rid}"}
        return {"success": True, "reason_code": "ok", "body": _trim_body(body)}
