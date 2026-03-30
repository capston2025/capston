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

DEFAULT_FILTER_VALIDATION_PROFILE: Dict[str, Any] = {
    "max_pages": 2,
    "max_cases": 3,
    "strict_mandatory": True,
    "page2_strict": False,
    "page1_sample_n": 8,
    "page1_match_ratio": 0.60,
    "selection_probe_schedule_ms": [200, 500, 1000, 1500, 1800],
    "pagination_persistence_page2_topk": 5,
    "pagination_persistence_page2_min_match": 1,
    "capture_case_screenshots": True,
}


def build_filter_validation_config(**overrides: Any) -> Dict[str, Any]:
    config = dict(DEFAULT_FILTER_VALIDATION_PROFILE)
    for key, value in overrides.items():
        config[key] = value
    return config


@dataclass
class FilterCheckRow:
    check_id: str
    name: str
    status: str  # pass | fail | skipped_not_applicable | skipped_error | skipped_timeout
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
        normalized_status = _normalize_check_status(self.status)
        return {
            "check_id": self.check_id,
            "name": self.name,
            "status": normalized_status,
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
    skipped_mandatory_checks: int
    success_rate: float
    strict_failed: bool
    goal_satisfied: bool
    required_option_count: int
    covered_option_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_type": self.goal_type,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "skipped_checks": self.skipped_checks,
            "failed_mandatory_checks": self.failed_mandatory_checks,
            "skipped_mandatory_checks": self.skipped_mandatory_checks,
            "success_rate": self.success_rate,
            "strict_failed": self.strict_failed,
            "goal_satisfied": self.goal_satisfied,
            "required_option_count": self.required_option_count,
            "covered_option_count": self.covered_option_count,
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
    attachments: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "success": self.success,
            "goal_satisfied": self.summary.goal_satisfied,
            "summary": self.summary.to_dict(),
            "checks": list(self.checks),
            "rules_used": list(self.rules_used),
            "pages_checked": self.pages_checked,
            "cases": list(self.cases),
            "failed_mandatory_count": int(self.summary.failed_mandatory_checks),
            "reason_code_summary": dict(self.reason_code_summary),
            "attachments": list(self.attachments or []),
        }


