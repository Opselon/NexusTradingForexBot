"""
Trade Intelligence Read Store
=============================
PHASE 09 bounded read facade over the intelligence tables.

All intelligence is DERIVED from the authoritative Phase 08 ledger; every
function here is a read-path query used for observability, forensics and the
self-healing rebuild. Writes are performed by the individual engines through the
AuditRepository background queue - this module owns no write path.

Every read is bounded and runs on the ACTIVE provider: a short-lived read-only
SQLite connection (so the live path is never blocked) or the audit domain's
pooled read-only backend under PostgreSQL. Both paths run the same SQL; the
provider helper resolves the connection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.provider_store import query_rows
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
    rows = query_rows(repo, sql, (*args, bounded), operation="intelligence.load_lifecycle_events")
    try:
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
    rows = query_rows(
        repo,
        "SELECT * FROM trade_autopsies WHERE ticket = ?;",
        (str(ticket),),
        operation="intelligence.load_autopsy",
    )
    return dict(rows[0]) if rows else None


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
    rows = query_rows(repo, sql, (*args, bounded), operation="intelligence.list_autopsies")
    for r in rows:
        out.append(dict(r))
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
    rows = query_rows(
        repo, sql, (*args, bounded), operation="intelligence.list_behavior_detections"
    )
    for r in rows:
        out.append(dict(r))
    return out


def list_anomaly_events(
    repo: AuditRepository,
    ticket: int | str | None = None,
    anomaly_type: str | None = None,
    limit: int = 100,
    grouped: bool = True,
) -> list[dict[str, Any]]:
    """Bounded listing of evidence-based anomaly events (TASK-2).

    ANOMALY-VERIFY-01 (grouped=True default): rows sharing the same incident
    identity (anomaly_type + ticket + algorithm_version) are collapsed into
    ONE incident with an `observation_count`. The dashboard must answer
    "how many unique incidents exist?" — not "how many DB rows exist?".
    Historical repeated observations are never deleted: grouping is a pure
    read-side projection (TEST-ANOM-16/17/19).
    """
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
    rows: list[dict[str, Any]] = query_rows(
        repo, sql, (*args, bounded), operation="intelligence.list_anomaly_events"
    )

    if not grouped:
        return rows
    incidents: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (
            str(r.get("ticket", "")),
            str(r.get("anomaly_type", "")),
            str(r.get("algorithm_version", "")),
        )
        inc = incidents.get(key)
        if inc is None:
            inc = dict(r)
            inc["observation_count"] = 1
            first = r.get("detected_at", "")
            last = r.get("detected_at", "")
            inc["first_seen"] = first
            inc["last_seen"] = last
            incidents[key] = inc
        else:
            inc["observation_count"] += 1
            if str(r.get("detected_at", "")) > str(inc.get("last_seen", "")):
                inc["last_seen"] = r.get("detected_at", "")
            if str(r.get("detected_at", "")) < str(inc.get("first_seen", "")):
                inc["first_seen"] = r.get("detected_at", "")
    return list(incidents.values())


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
    rows = query_rows(
        repo, sql, (*args, bounded), operation="intelligence.load_evolution_candidates"
    )
    for r in rows:
        out.append(dict(r))
    return out


def count_autopsies(repo: AuditRepository) -> int:
    if not repo._is_sqlite:
        return 0
    rows = query_rows(
        repo, "SELECT COUNT(*) AS c FROM trade_autopsies;", operation="intelligence.count_autopsies"
    )
    return int(rows[0]["c"]) if rows else 0


def count_lifecycle_events(repo: AuditRepository) -> int:
    if not repo._is_sqlite:
        return 0
    rows = query_rows(
        repo,
        "SELECT COUNT(*) AS c FROM position_lifecycle_events;",
        operation="intelligence.count_lifecycle_events",
    )
    return int(rows[0]["c"]) if rows else 0
