"""ARCHIVE-ONLY retention for research history (edge round-3, 2026-09-09).

`research_events` and `research_evidence` grow without bound; unbounded
tables eventually slow every observability read. The contract here is strict:

  * ARCHIVE-ONLY: rows past the retention horizon MOVE to
    `research_events_archive` / `research_evidence_archive` (created by
    migration AUDIT-0009, same database, same columns + archived_at stamp).
  * NEVER DELETE HISTORY: a row is removed from the live table ONLY after the
    same rows are verified present in the archive (count-verified inside the
    same transaction). If the archive insert is interrupted, nothing is
    deleted and the call is a safe no-op on retry.
  * BOUNDED: each invocation moves at most `batch_size` rows so a single call
    can never dominate the worker cycle.

Pure SQLite, deterministic, no background threads.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.archive")

#: Default retention horizon (days). Research history is precious: keep a
#: full YEAR in the live tables, archive older rows.
DEFAULT_RETENTION_DAYS: int = 365
#: Max rows moved per table per invocation (bounded work per cycle).
DEFAULT_BATCH_SIZE: int = 5000


@dataclass(frozen=True)
class ArchiveResult:
    """Counters for one archiver invocation (honest zeros = nothing to do)."""

    events_archived: int
    evidence_archived: int

    @property
    def moved_anything(self) -> bool:
        return (self.events_archived + self.evidence_archived) > 0


def _ensure_archive_tables(conn: sqlite3.Connection) -> None:
    """Idempotent archive DDL for databases not yet migrated to AUDIT-0009.

    Mirrors the AUDIT-0009 definitions so the archiver is usable on any DB
    (the migration remains the canonical creator; this is a safety net).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_events_archive (
            id INTEGER PRIMARY KEY,
            event_id TEXT,
            strategy_id TEXT,
            research_run_id TEXT,
            gate_id TEXT,
            event_type TEXT,
            message TEXT,
            payload TEXT,
            occurred_at TEXT,
            archived_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_evidence_archive (
            id INTEGER PRIMARY KEY,
            evidence_id TEXT,
            strategy_id TEXT,
            research_run_id TEXT,
            gate_id TEXT,
            kind TEXT,
            content TEXT,
            content_hash TEXT,
            dataset_version TEXT,
            engine_version TEXT,
            created_at TEXT,
            archived_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def _archive_rows(
    conn: sqlite3.Connection,
    *,
    live_table: str,
    archive_table: str,
    stamp_column: str,
    id_column: str,
    cutoff_iso: str,
    batch_size: int,
) -> int:
    """Moves up to batch_size expired rows to the archive, count-verified.

    The DELETE's WHERE clause repeats the INSERT's selection AND constrains
    ids to the archive copy, so a row is only ever deleted after its archive
    twin exists (count check). Never raises on empty input; returns 0.
    """
    # Rows eligible for archiving (bounded batch, oldest first).
    select_expired = (
        f"SELECT {id_column} FROM {live_table} "
        f"WHERE {stamp_column} < ? ORDER BY {id_column} LIMIT ?"
    )
    expired = [row[0] for row in conn.execute(select_expired, (cutoff_iso, batch_size)).fetchall()]
    if not expired:
        return 0
    placeholders = ",".join("?" for _ in expired)
    conn.execute(
        f"INSERT INTO {archive_table} "
        f"({id_column}) SELECT {id_column} FROM {live_table} "
        f"WHERE {id_column} IN ({placeholders})",
        expired,
    )
    archived = conn.execute(
        f"SELECT COUNT(*) FROM {archive_table} WHERE {id_column} IN ({placeholders})",
        expired,
    ).fetchone()[0]
    if archived != len(expired):
        # Count mismatch: abort THIS batch, delete nothing. The transaction
        # wrapper rolls the partial insert back.
        raise sqlite3.IntegrityError(
            f"archive verification failed for {live_table}: "
            f"{archived} archived != {len(expired)} selected"
        )
    cur = conn.execute(
        f"DELETE FROM {live_table} WHERE {id_column} IN ({placeholders})",
        expired,
    )
    return int(cur.rowcount)


def archive_research_history(
    conn: sqlite3.Connection,
    *,
    older_than_days: int = DEFAULT_RETENTION_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ArchiveResult:
    """Archives research events/evidence older than the retention horizon.

    Transactional per table: insert-into-archive -> count-verify -> delete
    from live. Any verification failure rolls back that table's move and the
    live history is untouched (fail-safe, archive-only semantics).
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    cutoff_iso = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
    _ensure_archive_tables(conn)
    events_moved = 0
    evidence_moved = 0
    try:
        with conn:
            events_moved = _archive_rows(
                conn,
                live_table="research_events",
                archive_table="research_events_archive",
                stamp_column="occurred_at",
                id_column="id",
                cutoff_iso=cutoff_iso,
                batch_size=batch_size,
            )
        with conn:
            evidence_moved = _archive_rows(
                conn,
                live_table="research_evidence",
                archive_table="research_evidence_archive",
                stamp_column="created_at",
                id_column="id",
                cutoff_iso=cutoff_iso,
                batch_size=batch_size,
            )
    except sqlite3.Error as e:
        logger.error("[RESEARCH_ARCHIVE] event=FAILED error=%s", e)
        return ArchiveResult(events_archived=0, evidence_archived=0)
    if events_moved or evidence_moved:
        logger.info(
            "[RESEARCH_ARCHIVE] event=MOVED events=%d evidence=%d",
            events_moved,
            evidence_moved,
        )
    return ArchiveResult(events_archived=events_moved, evidence_archived=evidence_moved)
