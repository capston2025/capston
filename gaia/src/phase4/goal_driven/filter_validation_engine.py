"""Semantic filter validation engine shared by GoalDriven and Exploratory flows.

OpenClaw-aligned principles:
- deterministic postcondition checks (not only state transition checks)
- strict mandatory check policy support
- reason-code telemetry hooks via adapter.record_reason(...)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .models import DOMElement


@dataclass
class FilterCheckRow:
    check_id: str
    name: str
    status: str  # passed | failed | skipped
    mandatory: bool
    scope: str  # global | page1 | page2
    check_type: str
    expected: str = ""
    observed: str = ""
    evidence: Dict[str, Any] | None = None
    action: str = "verify"
    input_value: str = "-"
    error: str = ""

    def to_dict(self, step: int) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": self.status,
            "step": step,
            "action": self.action,
            "input_value": self.input_value,
            "error": self.error,
            "check_type": self.check_type,
            "mandatory": self.mandatory,
            "scope": self.scope,
            "expected": self.expected,
            "observed": self.observed,
            "evidence": dict(self.evidence or {}),
        }


@dataclass
class FilterValidationSummary:
    goal_type: str
    total_checks: int
    passed_checks: int
    failed_checks: int
    skipped_checks: int
    failed_mandatory_checks: int
    success_rate: float
    strict_failed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_type": self.goal_type,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "skipped_checks": self.skipped_checks,
            "failed_mandatory_checks": self.failed_mandatory_checks,
            "success_rate": self.success_rate,
            "strict_failed": self.strict_failed,
        }


@dataclass
class FilterValidationReport:
    mode: str
    success: bool
    summary: FilterValidationSummary
    checks: List[Dict[str, Any]]
    rules_used: List[str]
    pages_checked: int
    cases: List[Dict[str, Any]]
    reason_code_summary: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "success": self.success,
            "summary": self.summary.to_dict(),
            "checks": list(self.checks),
            "rules_used": list(self.rules_used),
            "pages_checked": self.pages_checked,
            "cases": list(self.cases),
            "failed_mandatory_count": int(self.summary.failed_mandatory_checks),
            "reason_code_summary": dict(self.reason_code_summary),
        }


class FilterValidationAdapter(Protocol):
    def analyze_dom(self) -> List[DOMElement]:
        ...

    def apply_select(self, element_id: int, value: str) -> Dict[str, Any]:
        ...

    def click_element(self, element_id: int) -> Dict[str, Any]:
        ...

    def resolve_ref(self, element_id: int) -> str:
        ...

    def current_url(self) -> str:
        ...

    def record_reason(self, code: str) -> None:
        ...

    def log(self, message: str) -> None:
        ...


class FilterRule(Protocol):
    name: str
    mandatory_row_consistency: bool

    def supports(self, *, goal_text: str, control: DOMElement, option_text: str, option_value: str) -> bool:
        ...

    def evaluate_rows(self, *, selected_text: str, selected_value: str, row_texts: List[str]) -> Tuple[bool, str, Dict[str, Any]]:
        ...


class CreditFilterRule:
    name = "credit_rule"
    mandatory_row_consistency = True
    _credit_token = re.compile(r"(\d{1,2})\s*학점")
    _number_token = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")

    def supports(self, *, goal_text: str, control: DOMElement, option_text: str, option_value: str) -> bool:
        blob = " ".join(
            [
                _normalize(goal_text),
                _normalize(control.text),
                _normalize(control.aria_label),
                _normalize(control.title),
                _normalize(option_text),
                _normalize(option_value),
            ]
        )
        if any(token in blob for token in ("학점", "credit")):
            return True
        if self._credit_token.search(option_text or ""):
            return True
        if self._credit_token.search(option_value or ""):
            return True
        return False

    def evaluate_rows(self, *, selected_text: str, selected_value: str, row_texts: List[str]) -> Tuple[bool, str, Dict[str, Any]]:
        target = _extract_credit(selected_text) or _extract_credit(selected_value)
        if target is None:
            return False, "선택된 학점 값을 파싱하지 못했습니다.", {"target": None, "rows_with_credit": 0}

        extracted: List[int] = []
        matched_rows = 0
        mismatch_rows = 0
        mismatch_examples: List[str] = []
        for row in row_texts:
            row_credits = _extract_row_credits(row)
            if not row_credits:
                continue
            matched_rows += 1
            extracted.extend(row_credits)
            if any(v != target for v in row_credits):
                mismatch_rows += 1
                if len(mismatch_examples) < 3:
                    mismatch_examples.append(row[:120])

        if matched_rows == 0:
            return (
                False,
                "페이지 결과에서 학점 표본을 찾지 못했습니다.",
                {"target": target, "rows_with_credit": 0, "row_total": len(row_texts)},
            )

        if mismatch_rows > 0:
            return (
                False,
                f"학점 불일치 행이 {mismatch_rows}개 감지되었습니다.",
                {
                    "target": target,
                    "rows_with_credit": matched_rows,
                    "mismatch_rows": mismatch_rows,
                    "mismatch_examples": mismatch_examples,
                    "observed_credits": sorted(set(extracted)),
                },
            )

        return (
            True,
            "모든 학점 표본이 선택 값과 일치합니다.",
            {
                "target": target,
                "rows_with_credit": matched_rows,
                "observed_credits": sorted(set(extracted)),
            },
        )


class GenericOptionTokenRule:
    name = "generic_option_token_rule"
    mandatory_row_consistency = False

    def supports(self, *, goal_text: str, control: DOMElement, option_text: str, option_value: str) -> bool:
        _ = goal_text
        _ = control
        text = _normalize(option_text or option_value)
        return bool(text)

    def evaluate_rows(self, *, selected_text: str, selected_value: str, row_texts: List[str]) -> Tuple[bool, str, Dict[str, Any]]:
        tokens = _tokenize(selected_text or selected_value)
        if not tokens:
            return True, "옵션 토큰이 비어 있어 일반 규칙 체크를 건너뜁니다.", {"tokens": []}

        matched = 0
        for row in row_texts:
            row_norm = _normalize(row)
            if all(token in row_norm for token in tokens[:2]):
                matched += 1
        if matched == 0:
            return False, "옵션 토큰과 매칭되는 결과 행을 찾지 못했습니다.", {"tokens": tokens[:4], "matched_rows": 0}
        return True, "옵션 토큰 기반 표본이 확인되었습니다.", {"tokens": tokens[:4], "matched_rows": matched}


def run_filter_validation(
    adapter: FilterValidationAdapter,
    goal_text: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = dict(config or {})
    max_cases = max(1, int(cfg.get("max_cases", 3)))
    max_pages = max(1, int(cfg.get("max_pages", 2)))
    strict_mandatory = bool(cfg.get("strict_mandatory", True))
    use_current_selection_only = bool(cfg.get("use_current_selection_only", False))
    forced_selected_value = str(cfg.get("forced_selected_value") or "").strip()

    reason_counter: Dict[str, int] = {}
    checks: List[FilterCheckRow] = []
    cases: List[Dict[str, Any]] = []
    rules_used: List[str] = []
    pages_checked = 1

    def _record_reason(code: str) -> None:
        key = str(code or "").strip()
        if not key:
            return
        reason_counter[key] = int(reason_counter.get(key, 0)) + 1
        try:
            adapter.record_reason(key)
        except Exception:
            pass

    def _add_check(row: FilterCheckRow) -> None:
        checks.append(row)

    dom = adapter.analyze_dom()
    control = _pick_filter_control(dom, goal_text)
    if control is None:
        _record_reason("filter_case_failed")
        _add_check(
            FilterCheckRow(
                check_id="filter_control_detect",
                name="필터 컨트롤 감지",
                status="failed",
                mandatory=True,
                scope="global",
                check_type="control_detect",
                expected="필터 가능한 select 컨트롤 존재",
                observed="감지 실패",
                error="필터 컨트롤을 찾지 못했습니다.",
            )
        )
        return _build_report(
            checks=checks,
            cases=cases,
            rules_used=rules_used,
            pages_checked=1,
            reason_counter=reason_counter,
            strict_mandatory=strict_mandatory,
        ).to_dict()

    options = _collect_option_cases(control)
    if use_current_selection_only:
        current_val = forced_selected_value or str(control.selected_value or "").strip()
        current_text = ""
        if current_val and isinstance(control.options, list):
            for item in control.options:
                if not isinstance(item, dict):
                    continue
                if str(item.get("value") or "").strip() == current_val:
                    current_text = str(item.get("text") or "").strip()
                    break
        if not current_val and options:
            current_val = str(options[0].get("value") or "").strip()
            current_text = str(options[0].get("text") or "").strip()
        options = [{"value": current_val, "text": current_text}] if current_val else []

    if not options:
        _record_reason("filter_case_failed")
        _add_check(
            FilterCheckRow(
                check_id="filter_option_cases",
                name="필터 옵션 케이스 구성",
                status="failed",
                mandatory=True,
                scope="global",
                check_type="option_case_build",
                expected="검증 가능한 옵션 1개 이상",
                observed="0개",
                error="전체/기본값 제외 후 옵션 케이스가 없습니다.",
            )
        )
        return _build_report(
            checks=checks,
            cases=cases,
            rules_used=rules_used,
            pages_checked=1,
            reason_counter=reason_counter,
            strict_mandatory=strict_mandatory,
        ).to_dict()

    for case_idx, option in enumerate(options[:max_cases], start=1):
        selected_value = str(option.get("value") or "").strip()
        selected_text = str(option.get("text") or "").strip()
        if not selected_value:
            continue

        _record_reason("filter_case_started")
        case_info: Dict[str, Any] = {
            "case_index": case_idx,
            "selected_value": selected_value,
            "selected_text": selected_text,
            "checks": [],
        }

        if not use_current_selection_only:
            apply_result = adapter.apply_select(control.id, selected_value)
            apply_ok = bool(apply_result.get("success")) and bool(apply_result.get("effective", True))
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_selection_apply",
                    name=f"필터 적용 실행(case {case_idx})",
                    status="passed" if apply_ok else "failed",
                    mandatory=True,
                    scope="global",
                    check_type="selection_apply",
                    expected=f"value={selected_value}",
                    observed=str(apply_result.get("reason_code") or ("ok" if apply_ok else "failed")),
                    evidence={
                        "reason": apply_result.get("reason") or "",
                        "reason_code": apply_result.get("reason_code") or "",
                    },
                    action="select",
                    input_value=selected_text or selected_value,
                    error="" if apply_ok else str(apply_result.get("reason") or "select 실행 실패"),
                )
            )
            if not apply_ok:
                _record_reason("filter_selection_mismatch")
                _record_reason("filter_case_failed")
                case_info["status"] = "failed"
                cases.append(case_info)
                continue

        page1_dom = adapter.analyze_dom()
        control_page1 = _pick_filter_control(page1_dom, goal_text) or control
        selected_ok, selected_obs = _selection_reflected(control_page1, selected_value, selected_text)
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_selection_reflected",
                name=f"필터 선택 상태 반영(case {case_idx})",
                status="passed" if selected_ok else "failed",
                mandatory=True,
                scope="page1",
                check_type="selection_reflected",
                expected=f"{selected_text or selected_value}",
                observed=selected_obs,
                evidence={
                    "selected_value": control_page1.selected_value,
                    "ref_id": adapter.resolve_ref(control_page1.id),
                },
                action="select",
                input_value=selected_text or selected_value,
                error="" if selected_ok else "선택 상태가 DOM에 반영되지 않았습니다.",
            )
        )
        if selected_ok:
            _record_reason("filter_selection_verified")
        else:
            _record_reason("filter_selection_mismatch")

        active_rule = _pick_rule(goal_text, control_page1, selected_text, selected_value)
        rules_used.append(active_rule.name)
        row_texts_page1 = _collect_result_rows(page1_dom)
        row_ok1, row_msg1, row_ev1 = active_rule.evaluate_rows(
            selected_text=selected_text,
            selected_value=selected_value,
            row_texts=row_texts_page1,
        )
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_result_consistency_page1",
                name=f"결과 정합성(page1, case {case_idx})",
                status="passed" if row_ok1 else "failed",
                mandatory=bool(active_rule.mandatory_row_consistency),
                scope="page1",
                check_type="result_consistency_page1",
                expected=f"선택 옵션={selected_text or selected_value}",
                observed=row_msg1,
                evidence=row_ev1,
                action="verify",
                input_value=selected_text or selected_value,
                error="" if row_ok1 else row_msg1,
            )
        )
        if row_ok1:
            _record_reason("filter_case_passed")
        else:
            _record_reason("filter_result_mismatch")

        if max_pages <= 1:
            case_info["status"] = "passed" if (selected_ok and row_ok1) else "failed"
            cases.append(case_info)
            continue

        next_el = _pick_next_pagination(page1_dom)
        if next_el is None:
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_pagination_persistence",
                    name=f"페이지네이션 유지성(case {case_idx})",
                    status="skipped",
                    mandatory=False,
                    scope="page2",
                    check_type="pagination_persistence",
                    expected="다음 페이지 이동 후 선택 유지",
                    observed="페이지네이션 컨트롤 없음",
                    evidence={},
                    action="click",
                    input_value="다음 페이지",
                )
            )
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_result_consistency_page2",
                    name=f"결과 정합성(page2, case {case_idx})",
                    status="skipped",
                    mandatory=False,
                    scope="page2",
                    check_type="result_consistency_page2",
                    expected=f"선택 옵션={selected_text or selected_value}",
                    observed="페이지네이션 미적용",
                    evidence={},
                    action="verify",
                    input_value=selected_text or selected_value,
                )
            )
            _record_reason("filter_pagination_not_available")
            case_info["status"] = "passed" if (selected_ok and row_ok1) else "failed"
            cases.append(case_info)
            continue

        pages_checked = max(pages_checked, 2)
        click_result = adapter.click_element(next_el.id)
        click_ok = bool(click_result.get("success")) and bool(click_result.get("effective", True))
        page2_dom = adapter.analyze_dom() if click_ok else []
        control_page2 = _pick_filter_control(page2_dom, goal_text) if page2_dom else None
        persisted_ok = bool(click_ok and control_page2 and _selection_reflected(control_page2, selected_value, selected_text)[0])
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_pagination_persistence",
                name=f"페이지네이션 유지성(case {case_idx})",
                status="passed" if persisted_ok else "failed",
                mandatory=True,
                scope="page2",
                check_type="pagination_persistence",
                expected=f"{selected_text or selected_value} 유지",
                observed=str(click_result.get("reason_code") or ("ok" if persisted_ok else "failed")),
                evidence={
                    "from_url": click_result.get("before_url", ""),
                    "to_url": adapter.current_url(),
                    "next_ref": adapter.resolve_ref(next_el.id),
                },
                action="click",
                input_value="다음 페이지",
                error="" if persisted_ok else str(click_result.get("reason") or "선택 유지 실패"),
            )
        )

        row_ok2 = False
        row_msg2 = "page2 미검증"
        row_ev2: Dict[str, Any] = {}
        if persisted_ok and page2_dom:
            row_texts_page2 = _collect_result_rows(page2_dom)
            row_ok2, row_msg2, row_ev2 = active_rule.evaluate_rows(
                selected_text=selected_text,
                selected_value=selected_value,
                row_texts=row_texts_page2,
            )
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_result_consistency_page2",
                name=f"결과 정합성(page2, case {case_idx})",
                status="passed" if row_ok2 else "failed",
                mandatory=bool(active_rule.mandatory_row_consistency),
                scope="page2",
                check_type="result_consistency_page2",
                expected=f"선택 옵션={selected_text or selected_value}",
                observed=row_msg2,
                evidence=row_ev2,
                action="verify",
                input_value=selected_text or selected_value,
                error="" if row_ok2 else row_msg2,
            )
        )
        if not persisted_ok:
            _record_reason("filter_persistence_lost")
        if not row_ok2:
            _record_reason("filter_result_mismatch")

        mandatory_failed = any(
            row.mandatory and row.status == "failed"
            for row in checks
            if row.check_id.startswith(f"case_{case_idx}_")
        )
        case_info["status"] = "failed" if mandatory_failed else "passed"
        if case_info["status"] == "passed":
            _record_reason("filter_case_passed")
        else:
            _record_reason("filter_case_failed")
        cases.append(case_info)

    report = _build_report(
        checks=checks,
        cases=cases,
        rules_used=rules_used,
        pages_checked=pages_checked,
        reason_counter=reason_counter,
        strict_mandatory=strict_mandatory,
    )
    return report.to_dict()


def _build_report(
    *,
    checks: List[FilterCheckRow],
    cases: List[Dict[str, Any]],
    rules_used: List[str],
    pages_checked: int,
    reason_counter: Dict[str, int],
    strict_mandatory: bool,
) -> FilterValidationReport:
    rows = [row.to_dict(step=i + 1) for i, row in enumerate(checks)]
    total = len(rows)
    passed = sum(1 for r in rows if str(r.get("status")) == "passed")
    failed = sum(1 for r in rows if str(r.get("status")) == "failed")
    skipped = sum(1 for r in rows if str(r.get("status")) == "skipped")
    failed_mandatory = sum(
        1
        for r in rows
        if str(r.get("status")) == "failed" and bool(r.get("mandatory"))
    )
    success_rate = round((passed / total) * 100, 1) if total > 0 else 0.0
    strict_failed = bool(strict_mandatory and failed_mandatory > 0)
    summary = FilterValidationSummary(
        goal_type="filter_validation_semantic",
        total_checks=total,
        passed_checks=passed,
        failed_checks=failed,
        skipped_checks=skipped,
        failed_mandatory_checks=failed_mandatory,
        success_rate=success_rate,
        strict_failed=strict_failed,
    )
    return FilterValidationReport(
        mode="filter_semantic_v2",
        success=not strict_failed,
        summary=summary,
        checks=rows,
        rules_used=sorted(set(rules_used)),
        pages_checked=max(1, int(pages_checked)),
        cases=cases,
        reason_code_summary=dict(reason_counter),
    )


def _pick_filter_control(dom: List[DOMElement], goal_text: str) -> Optional[DOMElement]:
    goal_norm = _normalize(goal_text)
    best: Optional[Tuple[float, DOMElement]] = None
    for el in dom:
        if _normalize(el.tag) != "select":
            continue
        options = el.options if isinstance(el.options, list) else []
        if len(options) < 2:
            continue
        if not bool(el.is_visible) or not bool(el.is_enabled):
            continue
        blob = " ".join(
            [
                _normalize(el.text),
                _normalize(el.aria_label),
                _normalize(el.title),
                _normalize(el.class_name),
                _normalize(el.placeholder),
            ]
        )
        score = 1.0
        if any(token in blob for token in ("필터", "filter", "분류", "category", "정렬", "sort", "학점", "credit")):
            score += 2.0
        if any(token in goal_norm for token in ("필터", "filter", "학점", "credit")):
            score += 1.5
        if any("학점" in str(opt.get("text") or "") for opt in options if isinstance(opt, dict)):
            score += 2.5
        if best is None or score > best[0]:
            best = (score, el)
    return best[1] if best else None


def _collect_option_cases(control: DOMElement) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    options = control.options if isinstance(control.options, list) else []
    for raw in options:
        if not isinstance(raw, dict):
            continue
        value = str(raw.get("value") or "").strip()
        text = str(raw.get("text") or "").strip()
        key = f"{value}|{text}"
        if not value or key in seen:
            continue
        seen.add(key)
        lowered = _normalize(f"{value} {text}")
        if any(token in lowered for token in ("전체", "all", "선택", "default")):
            continue
        out.append({"value": value, "text": text})
    return out


def _selection_reflected(control: DOMElement, selected_value: str, selected_text: str) -> Tuple[bool, str]:
    current_value = str(control.selected_value or "").strip()
    if current_value and current_value == selected_value:
        return True, f"selected_value={current_value}"
    if selected_text:
        text_norm = _normalize(control.text)
        if _normalize(selected_text) and _normalize(selected_text) in text_norm:
            return True, f"text_match={selected_text}"
    options = control.options if isinstance(control.options, list) else []
    if current_value and options:
        for item in options:
            if not isinstance(item, dict):
                continue
            if str(item.get("value") or "").strip() == current_value:
                option_text = str(item.get("text") or "").strip()
                if selected_text and _normalize(option_text) == _normalize(selected_text):
                    return True, f"selected_option_text={option_text}"
    observed = current_value or (control.text or "")
    return False, f"observed={observed[:80]}"


def _pick_rule(goal_text: str, control: DOMElement, option_text: str, option_value: str) -> FilterRule:
    rules: List[FilterRule] = [CreditFilterRule(), GenericOptionTokenRule()]
    for rule in rules:
        if rule.supports(
            goal_text=goal_text,
            control=control,
            option_text=option_text,
            option_value=option_value,
        ):
            return rule
    return GenericOptionTokenRule()


def _collect_result_rows(dom: List[DOMElement]) -> List[str]:
    rows: List[str] = []
    seen: set[str] = set()
    for el in dom:
        text = str(el.text or "").strip()
        if len(text) < 4:
            continue
        tag = _normalize(el.tag)
        role = _normalize(el.role)
        cls = _normalize(el.class_name)
        is_row_like = (
            tag in {"tr", "li", "article"}
            or role in {"row", "listitem"}
            or any(tok in cls for tok in ("row", "item", "card", "subject", "course", "lecture"))
        )
        if not is_row_like:
            continue
        key = text[:160]
        if key in seen:
            continue
        seen.add(key)
        rows.append(text)
    if not rows:
        for el in dom:
            text = str(el.text or "").strip()
            if len(text) >= 8 and any(tok in text for tok in ("학점", "교과", "강의", "subject", "course")):
                key = text[:160]
                if key not in seen:
                    seen.add(key)
                    rows.append(text)
    return rows[:60]


def _pick_next_pagination(dom: List[DOMElement]) -> Optional[DOMElement]:
    candidates: List[Tuple[float, DOMElement]] = []
    for el in dom:
        if not bool(el.is_visible) or not bool(el.is_enabled):
            continue
        tag = _normalize(el.tag)
        role = _normalize(el.role)
        if tag not in {"a", "button"} and role not in {"button", "link", "tab"}:
            continue
        blob = _normalize(" ".join([el.text or "", el.aria_label or "", el.title or "", el.class_name or ""]))
        if not blob:
            continue
        score = 0.0
        if any(tok in blob for tok in ("다음", "next", "다음페이지", "next page")):
            score += 3.0
        if any(tok in blob for tok in ("›", "»", ">", "arrow-right", "chevron-right")):
            score += 1.5
        if any(tok in blob for tok in ("이전", "prev", "previous", "back")):
            score -= 3.0
        if "page" in blob or "pagination" in blob:
            score += 0.8
        if score > 0.5:
            candidates.append((score, el))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else None


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _tokenize(value: str) -> List[str]:
    stop = {"전체", "all", "select", "선택", "default", "option", "옵션"}
    raw = re.findall(r"[0-9a-zA-Z가-힣_]+", _normalize(value))
    return [tok for tok in raw if len(tok) >= 2 and tok not in stop]


def _extract_credit(value: str) -> Optional[int]:
    text = str(value or "")
    m = re.search(r"(\d{1,2})\s*학점", text)
    if m:
        return int(m.group(1))
    m2 = re.fullmatch(r"\s*(\d{1,2})\s*", text)
    if m2:
        return int(m2.group(1))
    return None


def _extract_row_credits(text: str) -> List[int]:
    if not text:
        return []
    out: List[int] = []
    for m in re.finditer(r"(\d{1,2})\s*학점", text):
        try:
            out.append(int(m.group(1)))
        except Exception:
            continue
    return out

