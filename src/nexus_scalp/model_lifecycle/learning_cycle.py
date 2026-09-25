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

from nexus_scalp.adapters.database.provider_store import (
    AUDIT_DOMAIN,
    query_one,
    query_rows,
    queue_write_batch,
)
from nexus_scalp.model_lifecycle.schema import model_lifecycle_schema_statements
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
    """Persistent LearningCycle state machine over the canonical audit.db.

    SQLite keeps the historical shape (short-lived WAL connections with a
    BEGIN IMMEDIATE transition). PostgreSQL routes through the audit domain's
    pooled fabric backends — the cycle tables live in that domain's schema,
    provisioned by ``model_lifecycle.schema`` — with each transition as ONE
    atomic pooled transaction.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        #: ``True`` while a pooled (non-SQLite) backend serves this store. The
        #: SQLite path keeps its own ``db_path``; a pooled provider ignores it.
        self._pooled = self._detect_pooled()
        self.ensure_schema()

    @staticmethod
    def _detect_pooled() -> bool:
        """A pooled provider is only active when the audit domain is provisioned.

        Never guesses: when no backend is registered the store stays on the
        documented SQLite path (or a local in-memory DB in tests) rather than
        silently dropping writes.
        """
        try:
            from nexus_scalp.database.fabric import get_domain_backend

            return get_domain_backend(AUDIT_DOMAIN, readonly=False) is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Creates the cycle tables if missing (idempotent).

        Under a pooled provider the tables are created by the domain's
        provisioning (``model_lifecycle_schema_statements``), so nothing to do
        here — a SQLite-only DDL path must never run against PostgreSQL.
        """
        if self._pooled:
            return
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            try:
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.executescript(";".join(model_lifecycle_schema_statements()))
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
        payload_json = str(payload or {}).replace("'", '"')
        statements = (
            (
                """
                INSERT INTO learning_cycles (
                    cycle_id, trigger, trigger_identity, created_at,
                    started_at, status, reasons, payload
                ) VALUES (?, ?, ?, ?, ?, 'TRIGGERED', '[]', ?);
                """,
                (cycle_id, trigger, trigger_identity, now, now, payload_json),
            ),
            (
                """
                INSERT INTO learning_cycle_events (
                    cycle_id, ts, previous_state, new_state, reason
                ) VALUES (?, ?, 'IDLE', 'TRIGGERED', ?);
                """,
                (cycle_id, now, f"trigger={trigger}"),
            ),
        )
        if not queue_write_batch(
            self._repo_for_write(), statements, operation="learning_cycle.start_cycle"
        ):
            raise LearningCycleError("cannot start learning cycle: write failed")
        logger.info("[LEARNING_CYCLE] event=STARTED cycle_id=%s trigger=%s", cycle_id, trigger)
        return cycle_id

    def recover_interrupted(self) -> list[str]:
        """Restart safety: in-flight cycles from a previous process -> FAILED.

        Returns the repaired cycle ids. Never resurrects partial work; the
        caller can start a fresh cycle (retry_count is preserved per trigger).
        """
        repaired: list[str] = []
        rows = query_rows(
            self._repo_for_read(),
            f"SELECT cycle_id FROM learning_cycles WHERE status IN "
            f"({','.join('?' for _ in IN_FLIGHT_STATES)});",
            tuple(sorted(IN_FLIGHT_STATES)),
            operation="learning_cycle.recover_interrupted",
        )
        if not rows:
            return repaired
        statements: list[tuple[str, tuple[Any, ...]]] = []
        for row in rows:
            cycle_id = str(row["cycle_id"])
            now = _utcnow()
            statements.append(
                (
                    "UPDATE learning_cycles SET status='FAILED', error_code=?, "
                    "completed_at=? WHERE cycle_id=?;",
                    ("RESTART_INTERRUPTED", now, cycle_id),
                )
            )
            statements.append(
                (
                    """
                    INSERT INTO learning_cycle_events (
                        cycle_id, ts, previous_state, new_state, reason
                    ) VALUES (?, ?, 'IN_FLIGHT', 'FAILED', 'restart_interrupted');
                    """,
                    (cycle_id, now),
                )
            )
            repaired.append(cycle_id)
        if statements and not queue_write_batch(
            self._repo_for_write(), statements, operation="learning_cycle.recover_interrupted"
        ):
            logger.error("[LEARNING_CYCLE] recovery write failed")
            return []
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
        current = self._current_status(cycle_id)
        if current is None:
            raise LearningCycleError(f"cycle not found: {cycle_id}")
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
        statements = [
            (
                f"UPDATE learning_cycles SET {', '.join(sets)} WHERE cycle_id=?;",
                tuple(args),
            ),
            (
                """
                INSERT INTO learning_cycle_events (
                    cycle_id, ts, previous_state, new_state, reason, payload
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (cycle_id, now, current, target, reason, str(payload or {}).replace("'", '"')),
            ),
        ]
        if not queue_write_batch(
            self._repo_for_write(), statements, operation="learning_cycle.transition"
        ):
            raise LearningCycleError(
                f"transition {current} -> {target} for cycle {cycle_id} failed to persist"
            )
        logger.info(
            "[LEARNING_CYCLE] event=TRANSITION cycle_id=%s -> %s reason=%s",
            cycle_id,
            target,
            reason or "",
        )
        return True

    def mark_retry(self, cycle_id: str) -> int:
        """Increments retry_count (for re-trigger after FAILED). Returns count."""
        if not queue_write_batch(
            self._repo_for_write(),
            [
                (
                    "UPDATE learning_cycles SET retry_count=retry_count+1 WHERE cycle_id=?;",
                    (cycle_id,),
                )
            ],
            operation="learning_cycle.mark_retry",
        ):
            return 0
        row = query_one(
            self._repo_for_read(),
            "SELECT retry_count FROM learning_cycles WHERE cycle_id=?;",
            (cycle_id,),
            operation="learning_cycle.mark_retry",
        )
        return int(row["retry_count"]) if row else 0

    # ------------------------------------------------------------------
    # Repository resolution
    # ------------------------------------------------------------------

    def _repo_for_write(self) -> Any:
        """The persistence target for writes: the pooled backend, or a shim.

        SQLite keeps using the store's own ``db_path`` (the historical path,
        unchanged): a lightweight adapter exposes the ``_connect_sqlite`` /
        ``_is_sqlite`` contract the shared helpers expect.
        """
        if self._pooled:
            return _PooledRepoShim()
        return _SqlitePathShim(self.db_path)

    def _repo_for_read(self) -> Any:
        return self._repo_for_write()

    def _current_status(self, cycle_id: str) -> str | None:
        row = query_one(
            self._repo_for_read(),
            "SELECT status FROM learning_cycles WHERE cycle_id=?;",
            (cycle_id,),
            operation="learning_cycle.transition",
        )
        return str(row["status"]) if row else None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        return query_one(
            self._repo_for_read(),
            "SELECT * FROM learning_cycles WHERE cycle_id=?;",
            (cycle_id,),
            operation="learning_cycle.get_cycle",
        )

    def active_cycle_for(self, trigger_identity: str) -> dict[str, Any] | None:
        """The single non-terminal cycle for a trigger identity, if any."""
        return query_one(
            self._repo_for_read(),
            "SELECT * FROM learning_cycles WHERE trigger_identity=? AND status NOT IN "
            f"({','.join('?' for _ in TERMINAL_STATES)}) "
            "ORDER BY created_at DESC LIMIT 1;",
            (trigger_identity, *sorted(TERMINAL_STATES)),
            operation="learning_cycle.active_cycle_for",
        )

    def list_cycles(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _MAX_LIST))
        if status:
            return query_rows(
                self._repo_for_read(),
                "SELECT * FROM learning_cycles WHERE status=? ORDER BY created_at DESC LIMIT ?;",
                (status, bounded),
                operation="learning_cycle.list_cycles",
            )
        return query_rows(
            self._repo_for_read(),
            "SELECT * FROM learning_cycles ORDER BY created_at DESC LIMIT ?;",
            (bounded,),
            operation="learning_cycle.list_cycles",
        )

    def cycle_events(self, cycle_id: str, limit: int = 200) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), _MAX_LIST))
        return query_rows(
            self._repo_for_read(),
            "SELECT * FROM learning_cycle_events WHERE cycle_id=? ORDER BY id LIMIT ?;",
            (cycle_id, bounded),
            operation="learning_cycle.cycle_events",
        )

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"by_status": {}, "active": 0}
        rows = query_rows(
            self._repo_for_read(),
            "SELECT status, COUNT(*) AS c FROM learning_cycles GROUP BY status;",
            operation="learning_cycle.summary",
        )
        for r in rows:
            out["by_status"][str(r["status"])] = int(r["c"])
        out["active"] = sum(c for s, c in out["by_status"].items() if s not in TERMINAL_STATES)
        return out


class _SqlitePathShim:
    """Exposes the repository contract the shared helpers expect.

    The SQLite branch of ``LearningCycleStore`` has always owned its own
    ``db_path`` (it is constructed with a plain string, not an
    ``AuditRepository``). The provider helpers speak the repository's
    ``_is_sqlite`` / ``_connect_sqlite`` surface; this adapter presents the
    store's path in that shape so the SQLite path is byte-for-byte the same
    connection behaviour as before.
    """

    def __init__(self, db_path: str) -> None:
        self._is_sqlite = True
        self._db_path = db_path

    def _connect_sqlite(self, timeout: float = 5.0) -> sqlite3.Connection:
        uri = self._db_path.startswith("file:")
        return sqlite3.connect(self._db_path, timeout=timeout, uri=uri)


class _PooledRepoShim:
    """Marks the store as served by the audit domain's pooled fabric backend.

    Under a pooled provider the store has no SQLite path at all: the helpers
    resolve the audit domain's pooled read/write backends through the fabric
    registry and never touch ``db_path``.
    """

    _is_sqlite = False
    _db_path = ""
