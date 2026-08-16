"""
Strategy Registry (Persistence)
===============================
PHASE 09B (spec 20 / 26 / 40).

The registry is the enduring home of validation truth. It preserves for every
strategy: identity, version, feature schema, discovery source, validation
lineage, backtest / walk-forward / OOS / robustness results, score, confidence,
lifecycle, creation time and retirement reason.

Registry is INDEPENDENT of the current model file (spec 24) and preserves
historical research data across model rebuilds and schema width changes.

Persistence: rows are written through the AuditRepository background queue so
the registry never blocks the live path. Historical validation truth is never
mutated; updates append lineage (spec 28 immutability).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.lifecycle import transition
from nexus_scalp.research.models import (
    BacktestResult,
    CandidateLifecycle,
    OOSResult,
    RobustnessResult,
    StrategyRegistryEntry,
    StrategyScore,
    WalkForwardResult,
)

logger = get_logger("nexus_scalp.research.registry")

UPSERT_ENTRY_SQL = """
    INSERT INTO strategy_registry (
        strategy_id, strategy_version, feature_schema_id, feature_dimension,
        discovery_source, discovery_window, context_definition,
        parent_strategy_ids, lifecycle, backtest, walkforward, oos, robustness,
        score, confidence, sample_count, validation_lineage, retirement_reason,
        created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(strategy_id, strategy_version) DO UPDATE SET
        feature_schema_id=excluded.feature_schema_id,
        feature_dimension=excluded.feature_dimension,
        discovery_source=excluded.discovery_source,
        discovery_window=excluded.discovery_window,
        context_definition=excluded.context_definition,
        parent_strategy_ids=excluded.parent_strategy_ids,
        lifecycle=excluded.lifecycle,
        backtest=excluded.backtest,
        walkforward=excluded.walkforward,
        oos=excluded.oos,
        robustness=excluded.robustness,
        score=excluded.score,
        confidence=excluded.confidence,
        sample_count=excluded.sample_count,
        validation_lineage=excluded.validation_lineage,
        retirement_reason=excluded.retirement_reason,
        updated_at=excluded.updated_at;
"""


class StrategyRegistry:
    """Bounded registry persistence over the research tables."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def upsert(self, entry: StrategyRegistryEntry) -> bool:
        if not self.audit_repo._is_sqlite:
            return False
        args = (
            entry.strategy_id,
            entry.strategy_version,
            entry.feature_schema_id,
            entry.feature_dimension,
            entry.discovery_source,
            entry.discovery_window,
            _json(entry.context_definition),
            _json(entry.parent_strategy_ids),
            entry.lifecycle.value,
            _json(entry.backtest.model_dump() if entry.backtest else None),
            _json(entry.walkforward.model_dump() if entry.walkforward else None),
            _json(entry.oos.model_dump() if entry.oos else None),
            _json(entry.robustness.model_dump() if entry.robustness else None),
            _json(entry.score.model_dump() if entry.score else None),
            entry.confidence,
            entry.sample_count,
            _json(entry.validation_lineage),
            entry.retirement_reason,
            entry.created_at.isoformat(),
            entry.updated_at.isoformat(),
        )
        try:
            if hasattr(self.audit_repo, "_queue"):
                self.audit_repo._queue.put_nowait((UPSERT_ENTRY_SQL, args))
                return True
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] upsert failed (isolated)", error=str(e))
        return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(
        self, strategy_id: str, strategy_version: str | None = None
    ) -> StrategyRegistryEntry | None:
        """Loads a registry entry; with no version, the most recent one."""
        if not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
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
            finally:
                conn.close()
            return self._from_row(row) if row else None
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] load failed", strategy=strategy_id, error=str(e))
            return None

    def list(self, lifecycle: str | None = None, limit: int = 200) -> list[StrategyRegistryEntry]:
        """Bounded listing, newest first."""
        if not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT * FROM strategy_registry"
        args: tuple[Any, ...] = ()
        if lifecycle:
            sql += " WHERE lifecycle = ?"
            args = (lifecycle,)
        sql += " ORDER BY updated_at DESC LIMIT ?;"
        out: list[StrategyRegistryEntry] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (*args, bounded)).fetchall()
            finally:
                conn.close()
            for r in rows:
                entry = self._from_row(r)
                if entry is not None:
                    out.append(entry)
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] list failed", error=str(e))
        return out

    def count(self, lifecycle: str | None = None) -> int:
        if not self.audit_repo._is_sqlite:
            return 0
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                if lifecycle:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM strategy_registry WHERE lifecycle=?;",
                        (lifecycle,),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM strategy_registry;").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Lifecycle transitions (persisted)
    # ------------------------------------------------------------------

    def transition_lifecycle(
        self, strategy_id: str, target: CandidateLifecycle, reason: str = ""
    ) -> StrategyRegistryEntry | None:
        """Loads, transitions lifecycle in-memory (state machine), persists."""
        entry = self.get(strategy_id)
        if entry is None:
            return None
        try:
            new_state = transition(entry.lifecycle, target)
        except ValueError as e:
            logger.error(
                "[STRATEGY_REGISTRY] illegal transition", strategy=strategy_id, error=str(e)
            )
            return None
        updated = entry.model_copy(
            update={
                "lifecycle": new_state,
                "updated_at": datetime.now(UTC),
                "validation_lineage": [
                    *entry.validation_lineage,
                    f"{datetime.now(UTC).isoformat()}:{new_state.value}"
                    + (f":{reason}" if reason else ""),
                ],
            }
        )
        self.upsert(updated)
        return updated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _from_row(row: sqlite3.Row) -> StrategyRegistryEntry | None:
        try:

            def _load(column: str, model: type[Any]) -> Any | None:
                raw = row[column]
                if not raw:
                    return None
                data = json.loads(raw)
                return model(**data) if isinstance(data, dict) else None

            bt = _load("backtest", BacktestResult)
            wf = _load("walkforward", WalkForwardResult)
            oos = _load("oos", OOSResult)
            rob = _load("robustness", RobustnessResult)
            score = _load("score", StrategyScore)

            created = _parse_ts(row["created_at"]) or datetime.now(UTC)
            updated = _parse_ts(row["updated_at"]) or created

            context = json.loads(row["context_definition"] or "{}")
            parents = json.loads(row["parent_strategy_ids"] or "[]")
            lineage = json.loads(row["validation_lineage"] or "[]")

            return StrategyRegistryEntry(
                strategy_id=row["strategy_id"],
                strategy_version=row["strategy_version"],
                feature_schema_id=row["feature_schema_id"],
                feature_dimension=int(row["feature_dimension"] or 0),
                discovery_source=row["discovery_source"] or "",
                discovery_window=row["discovery_window"] or "",
                context_definition=context if isinstance(context, dict) else {},
                parent_strategy_ids=parents if isinstance(parents, list) else [],
                lifecycle=CandidateLifecycle(row["lifecycle"]),
                backtest=bt,
                walkforward=wf,
                oos=oos,
                robustness=rob,
                score=score,
                confidence=float(row["confidence"] or 0.0),
                sample_count=int(row["sample_count"] or 0),
                validation_lineage=lineage if isinstance(lineage, list) else [],
                retirement_reason=row["retirement_reason"] or "",
                created_at=created,
                updated_at=updated,
            )
        except Exception as e:
            logger.error("[STRATEGY_REGISTRY] row decode failed", error=str(e))
            return None


def _json(value: Any) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return "{}"


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except Exception:
        return None
