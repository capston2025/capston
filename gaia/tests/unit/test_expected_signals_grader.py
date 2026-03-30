from gaia.harness.graders.expected_signals import ExpectedSignalsGrader


def test_expected_signals_grader_passes_when_all_required_signals_are_present() -> None:
    grader = ExpectedSignalsGrader(required_signals=["target_value_changed", "dom_changed"])
    outcome = grader.grade(
        {
            "summary": {
                "achieved_signals": ["target_value_changed", "dom_changed"],
            }
        }
    )

    assert outcome.passed is True


def test_expected_signals_grader_fails_when_signal_is_missing() -> None:
    grader = ExpectedSignalsGrader(required_signals=["pagination_advanced", "persistence_evaluated"])
    outcome = grader.grade(
        {
            "summary": {
                "achieved_signals": ["pagination_advanced"],
            }
        }
    )

    assert outcome.passed is False
    assert outcome.details["missing_signals"] == ["persistence_evaluated"]
