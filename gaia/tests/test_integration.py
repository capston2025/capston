from gaia.src.phase5.report import build_summary
from gaia.src.tracker.checklist import ChecklistTracker
from gaia.src.utils.models import ChecklistItem


def test_report_builds_summary():
    tracker = ChecklistTracker()
    tracker.items = {
        "TC_001": ChecklistItem(feature_id="TC_001", description="Login", checked=True),
        "TC_002": ChecklistItem(feature_id="TC_002", description="Signup", checked=False),
    }
    summary = build_summary(tracker)
    assert "coverage" in summary
    assert len(summary["covered"]) == 1
    assert len(summary["remaining"]) == 1
