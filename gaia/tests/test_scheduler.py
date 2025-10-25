"""
Unit tests for Adaptive Scheduler components.
"""
import pytest
from gaia.src.scheduler.state import GAIAState
from gaia.src.scheduler.scoring import compute_priority_score, compute_score_breakdown
from gaia.src.scheduler.priority_queue import AdaptivePriorityQueue
from gaia.src.scheduler.adaptive_scheduler import AdaptiveScheduler, compute_dom_signature
from gaia.src.scheduler.logger import PriorityLogger


class TestGAIAState:
    """Test state management."""

    def test_initial_state(self):
        state = GAIAState()
        assert len(state.visited_urls) == 0
        assert len(state.completed_test_ids) == 0
        assert state.execution_round == 0

    def test_url_tracking(self):
        state = GAIAState()
        assert state.is_url_new("https://example.com")
        state.mark_url_visited("https://example.com")
        assert not state.is_url_new("https://example.com")

    def test_test_lifecycle(self):
        state = GAIAState()
        state.mark_test_failed("TC001")
        assert state.was_test_failed("TC001")

        state.mark_test_completed("TC001")
        assert state.is_test_completed("TC001")
        assert not state.was_test_failed("TC001")


class TestScoring:
    """Test priority score calculations."""

    def test_base_priority_must(self):
        state = GAIAState()
        item = {"id": "TC001", "priority": "MUST"}
        score = compute_priority_score(item, state)
        assert score == 100

    def test_base_priority_should(self):
        state = GAIAState()
        item = {"id": "TC001", "priority": "SHOULD"}
        score = compute_priority_score(item, state)
        assert score == 60

    def test_new_elements_bonus(self):
        state = GAIAState()
        item = {"id": "TC001", "priority": "MUST", "new_elements": 3}
        score = compute_priority_score(item, state)
        assert score == 100 + (3 * 15)  # 145

    def test_unseen_url_bonus(self):
        state = GAIAState()
        item = {
            "id": "TC001",
            "priority": "MUST",
            "target_url": "https://example.com"
        }
        score = compute_priority_score(item, state)
        assert score == 100 + 20  # 120

    def test_url_already_visited(self):
        state = GAIAState()
        state.mark_url_visited("https://example.com")
        item = {
            "id": "TC001",
            "priority": "MUST",
            "target_url": "https://example.com"
        }
        score = compute_priority_score(item, state)
        assert score == 100  # No bonus

    def test_recent_fail_bonus(self):
        state = GAIAState()
        state.mark_test_failed("TC001")
        item = {"id": "TC001", "priority": "MUST"}
        score = compute_priority_score(item, state)
        assert score == 100 + 10  # 110

    def test_no_dom_change_penalty(self):
        state = GAIAState()
        item = {
            "id": "TC001",
            "priority": "MUST",
            "no_dom_change": True
        }
        score = compute_priority_score(item, state)
        assert score == 100 - 25  # 75

    def test_combined_scoring(self):
        state = GAIAState()
        item = {
            "id": "TC001",
            "priority": "MUST",
            "new_elements": 2,
            "target_url": "https://new.com",
        }
        score = compute_priority_score(item, state)
        # 100 + (2*15) + 20 = 150
        assert score == 150

    def test_score_breakdown(self):
        state = GAIAState()
        item = {
            "id": "TC001",
            "priority": "MUST",
            "new_elements": 1,
        }
        breakdown = compute_score_breakdown(item, state)
        assert breakdown["total_score"] == 115
        assert breakdown["base_priority_score"] == 100
        assert breakdown["dom_bonus"] == 15


class TestPriorityQueue:
    """Test priority queue management."""

    def test_push_and_pop(self):
        state = GAIAState()
        queue = AdaptivePriorityQueue()

        item1 = {"id": "TC001", "priority": "MUST"}
        item2 = {"id": "TC002", "priority": "SHOULD"}

        queue.push(item1, state)
        queue.push(item2, state)

        assert queue.size() == 2

        # Should pop MUST first (higher score)
        popped = queue.pop()
        assert popped["id"] == "TC001"

    def test_rescore_all(self):
        state = GAIAState()
        queue = AdaptivePriorityQueue()

        item = {"id": "TC001", "priority": "MUST", "target_url": "https://example.com"}
        queue.push(item, state)

        # Mark URL as visited
        state.mark_url_visited("https://example.com")

        # Re-score should adjust priorities
        queue.rescore_all(state)
        assert queue.size() == 1

    def test_skip_completed_tests(self):
        state = GAIAState()
        state.mark_test_completed("TC001")

        queue = AdaptivePriorityQueue()
        item = {"id": "TC001", "priority": "MUST"}

        queue.push(item, state)
        assert queue.size() == 0  # Should not add completed test

    def test_get_top_n(self):
        state = GAIAState()
        queue = AdaptivePriorityQueue()

        for i in range(10):
            queue.push({"id": f"TC{i:03d}", "priority": "MUST"}, state)

        top_3 = queue.get_top_n(3)
        assert len(top_3) == 3


