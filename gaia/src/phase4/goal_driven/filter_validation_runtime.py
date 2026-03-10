from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .models import DOMElement, TestGoal

if TYPE_CHECKING:
    from .agent import GoalDrivenAgent


class GoalFilterValidationAdapter:
    """Filter validation adapter for GoalDrivenAgent."""

    def __init__(self, agent: "GoalDrivenAgent"):
        self.agent = agent

    def analyze_dom(self) -> List[DOMElement]:
        return self.agent._analyze_dom()

    def apply_select(self, element_id: int, value: str) -> Dict[str, Any]:
        selector = self.agent._element_selectors.get(element_id)
        full_selector = self.agent._element_full_selectors.get(element_id) or selector
        ref_id = self.agent._element_ref_ids.get(element_id)
        exec_result = self.agent._execute_action(
            "select",
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=value,
        )
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def click_element(self, element_id: int) -> Dict[str, Any]:
        selector = self.agent._element_selectors.get(element_id)
        full_selector = self.agent._element_full_selectors.get(element_id) or selector
        ref_id = self.agent._element_ref_ids.get(element_id)
        before_url = str(self.agent._active_url or "")
        exec_result = self.agent._execute_action(
            "click",
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value=None,
        )
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
            "before_url": before_url,
            "after_url": str(self.agent._active_url or ""),
        }

    def scroll_for_pagination(self, anchor_element_id: int) -> Dict[str, Any]:
        selector = self.agent._element_selectors.get(anchor_element_id)
        full_selector = self.agent._element_full_selectors.get(anchor_element_id) or selector
        ref_id = self.agent._element_ref_ids.get(anchor_element_id)
        exec_result = self.agent._execute_action(
            "scroll",
            selector=selector,
            full_selector=full_selector,
            ref_id=ref_id,
            value="bottom",
        )
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def wait_for_pagination_probe(self, wait_ms: int = 900) -> Dict[str, Any]:
        exec_result = self.agent._execute_action("wait", value={"timeMs": int(max(100, wait_ms))})
        self.agent._last_exec_result = exec_result
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def reload_page(self, wait_ms: int = 900) -> Dict[str, Any]:
        current_url = str(self.agent._active_url or "")
        exec_result = self.agent._execute_action("goto", url=current_url)
        self.agent._last_exec_result = exec_result
        if wait_ms > 0:
            try:
                self.agent._execute_action("wait", value={"timeMs": int(max(100, wait_ms))})
            except Exception:
                pass
        return {
            "success": bool(exec_result.success),
            "effective": bool(exec_result.effective),
            "reason_code": str(exec_result.reason_code or ""),
            "reason": str(exec_result.reason or ""),
            "state_change": dict(exec_result.state_change or {}),
        }

    def resolve_ref(self, element_id: int) -> str:
        return str(self.agent._element_ref_ids.get(element_id) or "")

    def current_url(self) -> str:
        return str(self.agent._active_url or "")

    def record_reason(self, code: str) -> None:
        self.agent._record_reason_code(code)

    def log(self, message: str) -> None:
        self.agent._log(message)

    def capture_case_attachment(self, label: str) -> Optional[Dict[str, Any]]:
        shot = self.agent._capture_screenshot()
        if not isinstance(shot, str) or not shot.strip():
            return None
        return {
            "kind": "image_base64",
            "mime": "image/png",
            "data": shot,
            "label": str(label or "").strip(),
        }


