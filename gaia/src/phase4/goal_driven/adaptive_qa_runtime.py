"""Adaptive QA expansion helpers for goal-driven runs.

This module keeps the experimental QA-expansion mode outside the default
goal loop. The normal runner still executes the user's primary goal first;
only when explicitly enabled do we generate safe follow-up edge cases from
the observed page state and run them as additional goals.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from .models import GoalResult, TestGoal


ADAPTIVE_QA_MODE = "adaptive_qa"
DEEP_ADAPTIVE_QA_MODE = "deep_adaptive_qa"
_DEFAULT_MAX_EDGE_CASES = 5
_DEFAULT_DEEP_MAX_EDGE_CASES = 10
_ADAPTIVE_EDGE_CASE_CAP = 5
_DEEP_EDGE_CASE_CAP = 15

_RISKY_EDGE_TOKENS = (
    "결제",
    "구매",
    "주문",
    "예약",
    "삭제",
    "탈퇴",
    "가입",
    "checkout",
    "purchase",
    "order",
    "delete",
    "reserve",
)


_ABSENT_OR_NOT_APPLICABLE_TOKENS = (
    "존재하지 않",
    "없음",
    "보이지 않",
    "찾을 수 없",
    "지원되지 않",
    "지원하지 않",
    "not applicable",
    "not_applicable",
    "not found",
    "does not exist",
    "no such",
)

_UNSUPPORTED_TOOL_TOKENS = (
    "지원하지 않는 액션",
    "unsupported action",
    "action unsupported",
    "도구로 수행 불가",
    "tool unsupported",
    "resize",
    "viewport",
    "zoom",
)


_OPTIONAL_EDGE_TOKENS = (
    "존재하는 경우",
    "존재할 경우",
    "있는 경우",
    "있으면",
    "보이면",
    "제공되는 경우",
    "지원하는 경우",
    "존재 여부",
    "지원 여부",
    "if present",
    "if exists",
    "when available",
    "optional",
)


_STRICT_EDGE_TOKENS = (
    "유지",
    "보존",
    "동일",
    "그대로",
    "새로고침",
    "뒤로",
    "복귀",
    "세션",
    "정합성",
    "마감",
    "상세",
    "배지",
    "레이아웃",
    "깨짐",
)


def adaptive_qa_enabled(goal: TestGoal) -> bool:
    return adaptive_qa_mode(goal) is not None


def adaptive_qa_is_deep(goal: TestGoal) -> bool:
    return adaptive_qa_mode(goal) == DEEP_ADAPTIVE_QA_MODE


def adaptive_qa_mode(goal: TestGoal) -> str | None:
    data = goal.test_data if isinstance(goal.test_data, dict) else {}
    raw_deep = data.get(DEEP_ADAPTIVE_QA_MODE)
    if isinstance(raw_deep, dict):
        enabled = raw_deep.get("enabled", True)
        if str(enabled).strip().lower() not in {"0", "false", "no", "off", "disabled"}:
            return DEEP_ADAPTIVE_QA_MODE
    elif raw_deep is not None and str(raw_deep).strip().lower() not in {"0", "false", "no", "off", "disabled"}:
        return DEEP_ADAPTIVE_QA_MODE

    raw = data.get(ADAPTIVE_QA_MODE)
    if isinstance(raw, dict):
        enabled = raw.get("enabled", True)
        if str(enabled).strip().lower() not in {"0", "false", "no", "off", "disabled"}:
            raw_mode = str(raw.get("mode") or "").strip().lower()
            return DEEP_ADAPTIVE_QA_MODE if raw_mode in {"deep", "deep_qa", DEEP_ADAPTIVE_QA_MODE} else ADAPTIVE_QA_MODE
    elif raw is not None and str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}:
        return ADAPTIVE_QA_MODE

    qa_mode = str(data.get("qa_mode") or data.get("mode") or "").strip().lower()
    if qa_mode in {DEEP_ADAPTIVE_QA_MODE, "deep", "deep_qa", "aggressive_qa", "deep_adaptive"}:
        return DEEP_ADAPTIVE_QA_MODE
    if qa_mode in {ADAPTIVE_QA_MODE, "adaptive", "qa_adaptive", "progressive_qa"}:
        return ADAPTIVE_QA_MODE
    return None


def is_adaptive_edge_goal(goal: TestGoal) -> bool:
    data = goal.test_data if isinstance(goal.test_data, dict) else {}
    return bool(data.get("adaptive_qa_edge_case"))


def adaptive_qa_max_edge_cases(goal: TestGoal) -> int:
    data = goal.test_data if isinstance(goal.test_data, dict) else {}
    mode = adaptive_qa_mode(goal)
    raw_config = data.get(DEEP_ADAPTIVE_QA_MODE) if mode == DEEP_ADAPTIVE_QA_MODE else data.get(ADAPTIVE_QA_MODE)
    raw_value = raw_config.get("max_edge_cases") if isinstance(raw_config, dict) else data.get("adaptive_qa_max_edge_cases")
    try:
        value = int(raw_value)
    except Exception:
        value = _DEFAULT_DEEP_MAX_EDGE_CASES if mode == DEEP_ADAPTIVE_QA_MODE else _DEFAULT_MAX_EDGE_CASES
    cap = _DEEP_EDGE_CASE_CAP if mode == DEEP_ADAPTIVE_QA_MODE else _ADAPTIVE_EDGE_CASE_CAP
    return max(0, min(value, cap))


def _strip_code_fences(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```json"):
        value = value[7:].strip()
    elif value.startswith("```"):
        value = value[3:].strip()
    if value.endswith("```"):
        value = value[:-3].strip()
    return value


def _parse_json_object(text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fences(text)
    try:
        data = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}


def _truncate(text: Any, limit: int = 180) -> str:
    value = str(text or "").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _format_dom_for_adaptive_prompt(agent: Any, dom_elements: Iterable[Any]) -> str:
    formatter = getattr(agent, "_format_dom_for_llm", None)
    if callable(formatter):
        try:
            formatted = str(formatter(list(dom_elements or [])) or "").strip()
            if formatted:
                return formatted[:12000]
        except Exception:
            pass
    lines: list[str] = []
    for element in list(dom_elements or [])[:80]:
        parts = []
        for attr in ("role", "tag", "text", "aria_label", "context_text"):
            value = _truncate(getattr(element, attr, ""), 140)
            if value:
                parts.append(f"{attr}={value}")
        if parts:
            lines.append("- " + " | ".join(parts))
    return "\n".join(lines)[:12000]


def _format_observed_surface_for_adaptive_prompt(dom_elements: Iterable[Any]) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for element in list(dom_elements or [])[:140]:
        role = _truncate(getattr(element, "role", ""), 40)
        tag = _truncate(getattr(element, "tag", ""), 30)
        text = _truncate(
            getattr(element, "text", "")
            or getattr(element, "aria_label", "")
            or getattr(element, "context_text", ""),
            120,
        )
        if not text:
            continue
        kind = "control" if (role or tag).lower() in {"button", "link", "a", "input", "select", "combobox", "checkbox"} else "text"
        line = f"- {kind}: {text}"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= 80:
            break
    return "\n".join(lines)[:8000]


def _safe_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip()).strip("_")
    return (slug or fallback)[:48]


def _is_safe_edge_case(edge: Dict[str, Any]) -> bool:
    safety = str(edge.get("safety") or edge.get("risk") or "safe").strip().lower()
    if safety in {"unsafe", "dangerous", "risky", "blocked"}:
        return False
    blob = " ".join(
        str(edge.get(key) or "")
        for key in ("id", "name", "description", "reason", "expected_outcome")
    ).lower()
    return not any(token in blob for token in _RISKY_EDGE_TOKENS)


def _normalize_checks(raw_checks: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not isinstance(raw_checks, list):
        return checks
    for idx, item in enumerate(raw_checks, start=1):
        if not isinstance(item, dict):
            continue
        title = _truncate(item.get("title") or item.get("name") or item.get("id") or "", 120)
        if not title:
            continue
        checks.append(
            {
                "id": _safe_slug(str(item.get("id") or title), f"check_{idx}"),
                "title": title,
                "source": "agent_inferred",
                "status": "DISCOVERED",
                "rationale": _truncate(item.get("rationale") or item.get("reason") or "", 240),
                "evidence_hint": _truncate(item.get("evidence_hint") or item.get("evidence") or "", 240),
            }
        )
    return checks[:8]


def _normalize_observed_evidence(value: Any) -> list[str]:
    raw_values = value if isinstance(value, list) else [value]
    evidence: list[str] = []
    for item in raw_values:
        text = _truncate(item, 160)
        if text:
            evidence.append(text)
        if len(evidence) >= 4:
            break
    return evidence


def _infer_skip_if_absent(item: dict[str, Any], *, title: str, description: str) -> bool:
    raw_skip = item.get("skip_if_absent")
    text_blob = " ".join(
        [
            title,
            description,
            str(item.get("dimension") or item.get("axis") or item.get("category") or ""),
            str(item.get("reason") or item.get("rationale") or ""),
            " ".join(str(value or "") for value in list(item.get("success_criteria") or item.get("checks") or [])),
        ]
    ).lower()
    if any(token in text_blob for token in _OPTIONAL_EDGE_TOKENS):
        return True
    if any(token in text_blob for token in _STRICT_EDGE_TOKENS):
        return False
    if raw_skip is False:
        return False
    return True


def _normalize_edge_cases(raw_edges: Any, *, max_edge_cases: int) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    if max_edge_cases <= 0 or not isinstance(raw_edges, list):
        return edges
    for idx, item in enumerate(raw_edges, start=1):
        if not isinstance(item, dict):
            continue
        title = _truncate(item.get("name") or item.get("title") or item.get("id") or "", 120)
        description = _truncate(item.get("description") or item.get("goal") or title, 360)
        if not title or not description:
            continue
        observed_evidence = _normalize_observed_evidence(
            item.get("observed_evidence")
            or item.get("evidence")
            or item.get("observed_text")
            or item.get("basis")
        )
        if not observed_evidence:
            continue
        edge = {
            "id": _safe_slug(str(item.get("id") or title), f"edge_{idx}"),
            "name": title,
            "description": description,
            "dimension": _truncate(item.get("dimension") or item.get("axis") or item.get("category") or "", 120),
            "reason": _truncate(item.get("reason") or item.get("rationale") or "", 240),
            "safety": str(item.get("safety") or "safe_readonly_or_reversible").strip(),
            "observed_evidence": observed_evidence,
            "skip_if_absent": _infer_skip_if_absent(item, title=title, description=description),
            "success_criteria": [
                _truncate(value, 180)
                for value in list(item.get("success_criteria") or item.get("checks") or [])
                if str(value or "").strip()
            ][:4],
        }
        if _is_safe_edge_case(edge):
            edges.append(edge)
        if len(edges) >= max_edge_cases:
            break
    return edges


def _repair_empty_adaptive_plan(
    agent: Any,
    *,
    goal: TestGoal,
    primary_result: GoalResult,
    mode: str,
    edge_limit: int,
    formatted_dom: str,
    observed_surface: str,
    action_history: list[str],
    action_feedback: list[str],
    previous_edges: list[dict[str, str]],
    first_raw_response: str,
) -> dict[str, Any]:
    if edge_limit <= 0 or not primary_result.success:
        return {"raw_response": "", "checks": [], "edge_cases": [], "no_expand_reason": ""}
    prompt = f"""너는 웹 QA 리드다. 1차 Deep QA 확장 결과가 edge_cases=[]로 비어 있었다.
