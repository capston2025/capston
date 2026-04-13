from __future__ import annotations

import json
import subprocess
import sys

from gaia.src.phase4.goal_driven.models import ActionDecision, ActionType, TestGoal
from gaia.src.phase4.goal_driven.run_history_runtime import (
    build_run_history_attempt_digest_context,
    build_run_history_memory_context,
    build_run_history_memory_replay_context,
    build_run_history_prompt_context,
    build_run_history_progress_context,
    get_run_history_retrieval_entry,
    build_run_history_resume_checklist_context,
    build_run_history_replay_packet_context,
    build_run_history_retrieval_context,
    build_run_history_session_replay_context,
    build_run_history_session_summary_context,
    drain_pending_run_history_updates,
    initialize_run_history,
    list_run_history_pending_updates,
    record_run_history_transcript,
    record_run_history_decision,
    record_run_history_feedback,
    record_run_history_goal_outcome,
    refresh_run_history_state,
    run_history_artifact_only_updater_pass,
    search_run_history_retrieval_index,
)


class _HistoryAgent:
    def __init__(self) -> None:
        self.session_id = "history-session"
        self._run_history_enabled = None
        self._run_history_run_id = ""
        self._run_history_dir = ""
        self._run_history_events_path = ""
        self._run_history_state_path = ""
        self._run_history_summary_path = ""
        self._run_history_updater_path = ""
        self._run_history_updater_queue_path = ""
        self._run_history_updater_lock_path = ""
        self._run_history_replay_path = ""
        self._run_history_retrieval_path = ""
        self._run_history_retrieval_index_path = ""
        self._run_history_context_snapshot_path = ""
        self._run_history_prompt_path = ""
        self._run_history_memory_path = ""
        self._run_history_transcript_path = ""
        self._run_history_session_key = ""
        self._run_history_session_dir = ""
        self._run_history_session_events_path = ""
        self._run_history_session_state_path = ""
        self._run_history_session_summary_path = ""
        self._run_history_session_updater_path = ""
        self._run_history_session_updater_queue_path = ""
        self._run_history_session_updater_lock_path = ""
        self._run_history_session_replay_path = ""
        self._run_history_session_retrieval_path = ""
        self._run_history_session_retrieval_index_path = ""
        self._run_history_session_context_snapshot_path = ""
        self._run_history_session_prompt_path = ""
        self._run_history_session_memory_path = ""
        self._run_history_session_transcript_path = ""
        self._run_history_last_refresh_trigger = ""
        self._run_history_last_refresh_at = 0.0
        self._run_history_last_refresh_include_retrieval = False
        self._run_history_last_retrieval_refresh_trigger = ""
        self._run_history_last_retrieval_refresh_at = 0.0
        self._run_history_last_replay_refresh_trigger = ""
        self._run_history_last_replay_refresh_at = 0.0
        self._run_history_last_replay_refresh_include_retrieval = False
        self._run_history_session_summary = ""
        self._run_history_replay_packet_summary = ""
        self._run_history_prompt_summary = ""
        self._run_history_memory_summary = ""
        self._run_history_retrieval_summary = ""
        self._run_history_context_snapshot_cache = {}
        self._run_history_background_queue_triggers = []
        self._run_history_background_queue_since = 0.0
        self._run_history_background_last_queued_at = 0.0
        self._run_history_background_last_drained_at = 0.0
        self._run_history_background_drain_count = 0
        self._run_history_background_last_drain_reason = ""
        self._run_history_background_last_launch_status = ""
        self._run_history_background_last_launch_trigger = ""
        self._run_history_background_last_launch_at = 0.0
        self._run_history_background_last_launch_pid = 0
        self._run_history_background_launch_count = 0
        self._run_history_startup_recovery_drained = 0
        self._run_history_startup_recovery_failed = 0
        self._run_history_startup_recovery_at = 0.0
        self._run_history_background_pending_include_retrieval = False
        self._run_history_background_pending_artifacts = []
        self._run_history_background_last_updated_artifacts = []
        self._run_history_background_active = False
        self._recent_signal_history = []
        self._persistent_state_memory = []
        self._goal_constraints = {}
        self._active_goal_text = ""
        self._active_url = ""
        self._active_snapshot_id = ""


def _build_goal() -> TestGoal:
    return TestGoal(
        id="GH_PR_001",
        name="GitHub PR 탭 열기",
        description="검색 후 저장소 Pull requests 탭까지 이동해 PR 목록 화면을 확인해줘.",
        success_criteria=["repo page visible", "pull requests tab visible", "pr list visible"],
    )


def _build_readonly_goal() -> TestGoal:
    return TestGoal(
        id="BOJ_001",
        name="홈 로그인 링크 확인",
        description="현재 홈 화면에서 상단 로그인 링크가 이미 보이는지 확인하고 추가 조작 없이 종료해줘.",
        success_criteria=["로그인 링크 visible"],
        expected_signals=["text_visible", "link_visible"],
        preconditions=["현재 홈 화면 유지"],
        test_data={"username": "tester"},
        start_url="https://www.acmicpc.net/",
    )


