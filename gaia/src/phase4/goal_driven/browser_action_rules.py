"""Browser action rules — 브라우저 자동화 시 모델이 따라야 할 행동 규칙 모음.

이 모듈은 LLM에 주입되는 "웹 조작 규칙층"을 한 곳에 모은다.
domain-specific 단어를 포함하지 않으며, OpenClaw raw-first 철학을 유지한다.

역할 분리:
- 이 모듈: 모델이 행동을 "선택"할 때의 사전 규칙 (pre-action guidance)
- recovery/verifier: 모델이 행동을 "실행한 뒤"의 사후 검증 (post-action validation)
"""

from __future__ import annotations

import os
from typing import Any, List


# ── Core browser action rules ──────────────────────────────────────────
# 각 규칙은 짧고 강한 명령문으로, LLM 프롬프트에 직접 삽입된다.
# domain-specific 키워드는 사용하지 않는다.

ANTI_LOOP_RULES: list[str] = [
    "같은 ref_id에 같은 action이 직전 2턴 안에 이미 실패했다면 반복하지 말고 다른 경로를 탐색하세요.",
    "wait를 연속 2회 이상 선택했는데 DOM에 변화가 없으면 wait 대신 구체적 action을 시도하세요.",
    "최근 피드백이 no-op/duplicate/이미 존재 등의 무변화 응답이면 같은 CTA를 반복하지 마세요.",
]

STALE_REF_RULES: list[str] = [
    "페이지 네비게이션이 발생했거나 URL이 바뀌면 이전 턴의 ref_id를 신뢰하지 마세요. 현재 DOM에 존재하는 ref만 사용하세요.",
    "DOM 리스트에 없는 ref_id나 element_id를 추측하지 마세요.",
    "DOM이 'DOM 변경 없음'으로 표시되어도 ref는 현재 DOM 기준으로만 사용하세요.",
]

LOADING_STATE_RULES: list[str] = [
    "loading spinner/skeleton이 보이면 짧은 wait 후 다시 DOM을 확인하세요. 단, 같은 loading 상태에서 wait를 2회 이상 반복하지 마세요.",
    "연속 3턴 이상 DOM에 의미 있는 변화가 없으면 scroll, 다른 요소 클릭 등 구조 탐색으로 전환하세요.",
]

RESULT_RECOVERY_RULES: list[str] = [
    "실행/생성/apply 이후 `0개`, `없음`, `no results` 같은 명시적 zero-result surface가 뜨면 숨겨진 결과를 스크롤로 찾지 마세요. 현재 입력/선택/설정이 부족하다고 보고 수집 또는 파라미터 조정으로 돌아가세요.",
    "전면 모달/오버레이를 닫으려다 stale ref/not_found가 나오면 배경 탐색으로 넘어가지 말고, 새 snapshot에서 현재 전면 surface의 닫기/확인 CTA를 다시 찾으세요.",
]

DIALOG_AVOIDANCE_RULES: list[str] = [
    "alert/confirm/prompt 등 blocking dialog를 유발할 수 있는 action은 피하세요. 삭제/초기화/reset 계열 버튼은 목표가 직접 요구하지 않는 한 선택하지 마세요.",
    "모달/오버레이가 실제로 열려 있지 않다면 닫기/close/dismiss를 선택하지 마세요.",
]

CONTEXT_SHIFT_RULES: list[str] = [
    "로그인/인증 surface가 보이면 현재 surface를 새 화면으로 간주하고, 제공된 테스트 데이터가 있으면 그 화면 안에서만 처리하세요.",
    "로그아웃, 다운로드, 전체삭제 같은 전역/파괴적 컨트롤은 목표가 직접 요구하지 않는 한 선택하지 마세요.",
]

DOM_TRUST_RULES: list[str] = [
    "phase 이름이나 wrapper 상태보다 최신 DOM/스크린샷을 우선 신뢰하세요.",
    "현재 화면에서 직접 연결된 증거가 더 강한 쪽을 고르세요.",
    "방금 뜬 임시 피드백(toast/snackbar/banner)보다 지속 증거(row, counter, reveal surface)를 우선 확인하세요.",
    "`select`를 고를 때는 해당 combobox의 `options=[...]` 또는 바로 아래 subtree에 실제로 보이는 옵션만 선택하세요.",
]