이번 응답은 코드 fallback이 아니라, 네가 직접 빈 결과가 맞는지 재검토하는 repair pass다.

판단 목표:
- 현재 화면에 실제로 관찰된 텍스트/버튼/링크/입력/표/목록/상태값에서 추가 검증할 가치가 있으면 edge_cases를 만든다.
- 정말 확장할 surface가 없으면 edge_cases=[]를 유지하고 no_expand_reason에 구체적인 이유를 쓴다.
- 없는 검색/정렬/아코디언/새 탭/브라우저 뒤로가기/화면 크기 변경 기능은 절대 만들지 않는다.
- 코드가 자동 생성한 케이스처럼 보이는 일반론이 아니라, 현재 관찰 근거에 묶인 QA 판단이어야 한다.

사용자 목표:
{goal.description}

primary 실행 결과:
- status: {'PASS' if primary_result.success else 'FAIL'}
- reason: {primary_result.final_reason}
- steps: {primary_result.total_steps}

1차 raw response:
{_truncate(first_raw_response, 2000)}

최근 액션:
{json.dumps(action_history, ensure_ascii=False)}

최근 피드백:
{json.dumps(action_feedback, ensure_ascii=False)}

이미 제안/실행된 엣지 케이스:
{json.dumps(previous_edges, ensure_ascii=False)}