def test_run_history_persists_events_and_renders_summary(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)

    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e213",
        element_id=74,
        reasoning="Pull requests 탭으로 이동하기 위한 클릭",
        confidence=0.91,
    )
    record_run_history_decision(agent, step_number=4, decision=decision)
    record_run_history_feedback(
        agent,
        step_number=4,
        decision=decision,
        success=True,
        changed=True,
        error=None,
        reason_code="ok",
        state_change={"url_changed": True, "dom_changed": True},
    )
    record_run_history_goal_outcome(
        agent,
        goal=goal,
        status="success",
        reason="PR 목록 화면이 보입니다.",
        step_count=4,
        duration_seconds=12.5,
    )

    session_dir = tmp_path / "sessions" / agent._run_history_session_key
    run_dir = session_dir / "runs" / agent._run_history_run_id
    events_path = run_dir / "events.jsonl"
    state_path = run_dir / "state.md"
    summary_path = run_dir / "summary.md"
    updater_path = run_dir / "updater.md"
    updater_queue_path = run_dir / "updater_queue.json"
    updater_lock_path = run_dir / "updater_lock.json"
    replay_path = run_dir / "replay.md"
    retrieval_path = run_dir / "retrieval.md"
    retrieval_index_path = run_dir / "retrieval_index.json"
    context_snapshot_path = run_dir / "context_snapshot.json"
    prompt_path = run_dir / "compact.md"
    memory_path = run_dir / "MEMORY.md"
    transcript_path = run_dir / "transcript.jsonl"
    session_events_path = session_dir / "events.jsonl"
    session_state_path = session_dir / "state.md"
    session_summary_path = session_dir / "summary.md"
    session_updater_path = session_dir / "updater.md"
    session_updater_queue_path = session_dir / "updater_queue.json"
    session_updater_lock_path = session_dir / "updater_lock.json"
    session_replay_path = session_dir / "replay.md"
    session_retrieval_path = session_dir / "retrieval.md"
    session_retrieval_index_path = session_dir / "retrieval_index.json"
    session_context_snapshot_path = session_dir / "context_snapshot.json"
    session_prompt_path = session_dir / "compact.md"
    session_memory_path = session_dir / "MEMORY.md"
    session_transcript_path = session_dir / "transcript.jsonl"

    assert events_path.exists()
    assert state_path.exists()
    assert summary_path.exists()
    assert updater_path.exists()
    assert updater_queue_path.exists()
    assert updater_lock_path.exists() is False
    assert replay_path.exists()
    assert retrieval_path.exists()
    assert retrieval_index_path.exists()
    assert context_snapshot_path.exists()
    assert prompt_path.exists()
    assert memory_path.exists()
    assert transcript_path.exists() is False
    assert session_events_path.exists()
    assert session_state_path.exists()
    assert session_summary_path.exists()
    assert session_updater_path.exists()
    assert session_updater_queue_path.exists()
    assert session_updater_lock_path.exists() is False
    assert session_replay_path.exists()
    assert session_retrieval_path.exists()
    assert session_retrieval_index_path.exists()
    assert session_context_snapshot_path.exists()
    assert session_prompt_path.exists()
    assert session_memory_path.exists()
    assert session_transcript_path.exists() is False

    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [event["kind"] for event in events] == [
        "goal_start",
        "decision",
        "step_outcome",
        "goal_end",
    ]

    summary = build_run_history_prompt_context(agent, goal=goal)
    session_summary = build_run_history_session_summary_context(agent, goal=goal)
    memory_summary = build_run_history_memory_context(agent, goal=goal)
    assert "## 누적 실행 상태 기록(압축)" in summary
    assert "session_key:" in summary
    assert "### Current Objective" in summary
    assert "### Completed Progress" in summary
    assert "### Active Blockers" in summary
    assert "### Next Best Action" in summary
    assert "### Open Questions" in summary
    assert "Step 4 | outcome | click | success" in summary
    assert "### 현재 run 최근 진전" in summary
    assert "# Session Memory" in memory_summary
    assert "## Current Objective" in memory_summary
    assert "## Completed Progress" in memory_summary
    assert "## Active Blockers" in memory_summary
    assert "## Next Best Action" in memory_summary
    assert "## Open Questions" in memory_summary
    assert session_summary.startswith("# Session Summary")
    assert "## Summary Updater" in session_summary
    assert "last_refresh_trigger: goal_end" in session_summary
    assert "queue_state: idle" in session_summary
    assert "## Startup Continuity Audit" in session_summary
    assert "## Session Start Rules" in session_summary
    assert "reread_goal_contract_before_first_action" in session_summary
    assert "fresh_session_history_use_current_dom_as_source_of_truth" in session_summary
    assert "## Replay Guidance" in session_summary

    full_summary = state_path.read_text(encoding="utf-8")
    persisted_session_summary = summary_path.read_text(encoding="utf-8")
    persisted_updater = updater_path.read_text(encoding="utf-8")
    persisted_updater_queue = updater_queue_path.read_text(encoding="utf-8")
    persisted_replay = replay_path.read_text(encoding="utf-8")
    persisted_retrieval = retrieval_path.read_text(encoding="utf-8")
    persisted_retrieval_index = retrieval_index_path.read_text(encoding="utf-8")
    persisted_context_snapshot = context_snapshot_path.read_text(encoding="utf-8")
    persisted_session_updater = session_updater_path.read_text(encoding="utf-8")
    persisted_session_updater_queue = session_updater_queue_path.read_text(encoding="utf-8")
    persisted_session_replay = session_replay_path.read_text(encoding="utf-8")
    persisted_session_retrieval = session_retrieval_path.read_text(encoding="utf-8")
    persisted_session_context_snapshot = session_context_snapshot_path.read_text(encoding="utf-8")
    assert "## 누적 실행 상태 원장" in full_summary
    assert "terminal: status=success" in full_summary
    assert "# Session Summary" in persisted_session_summary
    assert "## Replay Guidance" in persisted_session_summary
    assert "# Run History Updater" in persisted_updater
    assert "mode: queued_background_simulation" in persisted_updater
    assert "trigger: goal_end" in persisted_updater
    assert "queue_state: idle" in persisted_updater
    assert "drain_count:" in persisted_updater
    assert "updated_artifacts:" in persisted_updater
    assert "\"queue_state\": \"idle\"" in persisted_updater_queue
    assert "\"run_id\"" in persisted_updater_queue
    assert "\"goal\"" in persisted_context_snapshot
    assert "\"action_history\"" in persisted_context_snapshot
    assert "# Replay Artifact" in persisted_replay
    assert "## Replay Updater" in persisted_replay
    assert "last_refresh_trigger: goal_end" in persisted_replay
    assert "retrieval_included: true" in persisted_replay
    assert "boundary_mode: fresh_session_start" in persisted_replay
    assert "resume checklist" in persisted_replay
    assert "## 세션 continuity replay packet" in persisted_replay
    assert "# Retrieval Artifact" in persisted_retrieval
    assert "## Retrieval Updater" in persisted_retrieval
    assert "last_refresh_trigger: goal_end" in persisted_retrieval
    assert "hit_count:" in persisted_retrieval
    assert "## 관련 세션 기억 검색 결과" in persisted_retrieval
    assert "\"run_id\"" in persisted_retrieval_index
    assert "\"entries\"" in persisted_retrieval_index
    assert "## Replay Updater" in persisted_session_replay
    assert "## Retrieval Updater" in persisted_session_retrieval
    assert "# Run History Updater" in persisted_session_updater
    assert "\"queue_state\": \"idle\"" in persisted_session_updater_queue
    assert "\"goal\"" in persisted_session_context_snapshot
    assert agent._run_history_last_retrieval_refresh_trigger == "goal_end"
    assert agent._run_history_last_replay_refresh_trigger == "goal_end"
    assert agent._run_history_last_replay_refresh_include_retrieval is True