class TestAdaptiveScheduler:
    """Test adaptive scheduler orchestration."""

    def test_initialization(self):
        scheduler = AdaptiveScheduler()
        assert scheduler.queue.size() == 0
        assert scheduler.state.execution_round == 0

    def test_ingest_items(self):
        scheduler = AdaptiveScheduler()
        items = [
            {"id": "TC001", "priority": "MUST"},
            {"id": "TC002", "priority": "SHOULD"},
        ]
        scheduler.ingest_items(items)
        assert scheduler.queue.size() == 2
        assert scheduler.stats["total_received"] == 2

    def test_execute_next_batch(self):
        scheduler = AdaptiveScheduler()
        items = [
            {"id": "TC001", "priority": "MUST"},
            {"id": "TC002", "priority": "SHOULD"},
        ]
        scheduler.ingest_items(items)

        # Mock executor
        def mock_executor(item):
            return {
                "status": "success",
                "dom_signature": "abc123",
            }

        results = scheduler.execute_next_batch(mock_executor, max_items=2)
        assert len(results) == 2
        assert scheduler.stats["total_executed"] == 2
        assert scheduler.stats["total_success"] == 2

    def test_failure_retry(self):
        scheduler = AdaptiveScheduler()
        item = {"id": "TC001", "priority": "MUST"}
        scheduler.ingest_items([item])

        # Mock failing executor
        def mock_executor(item):
            return {"status": "failed", "error": "test error"}

        results = scheduler.execute_next_batch(mock_executor, max_items=1)
        assert len(results) == 1
        assert scheduler.stats["total_failed"] == 1

        # Item should be re-added to queue with retry bonus
        assert scheduler.queue.size() > 0

    def test_dom_change_rescoring(self):
        scheduler = AdaptiveScheduler()
        items = [
            {"id": "TC001", "priority": "MUST"},
            {"id": "TC002", "priority": "MUST"},
        ]
        scheduler.ingest_items(items)

        call_count = [0]

        def mock_executor(item):
            call_count[0] += 1
            # Return different DOM on second call
            dom = "dom1" if call_count[0] == 1 else "dom2"
            return {
                "status": "success",
                "dom_signature": dom,
            }

        scheduler.execute_next_batch(mock_executor, max_items=2)

        # Should have triggered rescore due to DOM change
        assert scheduler.stats["rescore_count"] > 0


class TestDOMSignature:
    """Test DOM signature computation."""

    def test_compute_signature(self):
        dom_data = {
            "elements": [
                {"tag": "button", "selector": "#login"},
                {"tag": "input", "selector": "#username"},
            ]
        }
        sig = compute_dom_signature(dom_data)
        assert isinstance(sig, str)
        assert len(sig) == 32  # MD5 hex length

    def test_different_doms_different_signatures(self):
        dom1 = {"elements": [{"tag": "button", "selector": "#login"}]}
        dom2 = {"elements": [{"tag": "input", "selector": "#username"}]}

        sig1 = compute_dom_signature(dom1)
        sig2 = compute_dom_signature(dom2)

        assert sig1 != sig2


class TestPriorityLogger:
    """Test priority logging."""

    def test_log_score(self):
        state = GAIAState()
        logger = PriorityLogger(log_file="/tmp/test_log.json")

        item = {"id": "TC001", "priority": "MUST"}
        logger.log_score(item, state)

        entries = logger.get_entries()
        assert len(entries) == 1
        assert entries[0]["id"] == "TC001"
        assert entries[0]["score"] == 100

    def test_log_summary(self):
        state = GAIAState()
        logger = PriorityLogger(log_file="/tmp/test_log.json")

        logger.log_score({"id": "TC001", "priority": "MUST"}, state)
        logger.log_execution(
            {"id": "TC001", "priority": "MUST"},
            state,
            "success"
        )

        summary = logger.get_summary()
        assert summary["total_entries"] == 2
        assert summary["executed_tests"] == 1
        assert summary["success_count"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
