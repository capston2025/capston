from __future__ import annotations

import time
from typing import List, Optional

import requests

from .models import DOMElement
from .exploration_ui_runtime import is_mcp_transport_error, recover_mcp_host


def analyze_dom(
    self,
    url: Optional[str] = None,
    scope_container_ref_id: Optional[str] = None,
) -> List[DOMElement]:
    """MCP Host를 통해 DOM 분석"""
    if not str(scope_container_ref_id or "").strip():
        self._active_scoped_container_ref = ""
    last_error: Optional[str] = None
    for attempt in range(1, 4):
        try:
            response = requests.post(
                f"{self.mcp_host_url}/execute",
                json={
                    "action": "browser_snapshot",
                    "params": {
                        "session_id": self.session_id,
                        "url": url or "",
                        "scope_container_ref_id": str(scope_container_ref_id or "").strip(),
                    },
                },
                timeout=30,
            )
            try:
                data = response.json()
            except Exception:
                data = {"error": response.text or "invalid_json_response"}

            if response.status_code >= 400:
                detail = data.get("detail") or data.get("error") or response.reason
                last_error = f"HTTP {response.status_code} - {detail}"
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
            if not raw_elements and attempt < 3:
                last_error = "empty_dom_elements"
                self._record_reason_code("dom_snapshot_retry")
                time.sleep(0.25 * attempt)
                continue

            # 셀렉터 맵 초기화
            self._element_selectors = {}
            self._element_full_selectors = {}
            self._element_ref_ids = {}
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
            self._last_snapshot_evidence = evidence

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
            return elements

        except Exception as e:
            last_error = str(e)
            if is_mcp_transport_error(last_error) and recover_mcp_host(self, context="goal_dom_snapshot"):
                if attempt < 3:
                    self._record_reason_code("dom_snapshot_retry")
                    time.sleep(0.25 * attempt)
                    continue
            if attempt < 3:
                self._record_reason_code("dom_snapshot_retry")
                time.sleep(0.25 * attempt)
                continue
            self._log(f"DOM 분석 실패: {e}")
            return []

    if last_error:
        self._log(f"DOM 분석 실패: {last_error}")
    return []
