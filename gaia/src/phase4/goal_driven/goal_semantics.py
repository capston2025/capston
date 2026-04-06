from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Dict, Iterable, List, Sequence

from .goal_kinds import GoalKind

_AUTH_TOKENS = ("로그인", "회원가입", "인증", "sign in", "log in", "login", "auth", "otp", "2fa")
_OPEN_DETAIL_TOKENS = ("열어", "열기", "상세", "detail", "open")
_APPLY_TOKENS = ("적용", "선택", "추가해", "넣어", "apply", "select")
_MUTATION_REQUIRED_TOKENS = (
    "클릭", "click", "눌", "누르", "tap", "press",
    "담기", "담아", "담고", "추가", "넣어", "넣고",
    "삭제", "제거", "remove", "clear", "비우",
    "적용", "apply", "select",
)
_TARGET_TERM_NOISE_TOKENS = {
    "버튼", "버튼을", "클릭", "눌러서", "누른다음에", "테스트", "테스트해봐",
    "반영", "반영이", "추가", "삭제", "제거", "있으면", "있다면", "있었으면",
    "추가되어있었으면", "추가되어 있었으면", "already", "already_present",
}
_TARGET_TERM_ACTION_TOKENS = {
    "바로추가",
    "추가",
    "담기",
    "삭제",
    "제거",
    "강의평",
    "강의평보기",
    "상세정보보기",
    "보기",
    "열기",
    "로그인",
    "login",
    "remove",
    "delete",
    "add",
    "apply",
}
_TARGET_TERM_NOISE_SUFFIXES = (
    "해봐",
    "해주세요",
    "해줘",
    "되는지",
    "했는지",
    "있는지",
    "있었으면",
    "있으면",
    "이었다면",
    "라면",
)

_DESTINATION_CAPTURE_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(
        r"([가-힣A-Za-z0-9][가-힣A-Za-z0-9\s/_()\-]{1,40}?)\s*(?:를|을)\s*(?:모두\s*)?(?:비우|비워|삭제|제거|clear|empty)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([가-힣A-Za-z0-9][가-힣A-Za-z0-9\s/_()\-]{1,40}?)\s*(?:에|으로|로)\s*(?:담|넣|추가|저장|반영|적용)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([가-힣A-Za-z0-9][가-힣A-Za-z0-9\s/_()\-]{1,40}?)\s*(?:에서)\s*(?:삭제|제거|빼|remove|delete|clear)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:add|save|apply|move|send)\b.*?\b(?:to|into|in)\s+([A-Za-z][A-Za-z0-9\s/_()\-]{1,40}?)(?=\s+(?:and|then|after|before|with|where|that|which|verify|check)\b|[,.]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:remove|delete|clear|empty)\b.*?\bfrom\s+([A-Za-z][A-Za-z0-9\s/_()\-]{1,40}?)(?=\s+(?:and|then|after|before|with|where|that|which|verify|check)\b|[,.]|$)",
        re.IGNORECASE,
    ),
)
_DESTINATION_STRIP_PREFIXES = (
    "버튼을",
    "버튼",
    "클릭해서",
    "클릭해",
    "눌러서",
    "눌러",
    "누른 뒤",
    "누른 후",
    "after",
    "then",
    "to",
    "into",
    "from",
    "in",
)
_DESTINATION_NOISE_TOKENS = {
    "페이지",
    "화면",
    "버튼",
    "링크",
    "cta",
    "goal",
    "target",
    "result",
    "results",
    "목표",
    "결과",
    "동작",
    "기능",
    "화면으로",
}


def _is_actionish_target_term(normalized_term: str) -> bool:
    compact = re.sub(r"\s+", "", str(normalized_term or "").strip().lower())
    return bool(compact and compact in _TARGET_TERM_ACTION_TOKENS)


@dataclass
class GoalSemantics:
    goal_kind: GoalKind
    mutation_direction: str = ""
    remediation_direction: str = ""
    remediation_trigger: str = ""
    conditional_remediation: bool = False
    requires_pre_action_membership_check: bool = False
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


