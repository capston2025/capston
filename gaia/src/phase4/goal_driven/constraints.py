"""Constraint parsing and metric estimation helpers for GoalDrivenAgent."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple


NormalizeTextFn = Callable[[Optional[str]], str]

_GENERIC_GOAL_STOPWORDS = {
    "로그인", "login", "후", "하나", "한개", "과목", "문제", "페이지", "화면", "현재", "이미",
    "확인", "검증", "작동", "정상", "보이는지", "표시", "존재", "추가", "삭제", "제거", "담고",
    "담은", "비우기", "비우는", "증가", "감소", "clear", "remove", "delete", "add", "increase",
    "decrease", "check", "verify", "visible", "already", "without", "interaction", "goal",
    "수치", "count", "number", "total", "총", "해주세요", "해줘", "되는지", "했는지", "하고",
}


def _derive_context_terms(text: str, normalize_text: NormalizeTextFn) -> List[str]:
    tokens = re.findall(r"[a-z0-9가-힣]+", normalize_text(text))
    results: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = str(token or "").strip()
        if len(token) < 2 or token.isdigit() or token in _GENERIC_GOAL_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        results.append(token)
        if len(results) >= 8:
            break
    return results


def derive_goal_constraints(goal_blob: str, normalize_text: NormalizeTextFn) -> Dict[str, Any]:
    text = normalize_text(goal_blob)
    if not text:
        return {}

    no_navigation_hints = (
        "페이지 이동 없이",
        "url 변화 없이",
        "url 변경 없이",
        "같은 페이지",
        "no navigation",
        "without navigation",
        "stay on page",
        "same page",
    )
    require_no_navigation = any(hint in text for hint in no_navigation_hints)
    increase_hints = ("증가", "늘", "담", "추가", "add", "append", "increase", "grow", "more")
    decrease_hints = ("감소", "줄", "제거", "삭제", "remove", "decrease", "less")
    clear_hints = ("비우", "비웠", "전체 삭제", "전부 삭제", "clear", "empty", "remove all")
    mutation_direction: Optional[str] = None
    if any(hint in text for hint in clear_hints):
        mutation_direction = "clear"
    elif any(hint in text for hint in decrease_hints):
        mutation_direction = "decrease"
    elif any(hint in text for hint in increase_hints):
        mutation_direction = "increase"
    context_terms = _derive_context_terms(text, normalize_text)
    numeric_values: List[int] = []
    metric_terms: List[str] = []
    number_pattern = r"(\d{1,3}(?:,\d{3})*|\d{1,6})"
    for match in re.finditer(rf"(?<!\d){number_pattern}(?!\d)\s*([^\d\s,.;:()]{1,12})?", text):
        value = int(str(match.group(1)).replace(",", ""))
        numeric_values.append(value)
        maybe_term = (match.group(2) or "").strip()
        if maybe_term:
            metric_terms.append(maybe_term)

    if not numeric_values:
        payload: Dict[str, Any] = {}
        if require_no_navigation:
            payload["require_no_navigation"] = True
        if mutation_direction:
            payload["mutation_direction"] = mutation_direction
            payload["context_terms"] = context_terms
        return payload

    if len(numeric_values) == 1:
        only_value = int(numeric_values[0])
        id_like_patterns = (
            rf"(?<!\d){only_value}\s*(?:번|번문제|번 문제)",
            rf"(?:problem|문제)\s*{only_value}(?!\d)",
            rf"(?<!\d){only_value}\s*(?:id|번호)",
        )
        if any(re.search(pattern, text) for pattern in id_like_patterns):
            payload = {}
            if require_no_navigation:
                payload["require_no_navigation"] = True
            if mutation_direction:
                payload["mutation_direction"] = mutation_direction
                payload["context_terms"] = context_terms
            return payload

    collect_min: Optional[int] = None
    apply_target: Optional[int] = None

    if len(numeric_values) >= 2:
        collect_min = max(numeric_values)
        apply_target = min(numeric_values)
    else:
        collect_min = numeric_values[0]

    if apply_target is not None and collect_min is not None and apply_target >= collect_min:
        apply_target = None

    term_freq: Dict[str, int] = {}
    for term in metric_terms:
        term_freq[term] = int(term_freq.get(term, 0)) + 1
    sorted_terms = sorted(term_freq.items(), key=lambda kv: kv[1], reverse=True)
    top_terms = [t for t, _ in sorted_terms[:4]]
    metric_label = top_terms[0] if top_terms else "count"
    require_collect_before_progress = bool(collect_min is not None and apply_target is not None)

    payload = {
        "metric": "numeric",
        "metric_label": metric_label,
        "metric_terms": top_terms,
        "collect_min": collect_min,
        "apply_target": apply_target,
        "require_collect_before_progress": require_collect_before_progress,
        "require_no_navigation": require_no_navigation,
    }
    if mutation_direction:
        payload["mutation_direction"] = mutation_direction
        payload["context_terms"] = context_terms
    return payload


def extract_metric_values_from_text(
    value: str,
    metric_terms: List[str],
    normalize_text: NormalizeTextFn,
) -> List[int]:
    text = normalize_text(value)
    if not text:
        return []

    number_pattern = r"(\d{1,3}(?:,\d{3})*|\d{1,6})"

    def _to_int(raw: str) -> int:
        return int(str(raw).replace(",", ""))

    numbers: List[int] = []
    term_matches = 0
    for term in metric_terms or []:
        safe_term = re.escape(str(term))
        for m in re.finditer(rf"{number_pattern}\s*{safe_term}", text):
            numbers.append(_to_int(m.group(1)))
            term_matches += 1
        for m in re.finditer(rf"{safe_term}\s*{number_pattern}", text):
            numbers.append(_to_int(m.group(1)))
            term_matches += 1
    if term_matches > 0:
        numbers.extend(_to_int(m.group(1)) for m in re.finditer(rf"\({number_pattern}\)", text))
        return numbers

    if metric_terms:
        return []

    contextual_numbers: List[int] = []
    context_patterns = [
        rf"(?:총|합계|count|counts|items?|item|total|현재|수량|개수|학점)\s*[:=]?\s*{number_pattern}",
        rf"{number_pattern}\s*(?:개|건|명|점|학점|items?|item|count)",
    ]
    for pattern in context_patterns:
        for m in re.finditer(pattern, text):
            contextual_numbers.append(_to_int(m.group(1)))
    if contextual_numbers:
        return contextual_numbers

    return [_to_int(m.group(1)) for m in re.finditer(rf"\({number_pattern}\)", text)]


def estimate_goal_metric_from_dom(
    dom_elements: List[Any],
    goal_constraints: Dict[str, Any],
    normalize_text: NormalizeTextFn,
) -> Optional[float]:
    metric_kind = str(goal_constraints.get("metric") or "").strip().lower()
    if metric_kind != "numeric":
        return None
    metric_terms = [str(x) for x in (goal_constraints.get("metric_terms") or []) if str(x).strip()]

    values: List[int] = []
    contextual_values: List[int] = []
    aggregate_hints = (
        "총",
        "합계",
        "현재",
        "누적",
        "선택",
        "담은",
        "장바구니",
        "위시",
        "wishlist",
        "selected",
        "cart",
        "time table",
        "시간표",
    )
    for el in dom_elements:
        fields = [
            getattr(el, "text", None),
            getattr(el, "aria_label", None),
            getattr(el, "placeholder", None),
            getattr(el, "title", None),
        ]
        for field in fields:
            if not field:
                continue
            field_text = str(field)
            field_values = extract_metric_values_from_text(field_text, metric_terms, normalize_text)
            if not field_values:
                continue
            values.extend(field_values)
            normalized_field = normalize_text(field_text)
            if any(hint in normalized_field for hint in aggregate_hints):
                contextual_values.extend(field_values)

    collect_min = goal_constraints.get("collect_min")
    apply_target = goal_constraints.get("apply_target")
    dynamic_upper = 10000
    try:
        if collect_min is not None:
            dynamic_upper = max(dynamic_upper, int(collect_min) * 4)
        if apply_target is not None:
            dynamic_upper = max(dynamic_upper, int(apply_target) * 4)
    except Exception:
        pass
    dynamic_upper = min(dynamic_upper, 1_000_000)

    filtered = [v for v in values if 0 <= int(v) <= dynamic_upper]
    if not filtered:
        return None
    contextual_filtered = [v for v in contextual_values if 0 <= int(v) <= dynamic_upper]
    if contextual_filtered:
        return float(max(contextual_filtered))

    # context 힌트가 없는 숫자 추정치는 저신뢰로 취급한다.
    # 단일/유사 숫자만 반복 관측되고 collect_min 대비 너무 작으면 unknown으로 반환해
    # hard gate 루프를 방지한다.
    collect_min = goal_constraints.get("collect_min")
    try:
        collect_min_value = float(collect_min)
    except Exception:
        collect_min_value = 0.0
    max_value = float(max(filtered))
    unique_count = len({int(v) for v in filtered})
    if collect_min_value >= 3.0 and max_value < (collect_min_value * 0.35) and unique_count <= 2:
        return None
    return float(max(filtered))


def estimate_summary_counter_from_dom(
    dom_elements: List[Any],
    goal_constraints: Dict[str, Any],
    normalize_text: NormalizeTextFn,
) -> Tuple[Optional[int], bool]:
    context_terms = [
        str(x).strip().lower()
        for x in (goal_constraints.get("context_terms") or [])
        if str(x).strip()
    ]
    aggregate_hints = (
        "총", "합계", "현재", "누적", "selected", "selection", "total",
        "count", "item", "items", "credit", "credits", "학점", "개수", "수량",
    )
    zero_hints = (
        "비어", "empty", "없어요", "없음", "0개", "0학점",
    )
    best_score = -1.0
    best_value: Optional[int] = None
    zero_state = False
    for el in dom_elements:
        fields = [
            getattr(el, "text", None),
            getattr(el, "aria_label", None),
            getattr(el, "title", None),
            getattr(el, "placeholder", None),
        ]
        for field in fields:
            if not field:
                continue
            normalized = normalize_text(str(field))
            if not normalized:
                continue
            if any(hint in normalized for hint in zero_hints):
                zero_state = True

            numbers: List[int] = []
            for pattern in (
                r"(?:총|합계|현재|누적|selected|selection|count|counts|item|items|total|credit|credits|학점|개수|수량)\s*[:=]?\s*(\d{1,6})",
                r"(\d{1,6})\s*(?:개|건|명|점|학점|item|items|credit|credits)",
            ):
                for m in re.finditer(pattern, normalized):
                    try:
                        numbers.append(int(m.group(1)))
                    except Exception:
                        continue
            if not numbers:
                continue

            score = 0.0
            if any(term in normalized for term in context_terms):
                score += 3.0
            if any(hint in normalized for hint in aggregate_hints):
                score += 2.0
            if len(numbers) == 1:
                score += 0.5
            candidate = max(numbers)
            if score > best_score or (score == best_score and best_value is not None and candidate > best_value):
                best_score = score
                best_value = candidate
    return best_value, zero_state


def evaluate_mutation_contract(
    *,
    before_dom: List[Any],
    after_dom: List[Any],
    goal_constraints: Dict[str, Any],
    normalize_text: NormalizeTextFn,
) -> Optional[str]:
    direction = str(goal_constraints.get("mutation_direction") or "").strip().lower()
    if direction not in {"increase", "decrease", "clear"}:
        return None

    before_value, before_zero = estimate_summary_counter_from_dom(
        before_dom, goal_constraints, normalize_text
    )
    after_value, after_zero = estimate_summary_counter_from_dom(
        after_dom, goal_constraints, normalize_text
    )

    if direction == "clear":
        if after_zero:
            return "요약 상태가 비움/empty로 변경되어 목표를 완료로 판정했습니다."
        if after_value is not None and after_value == 0:
            return "요약 수치가 0으로 변경되어 목표를 완료로 판정했습니다."
        return None

    if before_value is None or after_value is None:
        return None

    if direction == "increase" and after_value > before_value:
        return f"요약 수치가 {before_value} -> {after_value}로 증가해 목표를 완료로 판정했습니다."
    if direction == "decrease" and after_value < before_value:
        return f"요약 수치가 {before_value} -> {after_value}로 감소해 목표를 완료로 판정했습니다."
    return None
