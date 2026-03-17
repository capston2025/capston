from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .exploratory_models import FoundIssue, IssueType, PageState, TestableAction


def _create_error_issue(
    self,
    action: TestableAction,
    error_logs: List[Any],
    url: str,
) -> Optional[FoundIssue]:
    """콘솔 에러 이슈 생성"""
    issue_id = f"ERR_{int(time.time())}_{len(self._found_issues)}"
    normalized_logs = [str(item) for item in error_logs]
    filtered_logs = [
        log
        for log in normalized_logs
        if not self._is_expected_non_bug_console_error(log)
    ]
    if not filtered_logs:
        return None

    return FoundIssue(
        issue_id=issue_id,
        issue_type=IssueType.ERROR,
        severity="medium",
        title=f"JavaScript 에러 발생: {action.description}",
        description=f"액션 실행 후 콘솔 에러가 발생했습니다.\n\n에러 로그:\n"
        + "\n".join(filtered_logs[:5]),
        url=url,
        steps_to_reproduce=[
            f"1. {url}로 이동",
            f"2. {action.description}를 {action.action_type}",
        ],
        error_message=filtered_logs[0] if filtered_logs else None,
        console_logs=filtered_logs,
    )


def _create_action_failure_issue(
    self,
    action: TestableAction,
    error_message: str,
    url: str,
) -> FoundIssue:
    """액션 실패 이슈 생성"""
    issue_id = f"FAIL_{int(time.time())}_{len(self._found_issues)}"
    err = str(error_message or "").lower()
    severity = "medium"
    issue_type = IssueType.UNEXPECTED_BEHAVIOR
    if "read timed out" in err or "request_exception" in err:
        severity = "low"
        issue_type = IssueType.TIMEOUT

    return FoundIssue(
        issue_id=issue_id,
        issue_type=issue_type,
        severity=severity,
        title=f"액션 실행 실패: {action.description}",
        description=f"액션을 실행했지만 실패했습니다.\n\n오류: {error_message}",
        url=url,
        steps_to_reproduce=[
            f"1. {url}로 이동",
            f"2. {action.description}를 {action.action_type}",
        ],
        error_message=error_message,
    )


def _create_intent_issue(
    self,
    action: TestableAction,
    url: str,
    reason: str,
    screenshot_before: Optional[str] = None,
    screenshot_after: Optional[str] = None,
) -> FoundIssue:
    issue_id = f"INTENT_{int(time.time())}_{len(self._found_issues)}"
    return FoundIssue(
        issue_id=issue_id,
        issue_type=IssueType.UNEXPECTED_BEHAVIOR,
        severity="low",
        title=f"의도한 결과 미확인: {action.description}",
        description=f"액션 실행 후 의도한 변화가 감지되지 않았습니다.\n\n사유: {reason}",
        url=url,
        steps_to_reproduce=[
            f"1. {url}로 이동",
            f"2. {action.description}를 {action.action_type}",
        ],
        screenshot_before=screenshot_before,
        screenshot_after=screenshot_after,
    )


def _verify_action_intent(
    self,
    action: TestableAction,
    before_state: PageState,
    after_state: PageState,
    before_url: str,
    after_url: str,
    screenshot_before: Optional[str],
    screenshot_after: Optional[str],
    expected_input: Optional[str],
    before_select_state: Optional[dict],
    before_toggle_state: Optional[dict],
) -> tuple[bool, Optional[str]]:
    if action.action_type == "navigate":
        target_url = self._resolve_navigation_target(action.element_id, before_url)
        if self._normalize_url_for_compare(
            after_url
        ) == self._normalize_url_for_compare(target_url):
            return True, None
        if after_url != before_url:
            return True, None
        return False, f"URL 이동이 확인되지 않음: {target_url}"

    if action.action_type == "fill":
        selector = self._find_selector_by_element_id(
            action.element_id, before_state
        )
        if not selector:
            return True, None
        if not expected_input:
            return True, None
        current_value = self._evaluate_selector(
            selector, "el => (el.value ?? el.textContent ?? '').toString()"
        )
        if current_value is None:
            return True, None
        if self._normalize_text(expected_input) in self._normalize_text(
            current_value
        ):
            return True, None
        return False, "입력값 반영이 확인되지 않음"

    if action.action_type == "hover":
        return True, None

    if action.action_type == "select":
        selector = self._find_selector_by_element_id(
            action.element_id, before_state
        )
        if not selector:
            return True, None
        after_select_state = self._get_select_state(selector)
        expected_label = None
        if ":" in action.description:
            expected_label = action.description.split(":", 1)[1].strip()
        if expected_label and after_select_state:
            after_text = self._normalize_text(after_select_state.get("text"))
            if self._normalize_text(expected_label) in after_text:
                return True, None
        if before_select_state and after_select_state:
            if before_select_state.get("value") != after_select_state.get("value"):
                return True, None
            if self._normalize_text(
                before_select_state.get("text")
            ) != self._normalize_text(after_select_state.get("text")):
                return True, None
        if after_select_state and (
            after_select_state.get("value") or after_select_state.get("text")
        ):
            return True, None
        return False, "드롭다운 선택 결과가 확인되지 않음"

    if action.action_type in ["click", "select"]:
        if after_url != before_url:
            return True, None

        if (
            screenshot_before
            and screenshot_after
            and screenshot_before != screenshot_after
        ):
            return True, None

        before_count = len(before_state.interactive_elements)
        after_count = len(after_state.interactive_elements)
        if before_count != after_count:
            return True, None

        element_before = self._find_element_by_id(action.element_id, before_state)
        selector = element_before.selector if element_before else None
        element_after = (
            self._find_element_by_selector(selector, after_state)
            if selector
            else None
        )
        if selector and element_after is None:
            return True, None

        if selector:
            toggle_state = self._get_toggle_state(selector)
            if toggle_state:
                if before_toggle_state and toggle_state != before_toggle_state:
                    return True, None
                if toggle_state.get("checked") is True:
                    return True, None
                if toggle_state.get("pressed") is True:
                    return True, None
                if toggle_state.get("selected") is True:
                    return True, None
                if toggle_state.get("expanded") is True:
                    return True, None
        if element_before and element_after:
            if self._normalize_text(element_before.text) != self._normalize_text(
                element_after.text
            ):
                return True, None
            if (element_before.aria_label or "").strip() != (
                element_after.aria_label or ""
            ).strip():
                return True, None

        return False, "URL/DOM 변화가 감지되지 않음"

    return True, None