GOAL_COMPLETION_RULES: list[str] = [
    "목표가 이미 달성됐다고 판단되면 `is_goal_achieved=true`와 이유를 반환하세요.",
    "DOM 요소에 `[ref=...]`가 표시된 경우 반드시 해당 `ref_id`를 응답에 포함하세요.",
]


def _numbered_rules(rules: list[str], start: int = 1) -> tuple[str, int]:
    """규칙 리스트를 번호 매겨서 문자열로 반환한다."""
    lines = []
    idx = start
    for rule in rules:
        lines.append(f"{idx}. {rule}")
        idx += 1
    return "\n".join(lines), idx


def get_recent_prompt_history_limit(default: int = 5) -> int:
    """프롬프트에 포함할 최근 실행 기록 길이를 반환한다.

    0 이하면 전체를 사용한다.
    """
    raw = str(os.getenv("GAIA_LLM_RECENT_HISTORY_LIMIT", str(default)) or "").strip()
    try:
        limit = int(raw)
    except Exception:
        limit = default
    return max(0, limit)


def slice_recent_prompt_items(items: list[str] | None, default: int = 5) -> list[str]:
    entries = list(items or [])
    limit = get_recent_prompt_history_limit(default=default)
    if limit == 0:
        return entries
    return entries[-limit:]


def build_browser_action_rules_block() -> str:
    """LLM 프롬프트에 삽입할 전체 브라우저 행동 규칙 블록을 생성한다."""
    all_rules: list[str] = []
    all_rules.extend(DOM_TRUST_RULES)
    all_rules.extend(ANTI_LOOP_RULES)
    all_rules.extend(STALE_REF_RULES)
    all_rules.extend(LOADING_STATE_RULES)
    all_rules.extend(RESULT_RECOVERY_RULES)
    all_rules.extend(DIALOG_AVOIDANCE_RULES)
    all_rules.extend(CONTEXT_SHIFT_RULES)
    all_rules.extend(GOAL_COMPLETION_RULES)

    text, _ = _numbered_rules(all_rules)
    return f"## 작업 규칙\n{text}"


def build_browser_action_rules_for_agent(agent: Any) -> str:
    """agent 상태에 따라 동적으로 규칙을 조합한다.

    현재는 정적 규칙만 반환하지만,
    향후 agent._action_history 기반으로 특정 규칙을 강조할 수 있다.
    """
    base = build_browser_action_rules_block()

    recent_actions = slice_recent_prompt_items(list(getattr(agent, "_action_history", []) or []), default=5)
    recent_feedback = slice_recent_prompt_items(list(getattr(agent, "_action_feedback", []) or []), default=5)

    reinforcements: List[str] = []

    if _detect_repeated_wait(recent_actions):
        reinforcements.append(
            "⚠ 최근 wait가 연속 반복됐습니다. DOM 변화가 없다면 wait 대신 구체적 action을 시도하세요."
        )

    if _detect_repeated_failure(recent_actions, recent_feedback):
        reinforcements.append(
            "⚠ 최근 같은 action이 반복 실패했습니다. 다른 ref나 다른 접근 경로를 탐색하세요."
        )

    if reinforcements:
        base += "\n\n## 긴급 행동 경고\n" + "\n".join(reinforcements)

    return base


def _detect_repeated_wait(recent_actions: list[str]) -> bool:
    """최근 액션에서 wait가 2회 이상 연속인지 확인한다."""
    if len(recent_actions) < 2:
        return False
    last_two = [str(a).strip().lower() for a in recent_actions[-2:]]
    return all("wait" in a.split(":")[0].split("(")[0].split(" ")[0] for a in last_two)


def _detect_repeated_failure(
    recent_actions: list[str],
    recent_feedback: list[str],
) -> bool:
    """최근 피드백에서 같은 실패가 2회 이상 반복인지 확인한다."""
    if len(recent_feedback) < 2:
        return False
    failure_markers = ("no-op", "no_op", "실패", "fail", "error", "duplicate", "이미")
    last_two_fb = [str(f).strip().lower() for f in recent_feedback[-2:]]
    return all(any(marker in fb for marker in failure_markers) for fb in last_two_fb)
