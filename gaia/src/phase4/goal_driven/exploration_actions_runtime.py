from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from .exploratory_models import ElementState, PageState, TestableAction
from .models import DOMElement


def generate_testable_actions(agent: Any, page_state: PageState) -> List[TestableAction]:
    """Build the testable action list for the current page."""
    actions: List[TestableAction] = []

    recent_action_counts: Dict[str, int] = {}
    for entry in agent._action_history[-5:]:
        if ": " in entry:
            action_part = entry.split(": ", 1)[1]
            action_type = action_part.split(" on ", 1)[0]
            recent_action_counts[action_type] = recent_action_counts.get(action_type, 0) + 1

    pending_inputs = has_pending_inputs(agent, page_state)
    has_tested_inputs_value = has_tested_inputs(agent, page_state)
    auth_form_active = has_login_form(agent, page_state)
    auth_phase_active = str(agent._runtime_phase or "").upper() == "AUTH"
    actions_with_status: List[tuple[TestableAction, bool]] = []

    for element in page_state.interactive_elements:
        priority = 0.3 if element.tested else 0.8
        element_label_value = element_label(agent, element)

        if element.tag == "input":
            if element.type in ["text", "email", "password", "search"]:
                action_type = "fill"
                field_hint = element_label_value or element.type or ""
                if element.type == "password":
                    description = f"비밀번호 입력: {field_hint}"
                elif element.type == "email":
                    description = f"이메일 입력: {field_hint}"
                else:
                    description = f"텍스트 입력({element.type}): {field_hint}"
            elif element.type in ["submit", "button", "image"]:
                action_type = "click"
                if has_login_form(agent, page_state):
                    description = "버튼: Login"
                else:
                    description = f"Input: {element.type or element_label_value}"
            elif element.type in ["checkbox", "radio"]:
                action_type = "click"
                description = f"체크박스/라디오: {element_label_value or element.type}"
            else:
                action_type = "click"
                description = f"Input: {element.type or element_label_value}"
        elif element.tag == "a":
            action_type = "click"
            link_label = element_label_value or "[icon link]"
            description = f"링크: {link_label}"
            if element.href:
                resolved = urljoin(page_state.url, element.href)
                current_host = urlparse(page_state.url).netloc
                target_host = urlparse(resolved).netloc
                if current_host and target_host and current_host != target_host:
                    continue
        elif element.tag == "button":
            action_type = "click"
            button_label = element_label_value or "[icon]"
            description = f"버튼: {button_label}"
        elif element.tag == "select":
            action_type = "select"
            opt_hint = ""
            if hasattr(element, "options") and element.options:
                opt_texts = [
                    str(o.get("text", "")).strip()
                    for o in element.options[:5]
                    if isinstance(o, dict) and str(o.get("text", "")).strip()
                ]
                if opt_texts:
                    opt_hint = f" [{' / '.join(opt_texts)}]"
            description = f"드롭다운: {element_label_value}{opt_hint}"
        else:
            action_type = "click"
            description = f"{element.tag}: {element_label_value or element.role}"

        auth_mode = str(agent._auth_input_values.get("auth_mode") or "").strip().lower()
        has_auth_credentials = bool(
            str(agent._auth_input_values.get("password") or "").strip()
            and (
                str(agent._auth_input_values.get("username") or "").strip()
                or str(agent._auth_input_values.get("email") or "").strip()
            )
        )
        if (
            auth_phase_active
            and has_auth_credentials
            and auth_mode not in {"signup", "register"}
            and action_type == "click"
        ):
            desc_lower = description.lower()
            signup_keywords = (
                "회원가입",
                "sign up",
                "signup",
                "register",
                "계정이 없으신가요",
            )
            if any(keyword in desc_lower for keyword in signup_keywords):
                continue

        if auth_phase_active:
            desc_lower = description.lower()
            auth_keywords = [
                "login",
                "log in",
                "sign in",
                "sign up",
                "signup",
                "auth",
                "password",
                "email",
                "username",
                "아이디",
                "비밀번호",
                "로그인",
                "회원가입",
                "인증",
                "captcha",
                "otp",
                "verify",
                "continue",
                "다음",
                "확인",
                "완료",
                "close",
                "dismiss",
                "cancel",
                "취소",
                "닫기",
            ]
            element_hint = " ".join(
                [
                    desc_lower,
                    str(element_label_value or "").lower(),
                    str(element.selector or "").lower(),
                    str(getattr(element, "aria_label", "") or "").lower(),
                    str(getattr(element, "placeholder", "") or "").lower(),
                    str(getattr(element, "title", "") or "").lower(),
                    str(getattr(element, "text", "") or "").lower(),
                ]
            )
            input_type = str(getattr(element, "type", "") or "").lower()
            is_auth_form_control = element.tag in {"input", "textarea"} and (
                input_type in {"password", "email"}
                or any(keyword in element_hint for keyword in auth_keywords)
            )
            is_auth_cta = action_type == "click" and any(
                keyword in element_hint for keyword in auth_keywords
            )
            if not (is_auth_form_control or is_auth_cta):
                continue
            priority = min(1.0, (priority * 1.15) + 0.05)

        if action_type == "select" and not str(element_label_value or "").strip():
            priority *= 0.25

        recent_count = recent_action_counts.get(action_type, 0)
        if recent_count >= 2:
            priority *= 0.6
        elif recent_count == 1:
            priority *= 0.8

        auth_trigger_click = False
        if auth_phase_active and action_type == "click":
            auth_trigger_keywords = [
                "login",
                "log in",
                "sign in",
                "signup",
                "sign up",
                "회원가입",
                "로그인",
                "인증",
                "verify",
            ]
            label_lower = description.lower()
            auth_trigger_click = any(
                keyword in label_lower for keyword in auth_trigger_keywords
            )

        if pending_inputs and action_type == "click" and not auth_trigger_click:
            if has_login_form(agent, page_state):
                if element.tag == "input" and (element.type or "").lower() in [
                    "submit",
                    "button",
                    "image",
                ]:
                    continue
                if element.tag == "button" and "login" in description.lower():
                    continue
            if element.tag == "input" and (element.type or "").lower() in [
                "submit",
                "button",
                "image",
            ]:
                if not has_tested_inputs_value:
                    continue
            if element.tag == "button":
                submit_keywords = [
                    "submit",
                    "login",
                    "log in",
                    "sign in",
                    "next",
                    "continue",
                    "confirm",
                    "ok",
                    "로그인",
                    "다음",
                    "확인",
                    "완료",
                ]
                label_lower = description.lower()
                if any(keyword in label_lower for keyword in submit_keywords):
                    if not has_tested_inputs_value:
                        continue
                    priority *= 0.7

        if action_type == "click":
            temp_action = TestableAction(
                element_id=element.element_id,
                action_type=action_type,
                description=description,
                priority=priority,
                reasoning="",
            )
            if is_toggle_action(agent, temp_action):
                toggle_key = (
                    f"{page_state.url_hash}:{element.element_id}:"
                    f"{normalize_action_description(agent, temp_action)}"
                )
                if agent._toggle_action_history.get(toggle_key, 0) >= 1:
                    continue

        attempt_key = f"{page_state.url_hash}:{element.element_id}:{action_type}"
        attempt_count = agent._action_attempts.get(attempt_key, 0)
        max_attempts = 2
        if (
            element.tag == "a"
            or "back" in description.lower()
            or "next" in description.lower()
        ):
            max_attempts = 4
        if attempt_count >= max_attempts:
            continue
        if action_type == "select" and not str(element_label_value or "").strip():
            if attempt_count >= 1:
                continue
        if attempt_count >= 1:
            priority *= 0.5

        if element.tag == "a" and element.href:
            resolved = urljoin(page_state.url, element.href)
            if resolved:
                current_host = urlparse(page_state.url).netloc
                target_host = urlparse(resolved).netloc
                if target_host and target_host != current_host:
                    priority *= 0.5
                else:
                    href_hash = agent._hash_url(resolved)
                    if href_hash not in agent._visited_pages:
                        priority = min(priority * 1.3, 1.0)

        if agent.config.avoid_destructive:
            destructive_keywords = [
                "delete",
                "삭제",
                "제거",
                "clear",
                "reset",
                "logout",
                "로그아웃",
                "로그 아웃",
                "log out",
                "sign out",
                "reset app state",
            ]
            if any(keyword in description.lower() for keyword in destructive_keywords):
                if any(
                    keyword in description.lower()
                    for keyword in agent.config.allow_destructive_keywords
                ):
                    priority *= 0.6
                elif action_type == "click":
                    continue
                priority *= 0.1

        action = TestableAction(
            element_id=element.element_id,
            action_type=action_type,
            description=description,
            priority=priority,
            reasoning=f"{'미테스트' if not element.tested else '재테스트'} 요소",
        )

        action = boost_action_priority(agent, action)
        action.priority = min(
            1.0,
            float(action.priority) + frontier_context_bonus(agent, element),
        )

        if (
            action.action_type == "click"
            and not element.tested
            and not pending_inputs
            and not is_toggle_action(agent, action)
        ):
            enqueue_frontier_action(agent, page_state, action)

        actions_with_status.append((action, element.tested))

    actions = [action for action, _ in actions_with_status]
    has_untested = any(not tested for _, tested in actions_with_status)
    if has_untested:
        actions = [action for action, tested in actions_with_status if not tested]
        if auth_phase_active and has_login_form(agent, page_state):
            auth_submit_keywords = ("login", "log in", "sign in", "로그인")
            for action, _tested in actions_with_status:
                if action.action_type != "click":
                    continue
                desc = str(action.description or "").lower()
                if not any(keyword in desc for keyword in auth_submit_keywords):
                    continue
                duplicate = any(
                    str(existing.element_id) == str(action.element_id)
                    and str(existing.action_type) == str(action.action_type)
                    for existing in actions
                )
                if duplicate:
                    continue
                action.priority = min(1.0, float(action.priority) + 0.35)
                actions.append(action)
    actions.extend(build_navigation_actions(agent, page_state))

    actions.sort(key=lambda x: x.priority, reverse=True)

    max_actions = 60
    if len(actions) > max_actions:
        category_buckets: Dict[str, List[TestableAction]] = {}
        for action in actions:
            if action.action_type == "fill":
                category = "fill"
            elif action.action_type == "select":
                category = "select"
            elif action.action_type == "navigate":
                category = "navigate"
            elif action.action_type == "click":
                if "[icon link]" in action.description:
                    category = "icon_link"
                elif "[icon]" in action.description:
                    category = "icon_button"
                elif action.description.startswith("링크:"):
                    category = "link"
                elif action.description.startswith("버튼:"):
                    category = "button"
                elif action.description.startswith("체크박스"):
                    category = "toggle"
                else:
                    category = "click"
            else:
                category = action.action_type
            category_buckets.setdefault(category, []).append(action)

        balanced: List[TestableAction] = []
        per_category = max(2, max_actions // max(len(category_buckets), 1))
        for category in [
            "fill",
            "select",
            "navigate",
            "icon_link",
            "icon_button",
            "link",
            "button",
            "toggle",
            "click",
        ]:
            bucket = category_buckets.get(category, [])
            if not bucket:
                continue
            balanced.extend(bucket[:per_category])

        if len(balanced) < max_actions:
            remaining = [action for action in actions if action not in balanced]
            balanced.extend(remaining[: max_actions - len(balanced)])

        return balanced[:max_actions]

    return actions


def enqueue_frontier_action(agent: Any, page_state: PageState, action: TestableAction) -> None:
    key = f"{page_state.url_hash}:{action.element_id}:{action.action_type}"
    if key in agent._action_frontier_set:
        return
    agent._action_frontier.append(
        {
            "url_hash": page_state.url_hash,
            "element_id": action.element_id,
            "action_type": action.action_type,
        }
    )
    agent._action_frontier_set.add(key)


def has_pending_inputs(agent: Any, page_state: PageState) -> bool:
    for element in page_state.interactive_elements:
        if element.tag != "input":
            continue
        input_type = (element.type or "text").lower()
        if input_type in ["submit", "button", "hidden", "image"]:
            continue
        if not element.tested:
            return True
    return False


def has_tested_inputs(agent: Any, page_state: PageState) -> bool:
    for element in page_state.interactive_elements:
        if element.tag != "input":
            continue
        input_type = (element.type or "text").lower()
        if input_type in ["submit", "button", "hidden", "image"]:
            continue
        if element.tested:
            return True
    return False


def has_login_form(agent: Any, page_state: PageState) -> bool:
    has_password = False
    has_user_input = False
    for element in page_state.interactive_elements:
        if element.tag != "input":
            continue
        input_type = (element.type or "text").lower()
        if input_type == "password":
            has_password = True
        if input_type in ["text", "email"]:
            has_user_input = True
    return has_password and has_user_input


def auth_field_order(agent: Any, bucket: Optional[str]) -> int:
    return {"username": 0, "password": 1, "otp": 2}.get(str(bucket or ""), 9)


def auth_field_bucket(agent: Any, action: TestableAction) -> Optional[str]:
    haystack = " ".join([str(action.description or ""), str(action.element_id or "")]).lower()
    if any(token in haystack for token in ("password", "비밀번호", "passwd", "pwd")):
        return "password"
    if any(token in haystack for token in ("username", "user id", "userid", "email", "이메일", "아이디", "사용자")):
        return "username"
    if any(token in haystack for token in ("otp", "2fa", "인증코드", "verification code")):
        return "otp"
    return None


def auth_field_needs_input(agent: Any, action: TestableAction, page_state: PageState) -> bool:
    bucket = auth_field_bucket(agent, action)
    selector = agent._find_selector_by_element_id(action.element_id, page_state)
    current_value = ""
    if selector:
        observed = agent._evaluate_selector(selector, "el => (el.value ?? '').toString()")
        current_value = str(observed or "").strip()
    if bucket and current_value:
        agent._auth_completed_fields.add(bucket)
        return False
    if bucket and bucket in agent._auth_completed_fields:
        return False
    return True


def is_high_priority_element(agent: Any, element: ElementState) -> bool:
    label = element_label(agent, element).lower()
    selector = (element.selector or "").lower()
    haystack = f"{label} {selector}".strip()
    if not haystack:
        return False
    return any(keyword in haystack for keyword in agent.config.high_priority_keywords)


def boost_action_priority(agent: Any, action: TestableAction) -> TestableAction:
    description = action.description.lower()
    if any(keyword in description for keyword in agent.config.high_priority_keywords):
        action.priority = min(1.0, action.priority + 0.35)
    return action


def frontier_context_bonus(agent: Any, element: ElementState) -> float:
    bonus = 0.0
    if str(getattr(element, "container_source", None) or "").strip() == "semantic-first":
        bonus += 0.18
    active_ref = str(getattr(agent, "_active_scoped_container_ref", "") or "").strip()
    if active_ref and str(getattr(element, "container_ref_id", None) or "").strip() == active_ref:
        bonus += 0.24
    container_role = str(getattr(element, "container_role", None) or "").strip().lower()
    if container_role in {"article", "listitem", "row", "region", "group"}:
        bonus += 0.08
    role = str(getattr(element, "role", None) or "").strip().lower()
    if role in {"button", "link", "tab", "menuitem", "option"}:
        bonus += 0.05
    group_actions = getattr(element, "group_action_labels", None) or []
    if group_actions:
        bonus += min(0.12, 0.03 * len(group_actions))
    return bonus


def normalize_seed_urls(agent: Any, start_url: str) -> List[str]:
    seeds: List[str] = []
    for url in agent.config.seed_urls:
        if not url:
            continue
        if url.startswith("http://") or url.startswith("https://"):
            seeds.append(url)
        else:
            seeds.append(urljoin(start_url, url))
    return list(dict.fromkeys(seeds))


def build_navigation_actions(agent: Any, page_state: PageState) -> List[TestableAction]:
    actions: List[TestableAction] = []
    seen: Set[str] = set()
    pending_inputs = has_pending_inputs(agent, page_state)
    base_priority = 0.95 if not pending_inputs else 0.4
    for url in agent._seed_urls:
        resolved = urljoin(page_state.url, url)
        if agent._hash_url(resolved) in agent._visited_pages:
            continue
        element_id = f"navigate:{resolved}"
        attempt_key = f"{page_state.url_hash}:{element_id}:navigate"
        if agent._action_attempts.get(attempt_key, 0) >= 3:
            continue
        if element_id in seen:
            continue
        seen.add(element_id)
        actions.append(
            TestableAction(
                element_id=element_id,
                action_type="navigate",
                description=f"URL 이동: {resolved}",
                priority=base_priority,
                reasoning="탐색 시드",
            )
        )

    actions.extend(build_saucedemo_item_actions(agent, page_state, seen))
    return actions


def build_saucedemo_item_actions(
    agent: Any,
    page_state: PageState,
    seen: Set[str],
) -> List[TestableAction]:
    if "saucedemo.com" not in page_state.url:
        return []
    if "inventory.html" not in page_state.url:
        return []
    parsed = urlparse(page_state.url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    actions: List[TestableAction] = []
    pending_inputs = has_pending_inputs(agent, page_state)
    base_priority = 0.9 if not pending_inputs else 0.35
    pattern = re.compile(r"item_(\d+)_")
    for element in page_state.interactive_elements:
        selector = element.selector or ""
        match = pattern.search(selector)
        if not match:
            continue
        item_id = match.group(1)
        target_url = f"{base_url}/inventory-item.html?id={item_id}"
        element_id = f"navigate:{target_url}"
        attempt_key = f"{page_state.url_hash}:{element_id}:navigate"
        if agent._action_attempts.get(attempt_key, 0) >= 3:
            continue
        if element_id in seen:
            continue
        seen.add(element_id)
        actions.append(
            TestableAction(
                element_id=element_id,
                action_type="navigate",
                description=f"상품 상세 이동: id={item_id}",
                priority=base_priority,
                reasoning="상품 상세 직접 이동",
            )
        )
    return actions


def resolve_navigation_target(agent: Any, element_id: str, current_url: str) -> str:
    target = element_id
    if element_id.startswith("navigate:"):
        target = element_id.split(":", 1)[1]
    if not target:
        return current_url
    return urljoin(current_url, target)


def element_label(agent: Any, element: ElementState) -> str:
    parts = [
        element.text or "",
        element.aria_label or "",
        element.title or "",
        element.placeholder or "",
        element.role or "",
    ]
    label = next((part for part in parts if part), "")
    return label.strip()


def action_signature(agent: Any, actions: List[TestableAction]) -> str:
    entries = [f"{action.action_type}:{normalize_action_description(agent, action)}" for action in actions]
    digest = hashlib.md5("|".join(entries).encode("utf-8")).hexdigest()[:12]
    return digest


def normalize_action_description(agent: Any, action: TestableAction) -> str:
    description = action.description.lower()
    if is_toggle_action(agent, action):
        for keyword in [
            "add to cart",
            "remove",
            "open",
            "close",
            "show",
            "hide",
            "expand",
            "collapse",
        ]:
            if keyword in description:
                return keyword
    return action.description


def build_action_for_element(agent: Any, element: ElementState, action_type: str) -> TestableAction:
    label = element_label(agent, element)
    if element.tag == "input":
        if element.type in ["text", "email", "password", "search"]:
            description = f"텍스트 입력({element.type}): {label or element.type}"
        elif element.type in ["checkbox", "radio"]:
            description = f"체크박스/라디오: {label or element.type}"
        else:
            description = f"Input: {element.type or label}"
    elif element.tag == "a":
        description = f"링크: {label or 'Link'}"
    elif element.tag == "button":
        description = f"버튼: {label or 'Button'}"
    elif element.tag == "select":
        description = f"드롭다운: {label}"
    else:
        description = f"{element.tag}: {label or element.role}"

    return TestableAction(
        element_id=element.element_id,
        action_type=action_type,
        description=description,
        priority=0.5,
        reasoning="BFS fallback",
    )


def state_key(agent: Any, page_state: PageState, actions: List[TestableAction]) -> str:
    dom_marker = agent._active_dom_hash or agent._active_snapshot_id or action_signature(agent, actions)
    epoch_marker = str(int(agent._active_snapshot_epoch or 0))
    return f"{page_state.url_hash}:{dom_marker}:{epoch_marker}"


def is_toggle_action(agent: Any, action: TestableAction) -> bool:
    label = action.description.lower()
    toggle_keywords = [
        "add to cart",
        "remove",
        "open",
        "close",
        "show",
        "hide",
        "expand",
        "collapse",
    ]
    return any(keyword in label for keyword in toggle_keywords)


def select_frontier_action(
    agent: Any,
    page_state: PageState,
    testable_actions: List[TestableAction],
) -> Optional[TestableAction]:
    if not agent._action_frontier:
        return None

    action_map = {
        f"{page_state.url_hash}:{action.element_id}:{action.action_type}": action
        for action in testable_actions
    }
    element_map = {el.element_id: el for el in page_state.interactive_elements}
    candidates: List[tuple[float, Dict[str, str], str, Optional[TestableAction], Optional[ElementState]]] = []
    for index, entry in enumerate(list(agent._action_frontier)):
        if entry["url_hash"] != page_state.url_hash:
            continue
        key = f"{entry['url_hash']}:{entry['element_id']}:{entry['action_type']}"
        action = action_map.get(key)
        element = element_map.get(entry["element_id"])
        score = float(action.priority) if action else 0.0
        if element is not None:
            score += frontier_context_bonus(agent, element)
        score += max(0.0, 0.25 - (index * 0.01))
        candidates.append((score, entry, key, action, element))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _score, entry, key, action, element = candidates[0]
    agent._action_frontier.remove(entry)
    agent._action_frontier_set.discard(key)
    if action:
        return action
    if element:
        return build_action_for_element(agent, element, entry["action_type"])

    return None