class FilterValidationAdapter(Protocol):
    def analyze_dom(self) -> List[DOMElement]:
        ...

    def apply_select(self, element_id: int, value: str) -> Dict[str, Any]:
        ...

    def click_element(self, element_id: int) -> Dict[str, Any]:
        ...

    def scroll_for_pagination(self, anchor_element_id: int) -> Dict[str, Any]:
        ...

    def wait_for_pagination_probe(self, wait_ms: int = 900) -> Dict[str, Any]:
        ...

    def reload_page(self, wait_ms: int = 900) -> Dict[str, Any]:
        ...

    def resolve_ref(self, element_id: int) -> str:
        ...

    def current_url(self) -> str:
        ...

    def record_reason(self, code: str) -> None:
        ...

    def log(self, message: str) -> None:
        ...

    def capture_case_attachment(self, label: str) -> Optional[Dict[str, Any]]:
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

    def __init__(self, match_ratio: float = 0.60) -> None:
        try:
            ratio = float(match_ratio)
        except Exception:
            ratio = 0.60
        self._match_ratio = max(0.10, min(1.0, ratio))

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
        sampled_rows = 0
        matched_rows = 0
        mismatch_rows = 0
        mismatch_examples: List[str] = []
        for row in row_texts:
            if _is_noise_row_for_credit(row):
                continue
            row_credits = _extract_row_credits(row)
            if not row_credits:
                continue
            sampled_rows += 1
            extracted.extend(row_credits)
            if all(v == target for v in row_credits):
                matched_rows += 1
            else:
                mismatch_rows += 1
                if len(mismatch_examples) < 3:
                    mismatch_examples.append(row[:120])

        if sampled_rows == 0:
            return (
                False,
                "페이지 결과에서 학점 표본을 찾지 못했습니다.",
                {"target": target, "rows_with_credit": 0, "row_total": len(row_texts)},
            )

        required_matches = max(1, int((sampled_rows * self._match_ratio) + 0.999))
        if sampled_rows >= 3:
            required_matches = max(required_matches, 2)
        pass_ratio = float(matched_rows) / float(sampled_rows) if sampled_rows > 0 else 0.0
        if matched_rows < required_matches:
            return (
                False,
                (
                    "학점 정합성 기준 미달: "
                    f"{matched_rows}/{sampled_rows} 매칭(요구 {required_matches}, 비율 {pass_ratio:.2f})"
                ),
                {
                    "target": target,
                    "rows_with_credit": sampled_rows,
                    "matched_rows": matched_rows,
                    "required_matches": required_matches,
                    "pass_ratio": round(pass_ratio, 3),
                    "mismatch_rows": mismatch_rows,
                    "mismatch_examples": mismatch_examples,
                    "observed_credits": sorted(set(extracted)),
                },
            )

        return (
            True,
            (
                "학점 표본 기준 통과: "
                f"{matched_rows}/{sampled_rows} 매칭(요구 {required_matches})"
            ),
            {
                "target": target,
                "rows_with_credit": sampled_rows,
                "matched_rows": matched_rows,
                "required_matches": required_matches,
                "pass_ratio": round(pass_ratio, 3),
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
    cfg = build_filter_validation_config(**dict(config or {}))
    max_cases = max(1, int(cfg.get("max_cases", DEFAULT_FILTER_VALIDATION_PROFILE["max_cases"])))
    max_pages = max(1, int(cfg.get("max_pages", DEFAULT_FILTER_VALIDATION_PROFILE["max_pages"])))
    strict_mandatory = bool(cfg.get("strict_mandatory", DEFAULT_FILTER_VALIDATION_PROFILE["strict_mandatory"]))
    page2_strict = bool(cfg.get("page2_strict", DEFAULT_FILTER_VALIDATION_PROFILE["page2_strict"]))
    page1_sample_n = max(3, int(cfg.get("page1_sample_n", DEFAULT_FILTER_VALIDATION_PROFILE["page1_sample_n"])))
    try:
        page1_match_ratio = float(cfg.get("page1_match_ratio", DEFAULT_FILTER_VALIDATION_PROFILE["page1_match_ratio"]))
    except Exception:
        page1_match_ratio = float(DEFAULT_FILTER_VALIDATION_PROFILE["page1_match_ratio"])
    page1_match_ratio = max(0.10, min(1.0, page1_match_ratio))
    selection_probe_schedule = cfg.get("selection_probe_schedule_ms")
    if not isinstance(selection_probe_schedule, list) or not selection_probe_schedule:
        selection_probe_schedule = list(DEFAULT_FILTER_VALIDATION_PROFILE["selection_probe_schedule_ms"])
    selection_probe_schedule = [
        max(100, int(v))
        for v in selection_probe_schedule
        if str(v).strip()
    ][:8] or [200, 500, 1000, 1500, 1800]
    page2_topk = max(1, int(cfg.get("pagination_persistence_page2_topk", DEFAULT_FILTER_VALIDATION_PROFILE["pagination_persistence_page2_topk"])))
    page2_min_match = max(1, int(cfg.get("pagination_persistence_page2_min_match", DEFAULT_FILTER_VALIDATION_PROFILE["pagination_persistence_page2_min_match"])))
    use_current_selection_only = bool(cfg.get("use_current_selection_only", False))
    forced_selected_value = str(cfg.get("forced_selected_value") or "").strip()
    validation_contract = cfg.get("validation_contract")
    preferred_control_hint = cfg.get("preferred_control_hint") if isinstance(cfg.get("preferred_control_hint"), dict) else {}
    capture_case_screenshots = bool(cfg.get("capture_case_screenshots", DEFAULT_FILTER_VALIDATION_PROFILE["capture_case_screenshots"]))
    max_case_attachments = max(0, int(cfg.get("max_case_attachments", max_cases)))

    reason_counter: Dict[str, int] = {}
    checks: List[FilterCheckRow] = []
    cases: List[Dict[str, Any]] = []
    rules_used: List[str] = []
    pages_checked = 1
    attachments: List[Dict[str, Any]] = []

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

    def _capture_case_attachment(case_no: int, selected_label: str) -> None:
        if not capture_case_screenshots:
            return
        if max_case_attachments <= 0 or len(attachments) >= max_case_attachments:
            return
        try:
            shot = adapter.capture_case_attachment(
                f"필터 케이스 {case_no}: {selected_label}"
            )
        except Exception:
            shot = None
        if not isinstance(shot, dict):
            return
        kind = str(shot.get("kind") or "").strip().lower()
        if kind != "image_base64":
            return
        data = shot.get("data")
        if not isinstance(data, str) or not data.strip():
            return
        item = {
            "kind": "image_base64",
            "mime": str(shot.get("mime") or "image/png"),
            "data": data,
            "label": str(shot.get("label") or f"case_{case_no}"),
        }
        attachments.append(item)

    dom = adapter.analyze_dom()
    control = _pick_filter_control(dom, goal_text, preferred_control_hint=preferred_control_hint)
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
            required_option_count=0,
            covered_option_count=0,
            attachments=attachments,
        ).to_dict()

    options = _collect_option_cases(control)
    required_map: Dict[str, str] = _build_required_map_from_contract(validation_contract, options)
    if not required_map:
        required_map = _derive_required_options(goal_text, options)
    required_credit_set: set[int] = set()
    for rv, rt in required_map.items():
        credit = _extract_credit(rt) or _extract_credit(rv)
        if credit is not None:
            required_credit_set.add(int(credit))
    covered_required: set[str] = set()

    def _mark_coverage(selected_value: str, selected_text: str, passed: bool) -> None:
        if not passed:
            return
        selected_credit = _extract_credit(selected_text) or _extract_credit(selected_value)
        if selected_value in required_map:
            covered_required.add(selected_value)
            return
        if selected_credit is not None and int(selected_credit) in required_credit_set:
            # value 표현(예: "3" vs "3학점")이 달라도 학점 의미가 같으면 커버로 인정
            for rv, rt in required_map.items():
                rv_credit = _extract_credit(rt) or _extract_credit(rv)
                if rv_credit is not None and int(rv_credit) == int(selected_credit):
                    covered_required.add(rv)
                    return
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
            required_option_count=len(required_map),
            covered_option_count=0,
            attachments=attachments,
        ).to_dict()

    for case_idx, option in enumerate(options[:max_cases], start=1):
        selected_value = str(option.get("value") or "").strip()
        selected_text = str(option.get("text") or "").strip()
        if not selected_value:
            continue

        _record_reason("filter_case_started")
        dom_before_case = adapter.analyze_dom()
        control_for_case = _pick_filter_control_for_option(
            dom=dom_before_case,
            goal_text=goal_text,
            selected_value=selected_value,
            selected_text=selected_text,
            required_map=required_map,
            preferred_control_hint=preferred_control_hint,
        ) or control
        case_info: Dict[str, Any] = {
            "case_index": case_idx,
            "selected_value": selected_value,
            "selected_text": selected_text,
            "checks": [],
        }

        if not use_current_selection_only:
            apply_result = adapter.apply_select(control_for_case.id, selected_value)
            apply_ok = bool(apply_result.get("success")) and bool(apply_result.get("effective", True))
            if not apply_ok:
                # select는 비동기 반영으로 인해 reason_code=not_actionable로 떨어져도
                # 실제 selected_value가 반영되는 경우가 있어 후속 DOM으로 보정한다.
                dom_after_apply = adapter.analyze_dom()
                control_after_apply = _pick_filter_control_for_option(
                    dom=dom_after_apply,
                    goal_text=goal_text,
                    selected_value=selected_value,
                    selected_text=selected_text,
                    required_map=required_map,
                    preferred_control_hint=preferred_control_hint,
                )
                if control_after_apply is not None:
                    reflected_ok, reflected_obs = _selection_reflected(
                        control_after_apply,
                        selected_value,
                        selected_text,
                    )
                    if reflected_ok:
                        apply_ok = True
                        apply_result = {
                            **dict(apply_result or {}),
                            "success": True,
                            "effective": True,
                            "reason_code": str(apply_result.get("reason_code") or "ok")
                            + "|selection_reflected_fallback",
                            "reason": reflected_obs,
                        }
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_selection_apply",
                    name=f"필터 적용 실행(case {case_idx})",
                    status="passed" if apply_ok else "failed",
                    mandatory=False,
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

        page1_dom: List[DOMElement] = []
        control_page1: Optional[DOMElement] = None
        selected_ok = False
        selected_obs = ""
        for wait_ms in [0, *selection_probe_schedule]:
            if wait_ms > 0:
                try:
                    adapter.wait_for_pagination_probe(int(wait_ms))
                except Exception:
                    pass
            page1_dom = adapter.analyze_dom()
            control_page1 = _pick_filter_control_for_option(
                dom=page1_dom,
                goal_text=goal_text,
                selected_value=selected_value,
                selected_text=selected_text,
                required_map=required_map,
                preferred_control_hint=preferred_control_hint,
            ) or _pick_filter_control(page1_dom, goal_text, preferred_control_hint=preferred_control_hint) or control_for_case
            selected_ok, selected_obs = _selection_reflected(control_page1, selected_value, selected_text)
            if selected_ok:
                break
        if control_page1 is None:
            control_page1 = control_for_case
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_selection_reflected",
                name=f"필터 선택 상태 반영(case {case_idx})",
                status="pass" if selected_ok else "fail",
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

        active_rule = _pick_rule(
            goal_text,
            control_page1,
            selected_text,
            selected_value,
            page1_match_ratio=page1_match_ratio,
        )
        rules_used.append(active_rule.name)
        row_texts_page1 = _collect_result_rows(page1_dom)[:page1_sample_n]
        row_ok1, row_msg1, row_ev1 = active_rule.evaluate_rows(
            selected_text=selected_text,
            selected_value=selected_value,
            row_texts=row_texts_page1,
        )
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_result_consistency_page1",
                name=f"결과 정합성(page1, case {case_idx})",
                status="pass" if row_ok1 else "fail",
                mandatory=True,
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
        _capture_case_attachment(case_idx, selected_text or selected_value)
        if row_ok1:
            _record_reason("filter_case_passed")
        else:
            _record_reason("filter_result_mismatch")

        if max_pages <= 1:
            case_info["status"] = "pass" if (selected_ok and row_ok1) else "fail"
            _mark_coverage(selected_value, selected_text, case_info["status"] == "pass")
            cases.append(case_info)
            continue

        wait_probe_info: Dict[str, Any] = {}
        scroll_probe_info: Dict[str, Any] = {}
        next_el = _pick_next_pagination(page1_dom)
        if next_el is None:
            wait_result = adapter.wait_for_pagination_probe(900)
            wait_ok = bool(wait_result.get("success")) and bool(wait_result.get("effective", True))
            wait_probe_info = {
                "attempted": True,
                "success": bool(wait_result.get("success")),
                "effective": bool(wait_result.get("effective", False)),
                "reason_code": str(wait_result.get("reason_code") or ""),
                "reason": str(wait_result.get("reason") or ""),
            }
            if wait_ok:
                page1_dom_after_wait = adapter.analyze_dom()
                if page1_dom_after_wait:
                    page1_dom = page1_dom_after_wait
                    control_after_wait = _pick_filter_control(page1_dom, goal_text, preferred_control_hint=preferred_control_hint)
                    if control_after_wait is not None:
                        control_page1 = control_after_wait
                    next_el = _pick_next_pagination(page1_dom)
        if next_el is None:
            scroll_anchor = _pick_scroll_anchor(page1_dom) or control_page1
            scroll_result = adapter.scroll_for_pagination(scroll_anchor.id)
            scroll_ok = bool(scroll_result.get("success")) and bool(scroll_result.get("effective", True))
            scroll_probe_info = {
                "attempted": True,
                "anchor_id": int(scroll_anchor.id),
                "success": bool(scroll_result.get("success")),
                "effective": bool(scroll_result.get("effective", False)),
                "reason_code": str(scroll_result.get("reason_code") or ""),
                "reason": str(scroll_result.get("reason") or ""),
            }
            if scroll_ok:
                page1_dom_after_scroll = adapter.analyze_dom()
                if page1_dom_after_scroll:
                    page1_dom = page1_dom_after_scroll
                    control_after_scroll = _pick_filter_control(page1_dom, goal_text, preferred_control_hint=preferred_control_hint)
                    if control_after_scroll is not None:
                        control_page1 = control_after_scroll
                    next_el = _pick_next_pagination(page1_dom)
        if next_el is None:
            pagination_diag = _collect_pagination_diagnostics(page1_dom)
            pagination_diag["wait_probe"] = wait_probe_info or {"attempted": False}
            pagination_diag["scroll_probe"] = scroll_probe_info or {"attempted": False}
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_pagination_persistence",
                    name=f"페이지네이션 유지성(case {case_idx})",
                    status="skipped_not_applicable",
                    mandatory=False,
                    scope="page2",
                    check_type="pagination_persistence",
                    expected="다음 페이지 이동 후 선택 유지",
                    observed="페이지네이션 컨트롤 없음",
                    evidence=pagination_diag,
                    action="click",
                    input_value="다음 페이지",
                )
            )
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_result_consistency_page2",
                    name=f"결과 정합성(page2, case {case_idx})",
                    status="skipped_not_applicable",
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
            reload_result = adapter.reload_page(900)
            reload_ok = bool(reload_result.get("success")) and bool(reload_result.get("effective", True))
            reload_dom = adapter.analyze_dom() if reload_ok else []
            reload_blocked_code = _blocked_reason_code_from_result(reload_result) or _blocked_reason_code_from_dom(reload_dom)
            if reload_blocked_code:
                _record_reason("blocked_user_action")
                _record_reason(reload_blocked_code)
            reload_control = _pick_filter_control_for_option(
                dom=reload_dom,
                goal_text=goal_text,
                selected_value=selected_value,
                selected_text=selected_text,
                required_map=required_map,
                preferred_control_hint=preferred_control_hint,
            ) or _pick_filter_control(reload_dom, goal_text, preferred_control_hint=preferred_control_hint) or control_page1
            reload_selected_ok, reload_selected_obs = _selection_reflected(
                reload_control,
                selected_value,
                selected_text,
            )
            reload_row_ok, reload_row_msg, reload_row_ev = active_rule.evaluate_rows(
                selected_text=selected_text,
                selected_value=selected_value,
                row_texts=_collect_result_rows(reload_dom)[:page1_sample_n],
            ) if reload_dom else (False, "reload 후 결과 행을 수집하지 못했습니다.", {})
            reload_persistence_ok = bool(reload_ok and reload_selected_ok and reload_row_ok)
            _add_check(
                FilterCheckRow(
                    check_id=f"case_{case_idx}_reload_persistence",
                    name=f"리로드 유지성(case {case_idx})",
                    status="pass" if reload_persistence_ok else "fail",
                    mandatory=True,
                    scope="page2",
                    check_type="reload_persistence",
                    expected=f"{selected_text or selected_value} 유지 + page1 정합성 유지",
                    observed=reload_selected_obs if reload_selected_ok else str(reload_result.get("reason_code") or "reload_fail"),
                    evidence={
                        "reload_result": reload_result,
                        "blocked_reason_code": reload_blocked_code,
                        "selection_observed": reload_selected_obs,
                        "row_message": reload_row_msg,
                        "row_evidence": reload_row_ev,
                    },
                    action="navigate",
                    input_value="reload",
                    error="" if reload_persistence_ok else (
                        ("사용자 개입 필요: " + reload_blocked_code)
                        if reload_blocked_code
                        else (reload_row_msg or str(reload_result.get("reason") or "reload persistence failed"))
                    ),
                )
            )
            if not reload_persistence_ok:
                _record_reason("filter_reload_persistence_failed")
            else:
                _record_reason("filter_reload_persistence_passed")
            case_info["status"] = "pass" if (selected_ok and row_ok1 and reload_persistence_ok) else "fail"
            _mark_coverage(selected_value, selected_text, case_info["status"] == "pass")
            cases.append(case_info)
            continue

        pages_checked = max(pages_checked, 2)
        click_result = adapter.click_element(next_el.id)
        click_ok = bool(click_result.get("success")) and bool(click_result.get("effective", True))
        page2_dom = adapter.analyze_dom() if click_ok else []
        click_blocked_code = _blocked_reason_code_from_result(click_result) or _blocked_reason_code_from_dom(page2_dom)
        if click_blocked_code:
            _record_reason("blocked_user_action")
            _record_reason(click_blocked_code)
        control_page2 = _pick_filter_control(page2_dom, goal_text, preferred_control_hint=preferred_control_hint) if page2_dom else None
        persisted_ok = bool(click_ok and control_page2 and _selection_reflected(control_page2, selected_value, selected_text)[0])
        page2_row_texts_for_persist = _collect_result_rows(page2_dom)[:page2_topk] if page2_dom else []
        page2_weak_ok = _page2_min_match_ok(
            rule=active_rule,
            selected_text=selected_text,
            selected_value=selected_value,
            row_texts=page2_row_texts_for_persist,
            min_match=page2_min_match,
        ) if persisted_ok else False
        persistence_ok = bool(persisted_ok and page2_weak_ok)
        _add_check(
            FilterCheckRow(
                check_id=f"case_{case_idx}_pagination_persistence",
                name=f"페이지네이션 유지성(case {case_idx})",
                status="pass" if persistence_ok else "fail",
                mandatory=True,
                scope="page2",
                check_type="pagination_persistence",
                expected=f"{selected_text or selected_value} 유지",
                observed=str(click_result.get("reason_code") or ("ok" if persistence_ok else "fail")),
                evidence={
                    "from_url": click_result.get("before_url", ""),
                    "to_url": adapter.current_url(),
                    "next_ref": adapter.resolve_ref(next_el.id),
                    "blocked_reason_code": click_blocked_code,
                    "persisted_ok": persisted_ok,
                    "page2_weak_ok": page2_weak_ok,
                    "page2_sample_topk": page2_topk,
                },
                action="click",
                input_value="다음 페이지",
                error="" if persistence_ok else (
                    ("사용자 개입 필요: " + click_blocked_code)
                    if click_blocked_code
                    else str(click_result.get("reason") or "선택 유지/약한 정합성 실패")
                ),
            )
        )

        row_ok2 = False
        row_msg2 = "page2 미검증"
        row_ev2: Dict[str, Any] = {}
        if persistence_ok and page2_dom:
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
                status="pass" if row_ok2 else "fail",
                mandatory=bool(page2_strict),
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
        if not persistence_ok:
            _record_reason("filter_persistence_lost")
        if not row_ok2:
            _record_reason("filter_result_mismatch")

        mandatory_failed = any(
            row.mandatory and _normalize_check_status(row.status) == "fail"
            for row in checks
            if row.check_id.startswith(f"case_{case_idx}_")
        )
        mandatory_skipped = any(
            row.mandatory and _normalize_check_status(row.status).startswith("skipped")
            for row in checks
            if row.check_id.startswith(f"case_{case_idx}_")
        )
        case_info["status"] = "fail" if (mandatory_failed or mandatory_skipped) else "pass"
        _mark_coverage(selected_value, selected_text, case_info["status"] == "pass")
        if case_info["status"] == "pass":
            _record_reason("filter_case_passed")
        else:
            _record_reason("filter_case_failed")
        cases.append(case_info)

    missing_required = [val for val in required_map.keys() if val not in covered_required]
    if missing_required:
        _record_reason("filter_goal_incomplete")
        _add_check(
            FilterCheckRow(
                check_id="goal_option_coverage",
                name="목표 옵션 커버리지",
                status="skipped_not_applicable",
                mandatory=False,
                scope="global",
                check_type="goal_coverage",
                expected=f"{len(required_map)}개 옵션 검증 완료",
                observed=f"{len(covered_required)}/{len(required_map)} 완료",
                evidence={
                    "required": [{"value": v, "text": required_map.get(v, "")} for v in required_map.keys()],
                    "covered": sorted(list(covered_required)),
                    "missing": missing_required,
                },
            )
        )
    else:
        _add_check(
            FilterCheckRow(
                check_id="goal_option_coverage",
                name="목표 옵션 커버리지",
                status="pass",
                mandatory=False,
                scope="global",
                check_type="goal_coverage",
                expected=f"{len(required_map)}개 옵션 검증 완료",
                observed=f"{len(covered_required)}/{len(required_map)} 완료",
                evidence={
                    "required": [{"value": v, "text": required_map.get(v, "")} for v in required_map.keys()],
                    "covered": sorted(list(covered_required)),
                    "missing": [],
                },
            )
        )

    report = _build_report(
        checks=checks,
        cases=cases,
        rules_used=rules_used,
        pages_checked=pages_checked,
        reason_counter=reason_counter,
        strict_mandatory=strict_mandatory,
        required_option_count=len(required_map),
        covered_option_count=len(covered_required),
        attachments=attachments,
    )
    report_dict = report.to_dict()
    report_dict["required_options"] = [{"value": k, "text": v} for k, v in required_map.items()]
    report_dict["missing_required_options"] = [
        {"value": v, "text": required_map.get(v, "")}
        for v in missing_required
    ]
    report_dict["contract_used"] = bool(isinstance(validation_contract, dict))
    return report_dict


