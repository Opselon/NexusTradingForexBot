"""
Research Read Store
===================
PHASE 09B bounded read facade over the research tables.

All research is DERIVED from the authoritative Phase 08 ledger; every function
here is a read-path query for observability, forensics and the self-healing
rebuild. Writes are performed by the engines through the AuditRepository
background queue; this module owns no write path.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.research.store")

MAX_READ_LIMIT = 2000


def list_registry(
    repo: AuditRepository,
    lifecycle: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Bounded listing of registry entries, newest first."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), MAX_READ_LIMIT))
    sql = "SELECT * FROM strategy_registry"
    args: tuple[Any, ...] = ()
    if lifecycle:
        sql += " WHERE lifecycle = ?"
        args = (lifecycle,)
    sql += " ORDER BY updated_at DESC LIMIT ?;"
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
        logger.error("[STRATEGY_REGISTRY] list failed", error=str(e))
    return out


def get_registry_entry(
    repo: AuditRepository, strategy_id: str, strategy_version: str | None = None
) -> dict[str, Any] | None:
    """Single registry entry."""
    if not repo._is_sqlite:
        return None
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            if strategy_version:
                row = conn.execute(
                    "SELECT * FROM strategy_registry WHERE strategy_id=? AND strategy_version=?;",
                    (strategy_id, strategy_version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM strategy_registry WHERE strategy_id=? "
                    "ORDER BY updated_at DESC LIMIT 1;",
                    (strategy_id,),
                ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        logger.error("[STRATEGY_REGISTRY] entry load failed", strategy=strategy_id, error=str(e))
        return None


def list_research_runs(
    repo: AuditRepository,
    strategy_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Append-only validation run records (reproducibility lineage)."""
    if not repo._is_sqlite:
        return []
    bounded = max(1, min(int(limit), 500))
    sql = "SELECT * FROM research_runs"
    args: tuple[Any, ...] = ()
    if strategy_id:
        sql += " WHERE strategy_id = ?"
        args = (strategy_id,)
    sql += " ORDER BY executed_at DESC LIMIT ?;"
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
        logger.error("[STRATEGY_RESEARCH] runs list failed", error=str(e))
    return out


def registry_summary(repo: AuditRepository) -> dict[str, Any]:
    """Candidate count / validation status / lifecycle distribution."""
    out: dict[str, Any] = {"available": False, "total": 0, "by_lifecycle": {}}
    if not repo._is_sqlite:
        return out
    try:
        conn = sqlite3.connect(repo._db_path, timeout=5.0)
        try:
            row = conn.execute("SELECT COUNT(*) FROM strategy_registry;").fetchone()
            out["total"] = int(row[0]) if row else 0
            out["by_lifecycle"] = {}
            for r in conn.execute(
                "SELECT lifecycle, COUNT(*) AS c FROM strategy_registry GROUP BY lifecycle;"
            ).fetchall():
                out["by_lifecycle"][str(r[0])] = int(r[1])
            out["available"] = True
        finally:
            conn.close()
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] summary failed", error=str(e))
    return out


def self_heal_research(repo: AuditRepository, registry) -> int:
    """
    Rebuilds derived research state from the immutable ledger when corrupted.

    Never touches historical validation truth; only derived rankings/summaries
    are rebuilt. Returns the number of registry entries repaired.
    """
    if not repo._is_sqlite:
        return 0
    repaired = 0
    try:
        entries = registry.list(limit=MAX_READ_LIMIT)
        for entry in entries:
            # Repair consistency between registry lifecycle and embedded results.
            needs = False
            if (
                entry.oos is not None
                and entry.oos.status != "PASS"
                and entry.lifecycle.value not in ("REJECTED", "DEGRADED", "RETIRED")
            ):
                needs = True
            if needs:
                from nexus_scalp.research.models import CandidateLifecycle

                repair = entry.model_copy(update={"lifecycle": CandidateLifecycle.REJECTED})
                registry.upsert(repair)
                repaired += 1
    except Exception as e:
        logger.error("[STRATEGY_RESEARCH] self-heal failed", error=str(e))
    return repaired
