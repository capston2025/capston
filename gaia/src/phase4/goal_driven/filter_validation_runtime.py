from __future__ import annotations
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
    preferred_control_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic semantic validation for filter-style goals."""
    merged_preferred_control_hint = _merge_control_hints(
        _contract_control_hint(validation_contract),
        preferred_control_hint,
    )
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
                preferred_control_hint=dict(merged_preferred_control_hint or {}),
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
    preferred_control_hint: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    scenario_control_hint = _goal_filter_control_hint(goal)
    merged_control_hint = _merge_control_hints(scenario_control_hint, preferred_control_hint)
    try:
        from .filter_validation_engine import _collect_option_cases, _pick_filter_control
    except Exception:
        _collect_option_cases = None
        _pick_filter_control = None

    picked_control = None
    if callable(_pick_filter_control):
        try:
            picked_control = _pick_filter_control(
                dom_elements,
                str(getattr(goal, "description", "") or ""),
                preferred_control_hint=merged_control_hint,
            )
        except Exception:
            picked_control = None
    contract_control_hint = build_filter_control_hint(agent, picked_control)

    if picked_control is not None and callable(_collect_option_cases):
        option_rows = list(_collect_option_cases(picked_control))
    else:
        option_rows = []

    if option_rows:
        best_select_options = list(option_rows)
        best_score = float(len(best_select_options))
    else:
        best_select_options: List[Dict[str, str]] = []
        best_score = -1.0
    preferred_hint = merged_control_hint
    for el in ([] if option_rows else dom_elements):
        if agent._normalize_text(el.tag) != "select":
            continue
        if not isinstance(el.options, list) or len(el.options) < 2:
            continue
        local_rows = list(_collect_option_cases(el)) if callable(_collect_option_cases) else []
        if not local_rows:
            continue
        score = float(len(local_rows))
        score += _preferred_control_match_score(agent, el, preferred_hint)
        if score > best_score:
            best_score = score
            best_select_options = local_rows

    option_rows = list(best_select_options)

    if not option_rows:
        return {
            "source": "fallback_empty",
            "required_options": [],
            "require_pagination_if_available": True,
            "control_ref_id": str(contract_control_hint.get("ref_id") or ""),
            "control_hint": dict(contract_control_hint or {}),
        }

    return {
        "source": "deterministic_control_options",
        "required_options": [
            {"value": str(row.get("value") or ""), "text": str(row.get("text") or "")}
            for row in option_rows
        ],
        "require_pagination_if_available": True,
        "control_ref_id": str(contract_control_hint.get("ref_id") or ""),
        "control_hint": dict(contract_control_hint or {}),
    }


def build_filter_control_hint(agent: "GoalDrivenAgent", element: Any) -> Dict[str, Any]:
    if element is None:
        return {}
    normalize = getattr(agent, "_normalize_text", None)
    if not callable(normalize):
        normalize = lambda value: str(value or "").strip().lower()
    tag = normalize(getattr(element, "tag", ""))
    role = normalize(getattr(element, "role", ""))
    if tag != "select" and role not in {"combobox", "listbox"}:
        return {}

    option_signature: List[str] = []
    for raw in list(getattr(element, "options", None) or []):
        if isinstance(raw, dict):
            token = str(raw.get("text") or raw.get("value") or "").strip()
        else:
            token = str(raw or "").strip()
        if token:
            option_signature.append(normalize(token))

    return {
        "ref_id": str(getattr(element, "ref_id", "") or "").strip(),
        "container_name": str(getattr(element, "container_name", "") or "").strip(),
        "context_text": str(getattr(element, "context_text", "") or "").strip(),
        "role_ref_name": str(getattr(element, "role_ref_name", "") or "").strip(),
        "selected_value": str(getattr(element, "selected_value", "") or "").strip(),
        "option_signature": option_signature[:16],
    }


def filter_validation_contract_needs_refresh(
    agent: "GoalDrivenAgent",
    contract: Optional[Dict[str, Any]],
    preferred_control_hint: Optional[Dict[str, Any]],
) -> bool:
    if not isinstance(contract, dict) or not isinstance(preferred_control_hint, dict):
        return False
    contract_hint = _contract_control_hint(contract)
    if not contract_hint:
        return False
    normalize = getattr(agent, "_normalize_text", None)
    if not callable(normalize):
        normalize = lambda value: str(value or "").strip().lower()

    contract_ref = str(contract_hint.get("ref_id") or "").strip()
    preferred_ref = str(preferred_control_hint.get("ref_id") or "").strip()
    if contract_ref and preferred_ref and contract_ref != preferred_ref:
        return True

    contract_signature = {
        normalize(token)
        for token in list(contract_hint.get("option_signature") or [])
        if str(token or "").strip()
    }
    preferred_signature = {
        normalize(token)
        for token in list(preferred_control_hint.get("option_signature") or [])
        if str(token or "").strip()
    }
    if contract_signature and preferred_signature and not contract_signature.intersection(preferred_signature):
        return True

    contract_container = normalize(contract_hint.get("container_name"))
    preferred_container = normalize(preferred_control_hint.get("container_name"))
    contract_role_ref = normalize(contract_hint.get("role_ref_name"))
    preferred_role_ref = normalize(preferred_control_hint.get("role_ref_name"))
    if contract_ref or preferred_ref:
        if (
            contract_container
            and preferred_container
            and contract_container != preferred_container
            and contract_role_ref
            and preferred_role_ref
            and contract_role_ref != preferred_role_ref
        ):
            return True
    return False


def _normalized_token(agent: "GoalDrivenAgent", value: Any) -> str:
    try:
        return agent._normalize_text(value)
    except Exception:
        return str(value or "").strip().lower()


def _goal_filter_control_hint(goal: TestGoal) -> Dict[str, Any]:
    test_data = getattr(goal, "test_data", None)
    if not isinstance(test_data, dict):
        return {}
    raw = test_data.get("filter_control_hint")
    return dict(raw) if isinstance(raw, dict) else {}


def _merge_control_hints(*hints: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for raw in hints:
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            if key in {"include_terms", "exclude_terms"} and isinstance(value, list):
                merged[key] = [str(item) for item in value if str(item or "").strip()]
            elif value not in (None, "", []):
                merged[key] = value
    return merged


def _contract_control_hint(contract: Any) -> Dict[str, Any]:
    if not isinstance(contract, dict):
        return {}
    raw = contract.get("control_hint")
    if isinstance(raw, dict):
        return dict(raw)
    control_ref = str(contract.get("control_ref_id") or "").strip()
    return {"ref_id": control_ref} if control_ref else {}


def _option_signature(agent: "GoalDrivenAgent", element: DOMElement) -> List[str]:
    signature: List[str] = []
    for raw in list(getattr(element, "options", None) or []):
        if isinstance(raw, dict):
            text = str(raw.get("text") or "").strip()
            value = str(raw.get("value") or "").strip()
        else:
            text = str(raw or "").strip()
            value = text
        token = _normalized_token(agent, text or value)
        if token:
            signature.append(token)
    return signature[:16]


def _preferred_control_match_score(
    agent: "GoalDrivenAgent",
    element: DOMElement,
    preferred_control_hint: Dict[str, Any],
) -> float:
    if not preferred_control_hint:
        return 0.0

    score = 0.0
    element_ref = str(getattr(element, "ref_id", "") or "").strip()
    if element_ref and element_ref == str(preferred_control_hint.get("ref_id") or "").strip():
        score += 200.0

    element_container = _normalized_token(agent, getattr(element, "container_name", ""))
    hint_container = _normalized_token(agent, preferred_control_hint.get("container_name"))
    if hint_container and element_container and hint_container == element_container:
        score += 16.0

    element_role_ref = _normalized_token(agent, getattr(element, "role_ref_name", ""))
    hint_role_ref = _normalized_token(agent, preferred_control_hint.get("role_ref_name"))
    if hint_role_ref and element_role_ref and hint_role_ref == element_role_ref:
        score += 14.0

    element_context = _normalized_token(agent, getattr(element, "context_text", ""))
    hint_context = _normalized_token(agent, preferred_control_hint.get("context_text"))
    if hint_context and element_context and hint_context == element_context:
        score += 12.0

    element_selected = _normalized_token(agent, getattr(element, "selected_value", ""))
    hint_selected = _normalized_token(agent, preferred_control_hint.get("selected_value"))
    if hint_selected and element_selected and hint_selected == element_selected:
        score += 10.0

    hint_signature = [
        _normalized_token(agent, token)
        for token in list(preferred_control_hint.get("option_signature") or [])
        if str(token or "").strip()
    ][:16]
    if hint_signature:
        element_signature = _option_signature(agent, element)
        if element_signature == hint_signature:
            score += 80.0
        elif element_signature:
            overlap = len(set(element_signature).intersection(set(hint_signature)))
            score += min(40.0, float(overlap) * 4.0)

    element_blob = _normalized_token(
        agent,
        " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "title", "") or ""),
                str(getattr(element, "class_name", "") or ""),
                str(getattr(element, "container_name", "") or ""),
                str(getattr(element, "context_text", "") or ""),
                " ".join(_option_signature(agent, element)[:8]),
            ]
        ),
    )
    include_terms = [
        _normalized_token(agent, token)
        for token in list(preferred_control_hint.get("include_terms") or [])
        if str(token or "").strip()
    ]
    exclude_terms = [
        _normalized_token(agent, token)
        for token in list(preferred_control_hint.get("exclude_terms") or [])
        if str(token or "").strip()
    ]
    for token in include_terms:
        if token and token in element_blob:
            score += 6.0
    for token in exclude_terms:
        if token and token in element_blob:
            score -= 10.0

    return score
