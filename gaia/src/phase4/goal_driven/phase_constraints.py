"""Phase constraint helpers for GoalDrivenAgent."""
from __future__ import annotations

from typing import Any, Dict, Optional


def is_collect_constraint_unmet(
    goal_constraints: Dict[str, Any],
    goal_metric_value: Optional[float],
) -> bool:
    collect_min = goal_constraints.get("collect_min")
    if collect_min is None:
        return False
    try:
        collect_min_value = float(collect_min)
    except Exception:
        collect_min_value = 0.0
    if goal_metric_value is None:
        # "1개 열기/보기/클릭"류 목표는 초기 metric이 unknown이어도
        # 상호작용 진입을 막지 않아야 루프를 피할 수 있다.
        return collect_min_value > 1.0
    return float(goal_metric_value) + 1e-9 < collect_min_value


def apply_phase_constraints(
    detected_phase: str,
    goal_constraints: Dict[str, Any],
    goal_metric_value: Optional[float],
) -> str:
    if not is_collect_constraint_unmet(goal_constraints, goal_metric_value):
        return detected_phase
    if detected_phase in {"COMPOSE", "APPLY", "VERIFY"}:
        return "COLLECT"
    return detected_phase


def build_constraint_failure_reason(
    goal_constraints: Dict[str, Any],
    goal_metric_value: Optional[float],
) -> Optional[str]:
    if not is_collect_constraint_unmet(goal_constraints, goal_metric_value):
        return None
    collect_min = int(goal_constraints.get("collect_min") or 0)
    metric_label = str(goal_constraints.get("metric_label") or "")
    current_text = "unknown" if goal_metric_value is None else str(int(goal_metric_value))
    return (
        f"목표 제약 미충족: 최소 {collect_min}{metric_label} 수집 전에는 완료로 판정할 수 없습니다. "
        f"(현재 추정값: {current_text}{metric_label})"
    )
