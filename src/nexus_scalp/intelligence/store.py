"""
Trade Intelligence Read Store
=============================
PHASE 09 bounded read facade over the intelligence tables.

All intelligence is DERIVED from the authoritative Phase 08 ledger; every
function here is a read-path query used for observability, forensics and the
self-healing rebuild. Writes are performed by the individual engines through the
AuditRepository background queue - this module owns no write path.

Every read is bounded and opens a short-lived read-only SQLite connection so the
live path is never blocked.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.intelligence.models import (
    DecisionContext,
    MarketContext,
    PositionEventType,
    PositionLifecycleEvent,
    PositionPerformance,
    PositionSnapshot,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.store")

MAX_READ_LIMIT = 2000


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except Exception:
        return None


def load_lifecycle_events(
    repo: AuditRepository, ticket: str | None = None, limit: int = 500
) -> list[PositionLifecycleEvent]:
    """Ordered immutable position-timeline events, optionally filtered by ticket."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = "SELECT * FROM position_lifecycle_events"
    args: tuple[Any, ...] = ()
    if ticket:
        sql += " WHERE ticket = ?"
        args = (str(ticket),)
    sql += " ORDER BY sequence ASC LIMIT ?;"
    out: list[PositionLifecycleEvent] = []
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, (*args, bounded)).fetchall()
        finally:
            conn.close()
        for r in rows:
            payload = json.loads(r["payload"] or "{}")
            # Rebuild the full self-describing event from its persisted payload.
            snapshot = payload.get("snapshot") or {}
            performance = payload.get("performance") or {}
            market = payload.get("market") or {}
            decision = payload.get("decision") or {}
            out.append(
                PositionLifecycleEvent(
                    event_key=r["event_key"],
                    ticket=r["ticket"],
                    trade_id=r["trade_id"],
                    experience_id=r["experience_id"],
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    event_type=PositionEventType(r["event_type"]),
                    sequence=r["sequence"],
                    event_timestamp=_parse_ts(r["event_timestamp"]) or datetime.now(UTC),
                    market_context=MarketContext(**market)
                    if market
                    else MarketContext(symbol=r["symbol"]),
                    position=PositionSnapshot(**snapshot) if snapshot else PositionSnapshot(),
                    performance=PositionPerformance(**performance)
                    if performance
                    else PositionPerformance(),
                    decision=DecisionContext(**decision) if decision else DecisionContext(),
                    detail=payload.get("detail", ""),
                )
            )
    except Exception as e:
        logger.error("[POSITION_TRACK] lifecycle load failed", ticket=ticket or "*", error=str(e))
    return out


def load_autopsy(repo: AuditRepository, ticket: int | str) -> dict[str, Any] | None:
    """Returns the persisted forensic autopsy row for a ticket, or None."""
    if not repo._is_sqlite:
        return None
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM trade_autopsies WHERE ticket = ?;", (str(ticket),)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.error("[TRADE_AUTOPSY] load failed", ticket=str(ticket), error=str(e))
        return None


def list_autopsies(
    repo: AuditRepository,
    strategy_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Bounded listing of forensic autopsies, newest first."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), 500))
    sql = "SELECT * FROM trade_autopsies"
    args: tuple[Any, ...] = ()
    if strategy_id:
        sql += " WHERE strategy_id = ?"
        args = (strategy_id,)
    sql += " ORDER BY autopsied_at DESC LIMIT ?;"
    out: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, (*args, bounded)).fetchall()
        finally:
            conn.close()
        for r in rows:
            out.append(dict(r))
    except Exception as e:
        logger.error("[TRADE_AUTOPSY] list failed", error=str(e))
    return out


def list_behavior_detections(
    repo: AuditRepository,
    ticket: int | str | None = None,
    pattern: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Bounded listing of measurable behavioral detections."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), 500))
    sql = "SELECT * FROM behavior_detections"
    clauses: list[str] = []
    args: list[Any] = []
    if ticket is not None:
        clauses.append("ticket = ?")
        args.append(str(ticket))
    if pattern:
        clauses.append("pattern = ?")
        args.append(str(pattern))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY detected_at DESC LIMIT ?;"
    out: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, (*args, bounded)).fetchall()
        finally:
            conn.close()
        for r in rows:
            out.append(dict(r))
    except Exception as e:
        logger.error("[BEHAVIOR] list failed", error=str(e))
    return out


def list_anomaly_events(
    repo: AuditRepository,
    ticket: int | str | None = None,
    anomaly_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Bounded listing of evidence-based anomaly events (TASK-2)."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), 500))
    sql = "SELECT * FROM anomaly_events"
    clauses: list[str] = []
    args: list[Any] = []
    if ticket is not None:
        clauses.append("ticket = ?")
        args.append(str(ticket))
    if anomaly_type:
        clauses.append("anomaly_type = ?")
        args.append(str(anomaly_type))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY detected_at DESC LIMIT ?;"
    out: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, (*args, bounded)).fetchall()
        finally:
            conn.close()
        for r in rows:
            out.append(dict(r))
    except Exception as e:
        logger.error("[BEHAVIOR] anomaly list failed", error=str(e))
    return out


def load_evolution_candidates(
    repo: AuditRepository,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Bounded listing of strategy evolution candidates."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), 500))
    sql = "SELECT * FROM strategy_evolution_candidates"
    args: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status = ?"
        args = (str(status),)
    sql += " ORDER BY discovered_at DESC LIMIT ?;"
    out: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, (*args, bounded)).fetchall()
        finally:
            conn.close()
        for r in rows:
            out.append(dict(r))
    except Exception as e:
        logger.error("[STRATEGY] evolution list failed", error=str(e))
    return out


def count_autopsies(repo: AuditRepository) -> int:
    if not repo._is_sqlite:
        return 0
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        try:
            row = conn.execute("SELECT COUNT(*) FROM trade_autopsies;").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def count_lifecycle_events(repo: AuditRepository) -> int:
    if not repo._is_sqlite:
        return 0
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        try:
            row = conn.execute("SELECT COUNT(*) FROM position_lifecycle_events;").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0
