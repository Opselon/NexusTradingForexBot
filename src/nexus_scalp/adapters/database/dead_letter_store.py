"""Durable dead-letter store (audit finding A4 split)
=====================================================

Owns the ``audit_dead_letter`` table and everything dead-letter-only: the
table DDL, the durable record path, the newest-first listing, and the
row/sequence bookkeeping consumed by the runtime-safety metrics.

ZERO schema or SQL-text change versus the pre-split AuditRepository: the
``CREATE TABLE`` below is moved verbatim from
``nexus_scalp.adapters.database.audit_repository._create_sqlite_tables`` and
the INSERT/SELECT statements are moved verbatim from the former
``record_dead_letter`` / ``get_dead_letter_rows``. This module is a pure code
move (audit A4): the public contract lives on
:class:`~nexus_scalp.adapters.database.audit_repository.AuditRepository`,
which composes this store and delegates.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.adapters.audit_db")


class DeadLetterStore:
    """Durable store for failed audit rows (runtime-safety mission, P0).

    Enough information to recover/replay is persisted per failure: the SQL +
    safely-encoded args, failure classification, timestamps and a producer
    note. ``record()`` never raises; on catastrophic failure the loss is
    still counted in metrics and logged CRITICAL.

    The store borrows the owner's SQLite handle via constructor injection
    (``conn_factory`` returning a NEW connection per use — the exact
    ``sqlite3.connect(...)`` call sites AuditRepository itself uses) so no
    second database connection/pool is created.

    BOUNDED RETENTION (PERF-DEADLETTER, 2026-09-10): the table is a
    DIAGNOSTIC log, not financial truth, and a producer that fails on every
    row (the schema-drift incident: ~553k rows in ~15h at ~10/s) must not be
    able to grow audit.db without bound. ``record()`` therefore throttles a
    bounded prune pass (by default at most once per 30s; first call is
    always due): keep the newest ``max_rows`` rows, delete older ones in
    short batched transactions (WAL-safe, never one giant DELETE), and warn
    ONCE per overflow event so the loss of older diagnostics is loud but not
    a per-row log flood. Forensic value is preserved by keeping the NEWEST
    rows (the current failure signature) and a per-window WARNING with the
    pruned count; the schema repair contract itself is pinned by
    tests/unit/test_perf_deadletter_skeleton_repro.py so the incident class
    cannot silently recur.
    """

    #: Default cap on retained dead-letter rows (this run measured the
    #: incident corpus at ~1.8KB/row ⇒ ~0.9GB at 500k; 20k rows ≈ 36MB and
    #: keeps days of the newest signatures at incident rates).
    DEFAULT_MAX_ROWS: int = 20000

    #: Default prune batch (short transaction per batch — WAL concurrency).
    DEFAULT_PRUNE_BATCH: int = 2000

    #: Minimum seconds between prune passes (a 10/s producer must not prune
    #: per-row; the first pass is always due via the None sentinel).
    PRUNE_MIN_INTERVAL_SEC: float = 30.0

    def __init__(
        self,
        *,
        conn_factory: Callable[[str], sqlite3.Connection],
        is_sqlite: bool,
        db_path: str,
        max_rows: int = DEFAULT_MAX_ROWS,
        prune_batch: int = DEFAULT_PRUNE_BATCH,
    ) -> None:
        self._conn_factory = conn_factory
        self._is_sqlite = is_sqlite
        self._db_path = db_path
        self._max_rows = max(0, int(max_rows))
        self._prune_batch = max(1, int(prune_batch))
        # None = never ran (first prune is always due — do NOT compare a
        # 0.0 sentinel against time.monotonic(): on a freshly booted host
        # monotonic < interval and the first pass would be silently skipped).
        self._last_prune: float | None = None
        # One WARNING per overflow event (re-armed when the table drops
        # back under the cap).
        self._overflow_warned = False
        # =================================================================
        # DATA-INTEGRITY METRICS (runtime safety mission, P0).
        # Financial record loss MUST be observable. These counters are the
        # canonical dead-letter surfaces consumed by debug_snapshot + the
        # safety tests (AuditRepository re-exports them by delegation).
        # =================================================================
        self.audit_dead_letter_rows: int = 0
        self._dead_letter_seq: int = 0
        # Rows removed by retention (observable: growth is bounded AND the
        # pruning is visible in debug_snapshot consumers of this store).
        self.dead_letter_pruned_rows: int = 0

    # ---------------------------------------------------------------------
    # SCHEMA (moved VERBATIM from AuditRepository._create_sqlite_tables —
    # same columns, same defaults, no index, no migration: ZERO change).
    # ---------------------------------------------------------------------

    def create_table(self, conn: sqlite3.Connection) -> None:
        """Creates the audit_dead_letter table on the OWNER's setup connection.

        Called by AuditRepository._create_sqlite_tables inside its existing
        setup transaction — never opens a connection of its own.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_dead_letter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                failed_at TEXT NOT NULL,
                table_name TEXT NOT NULL DEFAULT '',
                query TEXT NOT NULL DEFAULT '',
                args_json TEXT NOT NULL DEFAULT '',
                error_type TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                sequence_no INTEGER NOT NULL DEFAULT 0,
                payload_note TEXT NOT NULL DEFAULT ''
            );
            """
        )

    # ---------------------------------------------------------------------
    # DURABLE JSON ENCODING (moved verbatim from the dead-letter owner:
    # AuditRepository._json_safe_args — dead-letter-only helper).
    # ---------------------------------------------------------------------

    @staticmethod
    def _json_safe_args(args: tuple[Any, ...]) -> str:
        """Durable JSON encoding of a failed row's SQL args (dead-letter).

        Never raises and never silently discards: a value that cannot be
        serialized (binary blob, open handle, exotic object) is replaced by
        a safe diagnostic envelope describing it, so the failing row is
        still recoverable/replayable in identity.
        """
        safe: list[Any] = []
        for value in args:
            try:
                json.dumps(value)
                safe.append(value)
            except Exception:
                safe.append(
                    {
                        "__unserializable__": True,
                        "type": type(value).__name__,
                        "repr": repr(value)[:500],
                    }
                )
        return json.dumps(safe, ensure_ascii=False, default=str)

    # ---------------------------------------------------------------------
    # RECORD (moved verbatim from AuditRepository.record_dead_letter).
    # ---------------------------------------------------------------------

    def record(
        self,
        *,
        query: str,
        args: tuple[Any, ...],
        error: BaseException,
        table_name: str = "",
        retry_count: int = 0,
        payload_note: str = "",
    ) -> bool:
        """Durably stores one failed audit row (dead-letter path).

        Enough information to recover/replay: the SQL + safely-encoded args,
        failure classification, timestamps and a producer note. Never raises;
        on catastrophic failure the loss is still counted in metrics and
        logged CRITICAL.
        """
        if not self._is_sqlite:
            self.audit_dead_letter_rows += 1
            return False
        self._dead_letter_seq += 1
        from datetime import UTC, datetime

        err_type = type(error).__name__ if error is not None else "UnknownError"
        err_msg = str(error)[:2000] if error is not None else ""
        sql = """
            INSERT INTO audit_dead_letter
                (failed_at, table_name, query, args_json, error_type,
                 error_message, retry_count, sequence_no, payload_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            # Derive table_name from the INSERT target when not provided.
            derived = table_name
            if not derived:
                q = (query or "").strip().upper()
                if q.startswith("INSERT INTO") or q.startswith("REPLACE INTO"):
                    rest = (
                        query.strip()[len("INSERT INTO ") :].split()[0]
                        if q.startswith("INSERT INTO")
                        else query.strip()[len("REPLACE INTO ") :].split()[0]
                    )
                    derived = rest.strip('"`[]')
            with self._conn_factory(self._db_path) as conn:
                conn.execute(
                    sql,
                    (
                        datetime.now(UTC).isoformat(),
                        derived,
                        str(query or "")[:8000],
                        self._json_safe_args(args or ()),
                        err_type,
                        err_msg,
                        int(retry_count),
                        self._dead_letter_seq,
                        str(payload_note or "")[:1000],
                    ),
                )
                conn.commit()
            self.audit_dead_letter_rows += 1
            self._prune_if_due()
            return True
        except Exception as dl_err:
            # Dead-letter persistence itself failed: the loss MUST be loud.
            self.audit_dead_letter_rows += 1
            logger.critical(
                "DEAD-LETTER WRITE FAILED — financial record unrecoverable. "
                "query=%s error_type=%s dl_error=%s",
                (query or "")[:200],
                err_type,
                dl_err,
            )
            return False

    # ---------------------------------------------------------------------
    # BOUNDED RETENTION (PERF-DEADLETTER, 2026-09-10).
    # The dead-letter table is diagnostic evidence, NOT financial truth —
    # but without a cap a producer failure loop grows audit.db ~1GB/15h
    # (measured). Retention policy, deterministic and WAL-safe:
    #   * cap     = newest N rows retained (Default 20_000 ≈ 36MB);
    #   * trigger = throttled to one pass per PRUNE_MIN_INTERVAL_SEC
    #     (None sentinel: the FIRST pass is always due);
    #   * delete  = batched rowid-anchored DELETEs, one short transaction
    #     per batch (never one giant DELETE against a huge table);
    #   * loud    = ONE WARNING per overflow event (re-armed when the table
    #     drops back under the cap) — never a per-row log flood, never
    #     silent.
    # Cleanup NEVER touches any other table (no ledger/experience/research
    # deletes here) and is safe under WAL: each batch is its own short
    # transaction on a fresh read connection, so concurrent readers/writers
    # are never blocked longer than one batch.
    # ---------------------------------------------------------------------

    def _prune_if_due(self, now: float | None = None) -> None:
        """Runs at most one bounded prune pass when the throttle allows."""
        if self._max_rows <= 0 or not self._is_sqlite:
            return
        import time as _time

        now = _time.monotonic() if now is None else now
        if self._last_prune is not None and (now - self._last_prune) < self.PRUNE_MIN_INTERVAL_SEC:
            return
        self._last_prune = now
        try:
            pruned = self._prune_locked()
        except Exception as prune_err:  # retention must NEVER break record()
            logger.warning("dead-letter retention prune failed (isolated): %s", prune_err)
            return
        if pruned > 0 and not self._overflow_warned:
            self._overflow_warned = True
            logger.warning(
                "DEAD-LETTER RETENTION: cap=%d exceeded — %d oldest diagnostic rows "
                "pruned (newest failure signatures retained; pruned_rows=%d). "
                "Fix the producing failure — this table is bounded by design.",
                self._max_rows,
                pruned,
                self.dead_letter_pruned_rows,
            )
        elif pruned == 0:
            # Under cap again: re-arm the overflow warning for the NEXT event.
            self._overflow_warned = False

    def _prune_locked(self) -> int:
        """One bounded prune pass: delete rows older than the newest N.

        Returns the number of rows removed. Count source of truth is the
        live COUNT (not the in-memory counter, which tracks writes since
        construction and can drift after restarts).
        """
        pruned = 0
        with self._conn_factory(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
            if total <= self._max_rows:
                return 0
            keep_floor = conn.execute(
                "SELECT id FROM audit_dead_letter ORDER BY id DESC LIMIT 1 OFFSET ?",
                (self._max_rows - 1,),
            ).fetchone()
            if keep_floor is None:
                return 0
            floor_id = keep_floor[0]
            while True:
                with conn:
                    cur = conn.execute(
                        "DELETE FROM audit_dead_letter WHERE id IN ("
                        "SELECT id FROM audit_dead_letter WHERE id < ? "
                        "ORDER BY id LIMIT ?)",
                        (floor_id, self._prune_batch),
                    )
                    removed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                pruned += removed
                self.dead_letter_pruned_rows += removed
                if removed < self._prune_batch:
                    break
            conn.commit()
        return pruned

    # ---------------------------------------------------------------------
    # LIST (moved verbatim from AuditRepository.get_dead_letter_rows).
    # ---------------------------------------------------------------------

    def list_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Reads dead-letter rows for inspection/tests (newest first)."""
        if not self._is_sqlite:
            return []
        try:
            with self._conn_factory(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM audit_dead_letter ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_dead_letter_rows failed: %s", e)
            return []

    def take_sequence_no(self) -> int:
        """Read-then-increment dead-letter sequence (pre-split overflow-file
        naming semantics: the CURRENT value names the file, then it steps)."""
        seq = self._dead_letter_seq
        self._dead_letter_seq += 1
        return seq
