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
from nexus_scalp.adapters.database.provider_store import (
    provider_name,
    query_one,
    query_rows,
    query_scalar,
    queue_write,
)
from nexus_scalp.model_lifecycle.models import (
    ChampionChallengerComparison,
    TrainingRun,
)
from nexus_scalp.model_lifecycle.schema import model_lifecycle_schema_statements
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
        """Creates the Phase 10 tables if missing (idempotent, both providers).

        SQLite creates them on its own connection as before. PostgreSQL gets
        them from the fabric's domain provisioning, so this is a no-op there
        — re-running the migration is idempotent (``IF NOT EXISTS``), and a
        store must never open a SQLite connection on a PostgreSQL box.
        """
        if not self.audit_repo or not self.audit_repo._is_sqlite:
            return
        try:
            conn = sqlite3.connect(self.audit_repo._db_path, timeout=5.0)
            try:
                conn.executescript(";".join(model_lifecycle_schema_statements()))
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
        if not self.audit_repo:
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
        return queue_write(
            self.audit_repo, _INSERT_RUN_SQL, args, operation="training_run.save_run"
        )

    def save_comparison(self, comparison: ChampionChallengerComparison) -> bool:
        if not self.audit_repo:
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
        return queue_write(
            self.audit_repo,
            _INSERT_COMPARISON_SQL,
            args,
            operation="training_run.save_comparison",
        )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return query_one(
            self.audit_repo,
            "SELECT * FROM training_runs WHERE run_id=?;",
            (run_id,),
            operation="training_run.get_run",
        )

    def list_runs(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM training_runs"
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY started_at DESC LIMIT ?;"
        return query_rows(
            self.audit_repo, sql, (*args, bounded), operation="training_run.list_runs"
        )

    def get_comparison(self, run_id: str) -> dict[str, Any] | None:
        return query_one(
            self.audit_repo,
            "SELECT * FROM model_comparisons WHERE run_id=?;",
            (run_id,),
            operation="training_run.get_comparison",
        )

    def list_comparisons(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        return query_rows(
            self.audit_repo,
            "SELECT * FROM model_comparisons ORDER BY compared_at DESC LIMIT ?;",
            (bounded,),
            operation="training_run.list_comparisons",
        )

    def summary(self) -> dict[str, Any]:
        """Training run + comparison counts for the dashboard."""
        out: dict[str, Any] = {
            "available": False,
            "runs": {},
            "comparisons": 0,
            "provider": provider_name(self.audit_repo) if self.audit_repo else "unknown",
        }
        if not self.audit_repo:
            return out
        rows = query_rows(
            self.audit_repo,
            "SELECT status, COUNT(*) AS c FROM training_runs GROUP BY status;",
            operation="training_run.summary",
        )
        if not rows and not self.audit_repo._is_sqlite:
            # The read degraded (domain not provisioned): keep ``available``
            # False so the dashboard reports unavailable instead of "0 runs".
            return out
        for r in rows:
            out["runs"][str(r["status"])] = int(r["c"])
        count = query_scalar(
            self.audit_repo,
            "SELECT COUNT(*) FROM model_comparisons;",
            operation="training_run.summary_count",
        )
        out["comparisons"] = int(count or 0)
        out["available"] = True
        return out
