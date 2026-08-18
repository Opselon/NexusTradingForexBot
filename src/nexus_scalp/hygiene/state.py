"""
Worker State Store (TASK-11)
============================
Persists hygiene worker state + run history (spec §43, §51, §66).

Two SQLite tables inside a dedicated worker-state DB under the archives
root (artifacts/archive/_hygiene_state/hygiene_state.db):

    hygiene_worker_state  one row: current state, cycle, last_scan,
                          last_cleanup, last_success, last_failure,
                          stats, mode
    hygiene_run_history   one row per run: run_id, database, started_at,
                          finished_at, duration, mode, rows_scanned,
                          duplicates_found, orphans_found, archived,
                          deleted, errors, bytes_freed,
                          verification_status, correlation_id

Crash recovery: run rows with verification_status = 'IN_PROGRESS' at
startup are marked 'INTERRUPTED' and NEVER resumed blindly (spec §66).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.hygiene import WorkerState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hygiene_worker_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL,
    mode TEXT NOT NULL,
    cycle INTEGER NOT NULL DEFAULT 0,
    last_scan TEXT DEFAULT '',
    last_cleanup TEXT DEFAULT '',
    last_success TEXT DEFAULT '',
    last_failure TEXT DEFAULT '',
    stats TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hygiene_run_history (
    run_id TEXT PRIMARY KEY,
    database TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    duration_ms REAL DEFAULT 0.0,
    mode TEXT NOT NULL,
    rows_scanned INTEGER DEFAULT 0,
    duplicates_found INTEGER DEFAULT 0,
    orphans_found INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0,
    errors TEXT DEFAULT '',
    bytes_freed INTEGER DEFAULT 0,
    verification_status TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    plan_json TEXT DEFAULT '{}'
);
"""


class HygieneStateStore:
    """Persistent worker state + run history (thread-safe per call)."""

    def __init__(self, root: Path) -> None:
        d = Path(root) / "archive" / "_hygiene_state"
        d.mkdir(parents=True, exist_ok=True)
        self._db_path = d / "hygiene_state.db"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # worker state
    # ------------------------------------------------------------------
    def get_state(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM hygiene_worker_state WHERE id = 1").fetchone()
        finally:
            conn.close()
        if row is None:
            return {
                "state": WorkerState.IDLE.value,
                "mode": "AUDIT_ONLY",
                "cycle": 0,
                "last_scan": "",
                "last_cleanup": "",
                "last_success": "",
                "last_failure": "",
                "stats": {},
                "updated_at": "",
            }
        d = dict(row)
        try:
            d["stats"] = json.loads(d.get("stats") or "{}")
        except Exception:
            d["stats"] = {}
        return d

    def set_state(
        self,
        state: WorkerState,
        *,
        mode: str | None = None,
        cycle: int | None = None,
        last_scan: str | None = None,
        last_cleanup: str | None = None,
        last_success: str | None = None,
        last_failure: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO hygiene_worker_state "
                "(id, state, mode, cycle, last_scan, last_cleanup, "
                " last_success, last_failure, stats, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "state=excluded.state, mode=excluded.mode, cycle=excluded.cycle, "
                "last_scan=excluded.last_scan, last_cleanup=excluded.last_cleanup, "
                "last_success=excluded.last_success, last_failure=excluded.last_failure, "
                "stats=excluded.stats, updated_at=excluded.updated_at",
                (
                    state.value,
                    mode or "AUDIT_ONLY",
                    cycle if cycle is not None else 0,
                    last_scan or "",
                    last_cleanup or "",
                    last_success or "",
                    last_failure or "",
                    json.dumps(stats or {}),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # run history
    # ------------------------------------------------------------------
    def record_run(self, run: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO hygiene_run_history "
                "(run_id, database, started_at, finished_at, duration_ms, mode, "
                " rows_scanned, duplicates_found, orphans_found, archived, deleted, "
                " errors, bytes_freed, verification_status, correlation_id, plan_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.get("run_id", ""),
                    run.get("database", ""),
                    run.get("started_at", ""),
                    run.get("finished_at", ""),
                    float(run.get("duration_ms", 0.0)),
                    run.get("mode", ""),
                    int(run.get("rows_scanned", 0)),
                    int(run.get("duplicates_found", 0)),
                    int(run.get("orphans_found", 0)),
                    int(run.get("archived", 0)),
                    int(run.get("deleted", 0)),
                    json.dumps(run.get("errors", [])),
                    int(run.get("bytes_freed", 0)),
                    run.get("verification", ""),
                    run.get("correlation_id", ""),
                    json.dumps(run.get("plan_summary", {}), default=str),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM hygiene_run_history ORDER BY started_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def recover_interrupted(self) -> int:
        """
        Marks IN_PROGRESS runs as INTERRUPTED at startup (spec §66).
        Never resumes a destructive batch from an unknown state.
        Returns the number of runs marked.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE hygiene_run_history SET verification_status = 'INTERRUPTED' "
                "WHERE verification_status = 'IN_PROGRESS'"
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()


def new_run_id() -> str:
    return f"HYGRUN-{uuid.uuid4().hex[:12]}"