현재 DOM:
{formatted_dom or '(없음)'}

관찰된 화면 텍스트/컨트롤 단서:
{observed_surface or '(없음)'}

규칙:
- 각 edge case는 observed_evidence에 현재 화면에서 실제로 본 텍스트/컨트롤 라벨을 1개 이상 넣는다.
- 조작 가능성이 불확실하면 description에 먼저 존재/조작 가능 여부를 확인하고, 아니면 SKIP/not_applicable로 끝내라고 명시한다.
- skip_if_absent는 선택 기능 존재 여부 탐색처럼 optional/probe인 경우에만 true다. primary에서 이미 관찰한 항목의 유지, 상태 보존, 정합성, 상세 정보 검증은 false로 둔다.
- 결제/구매/주문/예약/삭제/탈퇴/가입/개인정보 변경처럼 위험한 행동은 제안하지 않는다.
- edge_cases는 최대 {edge_limit}개다.
- mode={mode}

JSON만 출력:
{{
  "checks": [
    {{
      "id": "snake_case_id",
      "title": "체크 이름",
      "rationale": "왜 이 체크가 필요한지",
      "evidence_hint": "어떤 화면 증거로 판정할지"
    }}
  ],
  "edge_cases": [
    {{
      "id": "snake_case_id",
      "name": "엣지 케이스 이름",
      "dimension": "검증 축",
      "description": "GAIA가 실행할 자연어 목표",
      "reason": "왜 확장했는지",
      "observed_evidence": ["현재 화면에서 실제로 본 텍스트/버튼/링크/입력 라벨"],
      "skip_if_absent": true,
      "safety": "safe_readonly_or_reversible",
      "success_criteria": ["판정 조건"]
    }}
  ],
  "no_expand_reason": "edge_cases가 비어 있을 때만 구체적으로 작성"
}}"""
    raw = ""
    try:
        raw = str(agent._call_llm_text_only(prompt) or "")
    except Exception as exc:
        return {
            "raw_response": "",
            "checks": [],
            "edge_cases": [],
            "no_expand_reason": f"repair generation failed: {str(exc)[:220]}",
        }
    parsed = _parse_json_object(raw)
    return {
        "raw_response": raw,
        "checks": _normalize_checks(parsed.get("checks")),
        "edge_cases": _normalize_edge_cases(parsed.get("edge_cases"), max_edge_cases=edge_limit),
        "no_expand_reason": _truncate(parsed.get("no_expand_reason") or "", 320),
    }


def classify_adaptive_edge_status(edge_case: Dict[str, Any], result: GoalResult) -> str:
    reason = str(getattr(result, "final_reason", "") or "")
    reason_l = reason.lower()
    if any(token in reason_l for token in _UNSUPPORTED_TOOL_TOKENS):
        return "UNSUPPORTED"
    absent_or_not_applicable = any(token in reason_l for token in _ABSENT_OR_NOT_APPLICABLE_TOKENS)
    if absent_or_not_applicable:
        if bool(edge_case.get("skip_if_absent", True)):
            return "SKIP"
        return "FAIL"
    return "PASS" if result.success else "FAIL"


def edge_case_fingerprint(edge_case: Dict[str, Any]) -> str:
    fields = [
        str(edge_case.get("id") or ""),
        str(edge_case.get("dimension") or ""),
        str(edge_case.get("name") or ""),
        str(edge_case.get("description") or ""),
    ]
    blob = " ".join(field.strip().lower() for field in fields if field.strip())
    blob = re.sub(r"\s+", " ", blob)
    blob = re.sub(r"[^0-9a-zA-Z가-힣 ]+", "", blob)
    return blob[:240]


def filter_new_edge_cases(
    edge_cases: Iterable[Dict[str, Any]],
    seen_fingerprints: set[str],
) -> list[dict[str, Any]]:
    new_cases: list[dict[str, Any]] = []
    for edge_case in edge_cases:
        fingerprint = edge_case_fingerprint(edge_case)
        if not fingerprint or fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        new_cases.append(dict(edge_case))
    return new_cases


def merge_adaptive_qa_plans(plans: Iterable[Dict[str, Any]]) -> dict[str, Any]:
    plan_list = [dict(plan) for plan in plans if isinstance(plan, dict)]
    checks: list[dict[str, Any]] = []
    check_ids: set[str] = set()
    edge_cases: list[dict[str, Any]] = []
    edge_fingerprints: set[str] = set()
    mode = ADAPTIVE_QA_MODE
    status = "generated" if plan_list else "not_generated"
    for plan in plan_list:
        mode = str(plan.get("mode") or mode)
        status = str(plan.get("status") or status)
        for check in list(plan.get("checks") or []):
            if not isinstance(check, dict):
                continue
            check_id = str(check.get("id") or check.get("title") or "").strip()
            if not check_id or check_id in check_ids:
                continue
            check_ids.add(check_id)
            checks.append(dict(check))
        for edge_case in list(plan.get("edge_cases") or []):
            if not isinstance(edge_case, dict):
                continue
            fingerprint = edge_case_fingerprint(edge_case)
            if not fingerprint or fingerprint in edge_fingerprints:
                continue
            edge_fingerprints.add(fingerprint)
            edge_cases.append(dict(edge_case))
    return {
        "enabled": bool(plan_list),
        "mode": mode,
        "status": status,
        "round_count": len(plan_list),
        "checks": checks,
        "edge_cases": edge_cases,
    }


def generate_adaptive_qa_plan(
    agent: Any,
    *,
    goal: TestGoal,
    primary_result: GoalResult,
    dom_elements: Optional[List[Any]] = None,
    max_edge_cases: Optional[int] = None,
    previous_edge_cases: Optional[List[Dict[str, Any]]] = None,
    round_index: int = 1,
) -> dict[str, Any]:
    """Ask the model for inferred QA checks and safe follow-up edge cases."""

    if is_adaptive_edge_goal(goal):
        return {"enabled": False, "reason": "edge_case_goals_do_not_expand"}
    mode = adaptive_qa_mode(goal) or ADAPTIVE_QA_MODE
    edge_cap = _DEEP_EDGE_CASE_CAP if mode == DEEP_ADAPTIVE_QA_MODE else _ADAPTIVE_EDGE_CASE_CAP
    edge_limit = adaptive_qa_max_edge_cases(goal) if max_edge_cases is None else max(0, min(int(max_edge_cases), edge_cap))
    expansion_style = (
        "공격적 Deep QA 모드다. 특정 서비스/도메인 이름이나 고정 예시를 가정하지 말고, 현재 DOM과 최근 행동에서 관찰되는 조작 가능한 surface와 검증 가능한 출력 surface를 먼저 추론하라. "
        "반복되는 컨트롤, 선택형 컨트롤, 입력 surface, 목록/표/카드, 내비게이션, 상태 표시, 결과 수치, 오류/빈 상태처럼 화면에 실제 존재하는 affordance를 서로 다른 variation dimension으로 나누고, 각 dimension에서 사람이 놓치기 쉬운 검증 목표를 최대한 많이 제안하라."
        if mode == DEEP_ADAPTIVE_QA_MODE
        else "일반 Adaptive QA 모드다. 현재 화면에서 가장 안전하고 직접적인 follow-up 검증만 고른다."
    )
    dom = list(dom_elements or [])
    formatted_dom = _format_dom_for_adaptive_prompt(agent, dom)
    observed_surface = _format_observed_surface_for_adaptive_prompt(dom)
    action_history = [
        _truncate(item, 220)
        for item in list(getattr(agent, "_action_history", []) or [])[-8:]
        if str(item or "").strip()
    ]
    action_feedback = [
        _truncate(item, 220)
        for item in list(getattr(agent, "_action_feedback", []) or [])[-8:]
        if str(item or "").strip()
    ]
    previous_edges = [
        {
            "id": _truncate(item.get("id"), 80),
            "dimension": _truncate(item.get("dimension"), 120),
            "name": _truncate(item.get("name"), 120),
            "description": _truncate(item.get("description"), 240),
        }
        for item in list(previous_edge_cases or [])[-30:]
        if isinstance(item, dict)
    ]
    prompt = f"""너는 웹 QA 리드다. 사용자가 요청한 primary test를 방금 실행했다.