def test_refresh_run_history_state_includes_signal_and_fill_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    agent._recent_signal_history = [
        {
            "action": "click",
            "pagination_candidate": True,
            "state_change": {"url_changed": True, "dom_changed": True},
        }
    ]
    agent._persistent_state_memory = [
        {
            "kind": "fill",
            "expected_value": "octocat/Hello-World",
            "container_name": "GitHub 검색",
            "context_text": "상단 검색창",
        }
    ]

    summary = refresh_run_history_state(agent, goal=goal)

    assert "### 최근 상태 신호" in summary
    assert "pagination_candidate=true" in summary
    assert "### 최근 fill/select 기억" in summary
    assert "octocat/Hello-World" in summary
    assert (tmp_path / "sessions" / agent._run_history_session_key / "compact.md").exists()
    assert (tmp_path / "sessions" / agent._run_history_session_key / "MEMORY.md").exists()


def test_run_history_continues_across_same_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    first_decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e51",
        element_id=11,
        reasoning="첫 번째 실행에서 저장소 진입",
        confidence=0.88,
    )
    record_run_history_decision(first_agent, step_number=2, decision=first_decision)
    record_run_history_feedback(
        first_agent,
        step_number=2,
        decision=first_decision,
        success=True,
        changed=True,
        error=None,
        reason_code="ok",
        state_change={"url_changed": True},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="PR 탭을 찾지 못했습니다.",
        step_count=2,
        duration_seconds=7.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)
    summary = build_run_history_prompt_context(second_agent, goal=goal)
    session_summary = build_run_history_session_summary_context(second_agent, goal=goal)
    memory_summary = build_run_history_memory_context(second_agent, goal=goal)

    assert second_agent._run_history_session_key == first_agent._run_history_session_key
    assert second_agent._run_history_run_id != first_agent._run_history_run_id
    assert "prior_runs: 1" in summary
    assert "### 이전 실행 carry-over" in summary
    assert "PR 탭을 찾지 못했습니다." in summary
    assert "collect_missing_completion_evidence_before_terminal_retry" in summary
    assert "## Prior Outcomes" in memory_summary
    assert "## Resume Hints" in memory_summary
    assert "## Recent Attempts" in memory_summary
    assert "## Active Blockers" in memory_summary
    assert 'what_was_missing_at_last_terminal: "PR 탭을 찾지 못했습니다."' in memory_summary
    assert "## Startup Continuity Audit" in session_summary
    assert "## Session Start Rules" in session_summary
    assert "last_refresh_trigger: goal_start" in session_summary
    assert "apply_resume_hints_only_if_current_surface_matches_last_goal_surface" in session_summary
    assert "last_terminal_checkpoint:" in session_summary
    session_replay = (
        tmp_path / "sessions" / second_agent._run_history_session_key / "replay.md"
    ).read_text(encoding="utf-8")
    session_retrieval = (
        tmp_path / "sessions" / second_agent._run_history_session_key / "retrieval.md"
    ).read_text(encoding="utf-8")
    session_updater = (
        tmp_path / "sessions" / second_agent._run_history_session_key / "updater.md"
    ).read_text(encoding="utf-8")
    assert "## Replay Updater" in session_replay
    assert "last_refresh_trigger: goal_start" in session_replay
    assert "boundary_mode: carry_over_resume" in session_replay
    assert "## Retrieval Updater" in session_retrieval
    assert "last_refresh_trigger: goal_start" in session_retrieval
    assert "# Run History Updater" in session_updater
    assert "trigger: goal_start" in session_updater
    assert "queue_state: idle" in session_updater


def test_run_history_decision_update_defers_until_context_read(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e201",
        element_id=21,
        reasoning="PR 탭 후보를 먼저 클릭",
        confidence=0.81,
    )

    record_run_history_decision(agent, step_number=1, decision=decision)

    updater_path = (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
        / "updater.md"
    )
    pending_updater = updater_path.read_text(encoding="utf-8")

    assert "queue_state: pending" in pending_updater
    assert "queued_triggers: decision" in pending_updater
    assert "deferred_flush: true" in pending_updater

    compact = build_run_history_prompt_context(agent, goal=goal)
    flushed_updater = updater_path.read_text(encoding="utf-8")

    assert "Step 1 | plan | click" in compact
    assert "queue_state: idle" in flushed_updater
    assert "last_drain_reason: context_read_flush:compact" in flushed_updater


def test_run_history_decision_update_launches_background_subprocess(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "1")
    from gaia.src.phase4.goal_driven import run_history_runtime as runtime

    launches: list[dict[str, object]] = []

    class _FakeProc:
        def __init__(self) -> None:
            self.pid = 4242

    def _fake_popen(command, **kwargs):
        launches.append({"command": list(command), "kwargs": dict(kwargs)})
        return _FakeProc()

    monkeypatch.setattr(runtime.subprocess, "Popen", _fake_popen)

    agent = _HistoryAgent()
    goal = _build_goal()
    initialize_run_history(agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e201",
        element_id=21,
        reasoning="PR 탭 후보를 먼저 클릭",
        confidence=0.81,
    )

    record_run_history_decision(agent, step_number=1, decision=decision)

    updater_path = (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
        / "updater.md"
    )
    pending_updater = updater_path.read_text(encoding="utf-8")
    lock_path = updater_path.parent / "updater_lock.json"

    assert launches
    command = launches[0]["command"]
    assert command[0] == sys.executable
    assert command[1].endswith("scripts/run_history_background_updater.py")
    assert "--run-dir" in command
    assert str(updater_path.parent) in command
    assert "queue_state: pending" in pending_updater
    assert "background_launch_status: spawned" in pending_updater
    assert "background_launch_pid: 4242" in pending_updater
    assert "background_lock_state: launching" in pending_updater
    lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock_payload["status"] == "launching"
    assert lock_payload["pid"] == 4242


