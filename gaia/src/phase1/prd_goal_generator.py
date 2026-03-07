"""Heuristic PRD -> goal generation."""
from __future__ import annotations

import re
from typing import Iterable, List

from gaia.src.phase1.prd_bundle import PRDFlow, PRDGoal, PRDRequirement


def _priority_from_requirement(priority: str) -> str:
    raw = str(priority or "").upper()
    if raw in {"P0", "MUST", "HIGH"}:
        return "MUST"
    if raw in {"P1", "SHOULD", "MEDIUM"}:
        return "SHOULD"
    return "MAY"


def _slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9가-힣]+", "_", str(text or "").lower()).strip("_")
    return base or "goal"


def _make_goal(*, prefix: str, idx: int, title: str, goal_text: str, priority: str, source_refs: Iterable[str], success_contract: str, keywords: Iterable[str]) -> PRDGoal:
    return PRDGoal(
        id=f"{prefix}_{idx:03d}_{_slug(title)}",
        title=title.strip() or f"{prefix}_{idx:03d}",
        goal_text=goal_text.strip(),
        priority=priority,
        source_refs=[ref for ref in source_refs if str(ref).strip()],
        success_contract=success_contract,
        keywords=[kw for kw in keywords if str(kw).strip()][:8],
    )


def _keyword_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9가-힣]{2,}", str(text or ""))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(token)
    return ordered


def goals_from_requirements(requirements: List[PRDRequirement]) -> List[PRDGoal]:
    goals: list[PRDGoal] = []
    seen_titles: set[str] = set()
    idx = 1
    for requirement in requirements:
        raw = f"{requirement.title} {requirement.description}".strip()
        lowered = raw.lower()
        refs = [requirement.id, requirement.source_section or ""]
        priority = _priority_from_requirement(requirement.priority)
        keywords = _keyword_tokens(raw)

        def add(title: str, goal_text: str, contract: str) -> None:
            nonlocal idx
            if title in seen_titles:
                return
            seen_titles.add(title)
            goals.append(
                _make_goal(
                    prefix="FR",
                    idx=idx,
                    title=title,
                    goal_text=goal_text,
                    priority=priority,
                    source_refs=refs,
                    success_contract=contract,
                    keywords=keywords,
                )
            )
            idx += 1

        if any(token in lowered for token in ("로그인", "login", "logout", "로그아웃", "인증", "회원가입", "signup", "register")):
            add("인증 흐름 검증", "로그인, 로그아웃, 인증 유도 흐름이 정상 동작하는지 검증해줘", "auth_access_flow")
        if any(token in lowered for token in ("검색", "search", "키워드")):
            add("검색 기능 검증", "검색 기능이 정상 동작하는지 검증해줘", "search_results_validation")
        if any(token in lowered for token in ("필터", "filter", "학점", "구분", "시간대")):
            add("필터 기능 검증", "필터링 로직이 결과 목록과 선택 상태에 반영되는지 검증해줘", "filter_validation_semantic")
        if any(token in lowered for token in ("페이지네이션", "pagination", "페이지 이동", "total 결과 수")):
            add("페이지네이션 검증", "페이지네이션이 결과 목록과 함께 정상 동작하는지 검증해줘", "pagination_validation")
        if any(token in lowered for token in ("위시리스트", "wishlist")):
            if any(token in lowered for token in ("추가", "add", "담기", "저장")):
                add("위시리스트 추가 검증", "과목을 위시리스트에 추가할 수 있는지 검증해줘", "wishlist_state_change")
            if any(token in lowered for token in ("삭제", "remove", "비우기", "clear")):
                add("위시리스트 제거 검증", "위시리스트에서 과목을 제거하거나 비울 수 있는지 검증해줘", "wishlist_state_change")
            if "위시리스트" in raw and not any(token in lowered for token in ("추가", "add", "삭제", "remove", "비우기", "clear", "담기", "저장")):
                add("위시리스트 관리 검증", "위시리스트 추가와 제거 흐름이 정상 동작하는지 검증해줘", "wishlist_state_change")
        if any(token in lowered for token in ("시간표", "timetable")):
            add("시간표 반영 검증", "선택한 과목이나 조합이 시간표에 정상 반영되는지 검증해줘", "timetable_apply")
        if any(token in lowered for token in ("메모", "memo", "note")):
            add("시간표 메모 검증", "시간표 메모 작성과 수정이 정상 동작하는지 검증해줘", "generic_feature_validation")
        if any(token in lowered for token in ("조합", "combination", "후보안")):
            add("조합 생성 검증", "위시리스트 기반 조합 생성과 결과 표시가 정상 동작하는지 검증해줘", "combination_generation")
        if any(token in lowered for token in ("toast", "오류 메시지", "실패 메시지", "피드백")):
            add("피드백 메시지 검증", "성공/실패/유도 메시지가 사용자에게 명확히 노출되는지 검증해줘", "generic_feature_validation")
        if any(token in lowered for token in ("모바일", "반응형", "responsive")):
            add("모바일 핵심 CTA 검증", "모바일 레이아웃에서도 핵심 CTA가 접근 가능한지 검증해줘", "generic_feature_validation")

        if not any(ref == requirement.id for goal in goals for ref in goal.source_refs):
            add(requirement.title, f"{requirement.description or requirement.title} 기능이 정상 동작하는지 검증해줘", "generic_feature_validation")
    return goals


def goals_from_flows(flows: List[PRDFlow]) -> List[PRDGoal]:
    goals: list[PRDGoal] = []
    idx = 1
    for flow in flows:
        if not flow.steps:
            continue
        lowered = " ".join(flow.steps).lower()
        if any(token in lowered for token in ("로그인", "wishlist", "위시리스트", "조합", "시간표")):
            goals.append(
                _make_goal(
                    prefix="FLOW",
                    idx=idx,
                    title=f"{flow.title} E2E 검증",
                    goal_text=f"{flow.title} 핵심 플로우를 처음부터 끝까지 검증해줘",
                    priority="MUST",
                    source_refs=[flow.id, flow.source_section or ""],
                    success_contract="end_to_end_flow",
                    keywords=_keyword_tokens(" ".join(flow.steps)),
                )
            )
            idx += 1
    return goals


def generate_prd_goals(requirements: List[PRDRequirement], flows: List[PRDFlow]) -> List[PRDGoal]:
    goals = goals_from_requirements(requirements)
    goals.extend(goals_from_flows(flows))
    return goals