def _clean_destination_candidate(raw: str, normalize_fn: Callable[[str], str]) -> str:
    candidate = re.sub(r"\s+", " ", str(raw or "")).strip(" '\"“”‘’()[]{}")
    if not candidate:
        return ""
    words = [token for token in candidate.split() if token]
    if len(words) > 3:
        candidate = " ".join(words[-3:])
    normalized = normalize_fn(candidate)
    if not normalized:
        return ""

    prefix_changed = True
    while prefix_changed:
        prefix_changed = False
        for prefix in _DESTINATION_STRIP_PREFIXES:
            prefix_norm = normalize_fn(prefix)
            if prefix_norm and normalized.startswith(prefix_norm + " "):
                candidate = candidate[len(prefix) :].strip()
                normalized = normalize_fn(candidate)
                prefix_changed = True
                break

    if not normalized:
        return ""
    if normalized in _DESTINATION_NOISE_TOKENS:
        return ""
    if any(token in normalized for token in _AUTH_TOKENS):
        return ""
    if _is_actionish_target_term(normalized):
        return ""
    return candidate


def _extract_destination_aliases(texts: Iterable[str], normalize_fn: Callable[[str], str]) -> Dict[str, List[str]]:
    matched: Dict[str, List[str]] = {}
    seen: set[str] = set()
    for text in texts:
        for pattern in _DESTINATION_CAPTURE_PATTERNS:
            for raw in pattern.findall(str(text or "")):
                candidate = _clean_destination_candidate(str(raw or ""), normalize_fn)
                normalized = normalize_fn(candidate)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                matched[normalized] = [candidate]
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


def _sanitize_target_terms(
    target_terms: Sequence[str],
    destination_aliases: Dict[str, List[str]],
    normalize_fn: Callable[[str], str],
) -> List[str]:
    destination_norms = {
        normalize_fn(alias)
        for aliases in destination_aliases.values()
        for alias in aliases
        if normalize_fn(alias)
    }
    output: List[str] = []
    seen: set[str] = set()
    for term in target_terms:
        raw = str(term or "").strip()
        norm = normalize_fn(raw)
        if not norm or norm in seen:
            continue
        if norm in _TARGET_TERM_NOISE_TOKENS:
            continue
        if _is_actionish_target_term(norm):
            continue
        if any(norm.endswith(suffix) for suffix in _TARGET_TERM_NOISE_SUFFIXES):
            continue
        if any(dest == norm or dest in norm or norm in dest for dest in destination_norms):
            continue
        seen.add(norm)
        output.append(raw)
    return output


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
    target_terms = _sanitize_target_terms(target_terms, destination_aliases, normalize)
    goal_kind = _extract_goal_kind(texts, goal_constraints, filter_style, verification_style, destination_aliases)
    explicit_auth_goal = goal_kind == GoalKind.AUTH
    mutation_direction = str(goal_constraints.get("mutation_direction") or "").strip().lower()
    remediation_direction = str(goal_constraints.get("remediation_direction") or "").strip().lower()
    remediation_trigger = str(goal_constraints.get("remediation_trigger") or "").strip().lower()
    conditional_remediation = bool(goal_constraints.get("conditional_remediation"))
    requires_pre_action_membership_check = bool(goal_constraints.get("requires_pre_action_membership_check"))
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
    if remediation_trigger:
        mutate_required = True
    already_satisfied_ok = bool(goal_constraints.get("already_satisfied_ok", True))
    if remediation_trigger:
        already_satisfied_ok = False
    return GoalSemantics(
        goal_kind=goal_kind,
        mutation_direction=mutation_direction,
        remediation_direction=remediation_direction,
        remediation_trigger=remediation_trigger,
        conditional_remediation=conditional_remediation,
        requires_pre_action_membership_check=requires_pre_action_membership_check,
        target_terms=target_terms,
        destination_terms=destination_terms,
        destination_aliases={k: list(v) for k, v in destination_aliases.items()},
        constraints=goal_constraints,
        quoted_targets=quoted_targets,
        already_satisfied_ok=already_satisfied_ok,
        mutate_required=bool(mutate_required),
        explicit_auth_goal=explicit_auth_goal,
        require_no_navigation=bool(goal_constraints.get("require_no_navigation")),
        current_view_only=bool(goal_constraints.get("current_view_only")),
        forbid_search_action=bool(goal_constraints.get("forbid_search_action")),
        steer_constraints={},
    )