def test_run_history_decision_update_skips_spawn_when_active_lock_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "1")
    from gaia.src.phase4.goal_driven import run_history_runtime as runtime

    def _unexpected_popen(*args, **kwargs):
        raise AssertionError("background subprocess should not launch when active lock exists")

    monkeypatch.setattr(runtime.subprocess, "Popen", _unexpected_popen)

    agent = _HistoryAgent()
    goal = _build_goal()
    initialize_run_history(agent, goal)
    run_dir = (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
    )
    lock_path = run_dir / "updater_lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "run_id": agent._run_history_run_id,
                "status": "running",
                "owner": "artifact_only_worker",
                "trigger": "decision",
                "pid": 9191,
                "updated_at_ts": 0.0,
                "lease_expires_at_ts": 9999999999.0,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir.parent.parent / "updater_lock.json").write_text(lock_path.read_text(encoding="utf-8"), encoding="utf-8")

    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e202",
        element_id=22,
        reasoning="active lock skip test",
        confidence=0.81,
    )
    record_run_history_decision(agent, step_number=1, decision=decision)

    updater = (run_dir / "updater.md").read_text(encoding="utf-8")
    assert "background_launch_status: skipped_active_lock" in updater
    assert "background_launch_pid: 9191" in updater
    assert "background_lock_state: running" in updater


def test_run_history_restores_pending_queue_from_artifact(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e305",
        element_id=31,
        reasoning="PR 탭 후보 클릭",
        confidence=0.8,
    )
    record_run_history_decision(agent, step_number=1, decision=decision)

    agent._run_history_background_queue_triggers = []
    agent._run_history_background_pending_artifacts = []
    agent._run_history_background_pending_include_retrieval = False
    agent._run_history_prompt_summary = ""

    compact = build_run_history_prompt_context(agent, goal=goal)

    assert "Step 1 | plan | click" in compact
    assert agent._run_history_background_queue_triggers == []
    assert "queue_state: idle" in (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
        / "updater.md"
    ).read_text(encoding="utf-8")


def test_run_history_artifact_only_updater_drains_pending_queue_with_snapshot_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    agent._goal_constraints = {
        "require_no_navigation": True,
        "current_view_only": True,
    }
    goal = _build_readonly_goal()

    initialize_run_history(agent, goal)
    agent._recent_signal_history = [
        {
            "action": "inspect",
            "pagination_candidate": False,
            "state_change": {"dom_changed": True},
        }
    ]
    agent._persistent_state_memory = [
        {
            "kind": "fill_memory",
            "expected_value": "tester",
            "container_name": "login form",
            "context_text": "username field",
        }
    ]
    agent._action_history = ["홈 화면 로그인 링크를 먼저 본다"]
    agent._action_feedback = ["reason_code=already_visible"]
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e11",
        element_id=11,
        reasoning="로그인 링크를 확인한 뒤 더 진행할지 판단",
        confidence=0.77,
    )
    record_run_history_decision(agent, step_number=1, decision=decision)

    run_dir = (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
    )
    drained = run_history_artifact_only_updater_pass(str(run_dir))

    compact = (run_dir / "compact.md").read_text(encoding="utf-8")
    session_summary = (tmp_path / "sessions" / agent._run_history_session_key / "summary.md").read_text(
        encoding="utf-8"
    )
    updater = (run_dir / "updater.md").read_text(encoding="utf-8")
    context_snapshot = (run_dir / "context_snapshot.json").read_text(encoding="utf-8")

    assert "compact" in drained
    assert "session_summary" in drained
    assert "Step 1 | plan | click" in compact
    assert "readonly_visibility_first: inspect current surface before navigation or repeated clicking" in session_summary
    assert "respect_harness_contract: no_navigation, current_view_only" in session_summary
    assert "queue_state: idle" in updater
    assert "last_drain_reason: artifact_only_queue_drain" in updater
    assert "\"recent_signal_history\"" in context_snapshot
    assert "\"persistent_state_memory\"" in context_snapshot
    assert "\"goal_constraints\"" in context_snapshot


def test_run_history_background_updater_script_drains_pending_queue(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "0")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e44",
        element_id=44,
        reasoning="background script drain test",
        confidence=0.71,
    )
    record_run_history_decision(agent, step_number=1, decision=decision)

    run_dir = (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_history_background_updater.py",
            "--run-dir",
            str(run_dir),
            "--drain-reason",
            "script_test",
        ],
        cwd="/Users/coldmans/Documents/GitHub/capston",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "compact" in payload["updated_artifacts"]
    updater = (run_dir / "updater.md").read_text(encoding="utf-8")
    lock_payload = json.loads((run_dir / "updater_lock.json").read_text(encoding="utf-8"))
    assert "queue_state: idle" in updater
    assert "last_drain_reason: script_test" in updater
    assert "background_lock_state: idle" in updater
    assert lock_payload["status"] == "idle"
    assert lock_payload["reason"] == "worker_completed"


def test_run_history_pending_update_helpers_scan_and_drain(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "0")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    record_run_history_decision(
        first_agent,
        step_number=1,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e61",
            element_id=61,
            reasoning="first pending queue",
            confidence=0.72,
        ),
    )

    second_agent = _HistoryAgent()
    second_agent.session_id = "history-session-2"
    initialize_run_history(second_agent, goal)
    record_run_history_decision(
        second_agent,
        step_number=1,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e62",
            element_id=62,
            reasoning="second pending queue",
            confidence=0.73,
        ),
    )

    pending = list_run_history_pending_updates(history_root=str(tmp_path), limit=10)

    assert len(pending) == 2
    assert {item["queue_state"] for item in pending} == {"pending"}
    assert {item["lock_state"] for item in pending} == {"none"}

    drained = drain_pending_run_history_updates(
        history_root=str(tmp_path),
        limit=1,
        drain_reason="helper_sweep",
    )

    assert drained["discovered"] == 1
    assert drained["drained"] == 1
    assert drained["skipped_locked"] == 0
    assert drained["failed"] == 0

    remaining = list_run_history_pending_updates(history_root=str(tmp_path), limit=10)
    assert len(remaining) == 1


