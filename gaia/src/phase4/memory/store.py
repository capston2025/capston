"""SQLite-backed memory store for GAIA execution traces."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import MemoryActionRecord, MemorySummaryRecord


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    def __init__(self, db_path: Path | None = None, enabled: bool = True):
        self.enabled = bool(enabled)
        self.db_path = db_path or (Path.home() / ".gaia" / "memory" / "kb.sqlite3")
        if self.enabled:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    runtime TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    goal_text TEXT NOT NULL,
                    url TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS action_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    url TEXT NOT NULL,
                    step_number INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    selector TEXT NOT NULL,
                    full_selector TEXT NOT NULL,
                    ref_id TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    effective INTEGER NOT NULL,
                    changed INTEGER NOT NULL,
                    reason_code TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    dom_hash TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    frame_index INTEGER,
                    tab_index INTEGER,
                    state_change_json TEXT NOT NULL,
                    attempt_logs_json TEXT NOT NULL,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id)
                );
                CREATE TABLE IF NOT EXISTS dialog_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    episode_id INTEGER,
                    created_at TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    command TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id)
                );
                CREATE INDEX IF NOT EXISTS idx_episodes_domain_created
                    ON episodes(domain, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_action_domain_reason_created
                    ON action_records(domain, reason_code, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_action_domain_action_created
                    ON action_records(domain, action, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_dialog_domain_created
                    ON dialog_summaries(domain, created_at DESC);
                """
            )

    def garbage_collect(self, retention_days: int = 30) -> int:
        if not self.enabled:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).isoformat()
        deleted = 0
        with self._connect() as conn:
            cur1 = conn.execute(
                "DELETE FROM action_records WHERE created_at < ?",
                (cutoff,),
            )
            cur2 = conn.execute(
                "DELETE FROM dialog_summaries WHERE created_at < ?",
                (cutoff,),
            )
            cur3 = conn.execute(
                """
                DELETE FROM episodes
                WHERE created_at < ?
                  AND id NOT IN (SELECT DISTINCT episode_id FROM action_records WHERE episode_id IS NOT NULL)
                  AND id NOT IN (SELECT DISTINCT episode_id FROM dialog_summaries WHERE episode_id IS NOT NULL)
                """,
                (cutoff,),
            )
            deleted = int(cur1.rowcount or 0) + int(cur2.rowcount or 0) + int(cur3.rowcount or 0)
        return deleted

    def start_episode(
        self,
        *,
        provider: str,
        model: str,
        runtime: str,
        domain: str,
        goal_text: str,
        url: str,
    ) -> int | None:
        if not self.enabled:
            return None
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO episodes (created_at, provider, model, runtime, domain, goal_text, url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now_iso(),
                    provider,
                    model,
                    runtime,
                    domain,
                    goal_text,
                    url,
                ),
            )
            return int(cur.lastrowid)

    def record_action(self, record: MemoryActionRecord) -> None:
        if not self.enabled:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO action_records (
                    episode_id, created_at, domain, url, step_number, action,
                    selector, full_selector, ref_id, success, effective, changed,
                    reason_code, reason, snapshot_id, dom_hash, epoch,
                    frame_index, tab_index, state_change_json, attempt_logs_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.episode_id,
                    _utc_now_iso(),
                    record.domain,
                    record.url,
                    int(record.step_number),
                    record.action,
                    record.selector,
                    record.full_selector,
                    record.ref_id,
                    1 if record.success else 0,
                    1 if record.effective else 0,
                    1 if record.changed else 0,
                    record.reason_code,
                    record.reason,
                    record.snapshot_id,
                    record.dom_hash,
                    int(record.epoch),
                    record.frame_index,
                    record.tab_index,
                    json.dumps(record.state_change or {}, ensure_ascii=False),
                    json.dumps(record.attempt_logs or [], ensure_ascii=False),
                ),
            )

    def add_dialog_summary(self, record: MemorySummaryRecord) -> None:
        if not self.enabled:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dialog_summaries (
                    episode_id, created_at, domain, command, summary, status, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.episode_id,
                    _utc_now_iso(),
                    record.domain,
                    record.command,
                    record.summary,
                    record.status,
                    json.dumps(record.metadata or {}, ensure_ascii=False),
                ),
            )

    def get_stats(self, domain: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        filters = ""
        params: list[Any] = []
        if domain:
            filters = " WHERE domain = ? "
            params.append(domain)
        with self._connect() as conn:
            episodes = conn.execute(
                f"SELECT COUNT(*) AS n FROM episodes{filters}",
                params,
            ).fetchone()["n"]
            actions = conn.execute(
                f"SELECT COUNT(*) AS n FROM action_records{filters}",
                params,
            ).fetchone()["n"]
            dialogs = conn.execute(
                f"SELECT COUNT(*) AS n FROM dialog_summaries{filters}",
                params,
            ).fetchone()["n"]
        return {
            "enabled": True,
            "domain": domain or "*",
            "episodes": int(episodes or 0),
            "action_records": int(actions or 0),
            "dialog_summaries": int(dialogs or 0),
            "db_path": str(self.db_path),
        }

    def clear_domain(self, domain: str | None = None) -> int:
        if not self.enabled:
            return 0
        deleted = 0
        with self._connect() as conn:
            if domain:
                cur1 = conn.execute("DELETE FROM action_records WHERE domain = ?", (domain,))
                cur2 = conn.execute("DELETE FROM dialog_summaries WHERE domain = ?", (domain,))
                cur3 = conn.execute(
                    "DELETE FROM episodes WHERE domain = ?",
                    (domain,),
                )
            else:
                cur1 = conn.execute("DELETE FROM action_records")
                cur2 = conn.execute("DELETE FROM dialog_summaries")
                cur3 = conn.execute("DELETE FROM episodes")
            deleted = int(cur1.rowcount or 0) + int(cur2.rowcount or 0) + int(cur3.rowcount or 0)
        return deleted

    def query_actions(
        self,
        *,
        domain: str,
        limit: int = 200,
        reason_codes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        clauses = ["domain = ?"]
        params: list[Any] = [domain]
        if reason_codes:
            placeholders = ",".join(["?"] * len(reason_codes))
            clauses.append(f"reason_code IN ({placeholders})")
            params.extend(reason_codes)
        where = " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM action_records
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [*params, max(1, int(limit))],
            ).fetchall()
        return [dict(row) for row in rows]

    def query_recent_summaries(self, *, domain: str, limit: int = 5) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM dialog_summaries
                WHERE domain = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (domain, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