def _build_report(
    *,
    checks: List[FilterCheckRow],
    cases: List[Dict[str, Any]],
    rules_used: List[str],
    pages_checked: int,
    reason_counter: Dict[str, int],
    strict_mandatory: bool,
    required_option_count: int,
    covered_option_count: int,
    attachments: List[Dict[str, Any]],
) -> FilterValidationReport:
    rows = [row.to_dict(step=i + 1) for i, row in enumerate(checks)]
    total = len(rows)
    passed = sum(1 for r in rows if str(r.get("status")) == "pass")
    failed = sum(1 for r in rows if str(r.get("status")) == "fail")
    skipped = sum(1 for r in rows if str(r.get("status")).startswith("skipped"))
    failed_mandatory = sum(
        1
        for r in rows
        if str(r.get("status")) == "fail" and bool(r.get("mandatory"))
    )
    skipped_mandatory = sum(
        1
        for r in rows
        if str(r.get("status")).startswith("skipped") and bool(r.get("mandatory"))
    )
    success_rate = round((passed / total) * 100, 1) if total > 0 else 0.0
    strict_failed = bool(strict_mandatory and (failed_mandatory > 0 or skipped_mandatory > 0))
    goal_satisfied = bool(
        (not strict_failed)
        and int(required_option_count) > 0
        and int(covered_option_count) >= int(required_option_count)
    )
    summary = FilterValidationSummary(
        goal_type="filter_validation_semantic",
        total_checks=total,
        passed_checks=passed,
        failed_checks=failed,
        skipped_checks=skipped,
        failed_mandatory_checks=failed_mandatory,
        skipped_mandatory_checks=skipped_mandatory,
        success_rate=success_rate,
        strict_failed=strict_failed,
        goal_satisfied=goal_satisfied,
        required_option_count=int(required_option_count),
        covered_option_count=int(covered_option_count),
    )
    return FilterValidationReport(
        mode="filter_semantic_v2",
        success=bool(goal_satisfied and not strict_failed),
        summary=summary,
        checks=rows,
        rules_used=sorted(set(rules_used)),
        pages_checked=max(1, int(pages_checked)),
        cases=cases,
        reason_code_summary=dict(reason_counter),
        attachments=list(attachments or []),
    )


