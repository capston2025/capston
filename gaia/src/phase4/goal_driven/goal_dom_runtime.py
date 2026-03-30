from __future__ import annotations

import time
from typing import List, Optional

from .models import DOMElement
from .exploration_ui_runtime import is_mcp_transport_error, recover_mcp_host
from gaia.src.phase4.mcp_transport_retry_runtime import execute_mcp_action_with_recovery


def analyze_dom(
    self,
    url: Optional[str] = None,
    scope_container_ref_id: Optional[str] = None,
) -> List[DOMElement]:
    """MCP Host를 통해 DOM 분석"""
    generation = int(getattr(self, "_dom_cache_generation", 0) or 0)
    requested_url = str(url or "").strip()
    requested_scope = str(scope_container_ref_id or "").strip()
    cache_key = (generation, requested_url, requested_scope)
    cache = getattr(self, "_dom_analyze_cache", None)
    if (
        isinstance(cache, dict)
        and tuple(cache.get("key") or ()) == cache_key
        and isinstance(cache.get("elements"), list)
    ):
        cached_elements = cache.get("elements") or []
        self._active_snapshot_id = str(cache.get("snapshot_id") or self._active_snapshot_id or "")
        self._active_dom_hash = str(cache.get("dom_hash") or self._active_dom_hash or "")
        self._active_snapshot_epoch = int(cache.get("epoch") or self._active_snapshot_epoch or 0)
        self._active_url = str(cache.get("active_url") or self._active_url or "")
        self._active_scoped_container_ref = str(cache.get("active_scope") or self._active_scoped_container_ref or "")
        self._last_context_snapshot = dict(cache.get("context_snapshot") or {})
        self._last_role_snapshot = dict(cache.get("role_snapshot") or {})
        self._last_snapshot_elements_by_ref = dict(cache.get("elements_by_ref") or {})
        self._last_snapshot_evidence = dict(cache.get("evidence") or {})
        self._last_container_source_summary = dict(cache.get("container_source_summary") or {})
        self._element_selectors = {}
        self._element_full_selectors = {}
        self._element_ref_ids = {}
        self._selector_to_ref_id = {}
        self._element_ref_meta_by_id = {}
        for element in cached_elements:
            element_id = getattr(element, "id", None)
            ref_id = str(getattr(element, "ref_id", "") or "").strip()
            if element_id is None or not ref_id:
                continue
            ref_meta = self._last_snapshot_elements_by_ref.get(ref_id)
            if isinstance(ref_meta, dict):
                self._element_ref_meta_by_id[int(element_id)] = dict(ref_meta)
                selector = str(ref_meta.get("selector") or "").strip()
                full_selector = str(ref_meta.get("full_selector") or "").strip()
                if selector:
                    self._element_selectors[int(element_id)] = selector
                    self._selector_to_ref_id.setdefault(selector, ref_id)
                if full_selector:
                    self._element_full_selectors[int(element_id)] = full_selector
                    self._selector_to_ref_id.setdefault(full_selector, ref_id)
            self._element_ref_ids[int(element_id)] = ref_id
        return list(cached_elements)
    if not str(scope_container_ref_id or "").strip():
        self._active_scoped_container_ref = ""
    last_error: Optional[str] = None
    for attempt in range(1, 4):
        try:
            dispatch = execute_mcp_action_with_recovery(
                raw_base_url=self.mcp_host_url,
                action="browser_snapshot",
                params={
                    "session_id": self.session_id,
                    "url": url or "",
                    "scope_container_ref_id": str(scope_container_ref_id or "").strip(),
                },
                timeout=30,
                attempts=2,
                is_transport_error=is_mcp_transport_error,
                recover_host=lambda *, context="": recover_mcp_host(self, context=context),
                context="goal_dom_snapshot",
            )
            data = dispatch.payload or {"error": dispatch.text or "invalid_json_response"}

            if dispatch.status_code >= 400:
                detail = data.get("detail") or data.get("error") or dispatch.text or "HTTP error"
                last_error = f"HTTP {dispatch.status_code} - {detail}"
                if attempt < 3:
                    self._record_reason_code("dom_snapshot_retry")
                    time.sleep(0.25 * attempt)
                    continue
                self._log(f"DOM 분석 오류: {last_error}")
                return []

            if "error" in data:
                last_error = str(data.get("error") or "snapshot_error")
                if attempt < 3:
                    self._record_reason_code("dom_snapshot_retry")
                    time.sleep(0.25 * attempt)
                    continue
                self._log(f"DOM 분석 오류: {last_error}")
                return []

            raw_elements = data.get("elements", []) or data.get("dom_elements", [])
            raw_elements_by_ref = (
                data.get("elements_by_ref") if isinstance(data.get("elements_by_ref"), dict) else {}
            )
            if not raw_elements and attempt < 3:
                last_error = "empty_dom_elements"
                self._record_reason_code("dom_snapshot_retry")
                time.sleep(0.25 * attempt)
                continue

            # 셀렉터 맵 초기화
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
            self._element_ref_meta_by_id = {}
            self._selector_to_ref_id = {}
            self._element_scopes = {}
            self._active_snapshot_id = str(data.get("snapshot_id") or "")
            self._active_dom_hash = str(data.get("dom_hash") or "")
            self._active_snapshot_epoch = int(data.get("epoch") or 0)
            self._active_url = str(data.get("url") or self._active_url or "")
            self._active_scoped_container_ref = str(data.get("scope_container_ref_id") or "").strip()
            self._last_context_snapshot = (
                data.get("context_snapshot") if isinstance(data.get("context_snapshot"), dict) else {}
            )
            self._last_role_snapshot = (
                data.get("role_snapshot") if isinstance(data.get("role_snapshot"), dict) else {}
            )
            evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
            self._last_snapshot_elements_by_ref = dict(raw_elements_by_ref or {})
            self._last_snapshot_evidence = evidence

            if isinstance(raw_elements_by_ref, dict):
                for rid, meta in raw_elements_by_ref.items():
                    ref_key = str(rid or "").strip()
                    if not ref_key or not isinstance(meta, dict):
                        continue
                    selector = str(meta.get("selector") or "").strip()
                    full_selector = str(meta.get("full_selector") or "").strip()
                    if selector:
                        self._selector_to_ref_id.setdefault(selector, ref_key)
                    if full_selector:
                        self._selector_to_ref_id.setdefault(full_selector, ref_key)

            # DOMElement로 변환 (ID 부여)
            elements = []
            for idx, el in enumerate(raw_elements):
                attrs = el.get("attributes", {})
                disabled_attr = attrs.get("disabled")
                disabled_flag = (
                    disabled_attr is not None
                    and str(disabled_attr).strip().lower() not in {"false", "0", "none"}
                )
                aria_disabled_flag = str(attrs.get("aria-disabled") or "").strip().lower() == "true"
                gaia_disabled_flag = str(attrs.get("gaia-disabled") or "").strip().lower() == "true"
                is_enabled = not (disabled_flag or aria_disabled_flag or gaia_disabled_flag)

                selector = el.get("selector", "")
                full_selector = el.get("full_selector") or selector
                ref_id = el.get("ref_id", "")
                scope = el.get("scope")
                if selector:
                    self._element_selectors[idx] = selector
                if full_selector:
                    self._element_full_selectors[idx] = full_selector
                if isinstance(ref_id, str) and ref_id:
                    self._element_ref_ids[idx] = ref_id
                    if selector:
                        self._selector_to_ref_id[selector] = ref_id
                    if full_selector:
                        self._selector_to_ref_id[full_selector] = ref_id
                    ref_meta = raw_elements_by_ref.get(ref_id)
                    if isinstance(ref_meta, dict):
                        self._element_ref_meta_by_id[idx] = dict(ref_meta)
                if isinstance(scope, dict):
                    self._element_scopes[idx] = scope

                elements.append(
                    DOMElement(
                        id=idx,
                        tag=el.get("tag", ""),
                        text=el.get("text", "")[:100],
                        role=attrs.get("role"),
                        type=attrs.get("type"),
                        placeholder=attrs.get("placeholder"),
                        aria_label=attrs.get("aria-label"),
                        aria_modal=attrs.get("aria-modal"),
                        title=attrs.get("title"),
                        class_name=attrs.get("class"),
                        href=attrs.get("href"),
                        bounding_box=el.get("bounding_box"),
                        options=attrs.get("options"),
                        selected_value=str(attrs.get("selected_value") or ""),
                        container_name=attrs.get("container_name"),
                        container_role=attrs.get("container_role"),
                        container_ref_id=attrs.get("container_ref_id") or attrs.get("container_dom_ref"),
                        container_source=attrs.get("container_source"),
                        context_text=attrs.get("context_text"),
                        group_action_labels=attrs.get("group_action_labels"),
                        role_ref_role=attrs.get("role_ref_role"),
                        role_ref_name=attrs.get("role_ref_name"),
                        role_ref_nth=attrs.get("role_ref_nth"),
                        context_score_hint=attrs.get("context_score_hint"),
                        ref_id=ref_id if isinstance(ref_id, str) and ref_id else None,
                        is_visible=bool(el.get("is_visible", True)),
                        is_enabled=is_enabled,
                    )
                )
            source_summary: dict[str, int] = {}
            for item in elements:
                source = str(getattr(item, "container_source", None) or "").strip()
                if not source:
                    continue
                source_summary[source] = int(source_summary.get(source, 0)) + 1
            self._last_container_source_summary = source_summary
            self._dom_analyze_cache = {
                "key": cache_key,
                "elements": list(elements),
                "snapshot_id": self._active_snapshot_id,
                "dom_hash": self._active_dom_hash,
                "epoch": self._active_snapshot_epoch,
                "active_url": self._active_url,
                "active_scope": self._active_scoped_container_ref,
                "context_snapshot": dict(self._last_context_snapshot or {}),
                "role_snapshot": dict(self._last_role_snapshot or {}),
                "elements_by_ref": dict(self._last_snapshot_elements_by_ref or {}),
                "evidence": dict(self._last_snapshot_evidence or {}),
                "container_source_summary": dict(source_summary or {}),
            }
            return elements

        except Exception as e:
            last_error = str(e)
            if attempt < 3:
                self._record_reason_code("dom_snapshot_retry")
                time.sleep(0.25 * attempt)
                continue
            self._log(f"DOM 분석 실패: {e}")
            return []

    if last_error:
        self._log(f"DOM 분석 실패: {last_error}")
    return []
