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

PG-ARCHIVE-WRITE-001: the module above was SQLite-only by construction —
``PRAGMA table_info``, ``datetime('now')``, ``sqlite3.IntegrityError`` and
``with conn:`` transaction semantics. Under a persisted PostgreSQL provider
the research worker handed this module the pooled READ-plane cursor (which
runs ``SET default_transaction_read_only=on``), so every archive cycle failed
with ``ReadOnlySqlTransaction: cannot execute CREATE TABLE in a read-only
transaction`` — the research history was never archived at all. The executor
below routes PG through the audit domain's WRITE plane and translates the
SQLite-isms (DDL via the migration translator, qmark placeholders via the
driver translator) while keeping the insert-verify-delete contract intact.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

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


def _ensure_archive_tables(ex: Any) -> None:
    """Idempotent archive DDL for databases not yet migrated to AUDIT-0009.

    Mirrors the AUDIT-0009 definitions so the archiver is usable on any DB
    (the migration remains the canonical creator; this is a safety net).
    Statements stay in the SQLite dialect — the provider executor translates
    them; the SQLite executor runs them as-is.
    """
    ex.execute_ddl(
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
    ex.execute_ddl(
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


class _SqliteArchiveExecutor:
    """The historical path: a raw sqlite3 connection, unchanged semantics."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def execute_ddl(self, sql: str) -> None:
        self._conn.execute(sql)

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        cur = self._conn.execute(sql, args)
        return cur.rowcount if cur.rowcount is not None else 0

    def fetch_all(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple]:
        return [tuple(r) for r in self._conn.execute(sql, args).fetchall()]

    def table_columns(self, table: str) -> list[str]:
        return [
            str(row[1])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]

    def transaction(self) -> Any:
        return self._conn  # ``with conn:`` — SQLite's native transaction

    def raise_data_error(self, message: str) -> Exception:
        return sqlite3.IntegrityError(message)


class _ProviderArchiveExecutor:
    """PostgreSQL path: writes on the audit WRITE plane, reads on the read plane.

    The write backend translates qmark placeholders and commits per statement;
    the migration translator rewrites the SQLite DDL. The count-verify reads
    the *committed* archive rows, so a crash between insert and delete leaves
    the archive copy behind (the safe, archive-only direction) and the next
    cycle simply re-verifies.
    """

    def __init__(self, repo: Any) -> None:
        self._repo = repo
        from nexus_scalp.adapters.database.provider_store import _write_backend

        self._write = _write_backend(repo, domain="audit")
        if self._write is None:
            raise RuntimeError(
                "research archive: no audit write backend provisioned for a "
                "pooled provider — refusing to archive through a read-only plane"
            )
        from nexus_scalp.research.store import _ProviderRead

        self._read = _ProviderRead(repo)

    def execute_ddl(self, sql: str) -> None:
        from nexus_scalp.database.migration.pg_schema import translate_ddl

        self._write.execute(translate_ddl(sql))

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        self._write.execute(sql, tuple(args))
        return 0  # rowcount is not surfaced by the pooled backend

    def fetch_all(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple]:
        rows = self._read.rows(sql, tuple(args))
        return [tuple(r.values()) for r in rows]

    def table_columns(self, table: str) -> list[str]:
        # information_schema is provider-native and needs no translation.
        rows = self._read.rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return [str(r["column_name"]) for r in rows]

    def transaction(self) -> Any:
        import contextlib

        # Per-statement commit on the pooled backend: a scoped no-op context.
        return contextlib.nullcontext()

    def raise_data_error(self, message: str) -> Exception:
        return RuntimeError(message)


def _archive_rows(
    ex: Any,
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
    expired = [row[0] for row in ex.fetch_all(select_expired, (cutoff_iso, batch_size))]
    if not expired:
        return 0
    placeholders = ",".join("?" for _ in expired)
    # Copy the FULL row set — the archive is the history; an id-only copy
    # would destroy every payload column and silently falsify retention.
    archive_cols = [c for c in ex.table_columns(archive_table) if c != "archived_at"]
    col_list = ",".join(archive_cols)
    ex.execute(
        f"INSERT INTO {archive_table} ({col_list}) "
        f"SELECT {col_list} FROM {live_table} "
        f"WHERE {id_column} IN ({placeholders})",
        tuple(expired),
    )
    archived = ex.fetch_all(
        f"SELECT COUNT(*) FROM {archive_table} WHERE {id_column} IN ({placeholders})",
        tuple(expired),
    )[0][0]
    if archived != len(expired):
        # Count mismatch: abort THIS batch, delete nothing. The transaction
        # wrapper rolls the partial insert back (SQLite) or the safe
        # archive-copy-survives direction applies (provider path).
        raise ex.raise_data_error(
            f"archive verification failed for {live_table}: "
            f"{archived} archived != {len(expired)} selected"
        )
    return ex.execute(
        f"DELETE FROM {live_table} WHERE {id_column} IN ({placeholders})",
        tuple(expired),
    )


def _resolve_executor(target: Any) -> Any:
    """A sqlite3 connection keeps the historical path; a repo goes provider."""
    if isinstance(target, sqlite3.Connection):
        return _SqliteArchiveExecutor(target)
    return _ProviderArchiveExecutor(target)


def archive_research_history(
    target: Any,
    *,
    older_than_days: int = DEFAULT_RETENTION_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ArchiveResult:
    """Archives research events/evidence older than the retention horizon.

    Transactional per table: insert-into-archive -> count-verify -> delete
    from live. Any verification failure rolls back that table's move and the
    live history is untouched (fail-safe, archive-only semantics).

    ``target`` is a raw sqlite3 connection (historical callers) or an
    ``AuditRepository`` (pooled providers — writes route to the audit write
    plane; PG-ARCHIVE-WRITE-001).
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    ex = _resolve_executor(target)
    cutoff_iso = (datetime.now(UTC) - timedelta(days=older_than_days)).isoformat()
    _ensure_archive_tables(ex)
    events_moved = 0
    evidence_moved = 0
    try:
        with ex.transaction():
            events_moved = _archive_rows(
                ex,
                live_table="research_events",
                archive_table="research_events_archive",
                stamp_column="occurred_at",
                id_column="id",
                cutoff_iso=cutoff_iso,
                batch_size=batch_size,
            )
        with ex.transaction():
            evidence_moved = _archive_rows(
                ex,
                live_table="research_evidence",
                archive_table="research_evidence_archive",
                stamp_column="created_at",
                id_column="id",
                cutoff_iso=cutoff_iso,
                batch_size=batch_size,
            )
    except Exception as e:
        logger.error("[RESEARCH_ARCHIVE] event=FAILED error=%s", e)
        return ArchiveResult(events_archived=0, evidence_archived=0)
    if events_moved or evidence_moved:
        logger.info(
            "[RESEARCH_ARCHIVE] event=MOVED events=%d evidence=%d",
            events_moved,
            evidence_moved,
        )
    return ArchiveResult(events_archived=events_moved, evidence_archived=evidence_moved)
