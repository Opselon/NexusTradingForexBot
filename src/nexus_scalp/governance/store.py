"""
Governance Event Store
======================
TASK-6 / CHG-0003: append-only, idempotent persistence for lifecycle
transitions and every model-governance failure (spec 30 / 31 / 35).

Tables (created lazily in the canonical `audit.db`):
    model_governance_events     append-only event ledger
    model_governance_state      current lifecycle state per model
    model_shadow_comparisons    bounded canonical comparison rows
    model_runtime_health        periodic health snapshots

REUSE-FIRST RULE (spec 35): the ONLY new tables created here are the four
above; shadow decisions/comparisons continue to live in the existing
`shadow_*` tables (PHASE 11). We do NOT duplicate registries. Writes go
through the AuditRepository background queue so the live path is never
blocked (INV-001).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.governance.models import (
    GovernanceEvent,
    GovernanceStage,
    PromotionTransition,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.store")

MAX_EVENTS_READ = 2000

_INSERT_EVENT_SQL = """
    INSERT OR REPLACE INTO model_governance_events (
        event_id, event, stage, model_id, model_version, schema_id,
        correlation_id, error_code, error_type, duration_ms, actor,
        previous_state, new_state, reason, payload, timestamp
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_UPSERT_STATE_SQL = """
    INSERT OR REPLACE INTO model_governance_state (
        model_id, model_version, lifecycle_state, updated_at, evidence
    ) VALUES (?, ?, ?, ?, ?);
"""

_INSERT_COMPARISON_SQL = """
    INSERT OR REPLACE INTO model_shadow_comparisons (
        comparison_id, run_id, timestamp, symbol,
        champion_model_id, champion_version, challenger_model_id, challenger_version,
        champion_action, challenger_action, agreement,
        champion_probabilities, challenger_probabilities,
        feature_context_id, news_context_id, feature_schema_id,
        feature_parity_max_abs, feature_parity_mean_abs, feature_parity_mismatch,
        alignment, latency_champion_ms, latency_challenger_ms,
        regime, session, simulated, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_HEALTH_SQL = """
    INSERT OR REPLACE INTO model_runtime_health (
        checked_at, champion_id, champion_version, champion_schema, champion_healthy,
        challenger_id, challenger_version, challenger_state, shadow_running,
        shadow_comparisons, shadow_errors, shadow_dropped, last_update, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class GovernanceStore:
    """Append-only governance persistence (audit.db, queued writes)."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo
        self._schema_ensured: bool = False

    # ------------------------------------------------------------------
    # Schema (lazy, once per process — never on the tick hot path)
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        if self._schema_ensured:
            return
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_governance_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT UNIQUE NOT NULL,
                        event TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        model_id TEXT DEFAULT '',
                        model_version TEXT DEFAULT '',
                        schema_id TEXT DEFAULT '',
                        correlation_id TEXT DEFAULT '',
                        error_code TEXT DEFAULT '',
                        error_type TEXT DEFAULT '',
                        duration_ms REAL DEFAULT 0.0,
                        actor TEXT DEFAULT 'system',
                        previous_state TEXT DEFAULT '',
                        new_state TEXT DEFAULT '',
                        reason TEXT DEFAULT '',
                        payload TEXT DEFAULT '{}',
                        timestamp TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_governance_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        model_id TEXT NOT NULL,
                        model_version TEXT DEFAULT '',
                        lifecycle_state TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        evidence TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_shadow_comparisons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        comparison_id TEXT UNIQUE NOT NULL,
                        run_id TEXT DEFAULT '',
                        timestamp TEXT NOT NULL,
                        symbol TEXT DEFAULT '',
                        champion_model_id TEXT DEFAULT '',
                        champion_version TEXT DEFAULT '',
                        challenger_model_id TEXT DEFAULT '',
                        challenger_version TEXT DEFAULT '',
                        champion_action TEXT DEFAULT '',
                        challenger_action TEXT DEFAULT '',
                        agreement INTEGER DEFAULT 0,
                        champion_probabilities TEXT DEFAULT '[]',
                        challenger_probabilities TEXT DEFAULT '[]',
                        feature_context_id TEXT DEFAULT '',
                        news_context_id TEXT DEFAULT '',
                        feature_schema_id TEXT DEFAULT '',
                        feature_parity_max_abs REAL DEFAULT 0.0,
                        feature_parity_mean_abs REAL DEFAULT 0.0,
                        feature_parity_mismatch INTEGER DEFAULT 0,
                        alignment TEXT DEFAULT '',
                        latency_champion_ms REAL DEFAULT 0.0,
                        latency_challenger_ms REAL DEFAULT 0.0,
                        regime TEXT DEFAULT '',
                        session TEXT DEFAULT '',
                        simulated INTEGER DEFAULT 1,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_runtime_health (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        checked_at TEXT NOT NULL,
                        champion_id TEXT DEFAULT '',
                        champion_version TEXT DEFAULT '',
                        champion_schema TEXT DEFAULT '',
                        champion_healthy INTEGER DEFAULT 0,
                        challenger_id TEXT DEFAULT '',
                        challenger_version TEXT DEFAULT '',
                        challenger_state TEXT DEFAULT '',
                        shadow_running INTEGER DEFAULT 0,
                        shadow_comparisons INTEGER DEFAULT 0,
                        shadow_errors INTEGER DEFAULT 0,
                        shadow_dropped INTEGER DEFAULT 0,
                        last_update TEXT DEFAULT '',
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                for idx in (
                    "CREATE INDEX IF NOT EXISTS idx_gov_events_ts ON model_governance_events(timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_gov_events_model ON model_governance_events(model_id, event);",
                    "CREATE INDEX IF NOT EXISTS idx_gov_state_model ON model_governance_state(model_id, model_version);",
                    "CREATE INDEX IF NOT EXISTS idx_gov_comp_ts ON model_shadow_comparisons(timestamp);",
                ):
                    conn.execute(idx)
                conn.commit()
                self._schema_ensured = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] schema init failed", error=str(e))

    # ------------------------------------------------------------------
    # Writes (queued)
    # ------------------------------------------------------------------

    def record_event(self, event: GovernanceEvent) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        args = (
            event.event_id,
            event.event,
            event.stage.value if isinstance(event.stage, GovernanceStage) else str(event.stage),
            event.model_id,
            event.model_version,
            event.schema_id,
            event.correlation_id,
            event.error_code,
            event.error_type,
            event.duration_ms,
            event.actor,
            event.previous_state,
            event.new_state,
            event.reason,
            json.dumps(event.payload, default=str),
            event.timestamp.isoformat(),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_EVENT_SQL, args))
            return True
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] event write failed", error=str(e))
            return False

    def record_transition(self, t: PromotionTransition) -> bool:
        """Persists an audited lifecycle transition (spec 31)."""
        ev = GovernanceEvent(
            event_id=f"ev_{t.transition_id}",
            event="PROMOTION_TRANSITION",
            stage=GovernanceStage.PROMOTION,
            model_id=t.model_id,
            model_version=t.model_version,
            correlation_id=t.transition_id,
            actor=t.actor,
            previous_state=t.previous_state.value,
            new_state=t.new_state.value,
            reason=t.reason,
            payload={
                "evidence": t.evidence_snapshot,
                "source_commit": t.source_commit,
                "artifact_hash": t.artifact_hash,
            },
        )
        ok = self.record_event(ev)
        # Mirror the current state so the registry has a stable answer.
        self.set_state(t.model_id, t.model_version, t.new_state.value, evidence=t.evidence_snapshot)
        return ok

    def set_state(
        self,
        model_id: str,
        model_version: str,
        lifecycle_state: str,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        args = (
            model_id,
            model_version,
            lifecycle_state,
            datetime.now(UTC).isoformat(),
            json.dumps(evidence or {}, default=str),
        )
        try:
            self.audit_repo._queue.put_nowait((_UPSERT_STATE_SQL, args))
            return True
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] state write failed", error=str(e))
            return False

    def save_shadow_comparison(self, row: dict[str, Any]) -> bool:
        """Bounded canonical comparison row (spec 9 / 14: no raw ticks)."""
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        prob_cols = [
            "champion_probabilities",
            "challenger_probabilities",
        ]
        args = (
            row.get("comparison_id", f"cmp_{uuid.uuid4().hex[:16]}"),
            row.get("run_id", ""),
            (row.get("timestamp") or datetime.now(UTC)).isoformat()
            if hasattr(row.get("timestamp"), "isoformat")
            else str(row.get("timestamp", "")),
            row.get("symbol", ""),
            row.get("champion_model_id", ""),
            row.get("champion_version", ""),
            row.get("challenger_model_id", ""),
            row.get("challenger_version", ""),
            row.get("champion_action", ""),
            row.get("challenger_action", ""),
            1 if row.get("agreement") else 0,
            json.dumps(row.get("champion_probabilities", []), default=str),
            json.dumps(row.get("challenger_probabilities", []), default=str),
            row.get("feature_context_id", ""),
            row.get("news_context_id", ""),
            row.get("feature_schema_id", ""),
            float(row.get("feature_parity_max_abs", 0.0)),
            float(row.get("feature_parity_mean_abs", 0.0)),
            int(row.get("feature_parity_mismatch", 0)),
            row.get("alignment", ""),
            float(row.get("latency_champion_ms", 0.0)),
            float(row.get("latency_challenger_ms", 0.0)),
            row.get("regime", ""),
            row.get("session", ""),
            1 if row.get("simulated", True) else 0,
            json.dumps(row.get("payload", {}), default=str),
        )
        # probabilities columns are stored as JSON text; drop the raw lists
        for c in prob_cols:
            args = tuple(v for i, v in enumerate(args) if i != 0 or c not in row)
        try:
            self.audit_repo._queue.put_nowait((_INSERT_COMPARISON_SQL, args))
            return True
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] comparison write failed", error=str(e))
            return False

    def save_health(self, row: dict[str, Any]) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        args = (
            (row.get("checked_at") or datetime.now(UTC)).isoformat()
            if hasattr(row.get("checked_at"), "isoformat")
            else str(row.get("checked_at", "")),
            row.get("champion_id", ""),
            row.get("champion_version", ""),
            row.get("champion_schema", ""),
            1 if row.get("champion_healthy") else 0,
            row.get("challenger_id", ""),
            row.get("challenger_version", ""),
            row.get("challenger_state", ""),
            1 if row.get("shadow_running") else 0,
            int(row.get("shadow_comparisons", 0)),
            int(row.get("shadow_errors", 0)),
            int(row.get("shadow_dropped", 0)),
            str(row.get("last_update", "")),
            json.dumps(row.get("payload", {}), default=str),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_HEALTH_SQL, args))
            return True
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] health write failed", error=str(e))
            return False

    # ------------------------------------------------------------------
    # Reads (bounded, short-lived RO connections)
    # ------------------------------------------------------------------

    def get_state(self, model_id: str, model_version: str = "") -> dict[str, Any] | None:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return None
        # Governance transitions are rare operator actions (never the tick
        # hot path): flush the async queue so a just-recorded transition is
        # visible to the next transition read (consistency of the chain).
        try:
            self.audit_repo._queue.join()
        except Exception:
            pass
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM model_governance_state WHERE model_id=? AND (?='' OR model_version=?) "
                    "ORDER BY updated_at DESC LIMIT 1;",
                    (model_id, model_version, model_version),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def list_events(
        self, limit: int = 200, event: str = "", model_id: str = ""
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return out
        # Flush queued writes so freshly recorded events are visible to
        # operators/auditors (read path is never the tick hot path).
        try:
            self.audit_repo._queue.join()
        except Exception:
            pass
        bounded = max(1, min(int(limit), MAX_EVENTS_READ))
        clauses: list[str] = []
        args: list[Any] = []
        if event:
            clauses.append("event = ?")
            args.append(event)
        if model_id:
            clauses.append("model_id = ?")
            args.append(model_id)
        sql = "SELECT * FROM model_governance_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (*args, bounded)).fetchall()
                for r in rows:
                    out.append(dict(r))
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] events read failed", error=str(e))
        return out

    def list_comparisons(self, limit: int = 200, run_id: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return out
        bounded = max(1, min(int(limit), MAX_EVENTS_READ))
        sql = "SELECT * FROM model_shadow_comparisons"
        args: list[Any] = []
        if run_id:
            sql += " WHERE run_id = ?"
            args.append(run_id)
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (*args, bounded)).fetchall()
                for r in rows:
                    out.append(dict(r))
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] comparisons read failed", error=str(e))
        return out

    def latest_health(self) -> dict[str, Any] | None:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM model_runtime_health ORDER BY checked_at DESC LIMIT 1;"
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"available": False}
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return out
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                ev = conn.execute("SELECT COUNT(*) FROM model_governance_events;").fetchone()
                st = conn.execute(
                    "SELECT lifecycle_state, COUNT(*) FROM model_governance_state GROUP BY lifecycle_state;"
                ).fetchall()
                cm = conn.execute("SELECT COUNT(*) FROM model_shadow_comparisons;").fetchone()
                out = {
                    "available": True,
                    "events": int(ev[0]) if ev else 0,
                    "by_state": {str(r[0]): int(r[1]) for r in st},
                    "comparisons": int(cm[0]) if cm else 0,
                }
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] summary failed", error=str(e))
        return out
