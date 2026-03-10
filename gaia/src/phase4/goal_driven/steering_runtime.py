from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from .models import TestGoal


def build_steering_prompt(agent) -> str:
    policy = agent._steering_policy if isinstance(agent._steering_policy, dict) else {}
    if not policy or agent._steering_remaining_steps <= 0:
        return ""
    rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
    assertions = policy.get("assertions") if isinstance(policy.get("assertions"), list) else []
    if not rules and not assertions:
        return ""
    lines: List[str] = []
    lines.append("\n11. **사용자 스티어링 정책(우선 적용)**")
    lines.append(f"\n   - 남은 TTL: {int(agent._steering_remaining_steps)} steps")
    for row in rules[:8]:
        if not isinstance(row, dict):
            continue
        rule_type = str(row.get("type") or "").strip()
        enforcement = str(row.get("enforcement") or "soft").strip().lower()
        tag = str(row.get("tag") or "").strip()
        need = row.get("need")
        if isinstance(need, list):
            need_text = ",".join(str(x) for x in need if str(x).strip())
        else:
            need_text = str(need or "").strip()
        body = tag or need_text
        if not rule_type or not body:
            continue
        lines.append(f"\n   - {enforcement.upper()} {rule_type}: {body}")
    for row in assertions[:4]:
        if not isinstance(row, dict):
            continue
        a_type = str(row.get("type") or "").strip()
        need = row.get("need")
        if isinstance(need, list):
            need_text = ",".join(str(x) for x in need if str(x).strip())
        else:
            need_text = str(need or "").strip()
        if not a_type:
            continue
        lines.append(f"\n   - ASSERT {a_type}: {need_text}")
    lines.append(
        "\n   - HARD 규칙은 반드시 준수하고, SOFT 규칙은 가능한 경우 우선 적용하세요."
    )
    return "".join(lines)


def build_goal_constraint_prompt(agent) -> str:
    collect_min = agent._goal_constraints.get("collect_min")
    metric_label = str(agent._goal_constraints.get("metric_label") or "단위")
    require_no_navigation = bool(agent._goal_constraints.get("require_no_navigation"))
    current_view_only = bool(agent._goal_constraints.get("current_view_only"))
    forbid_search_action = bool(agent._goal_constraints.get("forbid_search_action"))
    lines: List[str] = []
    if collect_min is not None:
        current = agent._goal_metric_value
        current_text = "unknown" if current is None else str(int(current))
        apply_target = agent._goal_constraints.get("apply_target")
        target_line = ""
        if apply_target is not None:
            target_line = f"\n   - 최종 목표값: {int(apply_target)}{metric_label}"
        lines.append(
            "\n9. **목표 제약(강제)**"
            f"\n   - 현재 추정값: {current_text}{metric_label}"
            f"\n   - 최소 수집 기준: {int(collect_min)}{metric_label}"
            f"{target_line}"
            "\n   - 최소 수집 기준 미만이면 단계 전환 CTA를 선택하지 말고 수집 액션만 선택하세요."
        )
    if require_no_navigation:
        lines.append(
            "\n10. **페이지 고정 제약(강제)**"
            "\n   - 목표가 '페이지 이동 없이' 검증이므로 URL이 바뀌는 내비게이션 액션은 금지합니다."
            "\n   - 링크 이동보다 현재 페이지의 row/panel/modal/open/expand 계열 상호작용을 우선 선택하세요."
        )
    if current_view_only or forbid_search_action:
        lines.append(
            "\n11. **현재 화면/검색 금지 제약(강제)**"
            "\n   - 현재 화면에 이미 보이는 카드/행/목록 안에서만 대상을 찾아야 합니다."
            "\n   - 검색 입력/검색 버튼/검색 제출(Enter)은 금지합니다."
            "\n   - 타깃 텍스트와 가장 잘 맞는 현재 화면의 카드/행 내부 CTA를 우선 선택하세요."
        )
    steering_rule = build_steering_prompt(agent)
    if steering_rule:
        lines.append(steering_rule)
    return "".join(lines)


def activate_steering_policy(agent, goal: TestGoal) -> None:
    agent._steering_policy = {}
    agent._steering_remaining_steps = 0
    data = goal.test_data if isinstance(goal.test_data, dict) else {}
    policy = data.get("steering_policy")
    if not isinstance(policy, dict):
        return
    rules = policy.get("rules") if isinstance(policy.get("rules"), list) else []
    assertions = policy.get("assertions") if isinstance(policy.get("assertions"), list) else []
    if not rules and not assertions:
        return
    try:
        ttl = int(policy.get("ttl_steps") or 8)
    except Exception:
        ttl = 8
    ttl = max(3, min(15, ttl))
    scope = str(policy.get("scope") or "next_n_steps").strip().lower() or "next_n_steps"
    bound_goal_id = str(policy.get("bound_goal_id") or "").strip()
    bound_phase = str(policy.get("bound_phase") or "").strip().upper()
    bound_origin = str(policy.get("bound_origin") or "").strip()

    if scope in {"current_goal", "goal"} and not bound_goal_id:
        bound_goal_id = str(goal.id)
    if scope in {"current_phase", "phase"} and not bound_phase:
        bound_phase = str(agent._runtime_phase or "").strip().upper()
    if scope in {"current_origin", "origin"} and not bound_origin:
        try:
            parsed = urlparse(str(goal.start_url or ""))
            if parsed.scheme and parsed.netloc:
                bound_origin = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            bound_origin = ""

    agent._steering_policy = {
        "version": str(policy.get("version") or "steering.v1"),
        "raw_text": str(policy.get("raw_text") or ""),
        "scope": scope,
        "ttl_steps": ttl,
        "ttl_remaining": ttl,
        "priority": str(policy.get("priority") or "normal").strip().lower() or "normal",
        "rules": list(rules),
        "assertions": list(assertions),
        "bound_origin": bound_origin,
        "bound_goal_id": bound_goal_id,
        "bound_phase": bound_phase,
        "compile_confidence": policy.get("compile_confidence"),
        "_soft_relaxed_once": bool(policy.get("_soft_relaxed_once", False)),
    }
    agent._steering_remaining_steps = ttl
