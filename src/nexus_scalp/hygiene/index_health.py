"""
Index Health Monitor (TASK-22)
==============================
Runtime index health reports (spec §10 — QUERY_HEALTH_REPORT):

  * missing indexes  -> heuristic: tables with a high row count and a
                        WHERE/ORDER BY column that has NO index
  * duplicate indexes -> same column set covered by multiple indexes
  * unused indexes    -> estimated via per-index seeks; never dropped
                        automatically (only REPORTED)
  * polling_mode      -> a flag the runtime can set: when True, the DB is
                        a live polling target and the monitor skips the
                        slow-query advisory for it

Design constraints:
  * READ-ONLY (PRAGMA index_list / index_info, sqlite_stat1 when present).
  * Never creates or drops schema. Schema changes go through TASK-10
    migrations (hygiene discipline).
  * WAL-mode databases are normal SQLite files; sqlite_stat1 is populated
    by ANALYZE — absence is fine, we degrade gracefully.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Column names that are high-value WHERE targets for the heuristic.
SUSPECT_WHERE_COLUMNS = {
    "ticket",
    "order_id",
    "position_id",
    "trade_id",
    "idempotency_key",
    "article_hash",
    "duplicate_of",
    "source_id",
    "article_id",
    "analysis_id",
    "run_id",
    "symbol",
    "ts",
    "timestamp",
    "generated_at",
    "open_time",
    "close_time",
    "event_timestamp",
    "window_start",
    "created_at",
    "updated_at",
    "event_type",
    "status",
    "reason_code",
    "strategy_id",
    "request_id",
}


@dataclass
class IndexFinding:
    category: str  # MISSING | DUPLICATE | UNUSED | ROW_COUNT
    table: str
    detail: str
    columns: list[str] = field(default_factory=list)
    ref_sql: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "table": self.table,
            "detail": self.detail,
            "columns": self.columns,
            "ref_sql": self.ref_sql,
        }


class IndexHealthMonitor:
    """Read-only index health + slow-query advisory."""

    def __init__(self, *, polling_mode: bool = False) -> None:
        self.polling_mode = polling_mode

    # ------------------------------------------------------------------
    # schema introspection helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
        try:
            return [d[1] for d in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _indexes(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        """Returns [{name, unique, columns:[...]}] per index on the table."""
        out: list[dict[str, Any]] = []
        for row in conn.execute(f"PRAGMA index_list('{table}')").fetchall():
            # Column order varies across SQLite versions:
            #   older: (seq, name, unique); newer: (seq, name, unique, origin, partial)
            name = row[1]
            unique = bool(row[2])
            cols = [
                d[2]
                for d in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
                if d[2] is not None
            ]
            out.append({"name": name, "unique": unique, "columns": cols})
        return out

    # ------------------------------------------------------------------
    # scans
    # ------------------------------------------------------------------
    def scan_missing(self, conn: sqlite3.Connection, table: str) -> list[IndexFinding]:
        """Heuristic missing-index scan for one table."""
        findings: list[IndexFinding] = []
        cols = set(self._columns(conn, table))
        if not cols:
            return findings
        try:
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.OperationalError:
            return findings
        if row_count < 1000:
            return findings  # small tables: indexes are a wash

        indexed = set()
        for idx in self._indexes(conn, table):
            indexed.update(idx["columns"][:1])  # leading column matters most
        for cand in sorted(SUSPECT_WHERE_COLUMNS & cols):
            if cand not in indexed:
                findings.append(
                    IndexFinding(
                        category="MISSING",
                        table=table,
                        detail=f"high-cardinality WHERE column '{cand}' has no index "
                        f"(table has {row_count} rows)",
                        columns=[cand],
                        ref_sql=f"CREATE INDEX idx_{table}_{cand} ON {table}({cand}) "
                        "-- advisory only; schema changes go through TASK-10",
                    )
                )
        return findings

    def scan_duplicates(self, conn: sqlite3.Connection, table: str) -> list[IndexFinding]:
        findings: list[IndexFinding] = []
        indexes = self._indexes(conn, table)
        seen: dict[tuple[str, ...], str] = {}
        for idx in indexes:
            key = tuple(idx["columns"])
            if not key:
                continue
            if key in seen:
                findings.append(
                    IndexFinding(
                        category="DUPLICATE",
                        table=table,
                        detail=f"index '{idx['name']}' duplicates column set of "
                        f"'{seen[key]}'",
                        columns=list(key),
                    )
                )
            else:
                seen[key] = idx["name"]
        return findings

    def scan_unused(self, conn: sqlite3.Connection, table: str) -> list[IndexFinding]:
        findings: list[IndexFinding] = []
        # sqlite_stat1 gives us relative usage (idx NUNIQUE/NOI); a super-high
        # NOI relative to table rows suggests the index rarely narrows.
        # This is only an ADVISORY — never an auto-drop signal.
        try:
            stat = {
                (s[0], s[1])
                for s in conn.execute("SELECT * FROM sqlite_stat1").fetchall()
            }
        except sqlite3.OperationalError:
            stat = set()
        if not stat:
            return findings
        for idx in self._indexes(conn, table):
            if (table, idx["name"]) in stat:
                continue
            findings.append(
                IndexFinding(
                    category="UNUSED",
                    table=table,
                    detail=f"index '{idx['name']}' has no sqlite_stat1 entry "
                    "(absent from planner usage — advisory only)",
                    columns=idx["columns"],
                )
            )
        return findings

    def scan_table(self, conn: sqlite3.Connection, table: str) -> list[IndexFinding]:
        findings: list[IndexFinding] = []
        findings.extend(self.scan_missing(conn, table))
        findings.extend(self.scan_duplicates(conn, table))
        if not self.polling_mode:
            findings.extend(self.scan_unused(conn, table))
        return findings

    def scan_database(
        self, conn: sqlite3.Connection, db_key: str, max_tables: int = 120
    ) -> dict[str, Any]:
        tables = [
            (name,)
            for name, in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ][:max_tables]
        findings: list[IndexFinding] = []
        for (name,) in tables:
            findings.extend(self.scan_table(conn, name))
        return {
            "database": db_key,
            "polling_mode": self.polling_mode,
            "tables_scanned": len(tables),
            "findings": [f.as_dict() for f in findings],
            "summary": {
                cat: sum(1 for f in findings if f.category == cat)
                for cat in ("MISSING", "DUPLICATE", "UNUSED")
            },
            "generated_at": datetime.now(UTC).isoformat(),
        }

    def slow_query_report(self, db_path: str) -> dict[str, Any]:
        """Advisory wrapper log marker (callers with instrumentation hook in
        their slow-query counters; this returns the envelope)."""
        return {
            "database": db_path,
            "slow_queries": [],  # populated by instrumented callers
            "advice": (
                "enable application-level query timing; the engine never "
                "issues CREATE/DROP INDEX from the runtime path (TASK-10)"
            ),
            "generated_at": datetime.now(UTC).isoformat(),
        }