def _append_validation_report(self, report: Dict[str, Any], step_number: int) -> List[Dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    raw_checks = report.get("checks")
    if not isinstance(raw_checks, list):
        return []
    step_rows: List[Dict[str, Any]] = []
    for row in raw_checks:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["source_step"] = int(step_number)
        step_rows.append(item)
    if not step_rows:
        return []
    self._validation_checks.extend(step_rows)

    summary = report.get("summary")
    if isinstance(summary, dict):
        self._verification_report = {
            "mode": str(report.get("mode") or "filter_semantic_v2"),
            "summary": dict(summary),
            "rules_used": list(report.get("rules_used") or []),
            "pages_checked": int(report.get("pages_checked") or 1),
            "cases": list(report.get("cases") or []),
            "reason_code_summary": dict(self._validation_reason_counts or {}),
            "container_source_summary": dict(getattr(self, "_last_container_source_summary", {}) or {}),
            "active_scoped_container_ref": str(getattr(self, "_active_scoped_container_ref", "") or ""),
        }
    self._validation_summary = self._aggregate_validation_summary(self._validation_checks)
    return step_rows


def _aggregate_validation_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows or [])
    passed = 0
    failed = 0
    skipped = 0
    failed_mandatory = 0
    skipped_mandatory = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        mandatory = bool(row.get("mandatory"))
        if status in {"pass", "passed"}:
            passed += 1
        elif status in {"fail", "failed"}:
            failed += 1
            if mandatory:
                failed_mandatory += 1
        elif status.startswith("skipped") or status == "skipped":
            skipped += 1
            if mandatory:
                skipped_mandatory += 1
    success_rate = round((passed / total) * 100, 1) if total > 0 else 0.0
    return {
        "goal_type": "filter_validation_semantic",
        "total_checks": total,
        "passed_checks": passed,
        "failed_checks": failed,
        "skipped_checks": skipped,
        "failed_mandatory_checks": failed_mandatory,
        "skipped_mandatory_checks": skipped_mandatory,
        "strict_failed": bool((failed_mandatory + skipped_mandatory) > 0),
        "success_rate": success_rate,
    }


def _report_console_errors(
    self, console_errors: List[str], screenshot: Optional[str]
):
    """콘솔 에러 리포트"""
    filtered_errors = [
        str(log)
        for log in (console_errors or [])
        if not self._is_expected_non_bug_console_error(str(log))
    ]
    if not filtered_errors:
        return
    issue_id = f"CONSOLE_{int(time.time())}"

    issue = FoundIssue(
        issue_id=issue_id,
        issue_type=IssueType.ERROR,
        severity="medium",
        title=f"콘솔 에러 감지: {len(filtered_errors)}개",
        description=f"페이지 로드 시 콘솔 에러가 발견되었습니다.\n\n"
        + "\n".join(filtered_errors[:5]),
        url=self._current_url,
        steps_to_reproduce=[f"1. {self._current_url}로 이동"],
        console_logs=filtered_errors,
        screenshot_before=screenshot,
    )

    self._found_issues.append(issue)


def _is_expected_non_bug_console_error(log_text: str) -> bool:
    text = str(log_text or "").lower()
    if not text:
        return False
    expected_patterns = (
        "이미 사용 중인 아이디",
        "already used",
        "already exists",
        "duplicate",
        "invalid credentials",
        "wrong password",
        "비밀번호가 일치하지",
        "회원가입 실패",
        "로그인 실패",
        "api 에러 상세",
    )
    has_expected = any(pat in text for pat in expected_patterns)
    if not has_expected:
        return False
    if "400" in text or "failed to load resource" in text:
        return True
    # 사이트별 인증/중복 검증 메시지는 HTTP 코드가 노출되지 않아도 정상 동작일 수 있음
    auth_validation_hints = (
        "회원가입",
        "로그인",
        "auth",
        "credential",
        "아이디",
        "비밀번호",
        "validation",
    )
    if any(h in text for h in auth_validation_hints):
        return True
    return False


create_error_issue = _create_error_issue
create_action_failure_issue = _create_action_failure_issue
create_intent_issue = _create_intent_issue
verify_action_intent = _verify_action_intent
append_validation_report = _append_validation_report
aggregate_validation_summary = _aggregate_validation_summary
report_console_errors = _report_console_errors
is_expected_non_bug_console_error = _is_expected_non_bug_console_error
