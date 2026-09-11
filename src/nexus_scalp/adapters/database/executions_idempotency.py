"""BUG-254: idempotent repair + creation of the audit_executions UNIQUE index.

WHY THIS EXISTS
===============

Release of 2026-09-10 (commit c2662e31, agent-17 durability audit) added the
UNIQUE index ``idx_executions_order_status ON audit_executions(order_id,
status)`` to ``AuditRepository._create_sqlite_tables`` — the code path that
runs on EVERY ``AuditRepository`` construction (engine boot, web console,
CLI doctor, smoke, repair, api_v1). On any database created BEFORE the
release, that table already carried duplicate ``(order_id, status)`` rows
from the un-deduplicated plain-INSERT era, so ``CREATE UNIQUE INDEX``
raised ``sqlite3.IntegrityError: UNIQUE constraint failed:
audit_executions.order_id, audit_executions.status`` and construction
died — i.e. the upgrade itself could not start. The affected-table
bootstrap (``idx_orders_execution_idempotency``) already follows the
"bounded detect-and-repair before create" pattern; audit_executions was
missing both the repair and the connection-level lock that pattern needs.

CONTRACT (mirrors the audit_orders repair contract, agent-17 2026-09-10)
========================================================================

* Identity = ``(order_id, status)``: one durable row per dispatch ATTEMPT
  outcome (dispatch.py logs exactly one ``log_execution`` per order_id).
  A redelivered/replayed attempt must collapse onto the existing row;
  a second DISTINCT status (FILLED after a REJECTED retry) is a real
  lifecycle event and keeps inserting its own row.
* Survivor rule = LOWEST id (earliest observation), the same rule the
  audit_orders repair uses: the first durable record of an event wins;
  later exact-identity rows are redeliveries.
* Reconciliation is NON-DESTRUCTIVE: superseded duplicate rows are moved
  to ``audit_executions_reconciled`` (verbatim + ``reconciled_at``),
  never hard-deleted — audit history is preserved (INV-007 shape, TIER_1
  retention ``never_delete`` semantics; the moved rows remain queryable).
* Bounded: one construction pass repairs at most
  ``_EXECUTIONS_DEDUP_REPAIR_BATCH`` duplicated identities (mirrors
  ``_ORDERS_DEDUP_REPAIR_BATCH``); a pathological database converges over
  consecutive boots instead of running an unbounded delete on the boot
  path. An un-repaired remainder FAILS the index creation loudly (the
  engine must not boot silently without the dedup identity).
* Concurrency: two processes constructing ``AuditRepository`` against the
  same SQLite file must not race the repair (repair under
  ``BEGIN IMMEDIATE`` + ``busy_timeout``, and the final index creation is
  retried-once after a re-read so a loser of the race converges to
  "index exists" instead of crashing — SQLite DDL itself is serialized
  by the write lock, so exactly one process performs each step).

Import-safety: stdlib only, no app imports at module level (usable from
the audit bootstrap in any context, mirroring database/app_columns.py).
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time

logger = logging.getLogger("nexus_scalp.adapters.audit_db")

#: Name of the canonical UNIQUE index this module guarantees.
EXECUTIONS_IDEMPOTENCY_INDEX = "idx_executions_order_status"

#: Bounded repair scan width per construction pass (mirrors the audit_orders
#: repair bound). Remaining pathological volume repairs on subsequent boots.
EXECUTIONS_DEDUP_REPAIR_BATCH = 500

#: Archived copy of superseded duplicate rows (non-destructive reconcile).
EXECUTIONS_RECONCILED_TABLE = "audit_executions_reconciled"

_RECONCILED_DDL = (
    f"CREATE TABLE IF NOT EXISTS {EXECUTIONS_RECONCILED_TABLE} ("
    "id INTEGER PRIMARY KEY, "
    "order_id TEXT NOT NULL, "
    "symbol TEXT NOT NULL, "
    "order_type TEXT NOT NULL, "
    "volume REAL NOT NULL, "
    "price REAL NOT NULL, "
    "status TEXT NOT NULL, "
    "executed_at TEXT NOT NULL, "
    "payload TEXT NOT NULL, "
    "original_id INTEGER NOT NULL, "
    "reconciled_at TEXT NOT NULL, "
    "reconcile_reason TEXT NOT NULL"
    ")"
)


def _count_duplicate_identities(conn: sqlite3.Connection, limit: int) -> int:
    """Number of distinct (order_id, status) identities that are duplicated.

    Scans at most ``limit`` groups (bounded boot-path cost, mirrors the
    audit_orders repair). 0 => nothing to repair.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM ("
        "SELECT order_id, status FROM audit_executions "
        "GROUP BY order_id, status "
        "HAVING COUNT(*) > 1 LIMIT ?"
        ")",
        (int(limit),),
    ).fetchone()
    return int(row[0]) if row else 0


