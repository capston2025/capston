"""Agent Builder workflow runner integration."""
from __future__ import annotations

import json
import os
from typing import Any, Dict

import requests


class AgentWorkflowRunner:
    """Invokes an OpenAI Agent Builder workflow and returns the parsed payload."""

    _WORKFLOW_BASE_URL = "https://api.openai.com/v1/workflows"

    def __init__(
        self,
        workflow_id: str | None = None,
        *,
        workflow_version: str | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.workflow_id = workflow_id or os.getenv("GAIA_WORKFLOW_ID")
        self.workflow_version = workflow_version or os.getenv("GAIA_WORKFLOW_VERSION", "1")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._session = session or requests.Session()

        if not self.workflow_id:
            raise RuntimeError("GAIA_WORKFLOW_ID 환경 변수가 필요합니다.")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY 환경 변수가 필요합니다.")

    # ------------------------------------------------------------------
    def run(self, document_text: str) -> Dict[str, Any]:
        """Execute the configured workflow with the supplied document text."""

        url = f"{self._WORKFLOW_BASE_URL}/runs"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "workflows=v1",
        }
        payload: Dict[str, Any] = {
            "workflow": {
                "id": self.workflow_id,
                "version": self.workflow_version,
            },
            "input": {
                "input_as_text": document_text,
            },
        }

        response = self._session.post(url, json=payload, headers=headers, timeout=45)
        response.raise_for_status()
        data = response.json()
        return self._extract_payload(data)

    # ------------------------------------------------------------------
    def _extract_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize the workflow response into a Python dict."""

        if not data:
            raise ValueError("Agent workflow 응답이 비어 있습니다.")

        # Agent Builder responses commonly place JSON in outputs -> items
        outputs = data.get("outputs")
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, dict):
                if first.get("content"):
                    # GPT Agents beta style: content list of dicts
                    for item in first["content"]:
                        if item.get("type") == "output_text":
                            return self._parse_json_blob(item.get("text", ""))
                if first.get("value"):
                    return self._parse_json_blob(first["value"])

        # Fallback: some workflows return top-level output_text or data
        if "output_text" in data:
            return self._parse_json_blob(data["output_text"])

        if "data" in data and isinstance(data["data"], list):
            for item in data["data"]:
                if isinstance(item, dict) and "output_text" in item:
                    return self._parse_json_blob(item["output_text"])

        raise ValueError("Agent workflow 응답에서 checklist 데이터를 찾을 수 없습니다.")

    # ------------------------------------------------------------------
    def _parse_json_blob(self, blob: Any) -> Dict[str, Any]:
        if isinstance(blob, dict):
            return blob
        if isinstance(blob, str):
            blob = blob.strip()
            if not blob:
                raise ValueError("Agent workflow 응답이 비어 있습니다.")
            try:
                return json.loads(blob)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise ValueError("Agent workflow 응답이 JSON 형식이 아닙니다.") from exc
        raise ValueError("Agent workflow 응답 형식을 해석할 수 없습니다.")
