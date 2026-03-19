from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, Iterable, List, Sequence

from .goal_kinds import GoalKind


_CANONICAL_DESTINATION_ALIASES: Dict[str, Sequence[str]] = {
    "wishlist": ("위시리스트", "찜", "관심 목록", "관심목록", "저장 목록", "내 목록", "wishlist", "saved", "favorites"),
    "cart": ("장바구니", "카트", "cart", "basket"),
    "timetable": ("시간표", "내 시간표", "timetable", "schedule"),
    "selection": ("선택 목록", "선택한 목록", "선택 결과", "selected list", "selected items"),
}

_AUTH_TOKENS = ("로그인", "회원가입", "인증", "sign in", "log in", "login", "auth", "otp", "2fa")
_OPEN_DETAIL_TOKENS = ("열어", "열기", "상세", "detail", "open")
_APPLY_TOKENS = ("적용", "선택", "추가해", "넣어", "apply", "select")
_MUTATION_REQUIRED_TOKENS = (
    "클릭", "click", "눌", "누르", "tap", "press",
    "담기", "담아", "담고", "추가", "넣어", "넣고",
    "삭제", "제거", "remove", "clear", "비우",
    "적용", "apply", "select",
)


@dataclass
class GoalSemantics:
    goal_kind: GoalKind
    mutation_direction: str = ""
    target_terms: List[str] = field(default_factory=list)
    destination_terms: List[str] = field(default_factory=list)
    destination_aliases: Dict[str, List[str]] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    quoted_targets: List[str] = field(default_factory=list)
    already_satisfied_ok: bool = True
    mutate_required: bool = False
    explicit_auth_goal: bool = False
    require_no_navigation: bool = False
    current_view_only: bool = False
    forbid_search_action: bool = False
    steer_constraints: Dict[str, Any] = field(default_factory=dict)


def _default_normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _goal_text_chunks(goal: Any) -> List[str]:
    chunks: List[str] = []
    for attr in ("name", "description"):
        value = getattr(goal, attr, "") or ""
        if str(value).strip():
            chunks.append(str(value))
    success = getattr(goal, "success_criteria", None)
    if isinstance(success, list):
        for item in success:
            text = str(item or "").strip()
            if text:
                chunks.append(text)
    return chunks


def _extract_quotes(texts: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    output: List[str] = []
    patterns = [r'"([^"]{2,120})"', r"'([^']{2,120})'", r"“([^”]{2,120})”"]
    for text in texts:
        for pattern in patterns:
            for match in re.findall(pattern, text or ""):
                token = str(match or "").strip()
                if token and token not in seen:
                    seen.add(token)
                    output.append(token)
    return output


def _extract_destination_aliases(texts: Iterable[str], normalize_fn: Callable[[str], str]) -> Dict[str, List[str]]:
    combined = " ".join(str(t or "") for t in texts)
    norm = normalize_fn(combined)
    matched: Dict[str, List[str]] = {}
    for canonical, aliases in _CANONICAL_DESTINATION_ALIASES.items():
        for alias in aliases:
            if normalize_fn(alias) and normalize_fn(alias) in norm:
                matched.setdefault(canonical, []).append(alias)
    return matched


def _extract_goal_kind(texts: Iterable[str], constraints: Dict[str, Any], filter_style: bool, verification_style: bool, destination_aliases: Dict[str, List[str]]) -> GoalKind:
    joined = " ".join(str(t or "") for t in texts).lower()
    mutation_direction = str((constraints or {}).get("mutation_direction") or "").strip().lower()
    explicit_auth_goal = any(token in joined for token in _AUTH_TOKENS)
    if filter_style:
        return GoalKind.FILTER
    if explicit_auth_goal:
        return GoalKind.AUTH
    if mutation_direction == "clear" and destination_aliases:
        return GoalKind.CLEAR_LIST
    if mutation_direction == "decrease" and destination_aliases:
        return GoalKind.REMOVE_FROM_LIST
    if mutation_direction == "increase" and destination_aliases:
        return GoalKind.ADD_TO_LIST
    if any(token in joined for token in _OPEN_DETAIL_TOKENS):
        return GoalKind.OPEN_DETAIL
    if verification_style:
        return GoalKind.VERIFY_STATIC
    if any(token in joined for token in _APPLY_TOKENS) and destination_aliases:
        return GoalKind.APPLY_SELECTION
    return GoalKind.GENERIC_FALLBACK


def extract_goal_semantics(
    goal: Any,
    constraints: Dict[str, Any] | None,
    *,
    normalize_fn: Callable[[str], str] | None = None,
    filter_style: bool = False,
    verification_style: bool = False,
) -> GoalSemantics:
    normalize = normalize_fn or _default_normalize
    goal_constraints = dict(constraints or {})
    texts = _goal_text_chunks(goal)
    quoted_targets = _extract_quotes(texts)
    target_terms = list(goal_constraints.get("target_terms") or [])
    if quoted_targets:
        for token in quoted_targets:
            if token not in target_terms:
                target_terms.append(token)
    destination_aliases = _extract_destination_aliases(texts, normalize)
    destination_terms: List[str] = []
    for aliases in destination_aliases.values():
        for alias in aliases:
            if alias not in destination_terms:
                destination_terms.append(alias)
    goal_kind = _extract_goal_kind(texts, goal_constraints, filter_style, verification_style, destination_aliases)
    explicit_auth_goal = goal_kind == GoalKind.AUTH
    mutation_direction = str(goal_constraints.get("mutation_direction") or "").strip().lower()
    joined = " ".join(str(t or "") for t in texts).lower()
    explicit_mutation_request = any(token in joined for token in _MUTATION_REQUIRED_TOKENS)
    mutate_required = goal_constraints.get("mutate_required", None)
    if mutate_required is None:
        mutate_required = bool(
            explicit_mutation_request
            and goal_kind in {
                GoalKind.ADD_TO_LIST,
                GoalKind.REMOVE_FROM_LIST,
                GoalKind.CLEAR_LIST,
                GoalKind.APPLY_SELECTION,
            }
        )
    return GoalSemantics(
        goal_kind=goal_kind,
        mutation_direction=mutation_direction,
        target_terms=target_terms,
        destination_terms=destination_terms,
        destination_aliases={k: list(v) for k, v in destination_aliases.items()},
        constraints=goal_constraints,
        quoted_targets=quoted_targets,
        already_satisfied_ok=bool(goal_constraints.get("already_satisfied_ok", True)),
        mutate_required=bool(mutate_required),
        explicit_auth_goal=explicit_auth_goal,
        require_no_navigation=bool(goal_constraints.get("require_no_navigation")),
        current_view_only=bool(goal_constraints.get("current_view_only")),
        forbid_search_action=bool(goal_constraints.get("forbid_search_action")),
        steer_constraints={},
    )