def run_filter_semantic_validation(
    agent: "GoalDrivenAgent",
    goal_text: str,
    *,
    max_pages: int = 2,
    max_cases: int = 3,
    use_current_selection_only: bool = False,
    forced_selected_value: Optional[str] = None,
    validation_contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic semantic validation for filter-style goals."""
    try:
        from .filter_validation_engine import build_filter_validation_config, run_filter_validation

        adapter = GoalFilterValidationAdapter(agent)
        report = run_filter_validation(
            adapter=adapter,
            goal_text=goal_text,
            config=build_filter_validation_config(
                max_pages=max(1, int(max_pages)),
                max_cases=max(1, int(max_cases)),
                use_current_selection_only=bool(use_current_selection_only),
                forced_selected_value=str(forced_selected_value or "").strip(),
                validation_contract=dict(validation_contract or {}),
            ),
        )
        if isinstance(report, dict):
            return report
    except Exception as exc:
        agent._log(f"⚠️ semantic filter validation 실패: {exc}")
    return {
        "mode": "filter_semantic_v2",
        "success": False,
        "summary": {
            "goal_type": "filter_validation_semantic",
            "total_checks": 1,
            "passed_checks": 0,
            "failed_checks": 1,
            "skipped_checks": 0,
            "failed_mandatory_checks": 1,
            "success_rate": 0.0,
            "strict_failed": True,
        },
        "checks": [
            {
                "check_id": "filter_engine_error",
                "name": "필터 의미 검증 엔진 실행",
                "status": "fail",
                "step": 1,
                "action": "verify",
                "input_value": "-",
                "error": "semantic filter validation failed",
                "check_type": "engine_error",
                "mandatory": True,
                "scope": "global",
                "expected": "엔진 정상 실행",
                "observed": "실패",
                "evidence": {},
            }
        ],
        "rules_used": [],
        "pages_checked": 1,
        "cases": [],
        "failed_mandatory_count": 1,
        "reason_code_summary": {"filter_case_failed": 1},
    }


def build_filter_validation_contract(
    agent: "GoalDrivenAgent",
    *,
    goal: TestGoal,
    dom_elements: List[DOMElement],
) -> Dict[str, Any]:
    option_rows: List[Dict[str, Any]] = []
    best_select_options: List[Dict[str, str]] = []
    best_score = -1.0
    for el in dom_elements:
        if agent._normalize_text(el.tag) != "select":
            continue
        if not isinstance(el.options, list) or len(el.options) < 2:
            continue
        local_rows: List[Dict[str, str]] = []
        for item in el.options:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            text = str(item.get("text") or "").strip()
            if not value:
                continue
            lowered = agent._normalize_text(f"{value} {text}")
            if any(tok in lowered for tok in ("전체", "all", "선택", "default")):
                continue
            local_rows.append({"value": value, "text": text})
        if not local_rows:
            continue
        blob = agent._normalize_text(
            " ".join(
                [
                    str(el.text or ""),
                    str(el.aria_label or ""),
                    str(el.title or ""),
                    str(el.class_name or ""),
                    " ".join(str(x.get("text") or "") for x in local_rows[:8]),
                ]
            )
        )
        score = float(len(local_rows))
        if "학점" in blob or "credit" in blob:
            score += 100.0
        if score > best_score:
            best_score = score
            best_select_options = local_rows

    option_rows = list(best_select_options)

    if not option_rows:
        return {
            "source": "fallback_empty",
            "required_options": [],
            "require_pagination_if_available": True,
        }

    prompt = f"""당신은 테스트 목표를 검증 계약(JSON)으로 변환하는 엔진입니다.
아래 목표를 보고, 검증해야 할 필터 옵션만 deterministic JSON으로 반환하세요.

목표:
{goal.description}

사용 가능한 옵션 목록(JSON):
{json.dumps(option_rows, ensure_ascii=False)}

반드시 다음 스키마만 반환:
{{
  "required_options": [{{"value":"...", "text":"..."}}],
  "require_pagination_if_available": true
}}

규칙:
1) required_options는 반드시 위 옵션 목록에 있는 값만 사용
2) 목표가 특정 옵션 집합(예: 1,2,3학점)을 요구하면 그 집합만 포함
3) 목표가 '전체/전부/모두/자세히 검증'이면 가능한 옵션을 모두 포함
4) 설명 문장/마크다운 없이 JSON만 반환
"""

    try:
        raw = agent._call_llm_text_only(prompt)
        text = str(raw or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if not text.startswith("{"):
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last != -1 and last > first:
                text = text[first : last + 1].strip()
        data = json.loads(text)
        if isinstance(data, dict):
            raw_required = data.get("required_options")
            sanitized: List[Dict[str, str]] = []
            value_allow = {
                str(row.get("value") or "").strip(): str(row.get("text") or "").strip()
                for row in option_rows
            }
            if isinstance(raw_required, list):
                for item in raw_required:
                    if not isinstance(item, dict):
                        continue
                    val = str(item.get("value") or "").strip()
                    txt = str(item.get("text") or "").strip()
                    if val in value_allow:
                        sanitized.append({"value": val, "text": value_allow.get(val) or txt})
            if not sanitized:
                sanitized = [
                    {"value": str(row.get("value") or ""), "text": str(row.get("text") or "")}
                    for row in option_rows
                ]
            return {
                "source": "llm_contract",
                "required_options": sanitized,
                "require_pagination_if_available": bool(
                    data.get("require_pagination_if_available", True)
                ),
            }
    except Exception as exc:
        agent._log(f"⚠️ LLM 계약 파싱 실패, fallback 사용: {exc}")

    return {
        "source": "fallback_all_options",
        "required_options": [
            {"value": str(row.get("value") or ""), "text": str(row.get("text") or "")}
            for row in option_rows
        ],
        "require_pagination_if_available": True,
    }
