"""Streaming batch copier — the SQLite→PostgreSQL data-migration core.

Large-dataset contract (DATABASE PORTABILITY mission):
  * NEVER loads a whole table into RAM: rows are read in ordered batches
    (fetchmany) and inserted transactionally into PostgreSQL;
  * each batch is its own transaction (bounded work, short locks);
  * per-table + per-batch progress is reportable and persisted to a
    checkpoint table on the DESTINATION, so an interrupted migration can be
    RESUMED from the last committed batch instead of restarting;
  * identity/sequence values are carried over explicitly (INSERT includes the
    id column), so AUTOINCREMENT/rowid identity is preserved and
    checkpoints/validation compare real keys;
  * column layout is introspected from the SOURCE (PRAGMA table_info) and
    matched by NAME to the destination, so ordering/extra columns never
    corrupt data.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import get_driver

#: Checkpoint table on the PostgreSQL destination (kept out of the app schema
#: by its prefix; the migrator owns it exclusively).
CHECKPOINT_TABLE = "_nse_migration_checkpoints"

DEFAULT_BATCH_SIZE = 2000


class MigrationError(RuntimeError):
    """Raised when a migration batch fails fatally."""


def _sqlite_table_columns(driver: Any, table: str) -> list[dict[str, Any]]:
    """Column layout from the SQLite source driver (PRAGMA table_info)."""
    return driver.table_columns(table)


def ensure_checkpoint_table(pg_driver: Any) -> None:
    """Create the migration checkpoint table on the destination."""
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} ("
        "  table_name TEXT PRIMARY KEY,"
        "  last_rowid INTEGER NOT NULL DEFAULT 0,"
        "  rows_copied INTEGER NOT NULL DEFAULT 0,"
        "  total_rows INTEGER NOT NULL DEFAULT 0,"
        "  status TEXT NOT NULL DEFAULT 'RUNNING',"
        "  started_at TEXT NOT NULL DEFAULT '',"
        "  updated_at TEXT NOT NULL DEFAULT '',"
        "  finished_at TEXT NOT NULL DEFAULT '',"
        "  batch_size INTEGER NOT NULL DEFAULT 0,"
        "  last_error TEXT NOT NULL DEFAULT '',"
        "  checksum TEXT NOT NULL DEFAULT ''"
        ")"
    )
    with pg_driver.connect() as conn:
        conn.execute(ddl)
        conn.commit()


def load_checkpoints(pg_driver: Any) -> dict[str, dict[str, Any]]:
    """Read existing checkpoint rows (for resume)."""
    try:
        rows = pg_driver.query(f"SELECT * FROM {CHECKPOINT_TABLE}")
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[r["table_name"]] = {
            "last_rowid": int(r.get("last_rowid") or 0),
            "rows_copied": int(r.get("rows_copied") or 0),
            "total_rows": int(r.get("total_rows") or 0),
            "status": r.get("status") or "",
        }
    return out


def _save_checkpoint(
    pg_driver: Any,
    table: str,
    *,
    last_rowid: int,
    rows_copied: int,
    total_rows: int,
    status: str = "RUNNING",
    batch_size: int = 0,
    last_error: str = "",
    checksum: str = "",
    finish: bool = False,
) -> None:
    """Upsert one checkpoint row (separate short transaction)."""
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat(timespec="seconds")
    sql = (
        f"INSERT INTO {CHECKPOINT_TABLE} "
        "(table_name, last_rowid, rows_copied, total_rows, status, started_at, "
        " updated_at, finished_at, batch_size, last_error, checksum) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (table_name) DO UPDATE SET "
        "last_rowid=EXCLUDED.last_rowid, rows_copied=EXCLUDED.rows_copied, "
        "total_rows=EXCLUDED.total_rows, status=EXCLUDED.status, "
        "updated_at=EXCLUDED.updated_at, "
        "finished_at=EXCLUDED.finished_at, batch_size=EXCLUDED.batch_size, "
        "last_error=EXCLUDED.last_error, checksum=EXCLUDED.checksum"
    )
    with pg_driver.connect() as conn:
        conn.execute(
            sql,
            (
                table,
                int(last_rowid),
                int(rows_copied),
                int(total_rows),
                status if status else "RUNNING",
                now,
                now,
                now if finish else "",
                int(batch_size),
                (last_error or "")[:2000],
                checksum,
            ),
        )
        conn.commit()


def iter_table_batches(
    src_driver: Any,
    table: str,
    columns: list[str],
    *,
    batch_size: int,
    order_col: str,
    start_after: int = 0,
) -> Iterable[list[dict[str, Any]]]:
    """Yield ordered batches of dict rows from the SQLite source.

    `order_col` must be the table's rowid/identity column; batches are cut by
    ``WHERE rowid > start_after ORDER BY rowid ASC LIMIT batch_size``.
    """
    conn = src_driver.connect(timeout=30.0)
    try:
        # Discover whether the table has a rowid (WITHOUT ROWID tables need
        # the identity column itself as the cursor).
        has_rowid = True
        try:
            cur = conn.execute(f"SELECT rowid FROM {table} LIMIT 1")
            has_rowid = cur.fetchone() is not None
            cur.close()
        except Exception:
            has_rowid = False
        if has_rowid:
            order_col = "rowid"
            where = "rowid > ?"
            order_by = "rowid ASC"
        else:
            cols = src_driver.table_columns(table)
            pks = [c["name"] for c in cols if c.get("pk")]
            if not pks:
                raise MigrationError(f"table {table}: no rowid and no primary key — cannot migrate")
            order_col = pks[0]
            where = f"{order_col} > ?"
            order_by = f"{order_col} ASC"
        col_list = ", ".join(columns)
        sql = f"SELECT {col_list} FROM {table} WHERE {where} ORDER BY {order_by}"
        cursor = conn.execute(sql, [start_after])
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield [dict(r) for r in rows]
            if len(rows) < batch_size:
                break
    finally:
        conn.close()


def last_rowid_of_batch(rows: list[dict[str, Any]], order_col: str) -> int:
    """Largest rowid/identity value in a batch (resume cursor)."""
    return int(max((r.get(order_col) or 0) for r in rows))


def copy_table(
    src_cfg: DatabaseConfig,
    pg_cfg: DatabaseConfig,
    table: str,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    resume: bool = True,
    on_progress: Callable[[str, int, int, int], None] | None = None,
    force_restart: bool = False,
    checksum: bool = True,
) -> dict[str, Any]:
    """Copy ONE table from SQLite to PostgreSQL in streamed batches.

    Returns a per-table report dict.  Raises MigrationError on fatal failure.
    """
    src_driver = get_driver(src_cfg)
    pg_driver = get_driver(pg_cfg)
    started = time.monotonic()

    ensure_checkpoint_table(pg_driver)
    checkpoints = load_checkpoints(pg_driver)
    cp = checkpoints.get(table) or {
        "last_rowid": 0,
        "rows_copied": 0,
        "total_rows": 0,
        "status": "",
    }
    if cp.get("status") == "COMPLETE" and not force_restart:
        return {
            "table": table,
            "status": "ALREADY_COMPLETE",
            "rows_copied": cp["rows_copied"],
            "duration_ms": 0.0,
        }
    if force_restart:
        cp = {"last_rowid": 0, "rows_copied": 0, "total_rows": 0, "status": ""}

    columns = _sqlite_table_columns(src_driver, table)
    if not columns:
        return {"table": table, "status": "SKIPPED_EMPTY", "rows_copied": 0, "duration_ms": 0.0}
    col_names = [c["name"] for c in columns]
    # identity/order column: prefer rowid alias 'id' if present else first pk
    pks = [c["name"] for c in columns if c.get("pk")]
    order_col = "id" if "id" in col_names else (pks[0] if pks else col_names[0])

    total_rows = int(src_driver.row_count(table))
    if total_rows == 0:
        _save_checkpoint(
            pg_driver,
            table,
            last_rowid=0,
            rows_copied=0,
            total_rows=0,
            status="COMPLETE",
            batch_size=batch_size,
        )
        return {"table": table, "status": "EMPTY", "rows_copied": 0, "duration_ms": 0.0}

    _save_checkpoint(
        pg_driver,
        table,
        last_rowid=int(cp.get("last_rowid") or 0),
        rows_copied=int(cp.get("rows_copied") or 0),
        total_rows=total_rows,
        status="RUNNING",
        batch_size=batch_size,
    )
    rows_copied = int(cp.get("rows_copied") or 0)
    last_rowid = int(cp.get("last_rowid") or 0)
    batch_no = 0

    # destination insert template
    placeholders = ",".join("%s" for _ in col_names)
    insert_sql = (
        f"INSERT INTO {table} ({','.join(col_names)}) VALUES ({placeholders}) "
        "ON CONFLICT DO NOTHING"
    )

    def _checksum_for(rows: list[dict[str, Any]]) -> str:
        if not checksum:
            return ""
        import hashlib

        h = hashlib.sha256()
        for r in rows:
            h.update((json.dumps(r, sort_keys=True, default=str)).encode("utf-8"))
        return h.hexdigest()

    try:
        for batch in iter_table_batches(
            src_driver,
            table,
            col_names,
            batch_size=batch_size,
            order_col=order_col,
            start_after=last_rowid,
        ):
            batch_no += 1
            if not batch:
                break
            with pg_driver.connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, [tuple(r.get(c) for c in col_names) for r in batch])
                conn.commit()
            batch_last = last_rowid_of_batch(batch, order_col)
            last_rowid = max(last_rowid, batch_last)
            rows_copied += len(batch)
            chk = _checksum_for(batch)
            _save_checkpoint(
                pg_driver,
                table,
                last_rowid=last_rowid,
                rows_copied=rows_copied,
                total_rows=total_rows,
                status="RUNNING",
                batch_size=batch_size,
                checksum=chk,
            )
            if on_progress:
                on_progress(table, rows_copied, total_rows, batch_no)
    except Exception as exc:
        _save_checkpoint(
            pg_driver,
            table,
            last_rowid=last_rowid,
            rows_copied=rows_copied,
            total_rows=total_rows,
            status="FAILED",
            batch_size=batch_size,
            last_error=str(exc),
        )
        raise MigrationError(f"table {table} failed at batch {batch_no}: {exc}") from exc

    _save_checkpoint(
        pg_driver,
        table,
        last_rowid=last_rowid,
        rows_copied=rows_copied,
        total_rows=total_rows,
        status="COMPLETE",
        batch_size=batch_size,
        finish=True,
    )
    return {
        "table": table,
        "status": "COMPLETE",
        "rows_copied": rows_copied,
        "total_rows": total_rows,
        "duration_ms": round((time.monotonic() - started) * 1000.0, 1),
        "batches": batch_no,
    }