def test_run_history_background_sweeper_script_skips_locked_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "0")
    goal = _build_goal()

    agent = _HistoryAgent()
    initialize_run_history(agent, goal)
    record_run_history_decision(
        agent,
        step_number=1,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e71",
            element_id=71,
            reasoning="locked pending queue",
            confidence=0.74,
        ),
    )

    run_dir = (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "runs"
        / agent._run_history_run_id
    )
    lock_payload = {
        "run_id": agent._run_history_run_id,
        "status": "running",
        "owner": "artifact_only_worker",
        "trigger": "decision",
        "pid": 8181,
        "updated_at_ts": 0.0,
        "lease_expires_at_ts": 9999999999.0,
    }
    (run_dir / "updater_lock.json").write_text(json.dumps(lock_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (
        tmp_path
        / "sessions"
        / agent._run_history_session_key
        / "updater_lock.json"
    ).write_text(json.dumps(lock_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_history_background_sweeper.py",
            "--history-root",
            str(tmp_path),
            "--limit",
            "10",
            "--drain-reason",
            "sweeper_test",
        ],
        cwd="/Users/coldmans/Documents/GitHub/capston",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["discovered"] == 1
    assert payload["drained"] == 0
    assert payload["skipped_locked"] == 1
    assert payload["failed"] == 0


def test_run_history_startup_recovery_drains_same_session_pending_updates(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_BACKGROUND_SUBPROCESS", "0")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    record_run_history_decision(
        first_agent,
        step_number=1,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e81",
            element_id=81,
            reasoning="startup recovery pending queue",
            confidence=0.75,
        ),
    )
    first_run_dir = (
        tmp_path
        / "sessions"
        / first_agent._run_history_session_key
        / "runs"
        / first_agent._run_history_run_id
    )
    assert "queue_state: pending" in (first_run_dir / "updater.md").read_text(encoding="utf-8")

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    assert second_agent._run_history_startup_recovery_drained == 1
    assert second_agent._run_history_startup_recovery_failed == 0
    recovered_updater = (first_run_dir / "updater.md").read_text(encoding="utf-8")
    recovered_session_summary = (
        tmp_path / "sessions" / second_agent._run_history_session_key / "summary.md"
    ).read_text(encoding="utf-8")
    assert "queue_state: idle" in recovered_updater
    assert "session_startup_recovery" in recovered_updater
    assert "startup_recovery_drained: 1" in recovered_session_summary
    assert "startup_recovery_replayed_pending_updates: 1" in recovered_session_summary


def test_run_history_session_summary_includes_goal_derived_start_rules(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_readonly_goal()

    initialize_run_history(agent, goal)

    session_summary = build_run_history_session_summary_context(agent, goal=goal)

    assert "## Session Start Rules" in session_summary
    assert "prefer_current_surface_from_start_url: https://www.acmicpc.net/" in session_summary
    assert "verify_success_contract_against_current_surface: text_visible, link_visible" in session_summary
    assert "readonly_visibility_first: inspect current surface before navigation or repeated clicking" in session_summary
    assert "respect_preconditions: 현재 홈 화면 유지" in session_summary
    assert "only_apply_test_data_when_matching_input_surface_is_visible" in session_summary


def test_run_history_resume_checklist_includes_start_rules(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_readonly_goal()

    initialize_run_history(agent, goal)

    checklist = build_run_history_resume_checklist_context(agent, goal=goal)

    assert "- start_rules:" in checklist
    assert "readonly_visibility_first" in checklist
    assert "verify_success_contract_against_current_surface" in checklist
    assert "success_signals: text_visible; link_visible" in checklist


def test_run_history_session_summary_includes_goal_constraint_rules(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    agent._goal_constraints = {
        "require_no_navigation": True,
        "current_view_only": True,
        "require_state_change": True,
        "forbid_search_action": True,
        "mutation_direction": "increase",
        "collect_min": 3,
        "apply_target": 1,
        "metric_label": "items",
    }
    goal = _build_readonly_goal()

    initialize_run_history(agent, goal)

    session_summary = build_run_history_session_summary_context(agent, goal=goal)

    assert "respect_harness_contract: no_navigation, current_view_only, require_state_change, forbid_search_action, mutation_direction=increase" in session_summary
    assert "enforce_goal_thresholds: collect_min=3items, apply_target=1items" in session_summary


def test_run_history_prompt_context_rebuilds_compact_summary_on_cache_miss(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    agent._run_history_prompt_summary = ""

    rebuilt = build_run_history_prompt_context(agent, goal=goal)

    assert rebuilt.startswith("## 누적 실행 상태 기록(압축)")
    assert "### Current Objective" in rebuilt
    assert "## 누적 실행 상태 원장" not in rebuilt


def test_run_history_prompt_context_reuses_compact_artifact_on_cache_miss(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    custom_compact = "## 누적 실행 상태 기록(압축)\n- custom compact artifact"
    compact_path = tmp_path / "sessions" / agent._run_history_session_key / "runs" / agent._run_history_run_id / "compact.md"
    compact_path.write_text(custom_compact + "\n", encoding="utf-8")
    agent._run_history_prompt_summary = ""

    rebuilt = build_run_history_prompt_context(agent, goal=goal)

    assert rebuilt == custom_compact


def test_run_history_session_summary_rebuilds_on_cache_miss(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    agent._run_history_session_summary = ""

    rebuilt = build_run_history_session_summary_context(agent, goal=goal)

    assert rebuilt.startswith("# Session Summary")
    assert "## Current Objective" in rebuilt
    assert "## Replay Guidance" in rebuilt


def test_run_history_replay_packet_reuses_replay_artifact_on_cache_miss(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    custom_replay = "\n".join(
        [
            "# Replay Artifact",
            "",
            "## Replay Updater",
            "- updater_path: replay_side_pass",
            f"- run_id: {agent._run_history_run_id}",
            "",
            "## 세션 continuity replay packet",
            "- custom replay artifact hit",
        ]
    )
    replay_path = tmp_path / "sessions" / agent._run_history_session_key / "runs" / agent._run_history_run_id / "replay.md"
    replay_path.write_text(custom_replay + "\n", encoding="utf-8")
    agent._run_history_replay_packet_summary = ""

    packet = build_run_history_replay_packet_context(agent, goal=goal)

    assert packet == "## 세션 continuity replay packet\n- custom replay artifact hit"


def test_run_history_session_replay_context_extracts_core_sections(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_readonly_goal()

    initialize_run_history(agent, goal)

    replay = build_run_history_session_replay_context(agent, goal=goal)

    assert replay.startswith("## 세션 연속성 replay packet(summary.md)")
    assert "- Startup Continuity Audit:" in replay
    assert "- Session Start Rules:" in replay
    assert "- Current Objective:" in replay
    assert "- Next Best Action:" in replay
    assert "- Recent Attempts:" not in replay
    assert "- Completed Progress:" not in replay
    assert "## Replay Guidance" not in replay


def test_run_history_replay_packet_orders_replay_blocks(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_REPLAY_CHAR_LIMIT", "8000")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e77",
        element_id=17,
        reasoning="Pull requests 탭을 눌렀지만 빈 목록이었습니다.",
        confidence=0.7,
    )
    record_run_history_feedback(
        first_agent,
        step_number=5,
        decision=decision,
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 열렸지만 PR 목록이 비어 있었습니다.",
        step_count=5,
        duration_seconds=9.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    packet = build_run_history_replay_packet_context(second_agent, goal=goal)

    assert packet.startswith("## 세션 continuity replay packet")
    assert "## replay boundary" in packet
    assert "## resume checklist" in packet
    assert "## recent attempt digest" in packet
    assert "- mode: carry_over_resume" in packet
    assert '- resume_hint: "' in packet
    assert "## 세션 연속성 replay packet(summary.md)" in packet
    assert "## 세션 carry-over 기억(MEMORY replay)" in packet
    assert "## 관련 세션 기억 검색 결과" in packet
    assert packet.index("## replay boundary") < packet.index("## resume checklist")
    assert packet.index("## resume checklist") < packet.index("## recent attempt digest")
    assert packet.index("## recent attempt digest") < packet.index("## 세션 연속성 replay packet(summary.md)")
    assert packet.index("## 세션 연속성 replay packet(summary.md)") < packet.index("## 세션 carry-over 기억(MEMORY replay)")


def test_run_history_resume_checklist_prioritizes_resume_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e63",
        element_id=17,
        reasoning="Pull requests 탭이 안 보여 다른 탭을 눌렀음",
        confidence=0.73,
    )
    record_run_history_feedback(
        first_agent,
        step_number=4,
        decision=decision,
        success=False,
        changed=False,
        error="Pull requests 탭을 찾지 못했습니다.",
        reason_code="missing_pr_tab",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭을 찾지 못했습니다.",
        step_count=4,
        duration_seconds=10.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    checklist = build_run_history_resume_checklist_context(second_agent, goal=goal)

    assert checklist.startswith("## resume checklist")
    assert "- mode: carry_over_resume" in checklist
    assert "- objective:" in checklist
    assert "- start_rules:" in checklist
    assert "- verify_first:" in checklist
    assert "- recent_attempts:" in checklist
    assert "- last_terminal_reason:" in checklist
    assert "- resume_hint:" in checklist
    assert "- next_best_action:" in checklist


def test_run_history_recent_attempts_flow_into_replay(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e70",
        element_id=12,
        reasoning="PR 탭 진입을 시도했지만 탭을 찾지 못함",
        confidence=0.71,
    )
    record_run_history_feedback(
        first_agent,
        step_number=3,
        decision=decision,
        success=False,
        changed=False,
        error="PR 탭을 찾지 못했습니다.",
        reason_code="missing_pr_tab",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="PR 탭을 찾지 못했습니다.",
        step_count=3,
        duration_seconds=8.5,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    session_replay = build_run_history_session_replay_context(second_agent, goal=goal)
    attempt_digest = build_run_history_attempt_digest_context(second_agent, goal=goal)
    memory_replay = build_run_history_memory_replay_context(second_agent, goal=goal)

    assert "- Recent Attempts:" not in session_replay
    assert attempt_digest.startswith("## recent attempt digest")
    assert "reason_code=missing_pr_tab" in attempt_digest
    assert "- Recent Attempts:" in memory_replay
    assert "reason_code=missing_pr_tab" in memory_replay


def test_run_history_replay_packet_omits_blocks_when_char_limited(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_REPLAY_CHAR_LIMIT", "220")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e91",
        element_id=17,
        reasoning="Pull requests 탭 진입 후 빈 목록 문제 확인",
        confidence=0.72,
    )
    record_run_history_feedback(
        first_agent,
        step_number=7,
        decision=decision,
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 보였지만 PR 목록이 비어 있었습니다.",
        step_count=7,
        duration_seconds=14.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    packet = build_run_history_replay_packet_context(second_agent, goal=goal)

    assert "## replay boundary" in packet
    assert "## resume checklist" in packet
    assert "## Replay Packet Omitted" in packet
    assert "omitted_due_to_char_limit" in packet
    assert "truncated_due_to_char_limit" in packet


def test_run_history_replay_boundary_marks_fresh_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)

    packet = build_run_history_replay_packet_context(agent, goal=goal)

    assert "## replay boundary" in packet
    assert "- mode: fresh_session_start" in packet
    assert "- prior_runs: 0" in packet
    assert "## 세션 연속성 replay packet(summary.md)" not in packet


def test_run_history_memory_replay_context_keeps_only_carry_over_sections(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e51",
        element_id=11,
        reasoning="첫 실행에서 PR 탭을 찾으려 했지만 실패",
        confidence=0.82,
    )
    record_run_history_feedback(
        first_agent,
        step_number=3,
        decision=decision,
        success=False,
        changed=False,
        error="PR 탭을 찾지 못했습니다.",
        reason_code="missing_pr_tab",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="PR 탭을 찾지 못했습니다.",
        step_count=3,
        duration_seconds=8.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    replay = build_run_history_memory_replay_context(second_agent, goal=goal)

    assert replay.startswith("## 세션 carry-over 기억(MEMORY replay)")
    assert "- Prior Outcomes:" in replay
    assert "- Resume Hints:" in replay
    assert "- Current Objective:" not in replay
    assert "PR 탭을 찾지 못했습니다." in replay


def test_run_history_progress_context_keeps_current_run_tail_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    agent._recent_signal_history = [
        {
            "action": "wait",
            "pagination_candidate": False,
            "state_change": {"dom_changed": True},
        }
    ]
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e88",
        element_id=22,
        reasoning="PR 탭을 찾기 위해 탭 후보 클릭",
        confidence=0.79,
    )
    record_run_history_decision(agent, step_number=1, decision=decision)
    record_run_history_feedback(
        agent,
        step_number=1,
        decision=decision,
        success=False,
        changed=False,
        error="탭 변화가 없었습니다.",
        reason_code="no_state_change",
        state_change={},
    )

    replay = build_run_history_progress_context(agent, goal=goal)

    assert replay.startswith("## 현재 run replay tail")
    assert "- 현재 run 최근 계획:" in replay or "- 현재 run 최근 진전:" in replay
    assert "- 최근 상태 신호:" in replay
    assert "- Current Objective:" not in replay


def test_run_history_transcript_persists_to_run_and_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    record_run_history_transcript(
        agent,
        stage="actor_decision_prompt",
        role="user",
        content="prompt body",
        metadata={"phase": "collect"},
    )
    record_run_history_transcript(
        agent,
        stage="actor_decision_response",
        role="assistant",
        content='{"action":"click"}',
        metadata={"phase": "collect"},
    )

    session_dir = tmp_path / "sessions" / agent._run_history_session_key
    run_dir = session_dir / "runs" / agent._run_history_run_id
    run_transcript = run_dir / "transcript.jsonl"
    session_transcript = session_dir / "transcript.jsonl"

    assert run_transcript.exists()
    assert session_transcript.exists()

    rows = [
        json.loads(line)
        for line in run_transcript.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["stage"] for row in rows] == ["actor_decision_prompt", "actor_decision_response"]
    assert rows[0]["role"] == "user"
    assert rows[1]["role"] == "assistant"


def test_run_history_transcript_uses_configurable_large_char_limit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    monkeypatch.setenv("GAIA_RUN_HISTORY_TRANSCRIPT_CHAR_LIMIT", "7000")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)
    content = "x" * 6000
    record_run_history_transcript(
        agent,
        stage="actor_decision_prompt",
        role="user",
        content=content,
        metadata={"phase": "collect"},
    )

    session_dir = tmp_path / "sessions" / agent._run_history_session_key
    run_dir = session_dir / "runs" / agent._run_history_run_id
    run_transcript = run_dir / "transcript.jsonl"
    rows = [
        json.loads(line)
        for line in run_transcript.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert rows[0]["char_count"] == 6000
    assert rows[0]["content"] == content


def test_run_history_retrieval_context_finds_relevant_session_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e77",
        element_id=17,
        reasoning="Pull requests 탭을 눌렀지만 빈 목록이었습니다.",
        confidence=0.7,
    )
    record_run_history_feedback(
        first_agent,
        step_number=5,
        decision=decision,
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 열렸지만 PR 목록이 비어 있었습니다.",
        step_count=5,
        duration_seconds=9.0,
    )
    record_run_history_transcript(
        first_agent,
        stage="judge_response",
        role="assistant",
        content="Pull requests 탭은 열렸지만 PR 목록이 비어 있어 success=false",
        metadata={"goal_id": goal.id},
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)
    retrieval = build_run_history_retrieval_context(second_agent, goal=goal)

    assert "## 관련 세션 기억 검색 결과" in retrieval
    assert "MEMORY |" in retrieval
    assert "Recent Attempts" in retrieval or "Active Blockers" in retrieval or "Resume Hints" in retrieval
    assert "Pull requests" in retrieval
    assert "PR 목록이 비어" in retrieval


def test_run_history_retrieval_context_skips_generic_current_run_memory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    agent = _HistoryAgent()
    goal = _build_goal()

    initialize_run_history(agent, goal)

    retrieval = build_run_history_retrieval_context(agent, goal=goal)
    memory_replay = build_run_history_memory_replay_context(agent, goal=goal)

    assert retrieval == ""
    assert memory_replay == ""


def test_run_history_retrieval_context_reuses_retrieval_artifact_on_cache_miss(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    record_run_history_feedback(
        first_agent,
        step_number=4,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e66",
            element_id=12,
            reasoning="Pull requests 탭 이동 시도",
            confidence=0.74,
        ),
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 열렸지만 PR 목록이 비어 있었습니다.",
        step_count=4,
        duration_seconds=8.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)
    custom_retrieval = "\n".join(
        [
            "# Retrieval Artifact",
            "",
            "## Retrieval Updater",
            "- updater_path: retrieval_side_pass",
            f"- run_id: {second_agent._run_history_run_id}",
            "- last_refresh_trigger: manual_override",
            "",
            "## 관련 세션 기억 검색 결과",
            "- MEMORY | Recent Attempts: custom artifact retrieval hit",
        ]
    )
    run_retrieval_path = (
        tmp_path
        / "sessions"
        / second_agent._run_history_session_key
        / "runs"
        / second_agent._run_history_run_id
        / "retrieval.md"
    )
    session_retrieval_path = tmp_path / "sessions" / second_agent._run_history_session_key / "retrieval.md"
    run_retrieval_path.write_text(custom_retrieval + "\n", encoding="utf-8")
    session_retrieval_path.write_text(custom_retrieval + "\n", encoding="utf-8")
    second_agent._run_history_retrieval_summary = ""

    retrieval = build_run_history_retrieval_context(second_agent, goal=goal)

    assert retrieval == "## 관련 세션 기억 검색 결과\n- MEMORY | Recent Attempts: custom artifact retrieval hit"


def test_run_history_retrieval_context_reuses_retrieval_index_on_cache_miss(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    agent = _HistoryAgent()
    initialize_run_history(agent, goal)
    agent._action_feedback = ["재개 포인트 확인 필요: reason_code=empty_pr_list"]
    run_dir = tmp_path / "sessions" / agent._run_history_session_key / "runs" / agent._run_history_run_id
    session_dir = tmp_path / "sessions" / agent._run_history_session_key
    (run_dir / "retrieval.md").unlink(missing_ok=True)
    (session_dir / "retrieval.md").unlink(missing_ok=True)
    index_payload = {
        "run_id": agent._run_history_run_id,
        "trigger": "manual_override",
        "updated_at": "2026-04-08T00:00:00",
        "entries": [
            {
                "entry_id": "memory:recent-attempts:manualhit",
                "source": "memory",
                "header": "Recent Attempts",
                "text": "MEMORY | Recent Attempts: reason_code=empty_pr_list | PR 목록이 비었습니다.",
            }
        ],
    }
    (run_dir / "retrieval_index.json").write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent._run_history_retrieval_summary = ""

    retrieval = build_run_history_retrieval_context(agent, goal=goal)

    assert "## 관련 세션 기억 검색 결과" in retrieval
    assert "Recent Attempts" in retrieval
    assert "empty_pr_list" in retrieval


def test_run_history_retrieval_search_and_get_use_entry_ids(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    record_run_history_feedback(
        first_agent,
        step_number=4,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e66",
            element_id=12,
            reasoning="Pull requests 탭 이동 시도",
            confidence=0.74,
        ),
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 열렸지만 PR 목록이 비어 있었습니다.",
        step_count=4,
        duration_seconds=8.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    results = search_run_history_retrieval_index(second_agent, goal=goal, limit=3)

    assert results
    top = results[0]
    assert str(top.get("entry_id") or "").strip()
    assert int(top.get("score", 0) or 0) > 0

    entry = get_run_history_retrieval_entry(second_agent, str(top.get("entry_id") or ""), goal=goal)

    assert entry
    assert entry["entry_id"] == top["entry_id"]
    assert "text" in entry


def test_run_history_retrieval_skips_prompt_transcript_rows(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    record_run_history_feedback(
        first_agent,
        step_number=4,
        decision=ActionDecision(
            action=ActionType.CLICK,
            ref_id="e66",
            element_id=12,
            reasoning="Pull requests 탭 이동 시도",
            confidence=0.74,
        ),
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 열렸지만 PR 목록이 비어 있었습니다.",
        step_count=4,
        duration_seconds=8.0,
    )
    record_run_history_transcript(
        first_agent,
        stage="actor_decision_prompt",
        role="user",
        content="Pull requests Pull requests Pull requests PR 목록이 비었습니다. prompt recursion noise",
        metadata={"goal_id": goal.id},
    )
    record_run_history_transcript(
        first_agent,
        stage="actor_decision_response",
        role="assistant",
        content="PR 목록이 비어 있어 다른 evidence가 필요합니다.",
        metadata={"goal_id": goal.id},
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)

    retrieval = build_run_history_retrieval_context(second_agent, goal=goal)

    assert "transcript:actor_decision_prompt" not in retrieval
    assert "transcript:actor_decision_response" in retrieval or "goal_end |" in retrieval


def test_run_history_retrieval_prioritizes_memory_and_outcomes_over_transcript(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    first_agent._action_feedback = [
        "최근 실패: reason_code=empty_pr_list, PR 목록이 비었습니다.",
    ]
    decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e91",
        element_id=17,
        reasoning="Pull requests 탭 진입 후 빈 목록 문제 확인",
        confidence=0.72,
    )
    record_run_history_feedback(
        first_agent,
        step_number=7,
        decision=decision,
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 보였지만 PR 목록이 비어 있었습니다.",
        step_count=7,
        duration_seconds=14.0,
    )
    record_run_history_transcript(
        first_agent,
        stage="actor_decision_response",
        role="assistant",
        content="관련 없는 장문 transcript noise Pull requests Pull requests Pull requests",
        metadata={"goal_id": goal.id},
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)
    second_agent._action_feedback = [
        "재개 포인트 확인 필요: reason_code=empty_pr_list",
    ]

    retrieval = build_run_history_retrieval_context(second_agent, goal=goal)
    lines = [line for line in retrieval.splitlines() if line.startswith("- ")]

    assert lines
    normalized_first = lines[0]
    if normalized_first.startswith("- [") and "] " in normalized_first:
        normalized_first = "- " + normalized_first.split("] ", 1)[1]
    assert (
        normalized_first.startswith("- MEMORY |")
        or normalized_first.startswith("- goal_end |")
        or normalized_first.startswith("- step_outcome |")
    )
    assert any("empty_pr_list" in line for line in lines[:3])


def test_run_history_retrieval_prefers_recent_attempts_and_latest_prior_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GAIA_RUN_HISTORY_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_RUN_HISTORY_ENABLED", "1")
    goal = _build_goal()

    first_agent = _HistoryAgent()
    initialize_run_history(first_agent, goal)
    first_decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e41",
        element_id=10,
        reasoning="예전 실행에서 다른 실패를 기록",
        confidence=0.61,
    )
    record_run_history_feedback(
        first_agent,
        step_number=2,
        decision=first_decision,
        success=False,
        changed=False,
        error="예전 실패 기록",
        reason_code="older_noise",
        state_change={},
    )
    record_run_history_goal_outcome(
        first_agent,
        goal=goal,
        status="failed",
        reason="예전 실패 기록",
        step_count=2,
        duration_seconds=6.0,
    )

    second_agent = _HistoryAgent()
    initialize_run_history(second_agent, goal)
    latest_decision = ActionDecision(
        action=ActionType.CLICK,
        ref_id="e77",
        element_id=17,
        reasoning="최신 prior run에서 빈 PR 목록에 막힘",
        confidence=0.7,
    )
    record_run_history_feedback(
        second_agent,
        step_number=5,
        decision=latest_decision,
        success=False,
        changed=False,
        error="PR 목록이 비었습니다.",
        reason_code="empty_pr_list",
        state_change={},
    )
    record_run_history_goal_outcome(
        second_agent,
        goal=goal,
        status="failed",
        reason="Pull requests 탭은 열렸지만 PR 목록이 비어 있었습니다.",
        step_count=5,
        duration_seconds=9.0,
    )

    third_agent = _HistoryAgent()
    initialize_run_history(third_agent, goal)
    third_agent._action_feedback = [
        "재개 포인트 확인 필요: reason_code=empty_pr_list",
    ]

    retrieval = build_run_history_retrieval_context(third_agent, goal=goal)
    lines = [line for line in retrieval.splitlines() if line.startswith("- ")]

    assert lines
    assert "empty_pr_list" in lines[0] or "Recent Attempts" in lines[0]
    assert not any("older_noise" in line for line in lines[:1])
