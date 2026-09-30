"""Report-engine read adapter (Agent-5 P2-A modularization).

ONE home for the raw-SQL reads the performance report stages perform against
the audit DB (audit_signals / audit_orders / behavior_detections +
behavior_analysis / anomaly_events). The fetch blocks were extracted VERBATIM
from reporting/engine.py so the SQL, temp-table usage and exception scope are
bit-identical; only the location changed.

Contract (each function returns FetchResult):
    enabled=False  -> caller returns its empty Section (stage semantics)
    error set      -> caller logs + returns its failure Section
    rows           -> caller continues its existing computation verbatim

This module centralizes what previously reached into ``AccountingCore``
internals from four separate stage methods; the reporting engine remains a
read-only consumer and never writes financial truth.

Provider neutrality (PG-ACCT-READ-001 follow-up): all reads go through
``AccountingCore._query`` (SQLite connection OR the registered audit read
plane), never through a raw ``sqlite3.connect``. Timestamp literals are
space-separated (``%Y-%m-%d %H:%M:%S``); the column side is normalized with
``REPLACE`` so ISO ``T``-separated ``isoformat()`` values compare correctly on
both providers (see ``AccountingCore.load_snapshots`` for the same pattern).
Ticket joins use chunked ``IN (...)`` instead of SQLite temp tables — the
read plane is readonly and cannot create temp tables on PostgreSQL, and
SQLite accepts ``IN`` lists just the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.accounting.core import AccountingCore

#: Chunk size for IN (...) ticket joins (stays under SQLite's variable limit).
_TICKET_CHUNK = 400


@dataclass
class FetchResult:
    """Stage fetch outcome (see module docstring for the contract)."""

    enabled: bool
    rows: list[dict[str, Any]] = field(default_factory=list)
    rows2: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _core_disabled(core: AccountingCore) -> bool:
    """Single documented access point to the core's enabled flag."""
    return not core._enabled


def _normalized_ts_expr(column: str) -> str:
    """SQL expression normalizing an ISO timestamp column for text comparison.

    Mirrors the expression used by ``AccountingCore.load_snapshots``: strip the
    ``+00:00`` tz suffix, then turn the ISO ``T`` separator into a space so the
    value is directly comparable with ``%Y-%m-%d %H:%M:%S`` bound arguments on
    both SQLite and PostgreSQL.
    """
    return f"REPLACE(REPLACE({column}, 'T', ' '), '+00:00', '')"


def _chunked_in(chunk: list[Any]) -> str:
    """Comma-separated ``?`` placeholders for one chunk (caller binds values)."""
    return ",".join("?" for _ in chunk)


def fetch_model_rows(core: AccountingCore, start_sql: str, end_sql: str) -> FetchResult:
    """audit_signals rows in period (model/decision-funnel stage)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    ts = _normalized_ts_expr("generated_at")
    sql = f"SELECT action, blocked_by, payload FROM audit_signals WHERE {ts} >= ? AND {ts} < ?"
    try:
        rows = core._query(sql, (start_sql, end_sql))
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows)


def fetch_execution_rows(core: AccountingCore, start_sql: str, end_sql: str) -> FetchResult:
    """audit_orders latency rows in period (execution-quality stage)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    ts = _normalized_ts_expr("timestamp")
    sql = (
        "SELECT latency, reason, execution_mode, action FROM audit_orders "
        f"WHERE {ts} >= ? AND {ts} < ?"
    )
    try:
        rows = core._query(sql, (start_sql, end_sql))
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows)


def fetch_behavioral_rows(core: AccountingCore, tickets: list[str]) -> FetchResult:
    """behavior_detections + behavior_analysis rows for tickets (IN-chunk join)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    if not tickets:
        return FetchResult(enabled=True)
    try:
        rows: list[dict[str, Any]] = []
        rows2: list[dict[str, Any]] = []
        for start in range(0, len(tickets), _TICKET_CHUNK):
            chunk = tickets[start : start + _TICKET_CHUNK]
            placeholders = _chunked_in(chunk)
            rows.extend(
                core._query(
                    "SELECT d.behavior_key, d.pattern, d.severity, d.confidence, d.evidence "
                    f"FROM behavior_detections d WHERE d.ticket IN ({placeholders})",
                    chunk,
                )
            )
            rows2.extend(
                core._query(
                    f"SELECT a.* FROM behavior_analysis a WHERE a.ticket IN ({placeholders})",
                    chunk,
                )
            )
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows, rows2=rows2)


def fetch_anomaly_rows(core: AccountingCore, tickets: list[str]) -> FetchResult:
    """anomaly_events + behavior_analysis rows for tickets (IN-chunk join)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    if not tickets:
        return FetchResult(enabled=True)
    try:
        rows: list[dict[str, Any]] = []
        rows2: list[dict[str, Any]] = []
        for start in range(0, len(tickets), _TICKET_CHUNK):
            chunk = tickets[start : start + _TICKET_CHUNK]
            placeholders = _chunked_in(chunk)
            rows.extend(
                core._query(
                    "SELECT e.anomaly_type, e.severity, e.algorithm_version "
                    f"FROM anomaly_events e WHERE e.ticket IN ({placeholders})",
                    chunk,
                )
            )
            rows2.extend(
                core._query(
                    f"SELECT a.* FROM behavior_analysis a WHERE a.ticket IN ({placeholders})",
                    chunk,
                )
            )
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows, rows2=rows2)
