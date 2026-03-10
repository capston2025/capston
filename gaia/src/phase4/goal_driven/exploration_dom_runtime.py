from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from .exploratory_models import ElementState, PageState, TestableAction


def evaluate_selector(self, selector: str, script: str) -> Optional[str]:
    wrapped_fn = (
        "(() => {"
        f"const __selector = {json.dumps(selector)};"
        f"const __fnSource = {json.dumps(script)};"
        "const __el = document.querySelector(__selector);"
        "if (!__el) return null;"
        "try {"
        "  const __fn = eval('(' + __fnSource + ')');"
        "  return __fn(__el);"
        "} catch (_e) {"
        "  return null;"
        "}"
        "})()"
    )
    params: Dict[str, object] = {
        "session_id": self.session_id,
        "action": "evaluate",
        "url": "",
        "fn": wrapped_fn,
    }
    try:
        response = None
        last_exc: Optional[Exception] = None
        request_timeout = max(10.0, min(float(self.config.action_timeout), 30.0))
        for attempt in range(2):
            try:
                response = requests.post(
                    f"{self.mcp_host_url}/execute",
                    json={"action": "browser_act", "params": params},
                    timeout=(5, request_timeout),
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if (
                    attempt == 0
                    and self._is_mcp_transport_error(str(exc))
                    and self._recover_mcp_host(context="evaluate_selector")
                ):
                    continue
                raise
        if response is None and last_exc is not None:
            raise last_exc
        data = response.json()
        if not data.get("success"):
            return None
        result = data.get("result")
        return str(result) if result is not None else None
    except Exception:
        return None


def get_select_state(self, selector: Optional[str]) -> Optional[dict]:
    if not selector:
        return None
    result = evaluate_selector(
        self,
        selector,
        """
        el => JSON.stringify({
            value: el.value ?? '',
            text: (el.selectedOptions && el.selectedOptions[0]
                ? el.selectedOptions[0].textContent
                : '')
        })
        """,
    )
    if not result:
        return None
    try:
        return json.loads(result)
    except Exception:
        return None


def pick_select_option(self, element_state: Optional[Any]) -> str:
    if element_state is None:
        return "1"
    opts = getattr(element_state, "options", None)
    if not opts or not isinstance(opts, list):
        return "1"
    real_opts = [
        o
        for o in opts
        if isinstance(o, dict)
        and str(o.get("value", "")).strip()
        and str(o.get("value", "")).strip() != "__truncated__"
    ]
    if not real_opts:
        return "1"
    selected_val = getattr(element_state, "text", "") or ""
    for opt in real_opts:
        if str(opt.get("text", "")).strip() != selected_val.strip():
            return str(opt["value"])
    return str(real_opts[0]["value"])


def get_toggle_state(self, selector: Optional[str]) -> Optional[dict]:
    if not selector:
        return None
    result = evaluate_selector(
        self,
        selector,
        """
        el => JSON.stringify({
            checked: typeof el.checked === 'boolean' ? el.checked : null,
            pressed: (el.getAttribute && el.getAttribute('aria-pressed'))
                ? el.getAttribute('aria-pressed') === 'true'
                : null,
            selected: (el.getAttribute && el.getAttribute('aria-selected'))
                ? el.getAttribute('aria-selected') === 'true'
                : null,
            expanded: (el.getAttribute && el.getAttribute('aria-expanded'))
                ? el.getAttribute('aria-expanded') === 'true'
                : null
        })
        """,
    )
    if not result:
        return None
    try:
        return json.loads(result)
    except Exception:
        return None


def build_element_id(
    self,
    url_hash: str,
    element,
    selector: str,
) -> str:
    if selector:
        return f"{url_hash}:{selector}"
    parts = [
        element.tag,
        element.type or "",
        element.placeholder or "",
        element.aria_label or "",
        element.text[:30] if element.text else "",
    ]
    filtered = [part for part in parts if part]
    if not filtered:
        return f"{url_hash}:{element.tag}"
    return f"{url_hash}:" + ":".join(filtered)


def find_selector_by_element_id(
    self,
    element_id: str,
    page_state: PageState,
) -> Optional[str]:
    element = find_element_by_id(self, element_id, page_state)
    if not element:
        return None
    selector = element.selector
    if selector and is_selector_safe(self, selector):
        return selector
    fallback = fallback_selector_for_element(self, element, page_state)
    return fallback or selector


def find_element_by_id(
    self,
    element_id: str,
    page_state: PageState,
) -> Optional[ElementState]:
    for element in page_state.interactive_elements:
        if element.element_id == element_id:
            return element
    return None


def is_selector_safe(self, selector: str) -> bool:
    if not selector:
        return False
    if selector.startswith("role=") or selector.startswith("text="):
        return True
    if "[" in selector or "]" in selector:
        return False
    parts = selector.split(".")
    for part in parts[1:]:
        segment = part.split(" ")[0].split(">")[0]
        if ":" in segment:
            return False
    return True


def fallback_selector_for_element(
    self,
    element: ElementState,
    page_state: PageState,
) -> Optional[str]:
    label = self._element_label(element)
    if element.tag == "select":
        select_index = 0
        for candidate in page_state.interactive_elements:
            if candidate.tag == "select":
                if candidate.element_id == element.element_id:
                    return f"select >> nth={select_index}"
                select_index += 1
        return "select"

    if element.tag == "input":
        if element.placeholder:
            return f'input[placeholder="{element.placeholder}"]'
        if element.aria_label:
            return f'input[aria-label="{element.aria_label}"]'
        if element.type:
            input_index = 0
            for candidate in page_state.interactive_elements:
                if candidate.tag == "input" and candidate.type == element.type:
                    if candidate.element_id == element.element_id:
                        return f'input[type="{element.type}"] >> nth={input_index}'
                    input_index += 1

    if element.aria_label:
        return f'[aria-label="{element.aria_label}"]'
    if element.role:
        if label:
            return f'role={element.role}[name="{label}"]'
        return f"role={element.role}"
    if label and len(label) <= 40:
        return f'text="{label}"'
    return None


def determine_input_value(
    self,
    action: TestableAction,
    input_values: Dict[str, str],
) -> str:
    desc_lower = action.description.lower()

    if "saucedemo.com" in (self._current_url or ""):
        if "password" in desc_lower or "비밀번호" in desc_lower:
            return "secret_sauce"
        if "username" in desc_lower or "사용자" in desc_lower:
            return "standard_user"

    if self._auth_input_values:
        if "비밀번호" in desc_lower or "password" in desc_lower:
            password = str(self._auth_input_values.get("password") or "").strip()
            if password:
                return password
        else:
            username = str(
                self._auth_input_values.get("username")
                or self._auth_input_values.get("email")
                or ""
            ).strip()
            if username:
                return username

    if input_values:
        if "비밀번호" in desc_lower or "password" in desc_lower:
            for key in ["password", "비밀번호", "pw", "secret"]:
                if key in input_values:
                    self._log(f"📝 비밀번호 입력: {input_values[key]}")
                    return input_values[key]
        else:
            for key in ["username", "user", "id", "아이디", "사용자"]:
                if key in input_values:
                    self._log(f"📝 사용자명 입력: {input_values[key]}")
                    return input_values[key]
        first_key = list(input_values.keys())[0]
        first_value = input_values[first_key]
        self._log(f"📝 입력값 사용 (첫번째): {first_key}={first_value}")
        return first_value

    if "email" in desc_lower or "이메일" in desc_lower:
        return "test.explorer@example.com"
    if "password" in desc_lower or "비밀번호" in desc_lower:
        return "TestPass123!"
    if "name" in desc_lower or "이름" in desc_lower:
        return "Test User"
    if "phone" in desc_lower or "전화" in desc_lower:
        return "010-1234-5678"
    if "search" in desc_lower or "검색" in desc_lower:
        return "test"
    return "Test input"
