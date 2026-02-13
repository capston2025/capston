from gaia.src.phase4.goal_driven.exploratory_agent import ExploratoryAgent


def test_compute_coverage_metrics_contains_flow_metrics():
    coverage = ExploratoryAgent._compute_coverage_metrics(
        total_elements=5,
        tested_elements=3,
        total_pages=2,
        action_type_counts={"click": 4, "fill": 1},
        state_transitions={"a->b"},
        branch_outcomes={
            "console_clean",
            "action_success",
            "same_page",
            "decision_no_action",
        },
    )

    assert coverage["total_interactive_elements"] == 5
    assert coverage["tested_elements"] == 3
    assert coverage["total_pages"] == 2
    assert coverage["state_transitions"] == 1
    assert coverage["action_type_counts"]["click"] == 4
    assert coverage["action_type_coverage"] == 50.0  # 2 of 4 action types
    assert "decision_no_action" in coverage["branch_outcomes"]


def test_compute_coverage_metrics_handles_empty_state():
    coverage = ExploratoryAgent._compute_coverage_metrics(
        total_elements=0,
        tested_elements=0,
        total_pages=0,
        action_type_counts={},
        state_transitions=set(),
        branch_outcomes=set(),
    )

    assert coverage["coverage_percentage"] == 0
    assert coverage["action_type_coverage"] == 0
    assert coverage["branch_coverage"] == 0
