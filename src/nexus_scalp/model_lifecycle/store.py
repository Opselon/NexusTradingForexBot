"""
Training Run Store & Comparison Persistence
===========================================
PHASE 10 (spec 12 / 34 / 40).

`training_runs` is an append-only, immutable record of every controlled
training execution. `model_comparisons` stores the Champion-vs-Challenger
comparison lineage. Derived summaries are rebuildable; this truth is never
modified.

Writes go through the AuditRepository background queue (never blocks live).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.model_lifecycle.models import (
    ChampionChallengerComparison,
    TrainingRun,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.store")

MAX_READ_LIMIT = 2000

_INSERT_RUN_SQL = """
    INSERT OR REPLACE INTO training_runs (
        run_id, dataset_id, feature_schema_id, feature_dimension,
        model_id, model_version, parent_champion_id, parent_champion_version,
        hyperparameters, random_seed, architecture, train_range, validation_range,
        oos_range, embargo_bars, purge_bars, started_at, finished_at,
        artifacts, metrics, gates, status, failure_reason, build_identity
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_INSERT_COMPARISON_SQL = """
    INSERT OR REPLACE INTO model_comparisons (
        run_id, candidate_model_id, candidate_version, champion_model_id,
        champion_version, comparison, improvement_score, eligible, compared_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class TrainingRunStore:
    """Append-only persistence for training runs + comparisons."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Creates the Phase 10 tables if missing (idempotent)."""
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS training_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT UNIQUE NOT NULL,
                        dataset_id TEXT NOT NULL,
                        feature_schema_id TEXT DEFAULT 'scalp_v1',
                        feature_dimension INTEGER DEFAULT 50,
                        model_id TEXT DEFAULT '',
                        model_version TEXT DEFAULT '',
                        parent_champion_id TEXT DEFAULT '',
                        parent_champion_version TEXT DEFAULT '',
                        hyperparameters TEXT DEFAULT '{}',
                        random_seed INTEGER DEFAULT 42,
                        architecture TEXT DEFAULT 'scalp_net',
                        train_range TEXT DEFAULT '{}',
                        validation_range TEXT DEFAULT '{}',
                        oos_range TEXT DEFAULT '{}',
                        embargo_bars INTEGER DEFAULT 15,
                        purge_bars INTEGER DEFAULT 15,
                        started_at TEXT NOT NULL,
                        finished_at TEXT DEFAULT '',
                        artifacts TEXT DEFAULT '[]',
                        metrics TEXT DEFAULT '{}',
                        gates TEXT DEFAULT '[]',
                        status TEXT NOT NULL,
                        failure_reason TEXT DEFAULT '',
                        build_identity TEXT DEFAULT ''
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_comparisons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT UNIQUE NOT NULL,
                        candidate_model_id TEXT NOT NULL,
                        candidate_version TEXT NOT NULL,
                        champion_model_id TEXT DEFAULT '',
                        champion_version TEXT DEFAULT '',
                        comparison TEXT DEFAULT '{}',
                        improvement_score REAL DEFAULT 0.0,
                        eligible INTEGER DEFAULT 0,
                        compared_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_train_runs_dataset ON training_runs(dataset_id);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_train_runs_status ON training_runs(status);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_model_comp_candidate "
                    "ON model_comparisons(candidate_model_id);"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[TRAINING_RUNS] schema init failed", error=str(e))

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def save_run(self, run: TrainingRun) -> bool:
        """Persists an immutable training run. Idempotent on run_id."""
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        args = (
            run.run_id,
            run.dataset_id,
            run.feature_schema_id,
            run.feature_dimension,
            run.model_id,
            run.model_version,
            run.parent_champion_id,
            run.parent_champion_version,
            json.dumps(run.hyperparameters, default=str),
            run.random_seed,
            run.architecture,
            json.dumps(run.train_range, default=str),
            json.dumps(run.validation_range, default=str),
            json.dumps(run.oos_range, default=str),
            run.embargo_bars,
            run.purge_bars,
            run.started_at.isoformat(),
            run.finished_at.isoformat() if run.finished_at else "",
            json.dumps([a.model_dump(mode="json") for a in run.artifacts], default=str),
            json.dumps(run.metrics, default=str),
            json.dumps([g.model_dump(mode="json") for g in run.gates], default=str),
            run.status.value,
            run.failure_reason,
            run.build_identity,
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_RUN_SQL, args))
            return True
        except Exception as e:
            logger.error("[TRAINING_RUNS] save failed", run=run.run_id, error=str(e))
            return False

    def save_comparison(self, comparison: ChampionChallengerComparison) -> bool:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return False
        self.ensure_schema()
        args = (
            comparison.run_id,
            comparison.candidate_model_id,
            comparison.candidate_version,
            comparison.champion_model_id,
            comparison.champion_version,
            json.dumps(comparison.model_dump(mode="json"), default=str),
            comparison.improvement_score,
            1 if comparison.eligible else 0,
            comparison.compared_at.isoformat(),
        )
        try:
            self.audit_repo._queue.put_nowait((_INSERT_COMPARISON_SQL, args))
            return True
        except Exception as e:
            logger.error("[MODEL_COMPARISON] save failed", error=str(e))
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
                    "SELECT * FROM training_runs WHERE run_id=?;", (run_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[TRAINING_RUNS] get failed", error=str(e))
            return None

    def list_runs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM training_runs"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY started_at DESC LIMIT ?;"
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
            logger.error("[TRAINING_RUNS] list failed", error=str(e))
        return out

    def get_comparison(self, run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return None
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT * FROM model_comparisons WHERE run_id=?;", (run_id,)
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[MODEL_COMPARISON] get failed", error=str(e))
            return None

    def list_comparisons(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), 200))
        out: list[dict[str, Any]] = []
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM model_comparisons ORDER BY compared_at DESC LIMIT ?;",
                    (bounded,),
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                out.append(dict(r))
        except Exception as e:
            logger.error("[MODEL_COMPARISON] list failed", error=str(e))
        return out

    def summary(self) -> dict[str, Any]:
        """Training run + comparison counts for the dashboard."""
        out: dict[str, Any] = {"available": False, "runs": {}, "comparisons": 0}
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return out
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                for r in conn.execute(
                    "SELECT status, COUNT(*) AS c FROM training_runs GROUP BY status;"
                ).fetchall():
                    out["runs"][str(r[0])] = int(r[1])
                row = conn.execute("SELECT COUNT(*) FROM model_comparisons;").fetchone()
                out["comparisons"] = int(row[0]) if row else 0
                out["available"] = True
            finally:
                conn.close()
        except Exception as e:
            logger.error("[TRAINING_RUNS] summary failed", error=str(e))
        return out
