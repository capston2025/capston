"""
GAIA 상태 관리

적응형 스케줄링 결정을 위해 탐색 상태를 추적합니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Set


@dataclass
class GAIAState:
    """
    GAIA의 현재 탐색 상태를 유지합니다.

    속성:
        visited_urls: 이미 탐색한 URL 집합
        visited_dom_signatures: 확인된 DOM 구조 해시 집합
        failed_test_ids: 최근 실패한 테스트 ID 집합
        completed_test_ids: 성공적으로 완료된 테스트 ID 집합
        current_dom_signature: 현재 DOM 구조의 해시
        execution_round: 현재 실행 라운드 번호
    """
    visited_urls: Set[str] = field(default_factory=set)
    visited_dom_signatures: Set[str] = field(default_factory=set)
    failed_test_ids: Set[str] = field(default_factory=set)
    completed_test_ids: Set[str] = field(default_factory=set)
    current_dom_signature: str | None = None
    execution_round: int = 0

    def mark_url_visited(self, url: str) -> None:
        """URL을 방문한 것으로 표시합니다."""
        if url:  # 빈 문자열은 무시
            self.visited_urls.add(url)

    def mark_dom_seen(self, dom_signature: str) -> None:
        """DOM 시그니처를 확인된 것으로 표시합니다."""
        if dom_signature:  # 빈 문자열은 무시
            self.visited_dom_signatures.add(dom_signature)
            self.current_dom_signature = dom_signature

    def mark_test_failed(self, test_id: str) -> None:
        """테스트를 실패로 표시합니다."""
        if test_id:  # 빈 문자열은 무시
            self.failed_test_ids.add(test_id)

    def mark_test_completed(self, test_id: str) -> None:
        """테스트를 완료로 표시합니다."""
        if test_id:  # 빈 문자열은 무시
            self.completed_test_ids.add(test_id)
            # 실패 집합에 있었다면 제거
            self.failed_test_ids.discard(test_id)

    def is_url_new(self, url: str) -> bool:
        """URL을 방문한 적이 없는지 확인합니다."""
        return url not in self.visited_urls

    def is_dom_new(self, dom_signature: str) -> bool:
        """DOM 시그니처가 새로운지 확인합니다."""
        return dom_signature not in self.visited_dom_signatures

    def was_test_failed(self, test_id: str) -> bool:
        """테스트가 최근 실패했는지 확인합니다."""
        return test_id in self.failed_test_ids

    def is_test_completed(self, test_id: str) -> bool:
        """테스트가 이미 완료되었는지 확인합니다."""
        return test_id in self.completed_test_ids

    def increment_round(self) -> None:
        """다음 실행 라운드로 이동합니다."""
        self.execution_round += 1

    def reset(self) -> None:
        """상태를 초기 값으로 재설정합니다."""
        self.visited_urls.clear()
        self.visited_dom_signatures.clear()
        self.failed_test_ids.clear()
        self.completed_test_ids.clear()
        self.current_dom_signature = None
        self.execution_round = 0

    def get_stats(self) -> Dict[str, Any]:
        """
        현재 상태 통계를 반환합니다.

        반환:
            상태 지표를 담은 dict
        """
        return {
            "visited_urls_count": len(self.visited_urls),
            "visited_dom_count": len(self.visited_dom_signatures),
            "failed_tests_count": len(self.failed_test_ids),
            "completed_tests_count": len(self.completed_test_ids),
            "execution_round": self.execution_round,
        }
