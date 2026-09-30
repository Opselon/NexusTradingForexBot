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
import re
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

_ADD_COLUMN_RE = re.compile(
    r"""^\s*ALTER\s+TABLE\s*"?([A-Za-z_][\w$]*)"?\s+ADD\s+COLUMN\s*"?([A-Za-z_][\w$]*)"?""",
    re.IGNORECASE,
)

#: Extract the table name from a ``CREATE TABLE`` DDL statement. Used to decide
#: whether an auxiliary owner's schema is needed at all, so the pattern must
#: match the real DDL (``\s`` in a raw string), not a literal backslash-s.
_CREATE_TABLE_RE = re.compile(
    r"""CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s*"?([A-Za-z_][\w$]*)"?""",
    re.IGNORECASE,
)
_INDEX_RE = re.compile(
    r"""^\s*CREATE\s+(?:UNIQUE\s+)?INDEX""",
    re.IGNORECASE,
)


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

    def _ensure_destination_schema(
        self, dst_driver: Any, source_tables: set[str] | None = None
    ) -> None:
        """Replay the canonical schema idempotently onto the existing SQLite DB.

        ``replay_schema()`` is built by executing the migration chain against an
        empty in-memory SQLite database, so an ``ALTER TABLE ... ADD COLUMN``
        statement that was necessary during replay may already exist in the
        real destination. SQLite has no ``ADD COLUMN IF NOT EXISTS`` spelling;
        inspect the live destination first and skip only that exact already-
        present column. All other DDL errors remain fatal.
        """
        from nexus_scalp.database import migration
        from nexus_scalp.database.migration.schema_snapshot import replay_schema
        from nexus_scalp.database.registry import DatabaseDomain

        def apply_statements(statements: tuple[str, ...]) -> None:
            for statement in statements:
                match = _ADD_COLUMN_RE.match(statement)
                if match and dst_driver.table_exists(match.group(1)):
                    table = match.group(1)
                    column = match.group(2)
                    existing = {
                        str(item["name"]).casefold()
                        for item in dst_driver.table_columns(table)
                        if isinstance(item, dict) and "name" in item
                    }
                    if column.casefold() in existing:
                        logger.info(
                            "[DB-MIGRATE] reverse schema column already present: %s.%s",
                            table,
                            column,
                        )
                        continue
                try:
                    dst_driver.execute(statement)
                except Exception as exc:
                    if _INDEX_RE.match(statement) and "no such column" in str(exc).lower():
                        logger.warning(
                            "[DB-MIGRATE] skipping index creation for missing column: %s (%s)",
                            statement.strip(),
                            exc,
                        )
                        continue
                    raise

        apply_statements(replay_schema(domain=DatabaseDomain.AUDIT))

        # The PostgreSQL audit database can also contain lazy-owned operational
        # tables that are not part of AUDIT replay (for example shadow70_*).
        # Only replay an auxiliary owner when at least one of its tables is
        # actually present in the source, so an unrelated fresh destination is
        # not polluted with every optional schema.
        source_keys = {table.casefold() for table in (source_tables or set())}
        auxiliary = (
            ("model_lifecycle", migration.schema_snapshot.model_lifecycle_schema_statements),
            ("strategy_factory", migration.schema_snapshot.strategy_factory_schema_statements),
            ("ops_shadow", migration.schema_snapshot.ops_shadow_schema_statements),
            ("ops_hygiene", migration.schema_snapshot.ops_hygiene_schema_statements),
        )
        for owner, extractor in auxiliary:
            statements = tuple(extractor())
            expected = {
                match.group(1).casefold()
                for statement in statements
                for match in (_CREATE_TABLE_RE.search(statement),)
                if match
            }
            if not expected.intersection(source_keys):
                continue
            logger.info(
                "[DB-MIGRATE] reverse schema owner=%s source_tables=%d",
                owner,
                len(expected.intersection(source_keys)),
            )
            apply_statements(statements)

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
        self,
        dst_driver: Any,
        table: str,
        *,
        last_id: Any,
        rows_copied: int,
        total_rows: int,
        status: str,
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
            tables = [t for t in src_driver.list_tables() if t not in SKIP_TABLES]
            self._ensure_destination_schema(
                dst_driver,
                source_tables={table for table in tables},
            )
            self._ensure_checkpoint_table(dst_driver)
            checkpoints = self._load_checkpoints(dst_driver)
            if self.opts.tables:
                tables = [t for t in tables if t in self.opts.tables]

            for table in sorted(tables):
                self._migrate_table(
                    src_driver,
                    dst_driver,
                    table,
                    report,
                    on_progress,
                    checkpoints.get(table),
                )

            val = self.validate()
            report.validation = val.status
            report.provider_switch_ready = val.status == "PASS"
            report.status = "SUCCESS" if report.provider_switch_ready else "FAILED"
        except Exception as exc:
            logger.exception("Reverse migration failed: %s", exc)
            report.status = "FAILED"
            report.errors.append(str(exc))
        finally:
            src_driver.close()
            dst_driver.close()
            report.duration_ms = round((time.perf_counter() - t_start) * 1000.0, 1)

        return report

    @staticmethod
    def _column_mapping(src: Any, dst: Any, table: str) -> tuple[list[str], list[str]]:
        """Map source columns to destination columns case-insensitively."""
        source_columns = [str(c["name"]) for c in src.table_columns(table)]
        destination_columns = [str(c["name"]) for c in dst.table_columns(table)]
        source_by_key: dict[str, str] = {}
        destination_by_key: dict[str, str] = {}

        for name in source_columns:
            key = name.casefold()
            previous = source_by_key.get(key)
            if previous is not None and previous != name:
                raise RuntimeError(
                    f"Ambiguous source columns on {table}: {previous!r} and {name!r}"
                )
            source_by_key[key] = name

        for name in destination_columns:
            key = name.casefold()
            previous = destination_by_key.get(key)
            if previous is not None and previous != name:
                raise RuntimeError(
                    f"Ambiguous destination columns on {table}: {previous!r} and {name!r}"
                )
            destination_by_key[key] = name

        missing = [source_by_key[key] for key in source_by_key if key not in destination_by_key]
        if missing:
            raise RuntimeError(
                f"Destination SQLite table {table} is missing source columns: {missing}"
            )

        matched_dest = [name for name in destination_columns if name.casefold() in source_by_key]
        matched_src = [source_by_key[name.casefold()] for name in matched_dest]
        return (
            matched_src,
            matched_dest,
        )

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

        source_cols, destination_cols = self._column_mapping(src, dst, table)
        if not destination_cols:
            return

        source_by_key = {name.casefold(): name for name in source_cols}
        destination_by_key = {name.casefold(): name for name in destination_cols}
        order_key = "id" if "id" in destination_by_key else destination_cols[0].casefold()
        source_order_col = source_by_key[order_key]
        total_rows = src.scalar(f"SELECT COUNT(*) FROM {table}") or 0

        source_col_str = ", ".join(f'"{name}"' for name in source_cols)
        destination_col_str = ", ".join(f'"{name}"' for name in destination_cols)
        qmarks = ", ".join("?" for _ in destination_cols)
        insert_sql = f'INSERT OR REPLACE INTO "{table}" ({destination_col_str}) VALUES ({qmarks})'

        batch_size = self.opts.batch_size or DEFAULT_BATCH_SIZE
        copied = int((checkpoint or {}).get("rows_copied") or 0)
        last_val: Any = (checkpoint or {}).get("last_id")
        if (checkpoint or {}).get("status") == "COMPLETE":
            report.tables_migrated += 1
            report.rows_migrated += copied
            report.per_table[table] = {
                "source_rows": total_rows,
                "migrated_rows": copied,
                "status": "ALREADY_COMPLETE",
            }
            return
        self._save_checkpoint(
            dst,
            table,
            last_id=last_val,
            rows_copied=copied,
            total_rows=total_rows,
            status="RUNNING",
        )

        while True:
            where = ""
            args: list[Any] = []
            if last_val is not None:
                is_pg = getattr(src, "config", None) and src.config.is_postgresql
                ph = "%s" if is_pg else "?"
                where = f'WHERE "{source_order_col}" > {ph}'
                args.append(last_val)

            fetch_sql = (
                f'SELECT {source_col_str} FROM "{table}" {where} '
                f'ORDER BY "{source_order_col}" ASC LIMIT {batch_size}'
            )
            rows = src.query(fetch_sql, tuple(args))
            if not rows:
                break

            # Source rows use the PostgreSQL spelling; destination INSERTs use
            # the SQLite spelling. The mapping above makes this case-safe.
            tuples = [tuple(row.get(column) for column in source_cols) for row in rows]
            dst.executemany(insert_sql, tuples)

            copied += len(rows)
            last_val = rows[-1].get(source_order_col)
            self._save_checkpoint(
                dst,
                table,
                last_id=last_val,
                rows_copied=copied,
                total_rows=total_rows,
                status="RUNNING",
            )
            if on_progress:
                on_progress(table, copied, total_rows, len(rows))

        self._save_checkpoint(
            dst,
            table,
            last_id=last_val,
            rows_copied=copied,
            total_rows=total_rows,
            status="COMPLETE",
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
            digest.update(
                json.dumps(
                    [row.get(c) for c in columns],
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
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
                    details[t] = {
                        "source_rows": src.scalar(f"SELECT COUNT(*) FROM {t}") or 0,
                        "dest_rows": 0,
                        "match": False,
                        "missing": True,
                    }
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
                try:
                    source_columns, destination_columns = self._column_mapping(src, dst, t)
                except RuntimeError as exc:
                    row_ok = False
                    errors.append(str(exc))
                    details[t] = {
                        **detail,
                        "match": False,
                        "column_mapping_error": str(exc),
                    }
                    continue

                if source_columns and destination_columns:
                    destination_keys = {name.casefold() for name in destination_columns}
                    order_key = (
                        "id" if "id" in destination_keys else destination_columns[0].casefold()
                    )
                    source_order = next(
                        name for name in source_columns if name.casefold() == order_key
                    )
                    destination_order = next(
                        name for name in destination_columns if name.casefold() == order_key
                    )
                    source_digest = self._table_digest(src, t, source_columns, source_order)
                    dest_digest = self._table_digest(dst, t, destination_columns, destination_order)
                    detail.update(
                        {
                            "source_digest": source_digest,
                            "dest_digest": dest_digest,
                            "source_columns": source_columns,
                            "destination_columns": destination_columns,
                        }
                    )
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