def _pick_filter_control(
    dom: List[DOMElement],
    goal_text: str,
    *,
    preferred_control_hint: Optional[Dict[str, Any]] = None,
) -> Optional[DOMElement]:
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
                _normalize(el.container_name),
                _normalize(el.context_text),
            ]
        )
        score = 1.0
        score += _preferred_control_match_score(el, preferred_control_hint or {})
        if any(token in blob for token in ("필터", "filter", "분류", "category", "정렬", "sort")):
            score += 2.0
        if any(token in goal_norm for token in ("필터", "filter", "분류", "category", "정렬", "sort", "semantic", "의미", "일치", "consisten")):
            score += 1.5
        if any(token in goal_norm for token in ("결과", "목록", "search", "검색", "result", "course")):
            if any(token in blob for token in ("검색", "search", "결과", "목록", "course")):
                score += 6.0
            if any(token in blob for token in ("위시리스트", "wishlist", "내 시간표", "시간표", "목표", "target", "recommended", "권장")):
                score -= 10.0
        if best is None or score > best[0]:
            best = (score, el)
    return best[1] if best else None


def _normalize_check_status(status: Any) -> str:
    token = str(status or "").strip().lower()
    if token in {"pass", "passed", "ok", "success"}:
        return "pass"
    if token in {"fail", "failed", "error"}:
        return "fail"
    if token in {"skipped_not_applicable", "skip_not_applicable"}:
        return "skipped_not_applicable"
    if token in {"skipped_error", "skip_error"}:
        return "skipped_error"
    if token in {"skipped_timeout", "skip_timeout", "timeout"}:
        return "skipped_timeout"
    if token in {"skipped", "skip"}:
        return "skipped_not_applicable"
    return "skipped_error"


