"""
Shadow Persistence Store
========================
PHASE 11 append-oriented, auditable persistence (spec 20 / 24 / 25).

Tables:
    shadow_runs       one bounded shadow evaluation run
    shadow_decisions  one parallel Champion/Challenger decision per row
    shadow_comparisons aggregated multi-dimension comparison snapshots
    shadow_promotions promotion evaluations (eligibility + veto history)

Historical shadow results are NEVER overwritten: rows are append-only keyed by
run_id / decision_id. Model rebuilds and feature-schema evolution do NOT erase
shadow history (spec 25): every row preserves model version + schema identity.

Writes go through the AuditRepository background queue so the live path is
never blocked.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.models import (
    PromotionEvaluation,
    ShadowComparison,
    ShadowDecisionRecord,
    ShadowRun,
)

logger = get_logger("nexus_scalp.shadow.store")

MAX_READ_LIMIT = 3000

_INSERT_RUN_SQL = """
    INSERT OR REPLACE INTO shadow_runs (
        run_id, champion_model_id, champion_version, challenger_model_id,
        challenger_version, status, started_at, finished_at, decision_count, error,
        git_revision, configuration_version, challenger_artifact_hash, champion_artifact_hash
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_DECISION_SQL = """
    INSERT OR REPLACE INTO shadow_decisions (
        shadow_decision_id, run_id, decision_id, timestamp, symbol, timeframe,
        champion_model_id, champion_version, challenger_model_id, challenger_version,
        feature_schema_id, feature_dimension, feature_hash, regime, session,
        champion_action, champion_confidence, challenger_action, challenger_confidence,
        action_agreement, valid_comparison, invalid_reason,
        hypothetical_pnl_usd, hypothetical_r, mfe_r, mae_r, holding_duration_sec,
        exit_reason, simulated,
        champion_entry, champion_sl, champion_tp, shadow_entry, shadow_sl, shadow_tp,
        spread_usd, shadow_r, shadow_mfe_r, shadow_mae_r, shadow_pnl_usd,
        shadow_holding_sec, shadow_exit_reason, delta_r, outcome_status,
        payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_COMPARISON_SQL = """
    INSERT OR REPLACE INTO shadow_comparisons (
        run_id, champion_model_id, champion_version, challenger_model_id,
        challenger_version, sample_count, valid_comparisons, invalid_comparisons,
        action_agreement_rate, champion_expectancy_r, challenger_expectancy_r,
        champion_drawdown_r, challenger_drawdown_r, evidence_status,
        samples_required, samples_observed, by_regime, by_strategy,
        best_regimes, worst_regimes, degraded_regimes, degraded_strategies,
        evaluated_at, payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_PROMOTION_SQL = """
    INSERT OR REPLACE INTO shadow_promotions (
        run_id, candidate_model_id, candidate_version, champion_model_id,
        champion_version, final_score, eligible, vetoes, reasons, evaluated_at,
        payload
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class ShadowStore:
    """Append-only persistence for shadow evaluation."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo
        # Schema is created once per process; repeating the DDL on every live
        # tick (save_decision -> ensure_schema) is synchronous SQLite I/O on
        # the hot path. A miss re-checks; a hit skips all DDL entirely.
        self._schema_ensured: bool = False
        self._additive_ensured: bool = False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Creates the Phase 11 shadow tables if missing (idempotent).

        Guarded by an in-process flag: the DDL runs at most once. Live-path
        callers (ShadowEngine.record_shadow_decision -> save_decision) invoke
        this on every tick, so a per-tick sqlite3.connect + CREATE would be
        blocking I/O on the hot path.

        CHG-0046 (SHADOW_EVIDENCE v2): additive column migration for the
        paired-outcome + run-freeze fields. Legacy rows keep their values;
        new columns are NULL/'' (= NOT_RECORDED) — historical evidence is
        never rewritten, only extended.
        """
        if self._schema_ensured and self._additive_ensured:
            return
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT UNIQUE NOT NULL,
                        champion_model_id TEXT NOT NULL,
                        champion_version TEXT NOT NULL,
                        challenger_model_id TEXT NOT NULL,
                        challenger_version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT DEFAULT '',
                        decision_count INTEGER DEFAULT 0,
                        error TEXT DEFAULT ''
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        shadow_decision_id TEXT UNIQUE NOT NULL,
                        run_id TEXT NOT NULL,
                        decision_id TEXT DEFAULT '',
                        timestamp TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        timeframe TEXT DEFAULT '',
                        champion_model_id TEXT NOT NULL,
                        champion_version TEXT NOT NULL,
                        challenger_model_id TEXT NOT NULL,
                        challenger_version TEXT NOT NULL,
                        feature_schema_id TEXT DEFAULT 'scalp_v1',
                        feature_dimension INTEGER DEFAULT 50,
                        feature_hash TEXT DEFAULT '',
                        regime TEXT DEFAULT '',
                        session TEXT DEFAULT '',
                        champion_action TEXT DEFAULT '',
                        champion_confidence REAL DEFAULT 0.0,
                        challenger_action TEXT DEFAULT '',
                        challenger_confidence REAL DEFAULT 0.0,
                        action_agreement INTEGER DEFAULT 0,
                        valid_comparison INTEGER DEFAULT 1,
                        invalid_reason TEXT DEFAULT '',
                        hypothetical_pnl_usd REAL DEFAULT 0.0,
                        hypothetical_r REAL DEFAULT 0.0,
                        mfe_r REAL DEFAULT 0.0,
                        mae_r REAL DEFAULT 0.0,
                        holding_duration_sec REAL DEFAULT 0.0,
                        exit_reason TEXT DEFAULT '',
                        simulated INTEGER DEFAULT 1,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_comparisons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT UNIQUE NOT NULL,
                        champion_model_id TEXT NOT NULL,
                        champion_version TEXT NOT NULL,
                        challenger_model_id TEXT NOT NULL,
                        challenger_version TEXT NOT NULL,
                        sample_count INTEGER DEFAULT 0,
                        valid_comparisons INTEGER DEFAULT 0,
                        invalid_comparisons INTEGER DEFAULT 0,
                        action_agreement_rate REAL DEFAULT 0.0,
                        champion_expectancy_r REAL DEFAULT 0.0,
                        challenger_expectancy_r REAL DEFAULT 0.0,
                        champion_drawdown_r REAL DEFAULT 0.0,
                        challenger_drawdown_r REAL DEFAULT 0.0,
                        evidence_status TEXT DEFAULT 'INSUFFICIENT_EVIDENCE',
                        samples_required INTEGER DEFAULT 30,
                        samples_observed INTEGER DEFAULT 0,
                        by_regime TEXT DEFAULT '{}',
                        by_strategy TEXT DEFAULT '{}',
                        best_regimes TEXT DEFAULT '[]',
                        worst_regimes TEXT DEFAULT '[]',
                        degraded_regimes TEXT DEFAULT '[]',
                        degraded_strategies TEXT DEFAULT '[]',
                        evaluated_at TEXT NOT NULL,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_promotions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT UNIQUE NOT NULL,
                        candidate_model_id TEXT NOT NULL,
                        candidate_version TEXT NOT NULL,
                        champion_model_id TEXT NOT NULL,
                        champion_version TEXT NOT NULL,
                        final_score REAL DEFAULT 0.0,
                        eligible INTEGER DEFAULT 0,
                        vetoes TEXT DEFAULT '[]',
                        reasons TEXT DEFAULT '[]',
                        evaluated_at TEXT NOT NULL,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                for idx in (
                    "CREATE INDEX IF NOT EXISTS idx_shadow_decisions_run ON shadow_decisions(run_id, timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_shadow_decisions_symbol ON shadow_decisions(symbol, timestamp);",
                    "CREATE INDEX IF NOT EXISTS idx_shadow_runs_status ON shadow_runs(status);",
                ):
                    conn.execute(idx)
                conn.commit()
                self._schema_ensured = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] schema init failed", error=str(e))
        # Additive SHADOW_EVIDENCE v2 columns — AFTER base tables exist.
        self._ensure_additive_columns()

    def _ensure_additive_columns(self) -> None:
        """SHADOW_EVIDENCE v2 additive migration (runs at most once).

        Adds the paired-outcome columns to shadow_decisions and the
        run-freeze columns to shadow_runs when absent. Deterministic,
        non-destructive: existing rows read back as NULL (NOT_RECORDED).
        """
        if self._additive_ensured or not self.audit_repo or not self.audit_repo._is_sqlite:
            return
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                self._add_missing_columns(
                    conn,
                    "shadow_decisions",
                    [
                        ("champion_entry", "REAL DEFAULT 0.0"),
                        ("champion_sl", "REAL DEFAULT 0.0"),
                        ("champion_tp", "REAL DEFAULT 0.0"),
                        ("shadow_entry", "REAL DEFAULT 0.0"),
                        ("shadow_sl", "REAL DEFAULT 0.0"),
                        ("shadow_tp", "REAL DEFAULT 0.0"),
                        ("spread_usd", "REAL DEFAULT 0.0"),
                        ("shadow_r", "REAL"),
                        ("shadow_mfe_r", "REAL"),
                        ("shadow_mae_r", "REAL"),
                        ("shadow_pnl_usd", "REAL"),
                        ("shadow_holding_sec", "REAL"),
                        ("shadow_exit_reason", "TEXT DEFAULT ''"),
                        ("delta_r", "REAL"),
                        ("outcome_status", "TEXT DEFAULT 'NOT_RECORDED'"),
                    ],
                )
                self._add_missing_columns(
                    conn,
                    "shadow_runs",
                    [
                        ("git_revision", "TEXT DEFAULT ''"),
                        ("configuration_version", "TEXT DEFAULT ''"),
                        ("challenger_artifact_hash", "TEXT DEFAULT ''"),
                        ("champion_artifact_hash", "TEXT DEFAULT ''"),
                    ],
                )
                conn.commit()
                self._additive_ensured = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] additive migration failed (isolated)", error=str(e))

    @staticmethod
    def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table});")}
        except Exception:
            existing = set()
        for name, ddl in columns:
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl};")
                except Exception as e:
                    logger.error("[SHADOW] add column failed", table=table, column=name, error=str(e))

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_run(self, run: ShadowRun) -> bool:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        args = (
            run.run_id,
            run.champion.model_id,
            run.champion.model_version,
            run.challenger.model_id,
            run.challenger.model_version,
            run.status,
            run.started_at.isoformat(),
            run.finished_at.isoformat() if run.finished_at else "",
            run.decision_count,
            run.error,
            # CHG-0046 D11: run-freeze identity
            run.git_revision,
            run.configuration_version,
            run.challenger_artifact_hash,
            run.champion_artifact_hash,
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_RUN_SQL, args))
            return True
        except Exception as e:
            logger.error("[SHADOW] save_run failed", run=run.run_id, error=str(e))
            return False

    def save_decision(self, decision: ShadowDecisionRecord) -> bool:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        args = (
            decision.shadow_decision_id,
            decision.run_id,
            decision.decision_id,
            decision.timestamp.isoformat(),
            decision.symbol,
            decision.timeframe,
            decision.champion.model_id,
            decision.champion.model_version,
            decision.challenger.model_id,
            decision.challenger.model_version,
            decision.shared_input.feature_schema_id,
            decision.shared_input.feature_dimension,
            decision.shared_input.feature_hash,
            decision.shared_input.regime,
            decision.shared_input.session,
            decision.champion_action,
            decision.champion_confidence,
            decision.challenger_action,
            decision.challenger_confidence,
            1 if decision.action_agreement else 0,
            1 if decision.valid_comparison else 0,
            decision.invalid_reason,
            decision.hypothetical_pnl_usd,
            decision.hypothetical_r,
            decision.mfe_r,
            decision.mae_r,
            decision.holding_duration_sec,
            decision.exit_reason,
            1 if decision.simulated else 0,
            # CHG-0046: paired outcome + geometry fields (SHADOW_EVIDENCE v2)
            decision.champion_entry,
            decision.champion_sl,
            decision.champion_tp,
            decision.shadow_entry,
            decision.shadow_sl,
            decision.shadow_tp,
            decision.spread_usd,
            decision.shadow_r,
            decision.shadow_mfe_r,
            decision.shadow_mae_r,
            decision.shadow_pnl_usd,
            decision.shadow_holding_sec,
            decision.shadow_exit_reason,
            decision.delta_r,
            decision.outcome_status,
            json.dumps(decision.model_dump(mode="json"), default=str),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_DECISION_SQL, args))
            return True
        except Exception as e:
            logger.error("[SHADOW] save_decision failed", error=str(e))
            return False

    def save_comparison(self, comparison: ShadowComparison) -> bool:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        args = (
            comparison.run_id,
            comparison.champion.model_id,
            comparison.champion.model_version,
            comparison.challenger.model_id,
            comparison.challenger.model_version,
            comparison.sample_count,
            comparison.valid_comparisons,
            comparison.invalid_comparisons,
            comparison.action_agreement_rate,
            comparison.champion_expectancy_r,
            comparison.challenger_expectancy_r,
            comparison.champion_drawdown_r,
            comparison.challenger_drawdown_r,
            comparison.evidence_status.value,
            comparison.samples_required,
            comparison.samples_observed,
            json.dumps(comparison.by_regime, default=str),
            json.dumps(comparison.by_strategy, default=str),
            json.dumps(comparison.best_regimes),
            json.dumps(comparison.worst_regimes),
            json.dumps(comparison.degraded_regimes),
            json.dumps(comparison.degraded_strategies),
            comparison.evaluation_started_at.isoformat(),
            json.dumps(comparison.model_dump(mode="json"), default=str),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_COMPARISON_SQL, args))
            return True
        except Exception as e:
            logger.error("[SHADOW] save_comparison failed", error=str(e))
            return False

    def save_promotion(self, evaluation: PromotionEvaluation) -> bool:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        args = (
            evaluation.run_id,
            evaluation.candidate_model_id,
            evaluation.candidate_version,
            evaluation.champion_model_id,
            evaluation.champion_version,
            evaluation.final_score,
            1 if evaluation.eligible else 0,
            json.dumps(evaluation.vetoes),
            json.dumps(evaluation.reasons),
            evaluation.evaluated_at.isoformat(),
            json.dumps(evaluation.model_dump(mode="json"), default=str),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_PROMOTION_SQL, args))
            return True
        except Exception as e:
            logger.error("[SHADOW] save_promotion failed", error=str(e))
            return False

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM shadow_runs WHERE run_id=?;", (run_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] get_run failed", error=str(e))
            return None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), 500))
        out: list[dict[str, Any]] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM shadow_runs ORDER BY started_at DESC LIMIT ?;",
                    (bounded,),
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                out.append(dict(r))
        except Exception as e:
            logger.error("[SHADOW] list_runs failed", error=str(e))
        return out

    def list_decisions(
        self,
        run_id: str | None = None,
        symbol: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM shadow_decisions"
        clauses: list[str] = []
        args: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            args.append(run_id)
        if symbol:
            clauses.append("symbol = ?")
            args.append(symbol)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC LIMIT ?;"
        out: list[dict[str, Any]] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, (*args, bounded)).fetchall()
            finally:
                conn.close()
            for r in rows:
                out.append(dict(r))
        except Exception as e:
            logger.error("[SHADOW] list_decisions failed", error=str(e))
        return out

    def get_comparison(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM shadow_comparisons WHERE run_id=?;", (run_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] get_comparison failed", error=str(e))
            return None

    def get_promotion(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM shadow_promotions WHERE run_id=?;", (run_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] get_promotion failed", error=str(e))
            return None

    def list_promotions(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), 500))
        out: list[dict[str, Any]] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM shadow_promotions ORDER BY evaluated_at DESC LIMIT ?;",
                    (bounded,),
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                out.append(dict(r))
        except Exception as e:
            logger.error("[SHADOW] list_promotions failed", error=str(e))
        return out

    def summary(self) -> dict[str, Any]:
        """Shadow dashboard summary."""
        out: dict[str, Any] = {"available": False, "runs": {}, "decisions": 0, "promotions": 0}
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return out
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                for r in conn.execute(
                    "SELECT status, COUNT(*) AS c FROM shadow_runs GROUP BY status;"
                ).fetchall():
                    out["runs"][str(r[0])] = int(r[1])
                row = conn.execute("SELECT COUNT(*) FROM shadow_decisions;").fetchone()
                out["decisions"] = int(row[0]) if row else 0
                row = conn.execute("SELECT COUNT(*) FROM shadow_promotions;").fetchone()
                out["promotions"] = int(row[0]) if row else 0
                out["available"] = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[SHADOW] summary failed", error=str(e))
        return out