def _repair_duplicate_identities(
    conn: sqlite3.Connection,
    *,
    repair_batch: int,
    scan_limit: int,
    reconcile_at: str,
) -> int:
    """Moves superseded duplicate rows to the reconciled archive (one pass).

    Deterministic survivor rule: keep the LOWEST id per (order_id, status)
    — the earliest durable observation of the attempt outcome; every later
    exact-identity row is a redelivery and is archived verbatim (never
    deleted). Runs inside the caller's ``BEGIN IMMEDIATE`` transaction.
    Returns the number of rows archived.
    """
    conn.execute(_RECONCILED_DDL)
    dup_rows = conn.execute(
        "SELECT order_id, status FROM audit_executions "
        "GROUP BY order_id, status HAVING COUNT(*) > 1 LIMIT ?",
        (int(scan_limit),),
    ).fetchall()

    archived = 0
    for order_id, status in dup_rows[: int(repair_batch)]:
        rows = conn.execute(
            "SELECT id FROM audit_executions WHERE order_id = ? AND status = ? ORDER BY id ASC",
            (order_id, status),
        ).fetchall()
        survivor_id = int(rows[0][0])
        # Rows already archived by an earlier interrupted/failed repair pass
        # (archive carries original_id + verbatim copy): do NOT re-archive
        # them (archive PK collision), but they MUST leave the live table —
        # their durable copy exists in the reconciled archive.
        already = {
            int(r[0])
            for r in conn.execute(
                f"SELECT original_id FROM {EXECUTIONS_RECONCILED_TABLE} "
                f"WHERE order_id = ? AND status = ?",
                (order_id, status),
            ).fetchall()
        }
        loser_ids = [int(r[0]) for r in rows[1:] if int(r[0]) not in already]
        delete_ids = set(loser_ids) | {i for i in already if i != survivor_id}
        # If the survivor itself was archived by the interrupted pass, the
        # earliest NOT-yet-archived row becomes the survivor (the archived
        # earliest row stays reconciled evidence, not live state).
        if survivor_id in already:
            remaining = [int(r[0]) for r in rows if int(r[0]) not in already]
            if not remaining:
                # Fully archived already: every live row has its durable
                # archive copy; clear the identity from the live table.
                conn.executemany(
                    "DELETE FROM audit_executions WHERE id = ?",
                    [(i,) for i in already],
                )
                continue
            survivor_id = remaining[0]
            delete_ids |= set(already)
        for loser_id in sorted(delete_ids):
            if loser_id not in loser_ids:
                # Already archived by an interrupted earlier pass: its durable
                # copy exists — only the live row needs to leave the table.
                conn.execute("DELETE FROM audit_executions WHERE id = ?", (loser_id,))
                continue
            conn.execute(
                f"INSERT INTO {EXECUTIONS_RECONCILED_TABLE} "
                "(id, order_id, symbol, order_type, volume, price, status, "
                " executed_at, payload, original_id, reconciled_at, reconcile_reason) "
                "SELECT id, order_id, symbol, order_type, volume, price, status, "
                "executed_at, payload, id, ?, 'BUG-254 duplicate (order_id,status) "
                "redelivery; earliest row kept' "
                f"FROM audit_executions WHERE id = ?",
                (reconcile_at, loser_id),
            )
            conn.execute("DELETE FROM audit_executions WHERE id = ?", (loser_id,))
            archived += 1
        if order_id is not None:  # never fully silent on a repaired identity
            logger.warning(
                "audit_executions duplicate attempt rows reconciled "
                "(kept earliest row) order_id=%r status=%r archived=%d",
                order_id,
                status,
                len(loser_ids),
            )
    return archived