현재 화면과 최근 행동을 보고, 시중 사이트에서 안전하게 추가 검증할 수 있는 QA 체크와 엣지 케이스를 생성하라.
{expansion_style}

사용자 목표:
{goal.description}

primary 실행 결과:
- status: {'PASS' if primary_result.success else 'FAIL'}
- reason: {primary_result.final_reason}
- steps: {primary_result.total_steps}

최근 액션:
{json.dumps(action_history, ensure_ascii=False)}

최근 피드백:
{json.dumps(action_feedback, ensure_ascii=False)}

이미 제안/실행된 엣지 케이스:
{json.dumps(previous_edges, ensure_ascii=False)}

현재 DOM:
{formatted_dom or '(없음)'}

관찰된 화면 텍스트/컨트롤 단서:
{observed_surface or '(없음)'}

규칙:
- 사용자가 명시한 목표는 완화하지 마라.
- 실제 화면에서 관찰 가능한 텍스트, 버튼/링크/입력 컨트롤의 라벨, 표/목록의 헤더처럼 현재 evidence에 있는 단서만 근거로 체크를 만들라.
- DOM 단어만 보고 검색창, 정렬, 아코디언, 새 탭, 화면 확대/축소, 브라우저 뒤로가기 같은 기능이 있다고 가정하지 마라. 해당 버튼/입력/아이콘/라벨이 관찰된 경우에만 제안하라.
- 브라우저 창 크기 변경, zoom, 우클릭 컨텍스트 메뉴, 브라우저 chrome toolbar 조작처럼 현재 GAIA action으로 직접 수행하기 어려운 케이스는 제안하지 마라.
- 각 edge case는 observed_evidence에 현재 화면에서 실제로 본 텍스트/컨트롤 라벨을 1개 이상 짧게 인용하라.
- 선택 기능 존재 여부를 확인하는 probe성 케이스는 대상 UI가 실제로 없으면 SKIP/not_applicable로 끝내도록 description과 success_criteria에 명시하라.
- primary에서 이미 관찰한 항목의 유지, 상태 보존, 정합성, 상세 정보 검증은 관찰 근거가 사라지면 FAIL로 판정해야 하므로 skip_if_absent=false로 둔다.
- 이미 제안/실행된 엣지 케이스와 같은 목표, 같은 dimension, 같은 화면 확인을 반복하지 마라.
- edge_cases는 결제/구매/주문/예약/삭제/탈퇴/가입/개인정보 변경처럼 비용이 발생하거나 복구가 어려운 행동을 제안하지 마라.
- 사용자가 명시적으로 허용한 메일/메시지 전송은 가능하지만, 수신자와 내용이 명확하지 않으면 제안하지 마라.
- primary가 실패했으면 edge_cases는 빈 배열로 둔다.
- edge_cases는 이번 라운드에서 최대 {edge_limit}개만 제안한다.
- deep mode에서는 같은 dimension만 반복하지 말고, 화면에서 관찰 가능한 서로 다른 검증 축을 넓게 덮어라.
- 각 edge case는 GAIA가 자연어 goal로 바로 실행할 수 있어야 한다.

