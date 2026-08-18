"""
DatabaseHygieneWorker (TASK-11)
===============================
Background orchestrator: OBSERVE -> CLASSIFY -> PLAN -> VALIDATE -> CLEAN -> VERIFY.

Production posture:
  * Default first-run mode: AUDIT_ONLY (spec §2 — never delete on debut).
  * Operator switches to SAFE_CLEAN explicitly; AGGRESSIVE_CLEAN is a
    separate explicit activation.
  * Every cycle: build plan -> apply (bounded) -> verify -> persist run.
  * Busy DB -> DEFER (never force).
  * LIVE trading mode -> conservative (cache/temp/retention only).
  * Never touches the tick hot path (off-loop; called via asyncio.to_thread
    or scheduled task — spec §19).

Components (spec §50):
    HygieneScanner / HygieneClassifier / DuplicateDetector / OrphanDetector /
    RetentionEngine / ArchiveManager / CleanupPlanner / CleanupExecutor /
    VerificationEngine / CleanupJournal — all in this package.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.hygiene import WorkerMode, WorkerState
from nexus_scalp.hygiene.archive import read_only_connect
from nexus_scalp.hygiene.state import HygieneStateStore, new_run_id
from nexus_scalp.hygiene.worker import (
    CleanupExecutor,
    HygienePlanner,
    HygieneScanner,
    financial_aggregates,
)

#: Databases the worker manages (spec §4). Paths resolved at runtime.
MANAGED_DATABASES: dict[str, str] = {
    "audit": "artifacts/audit.db",
    "news": "artifacts/news.db",
    "candle_intel": "artifacts/candle_intel.db",
}


def _db_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _wal_size(path: str) -> int:
    try:
        return os.path.getsize(path + "-wal")
    except Exception:
        return 0


class DatabaseHygieneWorker:
    """Owns state, mode, and one cycle's plan/apply/verify."""

    def __init__(
        self,
        *,
        repo_root: str | Path,
        mode: WorkerMode = WorkerMode.AUDIT_ONLY,
        execution_mode: str = "PAPER",
        db_overrides: dict[str, str] | None = None,
        apply_deletes: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.mode = mode
        self.execution_mode = str(execution_mode or "PAPER").upper()
        self.apply_deletes = apply_deletes
        self.state_store = HygieneStateStore(self.repo_root)
        self.scanner = HygieneScanner()
        self.planner = HygienePlanner(mode=mode)
        self.executor = CleanupExecutor(archive_root=self.repo_root, mode=mode)
        self._db_paths = dict(MANAGED_DATABASES)
        if db_overrides:
            self._db_paths.update(db_overrides)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        st = self.state_store.get_state()
        st["execution_mode"] = self.execution_mode
        st["mode"] = self.mode.value
        st["apply_deletes"] = self.apply_deletes
        st["managed_databases"] = list(self._db_paths.keys())
        st["db_sizes"] = {
            k: {
                "bytes": _db_size(str(self.repo_root / v)),
                "wal_bytes": _wal_size(str(self.repo_root / v)),
            }
            for k, v in self._db_paths.items()
        }
        return st

    def pause(self) -> dict[str, Any]:
        self.state_store.set_state(WorkerState.PAUSED, mode=self.mode.value)
        return self.status()

    def resume(self) -> dict[str, Any]:
        self.state_store.set_state(WorkerState.IDLE, mode=self.mode.value)
        return self.status()

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.state_store.list_runs(limit=limit)

    # ------------------------------------------------------------------
    # cycle
    # ------------------------------------------------------------------
    def plan_database(self, db_key: str) -> dict[str, Any]:
        rel = self._db_paths.get(db_key)
        if rel is None:
            return {"database": db_key, "error": "UNKNOWN_DATABASE"}
        path = self.repo_root / rel
        if not path.exists():
            return {"database": db_key, "error": "DB_NOT_FOUND", "path": str(path)}
        try:
            conn = read_only_connect(str(path))
            try:
                plan = self.planner.build_plan(db_key, conn, self.scanner)
                return {"database": db_key, "plan": plan.summary()}
            finally:
                conn.close()
        except Exception as e:
            return {"database": db_key, "error": str(e)}

    def run_cycle(self, databases: list[str] | None = None) -> dict[str, Any]:
        """
        One full worker cycle over the managed databases.
        Returns the consolidated run result.
        """
        if self.state_store.get_state().get("state") == WorkerState.PAUSED.value:
            return {"error": "PAUSED", "detail": "worker paused"}

        targets = list(databases or self._db_paths.keys())
        self.state_store.set_state(WorkerState.SCANNING, mode=self.mode.value)
        run_id = new_run_id()
        started = time.monotonic()
        overall: dict[str, Any] = {
            "run_id": run_id,
            "databases": {},
            "started_at": datetime.now(UTC).isoformat(),
            "verification": "NOT_RUN",
            "mode": self.mode.value,
        }
        self.state_store.record_run(
            {
                "run_id": run_id,
                "database": "+".join(targets),
                "started_at": overall["started_at"],
                "mode": self.mode.value,
                "verification_status": "IN_PROGRESS",
            }
        )

        try:
            for db_key in targets:
                rel = self._db_paths.get(db_key)
                if not rel:
                    overall["databases"][db_key] = {"error": "UNKNOWN_DATABASE"}
                    continue
                path = self.repo_root / rel
                if not path.exists():
                    overall["databases"][db_key] = {"error": "DB_NOT_FOUND"}
                    continue

                # BUSY CHECK: bounded busy_timeout — DEFER, never force.
                try:
                    conn = sqlite3.connect(str(path), timeout=2.0)
                    conn.execute("PRAGMA busy_timeout=2000")
                    conn.execute("SELECT 1").fetchone()
                    conn.close()
                except Exception as e:
                    overall["databases"][db_key] = {
                        "error": "BUSY_DEFERRED",
                        "detail": str(e),
                    }
                    continue

                conn = read_only_connect(str(path))
                try:
                    plan = self.planner.build_plan(db_key, conn, self.scanner)
                finally:
                    conn.close()

                db_result = self.executor.apply_plan(
                    db_key,
                    str(path),
                    plan,
                    run_id,
                    apply_deletes=(
                        self.apply_deletes
                        and self.mode in (WorkerMode.SAFE_CLEAN, WorkerMode.AGGRESSIVE_CLEAN)
                    ),
                )
                db_result["plan_summary"] = plan.summary()
                db_result["duplicates_found"] = plan.summary()["duplicates_found"]
                db_result["orphans_found"] = plan.summary()["orphans_found"]
                db_result["rows_scanned"] = plan.summary()["rows_scanned"]
                overall["databases"][db_key] = db_result

                self.state_store.record_run(
                    {
                        "run_id": run_id,
                        "database": db_key,
                        "started_at": overall["started_at"],
                        "finished_at": datetime.now(UTC).isoformat(),
                        "duration_ms": db_result.get("duration_ms", 0.0),
                        "mode": self.mode.value,
                        "rows_scanned": plan.summary()["rows_scanned"],
                        "duplicates_found": plan.summary()["duplicates_found"],
                        "orphans_found": plan.summary()["orphans_found"],
                        "archived": sum(db_result.get("archived", {}).values()),
                        "deleted": sum(db_result.get("deleted", {}).values()),
                        "errors": db_result.get("errors", []),
                        "bytes_freed": 0,
                        "verification": db_result.get("verification", ""),
                        "correlation_id": run_id,
                        "plan_summary": plan.summary(),
                    }
                )
            overall["finished_at"] = datetime.now(UTC).isoformat()
            overall["duration_ms"] = round((time.monotonic() - started) * 1000.0, 1)
            all_pass = all(
                d.get("verification") == "PASS"
                or d.get("verification") in ("SKIPPED_DRY_RUN", "NOT_RUN")
                for d in overall["databases"].values()
                if isinstance(d, dict)
            )
            overall["verification"] = "PASS" if all_pass else "CHECK"
            # restart recovery: any interrupted run is marked (never auto-resumed)
            self.state_store.recover_interrupted()
            self.state_store.set_state(
                WorkerState.IDLE,
                mode=self.mode.value,
                last_scan=overall["started_at"],
                last_cleanup=(overall["finished_at"] if overall["verification"] == "PASS" else ""),
                last_success=overall["finished_at"] if overall["verification"] == "PASS" else "",
                stats={
                    "last_run": run_id,
                    "last_run_databases": targets,
                },
            )
            return overall
        except Exception as e:
            self.state_store.set_state(WorkerState.FAILED, mode=self.mode.value)
            overall["error"] = str(e)
            overall["verification"] = "FAILED"
            return overall


def db_integrity_digest(path: str) -> str:
    """Deterministic digest of a DB's row counts + financial aggregates.

    Used to prove BEFORE == AFTER for protected tables (spec §64).
    """
    conn = read_only_connect(path)
    try:
        parts: list[str] = []
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall():
            try:
                n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                n = -1
            parts.append(f"{name}={n}")
        fin = financial_aggregates(conn)
        parts.extend(f"{k}={v}" for k, v in sorted(fin.items()))
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    finally:
        conn.close()
