from __future__ import annotations

import time
from typing import List, Tuple

from gaia.src.utils.models import DomElement, TestStep
from gaia.src.phase4.mcp_transport_retry_runtime import execute_mcp_action_with_recovery


def get_page_state(orchestrator) -> Tuple[str, List[DomElement], str]:
    screenshot = orchestrator._capture_screenshot(url=None, send_to_gui=True)

    payload = {
        "action": "analyze_page",
        "params": {"session_id": orchestrator.session_id, "url": None},
    }
    try:
        response = execute_mcp_action_with_recovery(
            raw_base_url=orchestrator.mcp_config.host_url,
            action="analyze_page",
            params=dict(payload.get("params") or {}),
            timeout=30,
            attempts=2,
            is_transport_error=getattr(orchestrator, "_is_mcp_transport_error", None),
            recover_host=getattr(orchestrator, "_recover_mcp_host", None),
            context="orchestrator_page_state",
        )
        if response.status_code >= 400:
            raise RuntimeError(str(getattr(response, "text", "") or response.payload))
        data = response.payload if not hasattr(response, "json") else response.json()

        raw_dom_elements = data.get("dom_elements", []) or []
        orchestrator._active_snapshot_id = str(data.get("snapshot_id") or "")
        orchestrator._selector_to_ref_id = {}
        for raw_elem in raw_dom_elements:
            if not isinstance(raw_elem, dict):
                continue
            ref_id = str(raw_elem.get("ref_id") or "").strip()
            if not ref_id:
                continue
            selector = str(raw_elem.get("selector") or "").strip()
            full_selector = str(raw_elem.get("full_selector") or "").strip()
            if selector:
                orchestrator._selector_to_ref_id[selector] = ref_id
            if full_selector:
                orchestrator._selector_to_ref_id[full_selector] = ref_id

        dom_elements = [DomElement(**elem) for elem in raw_dom_elements]
        current_url = data.get("url", "")

    except Exception as e:
        print(f"Failed to get page state: {e}")
        dom_elements = []
        current_url = ""
        orchestrator._active_snapshot_id = ""
        orchestrator._selector_to_ref_id = {}

    return screenshot, dom_elements, current_url


def try_recover_from_empty_dom(orchestrator, current_url: str, progress_callback=None) -> bool:
    orchestrator._log("      🔄 Attempting recovery: Navigating to base URL...", progress_callback)

    base_url = current_url.split('#')[0] if current_url else orchestrator.mcp_config.base_url

    try:
        goto_success = orchestrator._execute_action(
            action="goto",
            selector="",
            params=[base_url],
            url=base_url,
        )
        if not goto_success:
            return False

        time.sleep(2)

        orchestrator._log("      📊 Re-analyzing page after recovery...", progress_callback)
        _, dom_elements, _ = get_page_state(orchestrator)

        if len(dom_elements) > 0:
            orchestrator._log(
                f"      ✅ Recovery successful! Found {len(dom_elements)} DOM elements",
                progress_callback,
            )
            return True
        orchestrator._log("      ❌ Recovery failed - still 0 DOM elements", progress_callback)
        return False

    except Exception as e:
        orchestrator._log(f"      ❌ Recovery navigation failed: {e}", progress_callback)
        return False


def generate_success_indicators(scenario_description: str, steps: List[TestStep]) -> List[str]:
    indicators = []
    scenario_lower = scenario_description.lower()

    if "로그인" in scenario_description or "login" in scenario_lower:
        indicators.extend([
            "로그아웃 버튼이 표시됨",
            "사용자 프로필이 표시됨",
            "환영 메시지가 표시됨",
            "로그인 버튼이 사라짐",
        ])

    if (
        "회원가입" in scenario_description
        or "가입" in scenario_description
        or "signup" in scenario_lower
        or "register" in scenario_lower
    ):
        indicators.extend([
            "회원가입 완료 메시지가 표시됨",
            "자동으로 로그인됨",
            "가입 완료 페이지로 이동됨",
        ])

    if "제출" in scenario_description or "등록" in scenario_description or "submit" in scenario_lower:
        indicators.extend([
            "제출 완료 메시지가 표시됨",
            "성공 알림이 표시됨",
            "폼이 초기화됨",
        ])

    if "장바구니" in scenario_description or "카트" in scenario_description or "cart" in scenario_lower:
        indicators.extend([
            "장바구니 개수가 증가함",
            "장바구니에 추가 메시지가 표시됨",
            "상품이 장바구니 목록에 표시됨",
        ])

    if "검색" in scenario_description or "search" in scenario_lower:
        indicators.extend([
            "검색 결과가 표시됨",
            "결과 목록이 업데이트됨",
            "검색어와 관련된 항목이 표시됨",
        ])

    if "이동" in scenario_description or "navigate" in scenario_lower or "페이지" in scenario_description:
        indicators.extend([
            "페이지가 변경됨",
            "새로운 콘텐츠가 표시됨",
            "URL이 업데이트됨",
        ])

    if (
        "삭제" in scenario_description
        or "제거" in scenario_description
        or "delete" in scenario_lower
        or "remove" in scenario_lower
    ):
        indicators.extend([
            "항목이 목록에서 사라짐",
            "삭제 완료 메시지가 표시됨",
            "개수가 감소함",
        ])

    for step in steps:
        step_desc_lower = step.description.lower()
        if "클릭" in step.description or "click" in step_desc_lower:
            if "제출" in step.description or "submit" in step_desc_lower:
                indicators.append("제출 후 확인 메시지나 페이지 변경")
            elif "저장" in step.description or "save" in step_desc_lower:
                indicators.append("저장 완료 메시지 표시")

        if "입력" in step.description or "fill" in step_desc_lower or "type" in step_desc_lower:
            indicators.append("입력한 값이 폼에 표시됨")

    if not indicators:
        indicators.extend([
            "시나리오 설명에 맞는 화면 변화가 발생함",
            "에러 메시지가 표시되지 않음",
            "예상한 UI 상태로 변경됨",
        ])

    seen = set()
    unique_indicators = []
    for indicator in indicators:
        if indicator not in seen:
            seen.add(indicator)
            unique_indicators.append(indicator)

    return unique_indicators


def record_page_elements(orchestrator, url: str, dom_elements: List[DomElement]) -> None:
    if len(orchestrator.page_element_map) >= 4 and url != orchestrator.home_url:
        return

    if url not in orchestrator.page_element_map:
        orchestrator.page_element_map[url] = {}

    nav_keywords = [
        "기본", "폼", "인터랙션", "홈", "home", "menu", "메뉴",
        "카테고리", "category", "페이지", "page", "시작", "start",
    ]

    recorded_count = 0
    for elem in dom_elements:
        if elem.text and elem.tag in ["button", "a"]:
            text_lower = elem.text.lower()
            if len(elem.text) < 30 or any(keyword in text_lower for keyword in nav_keywords):
                orchestrator.page_element_map[url][text_lower] = elem.selector
                recorded_count += 1

    print(
        f"[Smart Navigation] Recorded {recorded_count} navigation elements for {url} "
        f"(total pages: {len(orchestrator.page_element_map)})"
    )
