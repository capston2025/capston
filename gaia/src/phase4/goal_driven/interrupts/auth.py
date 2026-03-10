from __future__ import annotations

from typing import Any

from ..evidence_bundle import InterruptResult
from ..goal_kinds import GoalKind


class AuthInterruptPolicy:
    name = "auth_interrupt"

    def match(self, semantics: Any, evidence: Any) -> bool:
        return bool(evidence.raw.get("auth_prompt_visible")) and semantics.goal_kind != GoalKind.AUTH

    def run(self, ctx: Any, semantics: Any, evidence: Any) -> InterruptResult:
        return InterruptResult(
            matched=True,
            status="blocked",
            reason_code="login_required",
            proof="로그인 또는 인증 화면이 감지되어 사용자 개입이 필요합니다.",
            policy_name=self.name,
        )
