"""Memory retrieval logic for prompt-time hints and recovery guidance."""
from __future__ import annotations

import re
from typing import Sequence

from .models import MemorySuggestion
from .store import MemoryStore


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[A-Za-z0-9가-힣_]+", (text or "").lower()) if len(tok) >= 2}


def _overlap_score(left: Sequence[str], right: Sequence[str]) -> float:
    lset = set(left)
    rset = set(right)
    if not lset or not rset:
        return 0.0
    inter = len(lset.intersection(rset))
    union = len(lset.union(rset))
    return float(inter) / float(union or 1)


class MemoryRetriever:
    def __init__(self, store: MemoryStore):
        self.store = store

    def _score_row(self, row: dict, goal_tokens: set[str], history_tokens: set[str]) -> float:
        text_blob = " ".join(
            [
                str(row.get("action") or ""),
                str(row.get("selector") or ""),
                str(row.get("full_selector") or ""),
                str(row.get("reason") or ""),
                str(row.get("reason_code") or ""),
            ]
        )
        row_tokens = _tokenize(text_blob)
        score = 0.0
        score += 1.3 * _overlap_score(goal_tokens, row_tokens)
        score += 0.6 * _overlap_score(history_tokens, row_tokens)
        if int(row.get("success") or 0) == 1:
            score += 0.4
        if int(row.get("effective") or 0) == 1:
            score += 0.4
        if row.get("reason_code") in {"ok", "no_state_change", "not_found", "not_actionable"}:
            score += 0.1
        return score

    @staticmethod
    def _to_suggestion(source: str, row: dict, confidence: float) -> MemorySuggestion:
        selector = str(row.get("full_selector") or row.get("selector") or "")
        reason_code = str(row.get("reason_code") or "unknown")
        action = str(row.get("action") or "")
        reason = str(row.get("reason") or "").strip()
        changed = bool(int(row.get("changed") or 0))
        summary = (
            f"action={action}, selector={selector or '-'}, reason_code={reason_code}, "
            f"changed={changed}, reason={reason or '-'}"
        )
        return MemorySuggestion(
            source=source,
            reason_code=reason_code,
            summary=summary,
            selector_hint=selector,
            action=action,
            confidence=max(0.0, min(1.0, confidence)),
        )

    def retrieve_lightweight(
        self,
        *,
        domain: str,
        goal_text: str,
        action_history: Sequence[str],
        success_limit: int = 3,
        failure_limit: int = 2,
    ) -> list[MemorySuggestion]:
        if not self.store.enabled or not domain:
            return []
        rows = self.store.query_actions(domain=domain, limit=240)
        if not rows:
            return []
        goal_tokens = _tokenize(goal_text)
        history_tokens = _tokenize(" ".join(action_history[-6:]))
        ranked = sorted(
            rows,
            key=lambda row: self._score_row(row, goal_tokens, history_tokens),
            reverse=True,
        )
        successes: list[MemorySuggestion] = []
        failures: list[MemorySuggestion] = []
        for row in ranked:
            is_success = int(row.get("success") or 0) == 1 and int(row.get("effective") or 0) == 1
            confidence = self._score_row(row, goal_tokens, history_tokens)
            if is_success and len(successes) < max(1, success_limit):
                successes.append(self._to_suggestion("success_pattern", row, confidence))
            if (not is_success) and len(failures) < max(1, failure_limit):
                failures.append(self._to_suggestion("failure_pattern", row, confidence))
            if len(successes) >= success_limit and len(failures) >= failure_limit:
                break
        return [*successes, *failures]

    def retrieve_recovery(
        self,
        *,
        domain: str,
        goal_text: str,
        reason_code: str,
        limit: int = 5,
    ) -> list[MemorySuggestion]:
        if not self.store.enabled or not domain:
            return []
        target_codes = [reason_code] if reason_code else ["no_state_change", "not_found", "not_actionable"]
        rows = self.store.query_actions(domain=domain, reason_codes=target_codes, limit=max(20, limit * 5))
        if not rows:
            return []
        goal_tokens = _tokenize(goal_text)
        ranked = sorted(
            rows,
            key=lambda row: self._score_row(row, goal_tokens, set()),
            reverse=True,
        )
        out: list[MemorySuggestion] = []
        for row in ranked[: max(1, limit)]:
            out.append(self._to_suggestion("recovery", row, self._score_row(row, goal_tokens, set())))
        return out

    @staticmethod
    def format_for_prompt(
        suggestions: Sequence[MemorySuggestion],
        *,
        max_items: int = 6,
    ) -> str:
        if not suggestions:
            return ""
        lines = []
        for item in list(suggestions)[: max(1, max_items)]:
            lines.append(
                f"- [{item.source}] action={item.action or '-'} reason_code={item.reason_code} "
                f"hint={item.selector_hint or '-'} note={item.summary}"
            )
        return "\n".join(lines)

