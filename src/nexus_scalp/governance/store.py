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

import contextlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.adapters.database.provider_store import (
    OPS_SHADOW_DOMAIN,
    ops_ensure_schema,
    ops_query_rows,
    ops_query_scalar,
    ops_queue_write,
    query_rows,
    queue_write,
)
from nexus_scalp.database.upsert import build_upsert_sql
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

_INSERT_PROMOTION_AUDIT_SQL = """
    INSERT OR REPLACE INTO model_promotion_audit (
        promotion_id, old_champion_model_id, old_champion_version,
        old_champion_hash, old_champion_schema, new_champion_model_id,
        new_champion_version, new_champion_hash, new_champion_schema,
        candidate_hash, schema_id, approval_actor, approval_reason,
        approval_token, rollback_target, status, recorded_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_ROLLBACK_AUDIT_SQL = """
    INSERT OR REPLACE INTO model_rollback_audit (
        rollback_id, failed_model_id, failed_version, previous_model_id,
        previous_version, previous_artifact_hash, previous_manifest_hash,
        previous_schema_id, actor, reason, rollback_kind, status, recorded_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

#: Lane B (PG upsert parity, PR #480 pattern): each SQLite statement stays
#: byte-identical (SQLite remains a first-class provider — INSERT OR REPLACE
#: is untouched, and the SQLite write route is unchanged). The PostgreSQL
#: branch gets ``INSERT INTO ... ON CONFLICT (...) DO UPDATE SET``, because
#: ``INSERT OR REPLACE`` is SQLite-only syntax and the pooled write backend
#: hands the statement to the server verbatim apart from the ``?``->``%s``
#: placeholder translation — so it lands as
#: ``syntax error at or near "OR"`` (live engine log, op=governance.record_event
#: / governance.save_health).
#:
#: The ON CONFLICT target is resolved AND re-validated against the table's real
#: DDL by ``build_upsert_sql`` (see nexus_scalp/database/upsert.py): a column
#: that has no covering UNIQUE/PRIMARY KEY constraint would make the statement
#: invalid under PostgreSQL, and the helper raises rather than emit one.
_EVENT_COLUMNS = [
    "event_id",
    "event",
    "stage",
    "model_id",
    "model_version",
    "schema_id",
    "correlation_id",
    "error_code",
    "error_type",
    "duration_ms",
    "actor",
    "previous_state",
    "new_state",
    "reason",
    "payload",
    "timestamp",
]
_STATE_COLUMNS = ["model_id", "model_version", "lifecycle_state", "updated_at", "evidence"]
_COMPARISON_COLUMNS = [
    "comparison_id",
    "run_id",
    "timestamp",
    "symbol",
    "champion_model_id",
    "champion_version",
    "challenger_model_id",
    "challenger_version",
    "champion_action",
    "challenger_action",
    "agreement",
    "champion_probabilities",
    "challenger_probabilities",
    "feature_context_id",
    "news_context_id",
    "feature_schema_id",
    "feature_parity_max_abs",
    "feature_parity_mean_abs",
    "feature_parity_mismatch",
    "alignment",
    "latency_champion_ms",
    "latency_challenger_ms",
    "regime",
    "session",
    "simulated",
    "payload",
]
_HEALTH_COLUMNS = [
    "checked_at",
    "champion_id",
    "champion_version",
    "champion_schema",
    "champion_healthy",
    "challenger_id",
    "challenger_version",
    "challenger_state",
    "shadow_running",
    "shadow_comparisons",
    "shadow_errors",
    "shadow_dropped",
    "last_update",
    "payload",
]
_PROMOTION_AUDIT_COLUMNS = [
    "promotion_id",
    "old_champion_model_id",
    "old_champion_version",
    "old_champion_hash",
    "old_champion_schema",
    "new_champion_model_id",
    "new_champion_version",
    "new_champion_hash",
    "new_champion_schema",
    "candidate_hash",
    "schema_id",
    "approval_actor",
    "approval_reason",
    "approval_token",
    "rollback_target",
    "status",
    "recorded_at",
]
_ROLLBACK_AUDIT_COLUMNS = [
    "rollback_id",
    "failed_model_id",
    "failed_version",
    "previous_model_id",
    "previous_version",
    "previous_artifact_hash",
    "previous_manifest_hash",
    "previous_schema_id",
    "actor",
    "reason",
    "rollback_kind",
    "status",
    "recorded_at",
]

_SQLITE_EVENT_SQL, _PG_EVENT_SQL = build_upsert_sql(
    "model_governance_events", _EVENT_COLUMNS, sqlite_sql=_INSERT_EVENT_SQL
)
_SQLITE_STATE_SQL, _PG_STATE_SQL = build_upsert_sql(
    "model_governance_state", _STATE_COLUMNS, sqlite_sql=_UPSERT_STATE_SQL
)
_SQLITE_COMPARISON_SQL, _PG_COMPARISON_SQL = build_upsert_sql(
    "model_shadow_comparisons", _COMPARISON_COLUMNS, sqlite_sql=_INSERT_COMPARISON_SQL
)
_SQLITE_HEALTH_SQL, _PG_HEALTH_SQL = build_upsert_sql(
    "model_runtime_health", _HEALTH_COLUMNS, sqlite_sql=_INSERT_HEALTH_SQL
)
_SQLITE_PROMOTION_AUDIT_SQL, _PG_PROMOTION_AUDIT_SQL = build_upsert_sql(
    "model_promotion_audit", _PROMOTION_AUDIT_COLUMNS, sqlite_sql=_INSERT_PROMOTION_AUDIT_SQL
)
_SQLITE_ROLLBACK_AUDIT_SQL, _PG_ROLLBACK_AUDIT_SQL = build_upsert_sql(
    "model_rollback_audit", _ROLLBACK_AUDIT_COLUMNS, sqlite_sql=_INSERT_ROLLBACK_AUDIT_SQL
)


def _sql_for(repo: AuditRepository, sqlite_sql: str, pg_sql: str) -> str:
    """Pick the dialect-correct statement for the ACTIVE provider.

    Mirrors the split PR #480 landed for the incidents store: SQLite keeps the
    historical ``INSERT OR REPLACE`` (a first-class provider — its statement is
    untouched), PostgreSQL runs the ``ON CONFLICT ... DO UPDATE`` form the
    pooled write backend can execute.
    """
    return sqlite_sql if getattr(repo, "_is_sqlite", False) else pg_sql


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
        if not self.audit_repo:
            return
        if not getattr(self.audit_repo, "_is_sqlite", False):
            # PostgreSQL: the ops_shadow domain provisions the translated
            # schema; the audit-domain tables (model_promotion_audit /
            # model_rollback_audit) are provisioned WITH the audit domain.
            self._schema_ensured = ops_ensure_schema(
                self.audit_repo, OPS_SHADOW_DOMAIN, operation="governance.ensure_schema"
            )
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
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_promotion_audit (
                        promotion_id TEXT PRIMARY KEY,
                        old_champion_model_id TEXT NOT NULL DEFAULT '',
                        old_champion_version TEXT NOT NULL DEFAULT '',
                        old_champion_hash TEXT NOT NULL DEFAULT '',
                        old_champion_schema TEXT NOT NULL DEFAULT '',
                        new_champion_model_id TEXT NOT NULL DEFAULT '',
                        new_champion_version TEXT NOT NULL DEFAULT '',
                        new_champion_hash TEXT NOT NULL DEFAULT '',
                        new_champion_schema TEXT NOT NULL DEFAULT '',
                        candidate_hash TEXT NOT NULL DEFAULT '',
                        schema_id TEXT NOT NULL DEFAULT '',
                        approval_actor TEXT NOT NULL DEFAULT '',
                        approval_reason TEXT NOT NULL DEFAULT '',
                        approval_token TEXT NOT NULL DEFAULT '',
                        rollback_target TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'PROMOTION_RECORDED',
                        recorded_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_rollback_audit (
                        rollback_id TEXT PRIMARY KEY,
                        failed_model_id TEXT NOT NULL DEFAULT '',
                        failed_version TEXT NOT NULL DEFAULT '',
                        previous_model_id TEXT NOT NULL DEFAULT '',
                        previous_version TEXT NOT NULL DEFAULT '',
                        previous_artifact_hash TEXT NOT NULL DEFAULT '',
                        previous_manifest_hash TEXT NOT NULL DEFAULT '',
                        previous_schema_id TEXT NOT NULL DEFAULT '',
                        actor TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL DEFAULT '',
                        rollback_kind TEXT NOT NULL DEFAULT 'MANUAL',
                        status TEXT NOT NULL DEFAULT 'ROLLBACK_RECORDED',
                        recorded_at TEXT NOT NULL
                    );
                    """
                )
                for idx in (
                    "CREATE INDEX IF NOT EXISTS idx_gov_events_ts ON model_governance_events(timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_gov_events_model ON model_governance_events(model_id, event);",
                    "CREATE INDEX IF NOT EXISTS idx_gov_state_model ON model_governance_state(model_id, model_version);",
                    "CREATE INDEX IF NOT EXISTS idx_gov_comp_ts ON model_shadow_comparisons(timestamp);",
                    # Lane B (PG upsert parity): the ON CONFLICT target the
                    # PostgreSQL branch uses must be a real constraint under
                    # PostgreSQL too. SQLite's INSERT OR REPLACE keys on these
                    # columns implicitly; the explicit UNIQUE INDEX makes the
                    # constraint visible to both providers. Kept identical to
                    # the domain DDL in nexus_scalp/shadow/schema.py.
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_state_key "
                    "ON model_governance_state(model_id, model_version);",
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_health_checked_at "
                    "ON model_runtime_health(checked_at);",
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
        if not self.audit_repo:
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
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_EVENT_SQL, _PG_EVENT_SQL),
            args,
            operation="governance.record_event",
        )

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
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            model_id,
            model_version,
            lifecycle_state,
            datetime.now(UTC).isoformat(),
            json.dumps(evidence or {}, default=str),
        )
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_STATE_SQL, _PG_STATE_SQL),
            args,
            operation="governance.set_state",
        )

    def save_shadow_comparison(self, row: dict[str, Any]) -> bool:
        """Bounded canonical comparison row (spec 9 / 14: no raw ticks)."""
        if not self.audit_repo:
            return False
        self.ensure_schema()
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
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_COMPARISON_SQL, _PG_COMPARISON_SQL),
            args,
            operation="governance.save_shadow_comparison",
        )

    def save_health(self, row: dict[str, Any]) -> bool:
        if not self.audit_repo:
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
        return ops_queue_write(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            _sql_for(self.audit_repo, _SQLITE_HEALTH_SQL, _PG_HEALTH_SQL),
            args,
            operation="governance.save_health",
        )

    # ------------------------------------------------------------------
    # Reads (bounded, short-lived RO connections)
    # ------------------------------------------------------------------

    def get_state(self, model_id: str, model_version: str = "") -> dict[str, Any] | None:
        if not self.audit_repo:
            return None
        # Governance transitions are rare operator actions (never the tick
        # hot path): flush the async queue so a just-recorded transition is
        # visible to the next transition read (consistency of the chain).
        with contextlib.suppress(Exception):
            self.audit_repo._queue.join()
        rows = ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM model_governance_state WHERE model_id=? AND (?='' OR model_version=?) "
            "ORDER BY updated_at DESC LIMIT 1;",
            (model_id, model_version, model_version),
            operation="governance.get_state",
        )
        return rows[0] if rows else None

    def list_events(
        self, limit: int = 200, event: str = "", model_id: str = ""
    ) -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        # Flush queued writes so freshly recorded events are visible to
        # operators/auditors (read path is never the tick hot path).
        with contextlib.suppress(Exception):
            self.audit_repo._queue.join()
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
        return ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            sql,
            (*args, bounded),
            operation="governance.list_events",
        )

    def list_comparisons(self, limit: int = 200, run_id: str = "") -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        bounded = max(1, min(int(limit), MAX_EVENTS_READ))
        sql = "SELECT * FROM model_shadow_comparisons"
        args: list[Any] = []
        if run_id:
            sql += " WHERE run_id = ?"
            args.append(run_id)
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        return ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            sql,
            (*args, bounded),
            operation="governance.list_comparisons",
        )

    def latest_health(self) -> dict[str, Any] | None:
        if not self.audit_repo:
            return None
        rows = ops_query_rows(
            self.audit_repo,
            OPS_SHADOW_DOMAIN,
            "SELECT * FROM model_runtime_health ORDER BY checked_at DESC LIMIT 1;",
            (),
            operation="governance.latest_health",
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Promotion / rollback audit (TASK-08, persisted in model_promotion_audit
    # + model_rollback_audit — migration AUDIT-0005)
    # ------------------------------------------------------------------

    def record_promotion_audit(self, row: dict[str, Any]) -> bool:
        """Persists ONE promotion transaction audit record (spec 29)."""
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            row.get("promotion_id", f"prom_{uuid.uuid4().hex[:16]}"),
            row.get("old_champion_model_id", ""),
            row.get("old_champion_version", ""),
            row.get("old_champion_hash", ""),
            row.get("old_champion_schema", ""),
            row.get("new_champion_model_id", ""),
            row.get("new_champion_version", ""),
            row.get("new_champion_hash", ""),
            row.get("new_champion_schema", ""),
            row.get("candidate_hash", ""),
            row.get("schema_id", ""),
            row.get("approval_actor", ""),
            row.get("approval_reason", ""),
            row.get("approval_token", ""),
            row.get("rollback_target", ""),
            row.get("status", "PROMOTION_RECORDED"),
            (row.get("recorded_at") or datetime.now(UTC)).isoformat()
            if hasattr(row.get("recorded_at"), "isoformat")
            else str(row.get("recorded_at", datetime.now(UTC).isoformat())),
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_PROMOTION_AUDIT_SQL, _PG_PROMOTION_AUDIT_SQL),
            args,
            operation="governance.record_promotion_audit",
        )

    def record_rollback_audit(self, row: dict[str, Any]) -> bool:
        """Persists ONE rollback audit record (spec 30)."""
        if not self.audit_repo:
            return False
        self.ensure_schema()
        args = (
            row.get("rollback_id", f"rb_{uuid.uuid4().hex[:16]}"),
            row.get("failed_model_id", ""),
            row.get("failed_version", ""),
            row.get("previous_model_id", ""),
            row.get("previous_version", ""),
            row.get("previous_artifact_hash", ""),
            row.get("previous_manifest_hash", ""),
            row.get("previous_schema_id", ""),
            row.get("actor", ""),
            row.get("reason", ""),
            row.get("rollback_kind", "MANUAL"),
            row.get("status", "ROLLBACK_RECORDED"),
            (row.get("recorded_at") or datetime.now(UTC)).isoformat()
            if hasattr(row.get("recorded_at"), "isoformat")
            else str(row.get("recorded_at", datetime.now(UTC).isoformat())),
        )
        return queue_write(
            self.audit_repo,
            _sql_for(self.audit_repo, _SQLITE_ROLLBACK_AUDIT_SQL, _PG_ROLLBACK_AUDIT_SQL),
            args,
            operation="governance.record_rollback_audit",
        )

    def list_promotion_audits(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        with contextlib.suppress(Exception):
            self.audit_repo._queue.join()
        bounded = max(1, min(int(limit), MAX_EVENTS_READ))
        return query_rows(
            self.audit_repo,
            "SELECT * FROM model_promotion_audit ORDER BY recorded_at DESC LIMIT ?;",
            (bounded,),
            operation="governance.list_promotion_audits",
        )

    def list_rollback_audits(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_repo:
            return []
        with contextlib.suppress(Exception):
            self.audit_repo._queue.join()
        bounded = max(1, min(int(limit), MAX_EVENTS_READ))
        return query_rows(
            self.audit_repo,
            "SELECT * FROM model_rollback_audit ORDER BY recorded_at DESC LIMIT ?;",
            (bounded,),
            operation="governance.list_rollback_audits",
        )

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"available": False}
        if not self.audit_repo:
            return out
        try:
            st = ops_query_rows(
                self.audit_repo,
                OPS_SHADOW_DOMAIN,
                "SELECT lifecycle_state, COUNT(*) AS c FROM model_governance_state "
                "GROUP BY lifecycle_state;",
                (),
                operation="governance.summary.by_state",
            )
            out = {
                "available": True,
                "events": int(
                    ops_query_scalar(
                        self.audit_repo,
                        OPS_SHADOW_DOMAIN,
                        "SELECT COUNT(*) FROM model_governance_events;",
                        (),
                        operation="governance.summary.events",
                    )
                    or 0
                ),
                "by_state": {str(r["lifecycle_state"]): int(r["c"]) for r in st},
                "comparisons": int(
                    ops_query_scalar(
                        self.audit_repo,
                        OPS_SHADOW_DOMAIN,
                        "SELECT COUNT(*) FROM model_shadow_comparisons;",
                        (),
                        operation="governance.summary.comparisons",
                    )
                    or 0
                ),
            }
        except Exception as e:
            logger.error("[MODEL_GOVERNANCE] summary failed", error=str(e))
        return out