JSON만 출력:
{{
  "checks": [
    {{
      "id": "snake_case_id",
      "title": "체크 이름",
      "rationale": "왜 이 체크가 필요한지",
      "evidence_hint": "어떤 화면 증거로 판정할지"
    }}
  ],
  "edge_cases": [
    {{
      "id": "snake_case_id",
      "name": "엣지 케이스 이름",
      "dimension": "이 케이스가 덮는 검증 축",
      "description": "GAIA가 실행할 자연어 목표",
      "reason": "왜 확장했는지",
      "observed_evidence": ["현재 화면에서 실제로 본 텍스트/버튼/링크/입력 라벨"],
      "skip_if_absent": true,
      "safety": "safe_readonly_or_reversible",
      "success_criteria": ["판정 조건"]
    }}
  ]
}}"""

    raw = ""
    try:
        raw = str(agent._call_llm_text_only(prompt) or "")
    except Exception as exc:
        return {
            "enabled": True,
            "mode": mode,
            "round_index": round_index,
            "status": "generation_failed",
            "reason": str(exc)[:240],
            "checks": [],
            "edge_cases": [],
        }
    parsed = _parse_json_object(raw)
    checks = _normalize_checks(parsed.get("checks"))
    edges = _normalize_edge_cases(parsed.get("edge_cases"), max_edge_cases=edge_limit if primary_result.success else 0)
    status = "generated"
    repair_response = ""
    no_expand_reason = ""
    if primary_result.success and not edges and edge_limit > 0:
        repaired = _repair_empty_adaptive_plan(
            agent,
            goal=goal,
            primary_result=primary_result,
            mode=mode,
            edge_limit=edge_limit,
            formatted_dom=formatted_dom,
            observed_surface=observed_surface,
            action_history=action_history,
            action_feedback=action_feedback,
            previous_edges=previous_edges,
            first_raw_response=raw,
        )
        repair_response = str(repaired.get("raw_response") or "")[:4000]
        no_expand_reason = str(repaired.get("no_expand_reason") or "")
        repaired_checks = list(repaired.get("checks") or [])
        repaired_edges = list(repaired.get("edge_cases") or [])
        if repaired_checks:
            checks.extend(repaired_checks)
        if repaired_edges:
            edges = repaired_edges
            status = "regenerated_after_empty_plan"
        else:
            status = "no_observed_expansion"
    return {
        "enabled": True,
        "mode": mode,
        "round_index": round_index,
        "status": status,
        "raw_response": raw[:4000],
        "repair_response": repair_response,
        "no_expand_reason": no_expand_reason,
        "checks": checks,
        "edge_cases": edges,
    }


def build_edge_goal(parent_goal: TestGoal, edge_case: Dict[str, Any], *, index: int) -> TestGoal:
    data = dict(parent_goal.test_data or {})
    data.pop(ADAPTIVE_QA_MODE, None)
    data.pop(DEEP_ADAPTIVE_QA_MODE, None)
    for key in ("qa_mode", "mode"):
        if str(data.get(key) or "").strip().lower() in {
            ADAPTIVE_QA_MODE,
            DEEP_ADAPTIVE_QA_MODE,
            "adaptive",
            "qa_adaptive",
            "progressive_qa",
            "deep",
            "deep_qa",
            "aggressive_qa",
            "deep_adaptive",
        }:
            data.pop(key, None)
    data["adaptive_qa_edge_case"] = True
    data["adaptive_qa_parent_goal_id"] = parent_goal.id
    data["adaptive_qa_skip_if_absent"] = edge_case.get("skip_if_absent", True) is not False
    criteria = [
        str(value or "").strip()
        for value in list(edge_case.get("success_criteria") or [])
        if str(value or "").strip()
    ]
    description = str(edge_case.get("description") or edge_case.get("name") or "").strip()
    evidence = [
        str(value or "").strip()
        for value in list(edge_case.get("observed_evidence") or [])
        if str(value or "").strip()
    ][:4]
    if evidence:
        if data["adaptive_qa_skip_if_absent"]:
            description = (
                f"{description}\n"
                f"관찰 근거: {', '.join(evidence)}\n"
                "먼저 이 텍스트/버튼/링크/컨트롤이 현재 화면에 실제로 남아있는지 확인하십시오. "
                "대상 UI가 없거나 단순 텍스트라 조작할 수 없으면 성공/실패로 포장하지 말고 "
                "SKIP/not_applicable로 종료하십시오."
            )
            criteria = [
                *criteria,
                "관찰 근거가 사라졌거나 대상 UI가 실제로 없으면 SKIP/not_applicable로 판정한다.",
            ]
        else:
            description = (
                f"{description}\n"
                f"관찰 근거: {', '.join(evidence)}\n"
                "이 케이스는 primary 실행에서 관찰된 상태의 유지/정합성을 검증합니다. "
                "관찰 근거가 사라졌거나 기대 상태가 유지되지 않으면 FAIL로 판정하십시오."
            )
            criteria = [
                *criteria,
                "관찰 근거가 사라졌거나 기대 상태가 유지되지 않으면 FAIL로 판정한다.",
            ]
    return TestGoal(
        id=f"{parent_goal.id}_EDGE_{index}",
        name=str(edge_case.get("name") or f"Adaptive edge case {index}")[:80],
        description=description,
        priority="SHOULD",
        keywords=list(parent_goal.keywords or [])[:5],
        preconditions=list(parent_goal.preconditions or []),
        test_data=data,
        success_criteria=criteria or [description],
        expected_signals=list(parent_goal.expected_signals or []),
        max_steps=max(3, min(int(parent_goal.max_steps or 20), 8)),
        # Edge goals intentionally continue from the observed post-primary page.
        # Re-navigating to the original start URL would erase the UI state that
        # motivated the generated edge case.
        start_url=None,
    )


def summarize_adaptive_qa_report(
    *,
    primary_goal: TestGoal,
    primary_result: GoalResult,
    plan: Dict[str, Any],
    edge_results: List[Dict[str, Any]],
) -> dict[str, Any]:
    generated_edges = list(plan.get("edge_cases") or []) if isinstance(plan, dict) else []
    executed = len(edge_results)
    passed = sum(1 for item in edge_results if str(item.get("status") or "").lower() == "pass")
    failed = sum(1 for item in edge_results if str(item.get("status") or "").lower() == "fail")
    skipped = sum(1 for item in edge_results if str(item.get("status") or "").lower() == "skip")
    unsupported = sum(1 for item in edge_results if str(item.get("status") or "").lower() == "unsupported")
    checks = [
        {
            "id": "primary_goal",
            "title": primary_goal.description,
            "source": "user_explicit",
            "status": "PASS" if primary_result.success else "FAIL",
            "evidence": primary_result.final_reason,
        }
    ]
    checks.extend(list(plan.get("checks") or []) if isinstance(plan, dict) else [])
    total_scored = 1 + passed + failed + unsupported
    score = (int(primary_result.success) + passed) / total_scored if total_scored else 0.0
    mode = str(plan.get("mode") or adaptive_qa_mode(primary_goal) or ADAPTIVE_QA_MODE) if isinstance(plan, dict) else ADAPTIVE_QA_MODE
    return {
        "mode": mode,
        "summary": {
            "primary_status": "PASS" if primary_result.success else "FAIL",
            "generated_check_count": max(0, len(checks) - 1),
            "generated_edge_case_count": len(generated_edges),
            "executed_edge_case_count": executed,
            "passed_edge_case_count": passed,
            "failed_edge_case_count": failed,
            "skipped_edge_case_count": skipped,
            "unsupported_edge_case_count": unsupported,
            "scored_edge_case_count": passed + failed + unsupported,
            "score": round(score, 3),
        },
        "checks": checks,
        "edge_cases": generated_edges,
        "edge_results": edge_results,
    }
