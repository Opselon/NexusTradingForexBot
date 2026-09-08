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
    """

    def __init__(
        self,
        *,
        conn_factory: Callable[[str], sqlite3.Connection],
        is_sqlite: bool,
        db_path: str,
    ) -> None:
        self._conn_factory = conn_factory
        self._is_sqlite = is_sqlite
        self._db_path = db_path
        # =================================================================
        # DATA-INTEGRITY METRICS (runtime safety mission, P0).
        # Financial record loss MUST be observable. These counters are the
        # canonical dead-letter surfaces consumed by debug_snapshot + the
        # safety tests (AuditRepository re-exports them by delegation).
        # =================================================================
        self.audit_dead_letter_rows: int = 0
        self._dead_letter_seq: int = 0

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
