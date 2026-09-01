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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.accounting.core import AccountingCore


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


def _core_connect(core: AccountingCore):
    """Single documented access point to the core's connection."""
    return core._connect()


def fetch_model_rows(core: AccountingCore, start_sql: str, end_sql: str) -> FetchResult:
    """audit_signals rows in period (model/decision-funnel stage)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    sql = (
        "SELECT action, blocked_by, payload FROM audit_signals "
        "WHERE generated_at >= ? AND generated_at < ?"
    )
    try:
        with _core_connect(core) as conn:
            rows = [dict(r) for r in conn.execute(sql, (start_sql, end_sql))]
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows)


def fetch_execution_rows(core: AccountingCore, start_sql: str, end_sql: str) -> FetchResult:
    """audit_orders latency rows in period (execution-quality stage)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    sql = (
        "SELECT latency, reason, execution_mode, action FROM audit_orders "
        "WHERE timestamp >= ? AND timestamp < ?"
    )
    try:
        with _core_connect(core) as conn:
            rows = [dict(r) for r in conn.execute(sql, (start_sql, end_sql))]
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows)


def fetch_behavioral_rows(core: AccountingCore, tickets: list[str]) -> FetchResult:
    """behavior_detections + behavior_analysis rows for tickets (temp-table join)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    if not tickets:
        return FetchResult(enabled=True)
    try:
        with _core_connect(core) as conn:
            conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _tmp_rpt_tickets (ticket TEXT PRIMARY KEY)"
            )
            conn.execute("DELETE FROM _tmp_rpt_tickets")
            conn.executemany(
                "INSERT INTO _tmp_rpt_tickets (ticket) VALUES (?)", ((t,) for t in tickets)
            )

            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT behavior_key, pattern, severity, confidence, evidence "
                    "FROM behavior_detections d JOIN _tmp_rpt_tickets t ON d.ticket = t.ticket"
                )
            ]
            rows2 = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM behavior_analysis a JOIN _tmp_rpt_tickets t ON a.ticket = t.ticket"
                )
            ]
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows, rows2=rows2)


def fetch_anomaly_rows(core: AccountingCore, tickets: list[str]) -> FetchResult:
    """anomaly_events + behavior_analysis rows for tickets (temp-table join)."""
    if _core_disabled(core):
        return FetchResult(enabled=False)
    if not tickets:
        return FetchResult(enabled=True)
    try:
        with _core_connect(core) as conn:
            conn.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _tmp_rpt_tickets (ticket TEXT PRIMARY KEY)"
            )
            conn.execute("DELETE FROM _tmp_rpt_tickets")
            conn.executemany(
                "INSERT INTO _tmp_rpt_tickets (ticket) VALUES (?)", ((t,) for t in tickets)
            )

            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT anomaly_type, severity, algorithm_version "
                    "FROM anomaly_events e JOIN _tmp_rpt_tickets t ON e.ticket = t.ticket"
                )
            ]
            rows2 = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM behavior_analysis a JOIN _tmp_rpt_tickets t ON a.ticket = t.ticket"
                )
            ]
    except Exception as err:
        return FetchResult(enabled=True, error=str(err))
    return FetchResult(enabled=True, rows=rows, rows2=rows2)