def _page2_min_match_ok(
    *,
    rule: FilterRule,
    selected_text: str,
    selected_value: str,
    row_texts: List[str],
    min_match: int,
) -> bool:
    top_rows = list(row_texts or [])
    if not top_rows:
        return False
    min_match = max(1, int(min_match))
    if isinstance(rule, CreditFilterRule):
        target = _extract_credit(selected_text) or _extract_credit(selected_value)
        if target is None:
            return False
        matched = 0
        for row in top_rows:
            credits = _extract_row_credits(row)
            if not credits:
                continue
            if all(v == target for v in credits):
                matched += 1
            if matched >= min_match:
                return True
        return False

    tokens = _tokenize(selected_text or selected_value)
    if not tokens:
        return False
    matched = 0
    for row in top_rows:
        row_norm = _normalize(row)
        if all(tok in row_norm for tok in tokens[:2]):
            matched += 1
        if matched >= min_match:
            return True
    return False


def _blocked_reason_code_from_result(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    code = _normalize(str(result.get("reason_code") or ""))
    reason = _normalize(str(result.get("reason") or ""))
    if code in {"auth_required", "login_required", "captcha_detected", "2fa_required", "permission_prompt_detected", "blocked_timeout"}:
        return code
    if any(tok in f"{code} {reason}" for tok in ("captcha", "2fa", "auth required", "login required", "permission prompt")):
        if "captcha" in f"{code} {reason}":
            return "captcha_detected"
        if "2fa" in f"{code} {reason}":
            return "2fa_required"
        if "permission" in f"{code} {reason}":
            return "permission_prompt_detected"
        return "auth_required"
    return ""


def _blocked_reason_code_from_dom(dom: List[DOMElement]) -> str:
    if not isinstance(dom, list) or not dom:
        return ""
    blob = " ".join(
        _normalize(
            " ".join(
                [
                    str(el.text or ""),
                    str(el.aria_label or ""),
                    str(el.title or ""),
                    str(el.class_name or ""),
                ]
            )
        )
        for el in dom[:200]
        if isinstance(el, DOMElement)
    )
    if not blob:
        return ""
    if any(tok in blob for tok in ("captcha", "recaptcha", "로봇이 아닙니다", "보안 문자")):
        return "captcha_detected"
    if any(tok in blob for tok in ("2fa", "otp", "인증 코드", "verification code")):
        return "2fa_required"
    if any(tok in blob for tok in ("로그인 필요", "login required", "sign in required", "authentication required")):
        return "login_required"
    if any(tok in blob for tok in ("permission", "권한 허용", "allow location", "allow notifications")):
        return "permission_prompt_detected"
    return ""


def _pick_filter_control_for_option(
    *,
    dom: List[DOMElement],
    goal_text: str,
    selected_value: str,
    selected_text: str,
    required_map: Dict[str, str],
    preferred_control_hint: Optional[Dict[str, Any]] = None,
) -> Optional[DOMElement]:
    goal_norm = _normalize(goal_text)
    selected_value_norm = _normalize(selected_value)
    selected_text_norm = _normalize(selected_text)
    required_values = {_normalize(k) for k in required_map.keys() if str(k or "").strip()}
    required_texts = {_normalize(v) for v in required_map.values() if str(v or "").strip()}
    best: Optional[Tuple[float, DOMElement]] = None
    for el in dom:
        if _normalize(el.tag) != "select":
            continue
        options = el.options if isinstance(el.options, list) else []
        if len(options) < 2:
            continue
        if not bool(el.is_visible) or not bool(el.is_enabled):
            continue
        value_set: set[str] = set()
        text_set: set[str] = set()
        for item in options:
            if not isinstance(item, dict):
                continue
            value_set.add(_normalize(item.get("value")))
            text_set.add(_normalize(item.get("text")))
        if not value_set and not text_set:
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
        score = 0.0
        score += _preferred_control_match_score(el, preferred_control_hint or {})
        if any(token in blob for token in ("필터", "filter", "분류", "category", "정렬", "sort")):
            score += 2.0
        if any(token in goal_norm for token in ("필터", "filter", "분류", "category", "정렬", "sort")):
            score += 1.2
        if selected_value_norm and selected_value_norm in value_set:
            score += 4.5
        if selected_text_norm and selected_text_norm in text_set:
            score += 4.5
        if required_values:
            score += 1.2 * float(len(value_set & required_values))
        if required_texts:
            score += 1.2 * float(len(text_set & required_texts))
        if best is None or score > best[0]:
            best = (score, el)
    return best[1] if best else None


def _preferred_control_match_score(element: DOMElement, preferred_control_hint: Dict[str, Any]) -> float:
    if not preferred_control_hint:
        return 0.0

    score = 0.0
    hint_ref = str(preferred_control_hint.get("ref_id") or "").strip()
    element_ref = str(getattr(element, "ref_id", "") or "").strip()
    if hint_ref and element_ref and hint_ref == element_ref:
        score += 200.0

    hint_container = _normalize(preferred_control_hint.get("container_name"))
    element_container = _normalize(getattr(element, "container_name", ""))
    if hint_container and element_container and hint_container == element_container:
        score += 18.0

    hint_context = _normalize(preferred_control_hint.get("context_text"))
    element_context = _normalize(getattr(element, "context_text", ""))
    if hint_context and element_context and hint_context == element_context:
        score += 14.0

    hint_role_ref = _normalize(preferred_control_hint.get("role_ref_name"))
    element_role_ref = _normalize(getattr(element, "role_ref_name", ""))
    if hint_role_ref and element_role_ref and hint_role_ref == element_role_ref:
        score += 16.0

    hint_selected = _normalize(preferred_control_hint.get("selected_value"))
    element_selected = _normalize(getattr(element, "selected_value", ""))
    if hint_selected and element_selected and hint_selected == element_selected:
        score += 10.0

    hint_signature = [
        _normalize(token)
        for token in list(preferred_control_hint.get("option_signature") or [])
        if str(token or "").strip()
    ][:16]
    element_signature = [
        _normalize(str(item.get("text") or item.get("value") or ""))
        for item in list(getattr(element, "options", None) or [])
        if isinstance(item, dict) and str(item.get("text") or item.get("value") or "").strip()
    ][:16]
    if hint_signature and element_signature:
        if hint_signature == element_signature:
            score += 80.0
        else:
            overlap = len(set(hint_signature).intersection(set(element_signature)))
            score += min(42.0, float(overlap) * 4.0)

    element_blob = _normalize(
        " ".join(
            [
                str(getattr(element, "text", "") or ""),
                str(getattr(element, "aria_label", "") or ""),
                str(getattr(element, "title", "") or ""),
                str(getattr(element, "class_name", "") or ""),
                str(getattr(element, "placeholder", "") or ""),
                str(getattr(element, "container_name", "") or ""),
                str(getattr(element, "context_text", "") or ""),
                " ".join(element_signature[:8]),
            ]
        )
    )
    include_terms = [
        _normalize(token)
        for token in list(preferred_control_hint.get("include_terms") or [])
        if str(token or "").strip()
    ]
    exclude_terms = [
        _normalize(token)
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


def _derive_required_options(goal_text: str, options: List[Dict[str, str]]) -> Dict[str, str]:
    required: Dict[str, str] = {}
    goal_norm = _normalize(goal_text)
    if not options:
        return required

    # explicit target values in goal text (e.g., "1,2,3 학점")
    # 케이스 1) "1학점 2학점 3학점"
    explicit_credits = {int(m.group(1)) for m in re.finditer(r"(\d{1,2})\s*학점", goal_text or "")}
    # 케이스 2) "1,2,3 학점", "1/2/3 학점"
    for seq in re.finditer(r"((?:\d{1,2}\s*[,/]\s*)+\d{1,2})\s*학점", goal_text or ""):
        chunk = str(seq.group(1) or "")
        for n in re.findall(r"\d{1,2}", chunk):
            try:
                explicit_credits.add(int(n))
            except Exception:
                continue
    if not explicit_credits:
        explicit_credits = {int(m.group(1)) for m in re.finditer(r"(?<!\d)([1-9]|1\d|2\d)(?!\d)", goal_text or "")}

    for item in options:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        text = str(item.get("text") or "").strip()
        if not value:
            continue
        if explicit_credits:
            credit = _extract_credit(text) or _extract_credit(value)
            if credit is not None and credit in explicit_credits:
                required[value] = text

    if required:
        return required

    # credit filter goal이면 발견된 학점 옵션 전체를 요구
    if "학점" in goal_norm or "credit" in goal_norm:
        for item in options:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            text = str(item.get("text") or "").strip()
            if value and (_extract_credit(text) is not None or _extract_credit(value) is not None):
                required[value] = text
        if required:
            return required

    # generic fallback: 기본적으로 현재 옵션 풀 전체 검증
    for item in options:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        text = str(item.get("text") or "").strip()
        if value:
            required[value] = text
    return required


def _build_required_map_from_contract(
    contract: Any,
    options: List[Dict[str, str]],
) -> Dict[str, str]:
    if not isinstance(contract, dict):
        return {}
    raw_required = contract.get("required_options")
    if not isinstance(raw_required, list):
        return {}
    option_rows: List[Tuple[str, str]] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        text = str(item.get("text") or "").strip()
        if value:
            option_rows.append((value, text))
    if not option_rows:
        return {}

    out: Dict[str, str] = {}
    for item in raw_required:
        if not isinstance(item, dict):
            continue
        req_val = str(item.get("value") or "").strip()
        req_text = str(item.get("text") or "").strip()
        req_credit = _extract_credit(str(item.get("credit") or "")) or _extract_credit(req_text) or _extract_credit(req_val)

        # 1) value exact match
        if req_val:
            for val, txt in option_rows:
                if val == req_val:
                    out[val] = txt
                    break
            if req_val in out:
                continue

        # 2) text exact/contains match
        if req_text:
            req_norm = _normalize(req_text)
            for val, txt in option_rows:
                txt_norm = _normalize(txt)
                if txt_norm == req_norm or req_norm in txt_norm or txt_norm in req_norm:
                    out[val] = txt
                    break
            if any(_normalize(v) == req_norm for v in out.values()):
                continue

        # 3) semantic credit match
        if req_credit is not None:
            for val, txt in option_rows:
                credit = _extract_credit(txt) or _extract_credit(val)
                if credit is not None and int(credit) == int(req_credit):
                    out[val] = txt
                    break

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


def _openclaw_result_row_score(el: DOMElement) -> float:
    text = str(getattr(el, "text", "") or "").strip()
    if len(text) < 8:
        return -1.0
    tag = _normalize(getattr(el, "tag", ""))
    role = _normalize(getattr(el, "role", ""))
    container_role = _normalize(getattr(el, "container_role", ""))
    source = _normalize(getattr(el, "container_source", ""))
    if source != "openclaw-role-tree":
        return -1.0
    if tag in {"button", "a", "input", "select", "option"}:
        return -1.0
    if role in {"button", "link", "textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "option"}:
        return -1.0
    if container_role in {"banner", "navigation", "complementary", "button"}:
        return -1.0

    score = 0.0
    if role in {"generic", "paragraph", "article", "listitem", "row", "cell", "gridcell"}:
        score += 1.5
    try:
        if float(getattr(el, "context_score_hint", 0.0) or 0.0) >= 8.0:
            score += 3.0
    except Exception:
        pass
    if container_role == "main":
        score += 4.0
    elif container_role in {"list", "grid", "rowgroup", "region", "group"}:
        score += 2.0

    container_blob = _normalize(
        " ".join(
            [
                str(getattr(el, "container_name", "") or ""),
                str(getattr(el, "context_text", "") or ""),
            ]
        )
    )
    if any(token in container_blob for token in ("검색 결과", "search result", "search results", "result list", "results")):
        score += 3.0
    if "|" in text or "•" in text or "\n" in text:
        score += 1.5
    if _extract_row_credits(text):
        score += 2.5

    norm_text = _normalize(text)
    if any(token in norm_text for token in ("위시리스트", "목표 학점", "로그인", "로그아웃")):
        score -= 4.0
    if "총 " in norm_text and "학점" in norm_text and "강의" not in norm_text and "교과" not in norm_text:
        score -= 3.0
    return score


def _pick_rule(
    goal_text: str,
    control: DOMElement,
    option_text: str,
    option_value: str,
    *,
    page1_match_ratio: float = 0.60,
) -> FilterRule:
    rules: List[FilterRule] = [CreditFilterRule(match_ratio=page1_match_ratio), GenericOptionTokenRule()]
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
    openclaw_candidates: List[Tuple[str, float, str]] = []
    for el in dom:
        if not isinstance(el, DOMElement):
            continue
        score = _openclaw_result_row_score(el)
        if score < 5.0:
            continue
        text = str(el.text or "").strip()
        container_key = _normalize(getattr(el, "container_name", "") or "") or _normalize(getattr(el, "container_role", "") or "") or "__default__"
        openclaw_candidates.append((container_key, score, text))

    if openclaw_candidates:
        container_stats: Dict[str, Dict[str, float]] = {}
        for container_key, score, _text in openclaw_candidates:
            bucket = container_stats.setdefault(container_key, {"count": 0.0, "score_sum": 0.0, "score_max": 0.0})
            bucket["count"] += 1.0
            bucket["score_sum"] += float(score)
            bucket["score_max"] = max(float(bucket["score_max"]), float(score))
        best_container = max(
            container_stats.items(),
            key=lambda item: (item[1]["count"], item[1]["score_sum"], item[1]["score_max"], item[0]),
        )[0]
        rows: List[str] = []
        seen: set[str] = set()
        for container_key, _score, text in openclaw_candidates:
            if container_key != best_container:
                continue
            key = text[:160]
            if key in seen:
                continue
            seen.add(key)
            rows.append(text)
        if rows:
            return rows[:60]

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
    numeric_candidates: List[Tuple[int, float, DOMElement]] = []
    icon_geo_candidates: List[Tuple[float, DOMElement]] = []
    current_numeric_pages: set[int] = set()
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

        # Fallback: 숫자 페이지 버튼(예: 1,2,3) 기반 추론
        raw_text = str(el.text or "").strip()
        m = re.fullmatch(r"\s*(\d{1,3})\s*", raw_text)
        if m:
            try:
                page_num = int(m.group(1))
            except Exception:
                page_num = -1
            if page_num > 0:
                local_score = 0.5
                if "page" in blob or "pagination" in blob:
                    local_score += 1.0
                numeric_candidates.append((page_num, local_score, el))
                if any(tok in blob for tok in ("active", "current", "selected", "aria-current", "현재", "선택")):
                    current_numeric_pages.add(page_num)

        # Icon-only pagination fallback (mobile/compact UIs)
        box = el.bounding_box if isinstance(el.bounding_box, dict) else {}
        try:
            cx = float(box.get("center_x", 0.0) or 0.0)
            cy = float(box.get("center_y", 0.0) or 0.0)
            w = float(box.get("width", 0.0) or 0.0)
            h = float(box.get("height", 0.0) or 0.0)
        except Exception:
            cx = cy = w = h = 0.0
        if cx > 0 and cy > 0 and w >= 20 and h >= 20 and w <= 140 and h <= 100:
            raw_text = str(el.text or "").strip()
            raw_text_norm = _normalize(raw_text)
            looks_like_next_icon = (
                raw_text_norm in {"", ">", "›", "»", "→", "다음", "next"}
                or any(tok in blob for tok in ("next", "다음", "chevron-right", "arrow-right", "paginate", "pagination"))
            )
            looks_like_prev = (
                raw_text_norm in {"<", "‹", "«", "←", "이전", "prev", "previous", "back"}
                or any(tok in blob for tok in ("prev", "previous", "back", "이전", "chevron-left", "arrow-left"))
            )
            if looks_like_next_icon and not looks_like_prev:
                # bottom-right preference
                geo_score = (cy * 2.0) + (cx * 0.35)
                icon_geo_candidates.append((geo_score, el))
    candidates.sort(key=lambda x: x[0], reverse=True)
    if candidates:
        return candidates[0][1]

    if not numeric_candidates:
        return None

    # 현재 페이지 추정치가 있으면 그 다음 숫자를 우선
    if current_numeric_pages:
        current = max(current_numeric_pages)
        forward = [item for item in numeric_candidates if item[0] > current]
        if forward:
            forward.sort(key=lambda x: (x[0], -x[1]))
            return forward[0][2]

    # 현재 페이지를 모르면 2페이지를 우선(없으면 가장 작은 숫자 다음 값)
    numeric_candidates.sort(key=lambda x: (x[0], -x[1]))
    for page_num, _, el in numeric_candidates:
        if page_num == 2:
            return el
    if len(numeric_candidates) >= 2:
        return numeric_candidates[1][2]
    if icon_geo_candidates:
        icon_geo_candidates.sort(key=lambda x: x[0], reverse=True)
        return icon_geo_candidates[0][1]
    return None


def _pick_scroll_anchor(dom: List[DOMElement]) -> Optional[DOMElement]:
    best: Optional[Tuple[float, DOMElement]] = None
    for el in dom:
        box = el.bounding_box if isinstance(el.bounding_box, dict) else {}
        try:
            cy = float(box.get("center_y", 0.0) or 0.0)
            h = float(box.get("height", 0.0) or 0.0)
        except Exception:
            continue
        if cy <= 0.0 or h <= 0.0:
            continue
        blob = _normalize(" ".join([el.tag or "", el.role or "", el.class_name or "", el.text or ""]))
        score = cy
        if any(tok in blob for tok in ("row", "listitem", "card", "item", "subject", "course", "lecture", "li", "tr")):
            score += 120.0
        if best is None or score > best[0]:
            best = (score, el)
    return best[1] if best else None


def _collect_pagination_diagnostics(dom: List[DOMElement]) -> Dict[str, Any]:
    button_like = 0
    next_keyword = 0
    numeric_pages = 0
    samples: List[Dict[str, Any]] = []
    for el in dom:
        tag = _normalize(el.tag)
        role = _normalize(el.role)
        if tag not in {"a", "button"} and role not in {"button", "link", "tab"}:
            continue
        button_like += 1
        text = str(el.text or "").strip()
        aria = str(el.aria_label or "").strip()
        title = str(el.title or "").strip()
        blob = _normalize(" ".join([text, aria, title, el.class_name or ""]))
        if any(tok in blob for tok in ("다음", "next", "다음페이지", "next page", "›", "»", "arrow-right", "chevron-right")):
            next_keyword += 1
        if re.fullmatch(r"\s*\d{1,3}\s*", text):
            numeric_pages += 1
        if len(samples) < 8:
            samples.append(
                {
                    "id": int(el.id),
                    "tag": tag,
                    "role": role,
                    "text": text[:60],
                    "enabled": bool(el.is_enabled),
                    "visible": bool(el.is_visible),
                }
            )
    return {
        "button_like_count": button_like,
        "next_keyword_count": next_keyword,
        "numeric_page_count": numeric_pages,
        "samples": samples,
    }


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


def _is_noise_row_for_credit(text: str) -> bool:
    row = str(text or "").strip()
    if not row:
        return True
    norm = _normalize(row)
    # wishlist/summary/target-credit controls are not subject result rows
    if any(token in norm for token in ("위시리스트", "목표 학점", "총 0학점", "권장")):
        return True
    if "총 " in norm and "학점" in norm and "강의" not in norm and "교과" not in norm:
        if "|" not in row and "검색 결과" not in norm and "search result" not in norm and "results" not in norm:
            return True
    credits = _extract_row_credits(row)
    if len(set(credits)) >= 4:
        # option list-like rows (e.g., 12~24학점 selector text) should be excluded
        return True
    return False
