"""Matching helpers extracted from IntelligentOrchestrator."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from gaia.src.utils.models import DomElement


def detect_aria_roles(step_description: str, dom_elements: List[DomElement]) -> Dict[str, List[DomElement]]:
    matches: Dict[str, List[DomElement]] = {}
    desc_lower = step_description.lower()

    role_keywords = {
        "switch": (["toggle", "switch", "스위치", "토글"], "switch"),
        "slider": (["slider", "range", "슬라이더"], "slider"),
        "dialog": (["dialog", "modal", "다이얼로그", "모달", "popup", "팝업"], "dialog"),
        "checkbox": (["checkbox", "체크박스", "check"], "checkbox"),
        "radio": (["radio", "라디오"], "radio"),
        "tab": (["tab", "탭"], "tab"),
        "menu": (["menu", "메뉴", "dropdown", "드롭다운"], "menu"),
        "combobox": (["combobox", "autocomplete", "select", "선택", "자동완성"], "combobox"),
        "searchbox": (["search", "검색"], "searchbox"),
    }

    for role_name, (keywords, aria_role) in role_keywords.items():
        if any(keyword in desc_lower for keyword in keywords):
            matching_elements = [
                elem for elem in dom_elements if elem.attributes and elem.attributes.get("role") == aria_role
            ]
            if matching_elements:
                matches[role_name] = matching_elements
    return matches


def disambiguate_aria_matches(
    orch: Any,
    role_name: str,
    matches: List[DomElement],
    step_description: str,
    action: str,
) -> Optional[Dict[str, Any]]:
    import numpy as np
    import re

    if len(matches) == 1:
        nav_keywords = ["navigate", "go to", "open", "이동", "가기", "열기"]
        needs_navigation = any(kw in step_description.lower() for kw in nav_keywords)
        if needs_navigation:
            return None
        selector = f'[role="{matches[0].attributes.get("role")}"]'
        return {
            "selector": selector,
            "action": "click" if action != "fill" else "fill",
            "reasoning": f"ARIA role match: Single {role_name} found (role='{matches[0].attributes.get('role')}')",
            "confidence": 95,
        }

    print(f"[ARIA Disambiguate] Found {len(matches)} {role_name} elements")
    text_keywords = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", step_description)

    for elem in matches:
        if not elem.text or not elem.text.strip():
            for keyword in sorted(text_keywords, key=len, reverse=True):
                if len(keyword) >= 2:
                    selector = f'text="{keyword}" >> .. >> .. >> [role="{elem.attributes.get("role")}"]'
                    print(f"[ARIA Disambiguate] Using sibling traversal for {role_name}: {selector}")
                    return {
                        "selector": selector,
                        "action": "click" if action != "fill" else "fill",
                        "reasoning": f"ARIA + sibling label match: {role_name} with label '{keyword}'",
                        "confidence": 85,
                    }

    for elem in matches:
        if elem.text and elem.text.strip() in step_description:
            selector = f'[role="{elem.attributes.get("role")}"]:has-text("{elem.text}")'
            print(f"[ARIA Disambiguate] Using text match for {role_name}: {selector}")
            return {
                "selector": selector,
                "action": "click" if action != "fill" else "fill",
                "reasoning": f"ARIA + text match: {role_name} with text '{elem.text}'",
                "confidence": 90,
            }

    elements_with_text = [elem for elem in matches if elem.text and elem.text.strip()]
    if elements_with_text:
        desc_embedding = orch._get_embedding(step_description)
        if desc_embedding is not None:
            best_match = None
            best_similarity = 0.0
            for elem in elements_with_text:
                elem_embedding = orch._get_embedding(elem.text)
                if elem_embedding is None:
                    continue
                similarity = np.dot(desc_embedding, elem_embedding) / (
                    np.linalg.norm(desc_embedding) * np.linalg.norm(elem_embedding)
                )
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = elem
            if best_match and best_similarity >= 0.80:
                selector = f'[role="{best_match.attributes.get("role")}"]:has-text("{best_match.text}")'
                return {
                    "selector": selector,
                    "action": "click" if action != "fill" else "fill",
                    "reasoning": f"ARIA + semantic match: {role_name} '{best_match.text}' (similarity: {best_similarity:.2f})",
                    "confidence": int(best_similarity * 100),
                }

    print(f"[ARIA Disambiguate] Unable to disambiguate {len(matches)} {role_name} elements, falling back to LLM")
    return None


def try_aria_matching(
    orch: Any,
    step_description: str,
    dom_elements: List[DomElement],
    action: str,
) -> Optional[Dict[str, Any]]:
    try:
        aria_matches = detect_aria_roles(step_description, dom_elements)
        if aria_matches:
            for role_name, elements in aria_matches.items():
                result = disambiguate_aria_matches(orch, role_name, elements, step_description, action)
                if result:
                    return result
        return None
    except Exception as e:
        print(f"[ARIA Match] Error: {e}")
        return None


def try_pure_semantic_matching(
    orch: Any,
    step_description: str,
    dom_elements: List[DomElement],
    action: str,
) -> Optional[Dict[str, Any]]:
    try:
        try:
            import numpy as np
        except ImportError:
            print("[Semantic Match] Warning: numpy not available, skipping semantic matching")
            return None

        desc_embedding = orch._get_embedding(step_description)
        if desc_embedding is None:
            return offline_fuzzy_semantic_match(orch, step_description, dom_elements, action)

        best_match = None
        best_similarity = 0.0
        similarity_threshold = 0.82

        for elem in dom_elements:
            elem_text = elem.text.strip()
            if not elem_text or len(elem_text) < 2:
                continue
            elem_embedding = orch._get_embedding(elem_text)
            if elem_embedding is None:
                continue
            similarity = np.dot(desc_embedding, elem_embedding) / (
                np.linalg.norm(desc_embedding) * np.linalg.norm(elem_embedding)
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = elem

        if best_match and best_similarity >= similarity_threshold:
            element_type = best_match.tag if best_match.tag in ["button", "a", "input", "select", "textarea"] else "button"
            selector = f'{element_type}:has-text("{best_match.text}")'
            confidence = int(best_similarity * 100)
            return {
                "selector": selector,
                "action": action,
                "reasoning": f"Semantic match: '{step_description[:50]}' → '{best_match.text}' (similarity: {best_similarity:.2f})",
                "confidence": confidence,
            }

        return offline_fuzzy_semantic_match(orch, step_description, dom_elements, action)
    except Exception as e:
        print(f"[Semantic Match] Error: {e}")
        return None


def try_semantic_matching(
    orch: Any,
    step_description: str,
    dom_elements: List[DomElement],
    action: str,
    current_url: str = "",
    screenshot: str = "",
) -> Optional[Dict[str, Any]]:
    print("[Parallel Match] ARIA matching only (Semantic DISABLED due to embedding dimension mismatch)")
    aria_result = try_aria_matching(orch, step_description, dom_elements, action)
    semantic_result = None
    print(f"[Parallel Match] ARIA: {aria_result is not None}, Semantic: DISABLED")

    if aria_result and semantic_result:
        print(f"[Parallel Match] Both succeeded! ARIA conf={aria_result['confidence']}, Semantic conf={semantic_result['confidence']}")
        if aria_result["selector"] == semantic_result["selector"]:
            print("[Parallel Match] ✅ Both agree on same selector! Using it with high confidence.")
            aria_result["confidence"] = min(95, aria_result["confidence"] + 10)
            return aria_result

        print("[Parallel Match] ⚠️ Disagreement detected! Calling LLM Aggregator...")
        print(f"  ARIA: {aria_result['selector']}")
        print(f"  Semantic: {semantic_result['selector']}")
        vision_result = orch.llm_client.select_element_for_step(
            step_description=step_description,
            dom_elements=dom_elements,
            screenshot_base64=screenshot,
            url=current_url,
        )
        final_decision = orch.llm_client.aggregate_matching_results(
            step_description=step_description,
            aria_result=aria_result,
            semantic_result=semantic_result,
            vision_result=vision_result,
            url=current_url,
        )
        print(f"[Parallel Match] LLM Aggregator decision: {final_decision['selector']} (conf: {final_decision['confidence']})")
        return final_decision
    if aria_result:
        print(f"[Parallel Match] Using ARIA only (conf: {aria_result['confidence']})")
        return aria_result
    if semantic_result:
        print(f"[Parallel Match] Using Semantic only (conf: {semantic_result['confidence']})")
        return semantic_result
    print("[Parallel Match] Both ARIA and Semantic failed, will use LLM Vision")
    return None


def offline_fuzzy_semantic_match(
    orch: Any,
    step_description: str,
    dom_elements: List[DomElement],
    action: str,
) -> Optional[Dict[str, Any]]:
    normalized_desc = orch._normalize_text(step_description)
    if not normalized_desc:
        return None

    position_keywords = ["first", "second", "third", "last"]
    has_position = any(kw in normalized_desc for kw in position_keywords)
    is_interactive_action = action.lower() in ["click", "select", "choose", "pick"]

    best_match = None
    best_score = 0.0
    candidates = []

    for idx, elem in enumerate(dom_elements):
        elem_text = (elem.text or "").strip()
        if len(elem_text) < 3:
            continue
        normalized_elem = orch._normalize_text(elem_text)
        if not normalized_elem:
            continue
        if is_interactive_action and elem.tag not in ["button", "a", "input", "select", "textarea"]:
            continue

        score = SequenceMatcher(None, normalized_desc, normalized_elem).ratio()
        overlap = orch._token_overlap(normalized_desc, normalized_elem)
        if overlap:
            score = max(score, min(0.95, 0.6 + 0.35 * overlap))
        if len(elem_text) >= 5 and (normalized_elem in normalized_desc or normalized_desc in normalized_elem):
            score = max(score, 0.85)
        if len(elem_text) <= 4:
            score *= 0.7

        if score > best_score:
            best_score = score
            best_match = elem
            candidates.append((score, idx, elem))

    if has_position and candidates:
        candidates.sort(key=lambda x: (-x[0], x[1]))
        if "first" in normalized_desc and len(candidates) > 0:
            for score, idx, elem in candidates:
                if score >= 0.7:
                    best_match = elem
                    best_score = score
                    break
        elif "last" in normalized_desc and len(candidates) > 0:
            best_match = candidates[-1][2]
            best_score = candidates[-1][0]

    if best_match and best_score >= 0.70:
        element_type = best_match.tag if best_match.tag in ["button", "a", "input", "select", "textarea"] else "button"
        selector = f'{element_type}:has-text("{best_match.text}")'
        confidence = max(50, int(best_score * 100))
        print(f"[Semantic Match] Using offline fuzzy fallback (score: {best_score:.2f}, text: '{best_match.text[:30]}')")

        if best_score < 0.85:
            print("[Semantic Match] Score below 0.85, requesting LLM verification...")
            is_valid = verify_semantic_match_with_llm(
                orch,
                step_description=step_description,
                matched_text=best_match.text,
                matched_element=best_match,
            )
            if not is_valid:
                print(f"[Semantic Match] LLM rejected match: '{step_description}' != '{best_match.text}'")
                return None
            print("[Semantic Match] LLM confirmed match is valid")

        return {
            "selector": selector,
            "action": action,
            "reasoning": f"Offline fuzzy match: '{step_description[:50]}' → '{best_match.text}'",
            "confidence": confidence,
        }

    print(f"[Semantic Match] No reliable offline match (best score: {best_score:.2f})")
    return None


def verify_semantic_match_with_llm(
    orch: Any,
    step_description: str,
    matched_text: str,
    matched_element: Any,
) -> bool:
    prompt = f"""Verify if this semantic match is correct.

User requested: "{step_description}"
Matched element text: "{matched_text}"
Element type: {matched_element.tag}

Is this a valid match? Consider:
- Does the matched element actually help accomplish the requested task?
- Are they semantically related?
- Would clicking this element be the right action?

Examples of INVALID matches:
- User: "필터 버튼 클릭" → Matched: "전체 선택" (WRONG - completely different)
- User: "검색 입력" → Matched: "로그인" (WRONG - different purpose)

Examples of VALID matches:
- User: "이름 입력" → Matched: "이름" label + input (CORRECT - same field)
- User: "필터 선택" → Matched: "필터" (CORRECT - exact match)

Respond with JSON only:
{{
  "is_valid": true/false,
  "reasoning": "brief explanation"
}}"""
    try:
        import json
        import openai
        import os

        api_key = os.getenv("OPENAI_API_KEY")
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-5.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
        )
        result_text = response.choices[0].message.content.strip()
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
        result = json.loads(result_text.strip())
        return result.get("is_valid", False)
    except Exception as e:
        print(f"[Semantic Match] LLM verification failed: {e}, assuming valid")
        return True
