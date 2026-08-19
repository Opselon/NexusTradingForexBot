"""
DataQuarantine (TASK-22)
========================
MOVE -> MARK -> REPORT quarantine system for suspicious / uncertain data.

Instead of DELETE, rows whose identity or dependencies are uncertain are
MOVED into the quarantine store with full provenance (who/when/what/why),
MARKed in the hygiene journal, and REPORTed in the cycle telemetry.

Safety contract (mirrors TASK-11 hygiene):
  * The quarantine store is a separate SQLite DB under artifacts/archive/
    (_quarantine/quarantine_store.db) — never inside an active query path.
  * A quarantined row keeps:
        who    -> found_by (detector/worker/operator)
        when   -> detected_at
        what   -> database/table/row_id + full row snapshot (row_json)
        why    -> reason + cleanup_class + confidence
  * Restore is a first-class operation: restore() returns the snapshot and
    marks the item RESTORED (append-only event trail).
  * Deleting the SOURCE row happens ONLY when the caller passes an approved
    cleanup class with confidence EXACT_DUPLICATE (the CleanupExecutor's
    gates) — otherwise the source row stays and the quarantine copy is the
    additional evidence record (MARK + REPORT only).
  * Dedupe: one QUARANTINED item per (database, table, row_id); repeats are
    recorded as REPEAT events, not duplicate rows.

SQLite note: "database" and "table" are reserved words — every reference to
those COLUMN names is double-quoted in SQL text.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QUARANTINE_STATE_QUARANTINED = "QUARANTINED"
QUARANTINE_STATE_RESTORED = "RESTORED"
QUARANTINE_STATE_RESOLVED = "RESOLVED_DELETED"
QUARANTINE_STATE_EXTERMINATED = "EXTERMINATED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quarantine_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarantine_id TEXT UNIQUE NOT NULL,
    "database" TEXT NOT NULL,
    "table" TEXT NOT NULL,
    row_id TEXT NOT NULL,
    row_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    found_by TEXT NOT NULL DEFAULT '',
    cleanup_class TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'QUARANTINED',
    detected_at TEXT NOT NULL,
    resolved_at TEXT DEFAULT '',
    resolved_action TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine_items(status);
CREATE INDEX IF NOT EXISTS idx_quarantine_db_table
    ON quarantine_items("database", "table");
CREATE TABLE IF NOT EXISTS quarantine_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarantine_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_events_id
    ON quarantine_events(quarantine_id);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_quarantine_id() -> str:
    return f"Q-{uuid.uuid4().hex[:12]}"


class QuarantineStore:
    """Persistent quarantine store (thread-safe per call)."""

    def __init__(self, root: Path) -> None:
        d = Path(root) / "archive" / "_quarantine"
        d.mkdir(parents=True, exist_ok=True)
        self._db_path = d / "quarantine_store.db"
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
    # write path
    # ------------------------------------------------------------------
    def quarantine(
        self,
        *,
        database: str,
        table: str,
        row_id: Any,
        row: dict[str, Any] | None = None,
        reason: str = "",
        found_by: str = "",
        cleanup_class: str = "",
        confidence: str = "",
    ) -> dict[str, Any]:
        """Quarantine one row. Returns the item (existing on repeat)."""
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT * FROM quarantine_items "
                'WHERE "database" = ? AND "table" = ? '
                "AND row_id = ? AND status = 'QUARANTINED'",
                (database, table, str(row_id)),
            ).fetchone()
            if existing is not None:
                self._event(conn, existing["quarantine_id"], "REPEAT", reason)
                conn.commit()
                return dict(existing)
            qid = new_quarantine_id()
            conn.execute(
                "INSERT INTO quarantine_items "
                '(quarantine_id, "database", "table", row_id, row_json, reason, '
                " found_by, cleanup_class, confidence, status, detected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    qid,
                    database,
                    table,
                    str(row_id),
                    json.dumps(row or {}, default=str, sort_keys=True),
                    reason,
                    found_by,
                    cleanup_class,
                    confidence,
                    QUARANTINE_STATE_QUARANTINED,
                    _now_iso(),
                ),
            )
            self._event(conn, qid, "QUARANTINED", reason)
            conn.commit()
            return self._get_locked(conn, qid)
        finally:
            conn.close()

    def restore(self, quarantine_id: str, notes: str = "") -> dict[str, Any] | None:
        """Mark an item RESTORED and return its row snapshot for write-back."""
        conn = self._connect()
        try:
            row = self._get_locked(conn, quarantine_id)
            if row is None:
                return None
            conn.execute(
                "UPDATE quarantine_items SET status = ?, resolved_at = ?, "
                "resolved_action = 'RESTORED', notes = ? WHERE quarantine_id = ?",
                (QUARANTINE_STATE_RESTORED, _now_iso(), notes, quarantine_id),
            )
            self._event(conn, quarantine_id, "RESTORED", notes or "row snapshot returned")
            conn.commit()
            return self._get_locked(conn, quarantine_id)
        finally:
            conn.close()

    def resolve(
        self, quarantine_id: str, action: str = "RESOLVED_DELETED", notes: str = ""
    ) -> dict[str, Any] | None:
        """Mark RESOLVED_DELETED / EXTERMINATED after verified source deletion."""
        conn = self._connect()
        try:
            row = self._get_locked(conn, quarantine_id)
            if row is None:
                return None
            state = (
                QUARANTINE_STATE_RESOLVED
                if action == "RESOLVED_DELETED"
                else QUARANTINE_STATE_EXTERMINATED
            )
            conn.execute(
                "UPDATE quarantine_items SET status = ?, resolved_at = ?, "
                "resolved_action = ?, notes = ? WHERE quarantine_id = ?",
                (state, _now_iso(), action, notes, quarantine_id),
            )
            self._event(conn, quarantine_id, action, notes)
            conn.commit()
            return self._get_locked(conn, quarantine_id)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # read path
    # ------------------------------------------------------------------
    def get(self, quarantine_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            return self._get_locked(conn, quarantine_id)
        finally:
            conn.close()

    def list(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM quarantine_items WHERE status = ? "
                    "ORDER BY detected_at DESC LIMIT ?",
                    (status, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM quarantine_items ORDER BY detected_at DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            by_status: dict[str, int] = {}
            for (s, n) in conn.execute(
                "SELECT status, COUNT(*) FROM quarantine_items GROUP BY status"
            ).fetchall():
                by_status[s] = int(n)
            by_table: dict[str, int] = {}
            for (t, n) in conn.execute(
                "SELECT \"database\" || '.' || \"table\", COUNT(*) "
                "FROM quarantine_items GROUP BY \"database\", \"table\""
            ).fetchall():
                by_table[t] = int(n)
            return {
                "total": int(sum(by_status.values())),
                "by_status": by_status,
                "by_table": by_table,
                "store_path": str(self._db_path),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    @staticmethod
    def _get_locked(conn: sqlite3.Connection, quarantine_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM quarantine_items WHERE quarantine_id = ?", (quarantine_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["row"] = json.loads(d.pop("row_json") or "{}")
        except Exception:
            d["row"] = {}
        return d

    @staticmethod
    def _event(conn: sqlite3.Connection, quarantine_id: str, action: str, detail: str) -> None:
        conn.execute(
            "INSERT INTO quarantine_events (quarantine_id, action, detail, at) "
            "VALUES (?, ?, ?, ?)",
            (quarantine_id, action, detail or "", _now_iso()),
        )

    @property
    def db_path(self) -> Path:
        return self._db_path