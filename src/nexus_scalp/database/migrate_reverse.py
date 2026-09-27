"""PostgreSQL → SQLite reverse migration engine.

Enforces Section 13 & 14 of the Dual Database Architecture:
    The operator must be able to return to SQLite from PostgreSQL safely:
    - Streams rows in bounded batches from PostgreSQL to SQLite.
    - Preserves primary keys, numeric precision, and financial integrity.
    - Verifies row counts and PnL / equity sums before completion.
    - Checkpointed and resumable.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers import get_driver
from nexus_scalp.database.migrate_copier import DEFAULT_BATCH_SIZE
from nexus_scalp.database.migrate_engine import MigrationOptions, MigrationReport
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.database.migrate_reverse")

CHECKPOINT_TABLE = "_nse_reverse_migration_checkpoints"
SKIP_TABLES = frozenset({"_nse_migration_checkpoints", CHECKPOINT_TABLE})


@dataclass
class ReverseMigrationValidationResult:
    """Detailed verification comparing PostgreSQL and SQLite state."""

    status: str
    row_count_match: bool
    financial_match: bool
    tables_checked: int
    tables_passed: int
    table_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class PostgresToSqliteMigrator:
    """Orchestrates streaming copy from PostgreSQL to SQLite."""

    def __init__(
        self,
        src_config: DatabaseConfig,
        dst_config: DatabaseConfig,
        options: MigrationOptions | None = None,
    ) -> None:
        self.src_cfg = src_config
        self.dst_cfg = dst_config
        self.opts = options or MigrationOptions()

    def _ensure_checkpoint_table(self, dst_driver: Any) -> None:
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} ("
            "  table_name TEXT PRIMARY KEY,"
            "  last_id INTEGER NOT NULL DEFAULT 0,"
            "  rows_copied INTEGER NOT NULL DEFAULT 0,"
            "  total_rows INTEGER NOT NULL DEFAULT 0,"
            "  status TEXT NOT NULL DEFAULT 'RUNNING',"
            "  updated_at REAL NOT NULL DEFAULT 0.0"
            ")"
        )
        dst_driver.execute(ddl)

    def _load_checkpoints(self, dst_driver: Any) -> dict[str, dict[str, Any]]:
        """Read reverse checkpoints so interrupted tables resume safely."""
        try:
            rows = dst_driver.query(f"SELECT * FROM {CHECKPOINT_TABLE}")
        except Exception:
            return {}
        return {
            row["table_name"]: {
                "last_id": row.get("last_id"),
                "rows_copied": int(row.get("rows_copied") or 0),
                "total_rows": int(row.get("total_rows") or 0),
                "status": row.get("status") or "RUNNING",
            }
            for row in rows
        }

    def _save_checkpoint(
        self, dst_driver: Any, table: str, *, last_id: Any,
        rows_copied: int, total_rows: int, status: str,
    ) -> None:
        """Persist a checkpoint after each committed batch."""
        now = time.time()
        dst_driver.execute(
            f"INSERT OR REPLACE INTO {CHECKPOINT_TABLE} "
            "(table_name,last_id,rows_copied,total_rows,status,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (table, last_id or 0, rows_copied, total_rows, status, now),
        )

    def preview(self) -> dict[str, Any]:
        """Dry-run preview: tables, row counts, and volume."""
        src_driver = get_driver(self.src_cfg)
        try:
            tables = [t for t in src_driver.list_tables() if t not in SKIP_TABLES]
            total_rows = 0
            details: dict[str, int] = {}
            for t in sorted(tables):
                cnt = src_driver.scalar(f"SELECT COUNT(*) FROM {t}") or 0
                details[t] = cnt
                total_rows += cnt

            return {
                "source": self.src_cfg.provider.value,
                "destination": self.dst_cfg.provider.value,
                "tables": len(tables),
                "table_details": details,
                "rows": total_rows,
                "issues": [],
                "warnings": [],
            }
        finally:
            src_driver.close()

    def run(
        self,
        on_progress: Callable[[str, int, int, int], None] | None = None,
    ) -> MigrationReport:
        """Run the streaming migration from PostgreSQL into SQLite."""
        report = MigrationReport(
            status="RUNNING",
            source=(
                self.src_cfg.provider.value
                if hasattr(self.src_cfg.provider, "value")
                else str(self.src_cfg.provider)
            ),
            destination=(
                self.dst_cfg.provider.value
                if hasattr(self.dst_cfg.provider, "value")
                else str(self.dst_cfg.provider)
            ),
        )
        t_start = time.perf_counter()
        src_driver = get_driver(self.src_cfg)
        dst_driver = get_driver(self.dst_cfg)

        try:
            self._ensure_checkpoint_table(dst_driver)
            checkpoints = self._load_checkpoints(dst_driver)
            tables = [t for t in src_driver.list_tables() if t not in SKIP_TABLES]
            if self.opts.tables:
                tables = [t for t in tables if t in self.opts.tables]

            for table in sorted(tables):
                self._migrate_table(
                    src_driver, dst_driver, table, report, on_progress,
                    checkpoints.get(table),
                )

            val = self.validate()
            report.validation = val.status
            report.provider_switch_ready = val.status == "PASS"
            report.status = "SUCCESS" if report.provider_switch_ready else "FAILED"
        except Exception as exc:
            logger.error("Reverse migration failed: %s", exc)
            report.status = "FAILED"
            report.errors.append(str(exc))
        finally:
            src_driver.close()
            dst_driver.close()
            report.duration_ms = round((time.perf_counter() - t_start) * 1000.0, 1)

        return report

    def _migrate_table(
        self,
        src: Any,
        dst: Any,
        table: str,
        report: MigrationReport,
        on_progress: Callable[[str, int, int, int], None] | None,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        """Stream rows from PostgreSQL to SQLite in ordered batches."""
        if not dst.table_exists(table):
            # A missing destination is data loss, never a successful skip.
            message = f"Table {table} does not exist in SQLite destination"
            report.errors.append(message)
            raise RuntimeError(message)

        cols = [c["name"] for c in dst.table_columns(table)]
        if not cols:
            return

        has_id = "id" in cols
        order_col = "id" if has_id else cols[0]
        total_rows = src.scalar(f"SELECT COUNT(*) FROM {table}") or 0

        col_str = ", ".join(f'"{c}"' for c in cols)
        qmarks = ", ".join("?" for _ in cols)
        insert_sql = f'INSERT OR REPLACE INTO "{table}" ({col_str}) VALUES ({qmarks})'

        batch_size = self.opts.batch_size or DEFAULT_BATCH_SIZE
        copied = int((checkpoint or {}).get("rows_copied") or 0)
        last_val: Any = (checkpoint or {}).get("last_id")
        if (checkpoint or {}).get("status") == "COMPLETE":
            report.tables_migrated += 1
            report.rows_migrated += copied
            report.per_table[table] = {
                "source_rows": total_rows, "migrated_rows": copied,
                "status": "ALREADY_COMPLETE",
            }
            return
        self._save_checkpoint(
            dst, table, last_id=last_val, rows_copied=copied,
            total_rows=total_rows, status="RUNNING",
        )

        while True:
            where = ""
            args: list[Any] = []
            if last_val is not None:
                is_pg = getattr(src, "config", None) and src.config.is_postgresql
                ph = "%s" if is_pg else "?"
                where = f'WHERE "{order_col}" > {ph}'
                args.append(last_val)

            fetch_sql = (
                f'SELECT {col_str} FROM "{table}" {where} '
                f'ORDER BY "{order_col}" ASC LIMIT {batch_size}'
            )
            rows = src.query(fetch_sql, tuple(args))
            if not rows:
                break

            # Convert row dicts to tuples in column order
            tuples = [tuple(r.get(c) for c in cols) for r in rows]
            dst.executemany(insert_sql, tuples)

            copied += len(rows)
            last_val = rows[-1].get(order_col)
            self._save_checkpoint(
                dst, table, last_id=last_val, rows_copied=copied,
                total_rows=total_rows, status="RUNNING",
            )
            if on_progress:
                on_progress(table, copied, total_rows, len(rows))

        self._save_checkpoint(
            dst, table, last_id=last_val, rows_copied=copied,
            total_rows=total_rows, status="COMPLETE",
        )
        report.tables_migrated += 1
        report.rows_migrated += copied
        report.per_table[table] = {
            "source_rows": total_rows,
            "migrated_rows": copied,
            "status": "COPIED",
        }

    @staticmethod
    def _table_digest(driver: Any, table: str, columns: list[str], order: str) -> str:
        """Create a deterministic digest of primary-key/value rows."""
        digest = hashlib.sha256()
        col_sql = ", ".join(f'"{c}"' for c in columns)
        for row in driver.query(f'SELECT {col_sql} FROM "{table}" ORDER BY "{order}" ASC'):
            digest.update(json.dumps(
                [row.get(c) for c in columns], sort_keys=True, default=str,
                separators=(",", ":"),
            ).encode("utf-8"))
            digest.update(b"\\n")
        return digest.hexdigest()

    def validate(self) -> ReverseMigrationValidationResult:
        """Validate counts, row values, and financial aggregates between PG and SQLite."""
        src = get_driver(self.src_cfg)
        dst = get_driver(self.dst_cfg)
        try:
            tables = [t for t in src.list_tables() if t not in SKIP_TABLES]
            if self.opts.tables:
                tables = [t for t in tables if t in self.opts.tables]

            row_ok = True
            details: dict[str, dict[str, Any]] = {}
            errors: list[str] = []

            for t in tables:
                if not dst.table_exists(t):
                    row_ok = False
                    errors.append(f"Missing destination table: {t}")
                    details[t] = {"source_rows": src.scalar(f"SELECT COUNT(*) FROM {t}") or 0,
                                  "dest_rows": 0, "match": False, "missing": True}
                    continue
                s_cnt = src.scalar(f"SELECT COUNT(*) FROM {t}") or 0
                d_cnt = dst.scalar(f"SELECT COUNT(*) FROM {t}") or 0
                match = s_cnt == d_cnt
                if not match:
                    row_ok = False
                    errors.append(f"Row count mismatch on {t}: source={s_cnt}, dest={d_cnt}")
                detail = {
                    "source_rows": s_cnt,
                    "dest_rows": d_cnt,
                    "match": match,
                }
                columns = [c["name"] for c in src.table_columns(t)]
                dst_columns = {c["name"] for c in dst.table_columns(t)}
                if columns and set(columns) <= dst_columns:
                    order = next((c for c in columns if c == "id"), columns[0])
                    source_digest = self._table_digest(src, t, columns, order)
                    dest_digest = self._table_digest(dst, t, columns, order)
                    detail.update({"source_digest": source_digest, "dest_digest": dest_digest})
                    detail["match"] = detail["match"] and source_digest == dest_digest
                    if source_digest != dest_digest:
                        row_ok = False
                        errors.append(f"Value digest mismatch on {t}")
                details[t] = detail

            # Financial comparison on audit_ledger
            fin_ok = True
            if "audit_ledger" in tables and dst.table_exists("audit_ledger"):
                s_pnl = float(src.scalar("SELECT COALESCE(SUM(pnl), 0.0) FROM audit_ledger") or 0.0)
                d_pnl = float(dst.scalar("SELECT COALESCE(SUM(pnl), 0.0) FROM audit_ledger") or 0.0)
                pnl_diff = abs(s_pnl - d_pnl)
                fin_ok = pnl_diff < 0.01
                if not fin_ok:
                    errors.append(
                        f"Financial PnL sum mismatch on audit_ledger: source={s_pnl}, dest={d_pnl}, diff={pnl_diff}"
                    )

            passed_count = sum(1 for d in details.values() if d["match"])
            overall_pass = row_ok and fin_ok
            return ReverseMigrationValidationResult(
                status="PASS" if overall_pass else "FAIL",
                row_count_match=row_ok,
                financial_match=fin_ok,
                tables_checked=len(details),
                tables_passed=passed_count,
                table_details=details,
                errors=errors,
            )
        finally:
            src.close()
            dst.close()
