"""70D Shadow Persistence (TASK-05-70D-SHADOW).

REUSE-FIRST (spec 12): observations persist into the canonical audit.db via
the existing AuditRepository background queue — NO synchronous DB on the tick
path (spec 40 / INV-001) and NO new unrelated database.

Tables (lazy schema, once per process):
    shadow70_observations   canonical idempotent observation rows
    shadow70_events         append-only structured [SHADOW70] event ledger
    shadow70_feature_health periodic feature-health snapshots
    shadow70_drift_alerts   drift alerts (NORMAL/WATCH/WARNING/CRITICAL)

Idempotency (spec 13 / 14): observation_id is deterministic; a reconnect or
retry with the same id CANNOT duplicate a row (INSERT OR IGNORE on the
unique key, spec TEST-SHADOW-13/14).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.shadow70.models import Shadow70Observation

logger = get_logger("nexus_scalp.shadow.shadow70.store")

SHADOW70_MAX_READ: int = 2000


class Shadow70BackpressurePolicy:
    """Drop/coalesce policy for the bounded queue (spec 18)."""

    __slots__ = ("coalesced", "dropped_snapshots", "max_queue")

    def __init__(self, max_queue: int = 2000) -> None:
        self.max_queue = int(max_queue)
        self.dropped_snapshots: int = 0
        self.coalesced: int = 0

    def should_drop(self, current_size: int) -> bool:
        return current_size >= self.max_queue

    def record_drop(self) -> None:
        self.dropped_snapshots += 1
        logger.warning(
            "[SHADOW70] event=SHADOW_BACKPRESSURE", dropped_snapshots=self.dropped_snapshots
        )

    def summary(self) -> dict[str, Any]:
        return {
            "max_queue": self.max_queue,
            "dropped_snapshots": self.dropped_snapshots,
            "coalesced": self.coalesced,
        }


class Shadow70Persistence:
    """Minimal write contract the store exposes to the worker."""

    def save_observation(self, obs: Shadow70Observation) -> bool: ...
    def record_event(self, event: dict[str, Any]) -> bool: ...
    def save_feature_health(self, rows: list[dict[str, Any]]) -> bool: ...
    def save_drift_alerts(self, alerts: list[dict[str, Any]]) -> bool: ...


_INSERT_OBSERVATION_SQL = """
    INSERT OR IGNORE INTO shadow70_observations (
        observation_id, snapshot_id, timestamp, symbol, timeframe, simulated,
        model_id, model_version, model_hash, scaler_hash, schema_id, schema_dimension,
        champion_action, champion_probabilities, champion_confidence,
        shadow_action, shadow_probabilities, shadow_confidence, confidence_delta,
        disagreement, agreement, valid, reason, regime, session,
        news_state, liquidity_state, news_context_hash, liquidity_feature_hash,
        liquidity_features_10, feature_hash, sample_source, latency_ms, error_code,
        outcome, outcome_resolved_at, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_EVENT_SQL = """
    INSERT OR IGNORE INTO shadow70_events (
        event_id, event, stage, model_id, model_version, schema_id,
        error_code, reason, correlation_id, payload, timestamp
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_HEALTH_SQL = """
    INSERT OR IGNORE INTO shadow70_feature_health (
        snapshot_id, timestamp, feature, feat_index, samples, finite_rate,
        missing_rate, stale_rate, zero_rate, mean, std, min, max, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_DRIFT_SQL = """
    INSERT OR IGNORE INTO shadow70_drift_alerts (
        alert_id, timestamp, feature, metric, value, threshold, severity,
        reference_mean, live_mean, reference_std, live_std, samples, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class Shadow70Store(Shadow70Persistence):
    """Canonical persistence for the 70D shadow runtime (audit.db, queued)."""

    def __init__(self, audit_repo: AuditRepository | None, max_queue: int = 2000) -> None:
        self.audit_repo = audit_repo
        self.backpressure = Shadow70BackpressurePolicy(max_queue=max_queue)
        self._schema_ensured: bool = False

    # ------------------------------------------------------------------
    # Schema (lazy, once per process — never on the tick path)
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
                    CREATE TABLE IF NOT EXISTS shadow70_observations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        observation_id TEXT UNIQUE NOT NULL,
                        snapshot_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        symbol TEXT DEFAULT 'XAUUSD',
                        timeframe TEXT DEFAULT 'M1',
                        simulated INTEGER DEFAULT 1,
                        model_id TEXT DEFAULT '',
                        model_version TEXT DEFAULT '',
                        model_hash TEXT DEFAULT '',
                        scaler_hash TEXT DEFAULT '',
                        schema_id TEXT DEFAULT 'scalp_v3',
                        schema_dimension INTEGER DEFAULT 70,
                        champion_action TEXT DEFAULT '',
                        champion_probabilities TEXT DEFAULT '[]',
                        champion_confidence REAL DEFAULT 0.0,
                        shadow_action TEXT DEFAULT '',
                        shadow_probabilities TEXT DEFAULT '[]',
                        shadow_confidence REAL DEFAULT 0.0,
                        confidence_delta REAL DEFAULT 0.0,
                        disagreement TEXT DEFAULT 'AGREEMENT',
                        agreement INTEGER DEFAULT 1,
                        valid INTEGER DEFAULT 1,
                        reason TEXT DEFAULT '',
                        regime TEXT DEFAULT '',
                        session TEXT DEFAULT '',
                        news_state TEXT DEFAULT '',
                        liquidity_state TEXT DEFAULT '',
                        news_context_hash TEXT DEFAULT '',
                        liquidity_feature_hash TEXT DEFAULT '',
                        liquidity_features_10 TEXT DEFAULT '[]',
                        feature_hash TEXT DEFAULT '',
                        sample_source TEXT DEFAULT 'LIVE',
                        latency_ms REAL DEFAULT 0.0,
                        error_code TEXT DEFAULT '',
                        outcome TEXT DEFAULT 'PENDING',
                        outcome_resolved_at TEXT DEFAULT '',
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow70_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT UNIQUE NOT NULL,
                        event TEXT NOT NULL,
                        stage TEXT DEFAULT 'SHADOW70',
                        model_id TEXT DEFAULT '',
                        model_version TEXT DEFAULT '',
                        schema_id TEXT DEFAULT 'scalp_v3',
                        error_code TEXT DEFAULT '',
                        reason TEXT DEFAULT '',
                        correlation_id TEXT DEFAULT '',
                        payload TEXT DEFAULT '{}',
                        timestamp TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow70_feature_health (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_id TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        feature TEXT NOT NULL,
                        feat_index INTEGER NOT NULL,
                        samples INTEGER DEFAULT 0,
                        finite_rate REAL DEFAULT 0.0,
                        missing_rate REAL DEFAULT 0.0,
                        stale_rate REAL DEFAULT 0.0,
                        zero_rate REAL DEFAULT 0.0,
                        mean REAL DEFAULT 0.0,
                        std REAL DEFAULT 0.0,
                        min REAL DEFAULT 0.0,
                        max REAL DEFAULT 0.0,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow70_drift_alerts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        alert_id TEXT UNIQUE NOT NULL,
                        timestamp TEXT NOT NULL,
                        feature TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        value REAL DEFAULT 0.0,
                        threshold REAL DEFAULT 0.0,
                        severity TEXT DEFAULT 'NORMAL',
                        reference_mean REAL DEFAULT 0.0,
                        live_mean REAL DEFAULT 0.0,
                        reference_std REAL DEFAULT 0.0,
                        live_std REAL DEFAULT 0.0,
                        samples INTEGER DEFAULT 0,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                for idx in (
                    "CREATE INDEX IF NOT EXISTS idx_shadow70_obs_ts ON shadow70_observations(timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_shadow70_obs_model ON shadow70_observations(model_id, timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_shadow70_events_ts ON shadow70_events(timestamp);",
                ):
                    conn.execute(idx)
                conn.commit()
                self._schema_ensured = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW70] schema init failed (isolated)", error=str(e))

    # ------------------------------------------------------------------
    # Writes (all queued through AuditRepository — never sync on tick path)
    # ------------------------------------------------------------------

    def save_observation(self, obs: Shadow70Observation) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        if self.backpressure.should_drop(
            getattr(self.audit_repo, "_queue", type("Q", (), {"qsize": lambda s: 0})()).qsize()
        ):
            self.backpressure.record_drop()
            return False
        self.ensure_schema()
        args = (
            obs.observation_id,
            obs.snapshot_id,
            obs.timestamp.isoformat(),
            obs.symbol,
            obs.timeframe,
            1 if obs.simulated else 0,
            obs.model_id,
            obs.model_version,
            obs.model_hash,
            obs.scaler_hash,
            obs.schema_id,
            obs.schema_dimension,
            obs.champion_action,
            json.dumps(obs.champion_probabilities),
            obs.champion_confidence,
            obs.shadow_action,
            json.dumps(obs.shadow_probabilities),
            obs.shadow_confidence,
            obs.confidence_delta,
            obs.disagreement.value,
            1 if obs.agreement else 0,
            1 if obs.valid else 0,
            obs.reason,
            obs.regime,
            obs.session,
            obs.news_state,
            obs.liquidity_state,
            obs.news_context_hash,
            obs.liquidity_feature_hash,
            json.dumps(obs.liquidity_features_10),
            obs.feature_hash,
            obs.sample_source,
            obs.latency_ms,
            obs.error_code,
            obs.outcome,
            obs.outcome_resolved_at.isoformat() if obs.outcome_resolved_at else "",
            datetime.now(UTC).isoformat(),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_OBSERVATION_SQL, args))
            return True
        except Exception as e:
            logger.error("[SHADOW70] save_observation failed (isolated)", error=str(e))
            return False

    def record_event(self, event: dict[str, Any]) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        args = (
            event.get("event_id", ""),
            event.get("event", ""),
            event.get("stage", "SHADOW70"),
            event.get("model_id", ""),
            event.get("model_version", ""),
            event.get("schema_id", "scalp_v3"),
            event.get("error_code", ""),
            event.get("reason", ""),
            event.get("correlation_id", ""),
            json.dumps(event.get("payload", {}), default=str),
            event.get("timestamp") or datetime.now(UTC).isoformat(),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_EVENT_SQL, args))
            return True
        except Exception as e:
            logger.error("[SHADOW70] record_event failed (isolated)", error=str(e))
            return False

    def save_feature_health(self, rows: list[dict[str, Any]]) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        snapshot_id = rows[0].get("snapshot_id", "") if rows else ""
        ts = datetime.now(UTC).isoformat()
        ok = True
        for row in rows:
            args = (
                snapshot_id,
                ts,
                row.get("name", ""),
                int(row.get("index", 0)),  # maps to feat_index
                int(row.get("samples", 0)),
                float(row.get("finite_rate", 0.0)),
                float(row.get("missing_rate", 0.0)),
                float(row.get("stale_rate", 0.0)),
                float(row.get("zero_rate", 0.0)),
                float(row.get("mean", 0.0)),
                float(row.get("std", 0.0)),
                float(row.get("min", 0.0)),
                float(row.get("max", 0.0)),
                json.dumps(row, default=str),
            )
            try:
                self.audit_repo._queue.put_nowait((_INSERT_HEALTH_SQL, args))
            except Exception as e:
                ok = False
                logger.error("[SHADOW70] save_feature_health failed (isolated)", error=str(e))
        return ok

    def save_drift_alerts(self, alerts: list[dict[str, Any]]) -> bool:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return False
        self.ensure_schema()
        ok = True
        for a in alerts:
            args = (
                f"drift70_{a.get('feature', '')}_{a.get('metric', '')}_{int(a.get('samples', 0))}",
                a.get("timestamp") or datetime.now(UTC).isoformat(),
                a.get("feature", ""),
                a.get("metric", ""),
                float(a.get("value", 0.0)),
                float(a.get("threshold", 0.0)),
                a.get("severity", "NORMAL"),
                float(a.get("reference_mean", 0.0)),
                float(a.get("live_mean", 0.0)),
                float(a.get("reference_std", 0.0)),
                float(a.get("live_std", 0.0)),
                int(a.get("samples", 0)),
                json.dumps(a, default=str),
            )
            try:
                self.audit_repo._queue.put_nowait((_INSERT_DRIFT_SQL, args))
            except Exception as e:
                ok = False
                logger.error("[SHADOW70] save_drift_alerts failed (isolated)", error=str(e))
        return ok

    # ------------------------------------------------------------------
    # Reads (bounded, short-lived connections — API/worker only)
    # ------------------------------------------------------------------

    def list_observations(
        self, limit: int = 200, disagreement_only: bool = False
    ) -> list[dict[str, Any]]:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return []
        bounded = max(1, min(int(limit), SHADOW70_MAX_READ))
        sql = "SELECT * FROM shadow70_observations"
        if disagreement_only:
            sql += " WHERE agreement = 0"
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        return self._query(sql, (bounded,))

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return []
        bounded = max(1, min(int(limit), SHADOW70_MAX_READ))
        return self._query(
            "SELECT * FROM shadow70_events ORDER BY timestamp DESC LIMIT ?;", (bounded,)
        )

    def latest_drift_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return []
        bounded = max(1, min(int(limit), SHADOW70_MAX_READ))
        return self._query(
            "SELECT * FROM shadow70_drift_alerts ORDER BY timestamp DESC LIMIT ?;", (bounded,)
        )

    def latest_feature_health(self) -> list[dict[str, Any]]:
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return []
        return self._query("SELECT * FROM shadow70_feature_health ORDER BY id DESC LIMIT 10;", ())

    def disagreement_counts(self, valid_only: bool = True) -> dict[str, int]:
        """Disagreement-class histogram.

        CHG-0046 D9: counts VALID observations by default. The previous
        unfiltered histogram mixed SHADOW_BLOCKED/error rows (no shadow
        model, no comparison) into the disagreement taxonomy, poisoning
        the UI agreement% with rows that never compared anything.
        Historical invalid rows remain queryable via list_observations —
        no evidence is deleted.
        """
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return {}
        out: dict[str, int] = {}
        with contextlib.suppress(Exception):
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                sql = "SELECT disagreement, COUNT(*) AS c FROM shadow70_observations "
                if valid_only:
                    sql += "WHERE valid = 1 "
                sql += "GROUP BY disagreement;"
                for r in conn.execute(sql).fetchall():
                    out[str(r[0])] = int(r[1])
            finally:
                conn.close()
        return out

    def summary(self) -> dict[str, Any]:
        """Summary over persisted rows (spec 46: real data, no fake values).

        BUG-221: read path — ensure_schema() first so a fresh database
        reports an honest empty summary (available=True, zero counts)
        instead of '[SHADOW70] summary failed: no such table'.
        """
        out: dict[str, Any] = {"available": False, "observations": 0, "agreements": 0}
        if not self.audit_repo or not getattr(self.audit_repo, "_is_sqlite", False):
            return out
        self.ensure_schema()
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                row = conn.execute("SELECT COUNT(*) FROM shadow70_observations;").fetchone()
                out["observations"] = int(row[0]) if row else 0
                row = conn.execute(
                    "SELECT COUNT(*) FROM shadow70_observations WHERE agreement = 1 AND valid = 1;"
                ).fetchone()
                out["agreements"] = int(row[0]) if row else 0
                row = conn.execute(
                    "SELECT COUNT(*) FROM shadow70_observations WHERE valid = 0;"
                ).fetchone()
                out["invalid"] = int(row[0]) if row else 0
                row = conn.execute("SELECT COUNT(*) FROM shadow70_events;").fetchone()
                out["events"] = int(row[0]) if row else 0
                out["available"] = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW70] summary failed", error=str(e))
        return out

    def _query(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(sql, args).fetchall():
                    out.append(dict(r))
            finally:
                conn.close()
        return out
