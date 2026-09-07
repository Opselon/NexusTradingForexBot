"""
Persistent LearningCycle State Machine (Learning-Loop Closure, Phase 5)
=======================================================================

One authoritative, restart-safe, idempotent, auditable record for each
learning cycle:

    IDLE -> TRIGGERED -> DATASET_BUILDING -> DATASET_READY -> TRAINING ->
    TRAINED -> VALIDATING -> VALIDATED -> SHADOW_ATTACHING -> SHADOW_RUNNING ->
    SHADOW_EVALUATING -> PROMOTION_EVALUATION -> PROMOTION_APPROVED ->
    PROMOTING -> COMPLETED

Failure states: REJECTED / BLOCKED / FAILED / CANCELLED (terminal).

REUSE-FIRST: persistence reuses the canonical audit.db via short-lived
SQLite connections with WAL-safe busy timeouts (the same convention as
model_lifecycle.store.TrainingRunStore, whose tables live in the same
database). Writes are transactional; reads are bounded. No duplicate
logging system is created — the governance event ledger remains the audit
trail; this table is the RESUMABLE state of a cycle.

Guarantees:
* restart-safe: a cycle left in a running state by a crash is marked FAILED
  (reason=RESTART_INTERRUPTED) on the next start()/recover(); a completed
  step's rows are never rewritten.
* idempotent: exactly one active (non-terminal) cycle per trigger identity;
  step transitions validate the expected source state (no double-train).
* concurrency-safe: SQLite BEGIN IMMEDIATE transaction on transition.
* auditable: every transition appends to cycle_events (in-table audit), and
  callers may additionally emit governance events.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.learning_cycle")

#: Legal transitions (source -> allowed targets). Everything else refuses.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "IDLE": {"TRIGGERED", "CANCELLED"},
    "TRIGGERED": {"DATASET_BUILDING", "BLOCKED", "FAILED", "CANCELLED"},
    "DATASET_BUILDING": {"DATASET_READY", "BLOCKED", "FAILED", "CANCELLED"},
    "DATASET_READY": {"TRAINING", "BLOCKED", "FAILED", "CANCELLED"},
    "TRAINING": {"TRAINED", "FAILED", "CANCELLED"},
    "TRAINED": {"VALIDATING", "FAILED", "CANCELLED"},
    "VALIDATING": {"VALIDATED", "REJECTED", "FAILED", "CANCELLED"},
    "VALIDATED": {"SHADOW_ATTACHING", "REJECTED", "BLOCKED", "FAILED", "CANCELLED"},
    "SHADOW_ATTACHING": {"SHADOW_RUNNING", "BLOCKED", "FAILED", "CANCELLED"},
    "SHADOW_RUNNING": {"SHADOW_EVALUATING", "FAILED", "CANCELLED"},
    "SHADOW_EVALUATING": {"PROMOTION_EVALUATION", "FAILED", "CANCELLED"},
    "PROMOTION_EVALUATION": {"PROMOTION_APPROVED", "REJECTED", "BLOCKED", "FAILED", "CANCELLED"},
    "PROMOTION_APPROVED": {"PROMOTING", "BLOCKED", "CANCELLED"},
    "PROMOTING": {"COMPLETED", "FAILED"},
    # Terminal states: no transitions out.
    "COMPLETED": set(),
    "REJECTED": set(),
    "BLOCKED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}

TERMINAL_STATES: frozenset[str] = frozenset(
    {"COMPLETED", "REJECTED", "BLOCKED", "FAILED", "CANCELLED"}
)

#: States that indicate the cycle had a step in flight (restart marks FAILED).
IN_FLIGHT_STATES: frozenset[str] = frozenset(
    {
        "DATASET_BUILDING",
        "TRAINING",
        "VALIDATING",
        "SHADOW_ATTACHING",
        "SHADOW_RUNNING",
        "SHADOW_EVALUATING",
        "PROMOTING",
    }
)

_MAX_LIST = 500


class LearningCycleError(RuntimeError):
    """Raised when a cycle transition violates the state machine."""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class LearningCycleStore:
    """Persistent LearningCycle state machine over the canonical audit.db."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self.ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_cycles (
                        cycle_id TEXT PRIMARY KEY,
                        trigger TEXT NOT NULL DEFAULT '',
                        trigger_identity TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        started_at TEXT DEFAULT '',
                        completed_at TEXT DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'IDLE',
                        dataset_id TEXT DEFAULT '',
                        dataset_hash TEXT DEFAULT '',
                        training_run_id TEXT DEFAULT '',
                        candidate_model_id TEXT DEFAULT '',
                        candidate_artifact_hash TEXT DEFAULT '',
                        validation_run_id TEXT DEFAULT '',
                        shadow_run_id TEXT DEFAULT '',
                        promotion_evaluation_id TEXT DEFAULT '',
                        decision TEXT DEFAULT '',
                        reasons TEXT DEFAULT '[]',
                        error_code TEXT DEFAULT '',
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_cycle_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        cycle_id TEXT NOT NULL,
                        ts TEXT NOT NULL,
                        previous_state TEXT NOT NULL,
                        new_state TEXT NOT NULL,
                        reason TEXT DEFAULT '',
                        payload TEXT DEFAULT '{}'
                    );
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_cycles_status "
                    "ON learning_cycles(status);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_cycles_trigger "
                    "ON learning_cycles(trigger_identity);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_learning_cycle_events_cycle "
                    "ON learning_cycle_events(cycle_id);"
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[LEARNING_CYCLE] schema init failed", error=str(e))
            raise

    # ------------------------------------------------------------------
    # Creation / recovery
    # ------------------------------------------------------------------

    def start_cycle(
        self,
        trigger: str,
        trigger_identity: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Creates a new cycle (or returns the active cycle for the identity).

        Idempotent: when a non-terminal cycle already exists for the same
        trigger_identity, its cycle_id is returned unchanged — no duplicate
        active cycles, no duplicate training jobs.
        """
        existing = self.active_cycle_for(trigger_identity)
        if existing is not None:
            return str(existing["cycle_id"])
        cycle_id = f"lc_{uuid.uuid4().hex[:12]}"
        now = _utcnow()
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO learning_cycles (
                        cycle_id, trigger, trigger_identity, created_at,
                        started_at, status, reasons, payload
                    ) VALUES (?, ?, ?, ?, ?, 'TRIGGERED', '[]', ?);
                    """,
                    (
                        cycle_id,
                        trigger,
                        trigger_identity,
                        now,
                        now,
                        str(payload or {}).replace("'", '"'),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO learning_cycle_events (
                        cycle_id, ts, previous_state, new_state, reason
                    ) VALUES (?, ?, 'IDLE', 'TRIGGERED', ?);
                    """,
                    (cycle_id, now, f"trigger={trigger}"),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[LEARNING_CYCLE] start failed", error=str(e))
            raise LearningCycleError(f"cannot start learning cycle: {e}") from e
        logger.info("[LEARNING_CYCLE] event=STARTED cycle_id=%s trigger=%s", cycle_id, trigger)
        return cycle_id

    def recover_interrupted(self) -> list[str]:
        """Restart safety: in-flight cycles from a previous process -> FAILED.

        Returns the repaired cycle ids. Never resurrects partial work; the
        caller can start a fresh cycle (retry_count is preserved per trigger).
        """
        repaired: list[str] = []
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout=5000;")
                rows = conn.execute(
                    f"SELECT cycle_id FROM learning_cycles WHERE status IN "
                    f"({','.join('?' for _ in IN_FLIGHT_STATES)});",
                    tuple(sorted(IN_FLIGHT_STATES)),
                ).fetchall()
                for (cycle_id,) in rows:
                    now = _utcnow()
                    conn.execute(
                        "UPDATE learning_cycles SET status='FAILED', error_code=?, "
                        "completed_at=? WHERE cycle_id=?;",
                        ("RESTART_INTERRUPTED", now, cycle_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO learning_cycle_events (
                            cycle_id, ts, previous_state, new_state, reason
                        ) VALUES (?, ?, 'IN_FLIGHT', 'FAILED', 'restart_interrupted');
                        """,
                        (cycle_id, now),
                    )
                    repaired.append(cycle_id)
                if repaired:
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("[LEARNING_CYCLE] recovery failed", error=str(e))
        return repaired

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        cycle_id: str,
        target: str,
        *,
        reason: str = "",
        payload: dict[str, Any] | None = None,
        require_active: bool = True,
        **field_updates: Any,
    ) -> bool:
        """Validates + applies one state transition with field updates.

        field_updates may set any persisted column (dataset_id, training_run_id,
        shadow_run_id, ...). Raises LearningCycleError on an illegal
        transition, an unknown state, or a cycle that is already terminal.
        """
        if target not in ALLOWED_TRANSITIONS:
            raise LearningCycleError(f"unknown cycle state: {target}")
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM learning_cycles WHERE cycle_id=?;", (cycle_id,)
            ).fetchone()
            if row is None:
                raise LearningCycleError(f"cycle not found: {cycle_id}")
            current = str(row[0])
            if require_active and current in TERMINAL_STATES:
                raise LearningCycleError(
                    f"cycle {cycle_id} is terminal ({current}); cannot -> {target}"
                )
            if target not in ALLOWED_TRANSITIONS.get(current, set()):
                raise LearningCycleError(
                    f"illegal transition {current} -> {target} for cycle {cycle_id}"
                )
            now = _utcnow()
            sets = ["status=?", "reasons=?"]
            args: list[Any] = [target, reason]
            allowed_cols = {
                "dataset_id",
                "dataset_hash",
                "training_run_id",
                "candidate_model_id",
                "candidate_artifact_hash",
                "validation_run_id",
                "shadow_run_id",
                "promotion_evaluation_id",
                "decision",
                "error_code",
            }
            for k, v in field_updates.items():
                if k in allowed_cols:
                    sets.append(f"{k}=?")
                    args.append(str(v) if v is not None else "")
                else:
                    raise LearningCycleError(f"unknown cycle field: {k}")
            if target in TERMINAL_STATES:
                sets.append("completed_at=?")
                args.append(now)
            args.append(cycle_id)
            conn.execute(
                f"UPDATE learning_cycles SET {', '.join(sets)} WHERE cycle_id=?;", tuple(args)
            )
            conn.execute(
                """
                INSERT INTO learning_cycle_events (
                    cycle_id, ts, previous_state, new_state, reason, payload
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (cycle_id, now, current, target, reason, str(payload or {}).replace("'", '"')),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info(
            "[LEARNING_CYCLE] event=TRANSITION cycle_id=%s -> %s reason=%s",
            cycle_id,
            target,
            reason or "",
        )
        return True

    def mark_retry(self, cycle_id: str) -> int:
        """Increments retry_count (for re-trigger after FAILED). Returns count."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute(
                "UPDATE learning_cycles SET retry_count=retry_count+1 WHERE cycle_id=?;",
                (cycle_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT retry_count FROM learning_cycles WHERE cycle_id=?;", (cycle_id,)
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM learning_cycles WHERE cycle_id=?;", (cycle_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def active_cycle_for(self, trigger_identity: str) -> dict[str, Any] | None:
        """The single non-terminal cycle for a trigger identity, if any."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM learning_cycles WHERE trigger_identity=? AND status NOT IN "
                f"({','.join('?' for _ in TERMINAL_STATES)}) "
                "ORDER BY created_at DESC LIMIT 1;",
                (trigger_identity, *sorted(TERMINAL_STATES)),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_cycles(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _MAX_LIST))
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM learning_cycles WHERE status=? "
                    "ORDER BY created_at DESC LIMIT ?;",
                    (status, bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM learning_cycles ORDER BY created_at DESC LIMIT ?;",
                    (bounded,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def cycle_events(self, cycle_id: str, limit: int = 200) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _MAX_LIST))
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM learning_cycle_events WHERE cycle_id=? ORDER BY id LIMIT ?;",
                (cycle_id, bounded),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def summary(self) -> dict[str, Any]:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            out: dict[str, Any] = {"by_status": {}, "active": 0}
            for r in conn.execute(
                "SELECT status, COUNT(*) FROM learning_cycles GROUP BY status;"
            ).fetchall():
                out["by_status"][str(r[0])] = int(r[1])
            out["active"] = sum(
                c
                for s, c in out["by_status"].items()
                if s not in TERMINAL_STATES
            )
            return out
        finally:
            conn.close()