def ensure_executions_idempotency_index(
    conn: sqlite3.Connection,
    *,
    reconcile_at: str | None = None,
    repair_batch: int = EXECUTIONS_DEDUP_REPAIR_BATCH,
    scan_limit: int = EXECUTIONS_DEDUP_REPAIR_BATCH,
    busy_timeout_ms: int = 10_000,
) -> dict[str, int]:
    """Repairs legacy duplicates, then guarantees the UNIQUE index exists.

    Idempotent and re-runnable: on a clean/fresh/already-migrated database
    the scan is two cheap reads and the CREATE UNIQUE INDEX IF NOT EXISTS
    is a no-op. Concurrency-safe: the repair runs under ``BEGIN IMMEDIATE``
    (single writer at a time on the SQLite file) and a transient UNIQUE
    failure on index creation is retried ONCE after re-scanning, so the
    loser of a cross-process race archives nothing and converges to
    "index exists" instead of crashing construction.

    Returns counters for aggregate observability:
    ``{duplicates, repaired, preserved}`` where ``duplicates`` is the
    number of duplicated identities seen this pass and ``preserved`` the
    number of original rows kept verbatim.
    """
    started = time.monotonic()
    if reconcile_at is None:
        reconcile_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with contextlib.suppress(sqlite3.Error):
        # Give the cross-process write lock room instead of failing fast;
        # the default isolation-level transaction wrapper below manages
        # the actual BEGIN IMMEDIATE.
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")

    counts = {"duplicates": 0, "repaired": 0, "preserved": 0}
    for attempt in (0, 1):
        try:
            # python sqlite3 opens an implicit transaction before DML/DDL, so
            # a plain ``BEGIN IMMEDIATE`` inside _create_sqlite_tables raises
            # 'cannot start a transaction within a transaction'. Use the
            # driver's own immediate-transaction primitive instead: a write
            # statement acquires the RESERVED lock at statement start
            # (busy_timeout gives the cross-process race its wait window),
            # which is the same single-writer serialization BEGIN IMMEDIATE
            # would provide.
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as begin_err:
                if "within a transaction" not in str(begin_err):
                    raise
                # python sqlite3 implicit txn active: writes still acquire the
                # RESERVED lock at statement start (same single-writer effect)
            try:
                counts["duplicates"] = _count_duplicate_identities(conn, scan_limit)
                if counts["duplicates"]:
                    counts["repaired"] = _repair_duplicate_identities(
                        conn,
                        repair_batch=repair_batch,
                        scan_limit=scan_limit,
                        reconcile_at=reconcile_at,
                    )
                counts["preserved"] = int(
                    conn.execute("SELECT COUNT(*) FROM audit_executions").fetchone()[0]
                )
                conn.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {EXECUTIONS_IDEMPOTENCY_INDEX} "
                    "ON audit_executions (order_id, status)"
                )
                conn.commit()
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    conn.rollback()
                raise
            duration_ms = round((time.monotonic() - started) * 1000.0, 1)
            logger.info(
                "audit_executions idempotency index ensured "
                "(duplicates=%d repaired=%d preserved=%d attempt=%d duration_ms=%s)",
                counts["duplicates"],
                counts["repaired"],
                counts["preserved"],
                attempt,
                duration_ms,
            )
            return counts
        except sqlite3.IntegrityError:
            if attempt == 0:
                # A concurrent writer created a duplicate between our scan
                # and the index build (or we lost a repair race): re-scan
                # once and converge. Persisting duplication after attempt 1
                # fails loudly — construction must surface it.
                logger.warning(
                    "audit_executions index creation hit a concurrent duplicate; "
                    "re-running bounded repair before retry"
                )
                continue
            raise
    # Unreachable: the loop always returns or raises.
    return counts  # pragma: no cover
