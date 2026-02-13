from gaia.src.phase4.goal_driven.exploratory_agent import ExploratoryAgent


class _Page:
    def __init__(self, count: int):
        self.interactive_elements = [object()] * count


def _build_agent_for_metrics() -> ExploratoryAgent:
    agent = ExploratoryAgent.__new__(ExploratoryAgent)
    agent._visited_pages = {"a": _Page(3), "b": _Page(2)}
    agent._tested_elements = {"e1", "e2", "e3"}
    agent._action_type_counts = {"click": 4, "fill": 1}
    agent._state_transitions = {"a->b"}
    agent._branch_outcomes = {
        "console_clean",
        "action_success",
        "same_page",
        "decision_no_action",
    }
    return agent


def test_calculate_coverage_contains_flow_metrics():
    agent = _build_agent_for_metrics()

    coverage = agent._calculate_coverage()

    assert coverage["total_interactive_elements"] == 5
    assert coverage["tested_elements"] == 3
    assert coverage["total_pages"] == 2
    assert coverage["state_transitions"] == 1
    assert coverage["action_type_counts"]["click"] == 4
    assert coverage["action_type_coverage"] == 50.0  # 2 of 4 action types
    assert "decision_no_action" in coverage["branch_outcomes"]


def test_calculate_coverage_handles_empty_state():
    agent = ExploratoryAgent.__new__(ExploratoryAgent)
    agent._visited_pages = {}
    agent._tested_elements = set()
    agent._action_type_counts = {}
    agent._state_transitions = set()
    agent._branch_outcomes = set()

    coverage = agent._calculate_coverage()

    assert coverage["coverage_percentage"] == 0
    assert coverage["action_type_coverage"] == 0
    assert coverage["branch_coverage"] == 0
