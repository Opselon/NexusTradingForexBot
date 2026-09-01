"""
Research Observability Store
=============================
TASK-21-RESEARCH-OBSERVABILITY (2026-08-20).

Persistence facade for the research observability entities:

    research_gates           first-class gate rows (started/completed/failed)
    research_events          persisted gate timeline entries
    research_evidence        immutable evidence vault
    research_run_snapshots   reproducibility fingerprints per run
    research_worker_heartbeat   worker state/heartbeat rows
    research_queue           observable gate queue

All writes go through the AuditRepository background queue (never block the
live path). All reads are bounded and JSON-safe (BUG-075 defense).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.evidence import (
    EvidenceArtifact,
    FailureClass,
    GateStatus,
    GateType,
    ResearchEvent,
    ResearchGate,
    ResearchRunSnapshot,
    WorkerHealth,
)

logger = get_logger("nexus_scalp.research.observability")

MAX_READ_LIMIT = 2000


def _json(value: Any) -> str:
    """Canonical JSON text form: None -> '{}', never the literal 'null'."""
    if value is None:
        return "{}"
    try:
        import json

        encoded = json.dumps(value, default=str)
        return encoded if encoded != "null" else "{}"
    except Exception:
        return "{}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    import json

    try:
        data = json.loads(str(text))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _connect(repo: AuditRepository) -> sqlite3.Connection:
    conn = sqlite3.connect(repo._db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


class ResearchObservabilityStore:
    """Bounded persistence facade over the research observability tables."""

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo
        # In-memory gate cache: rows are written through the background
        # queue, so finish/start must not depend on a synchronous DB read.
        self._gates: dict[str, ResearchGate] = {}

    # ==================================================================
    # Gates
    # ==================================================================

    def create_gate(
        self,
        strategy_id: str,
        research_run_id: str,
        gate_type: GateType,
        *,
        status: GateStatus = GateStatus.PENDING,
        order_index: int = 0,
        dataset_version: str = "",
        engine_version: str = "",
        configuration_version: str = "",
        gate_id: str | None = None,
    ) -> ResearchGate:
        gid = gate_id or f"GATE-{uuid.uuid4().hex[:10].upper()}"
        gate = ResearchGate(
            gate_id=gid,
            strategy_id=strategy_id,
            research_run_id=research_run_id,
            gate_type=gate_type,
            status=status,
            configuration_version=configuration_version,
            dataset_version=dataset_version,
            engine_version=engine_version,
            order_index=order_index,
        )
        self._queue(
            _INSERT_GATE_SQL,
            (
                gate.gate_id,
                gate.strategy_id,
                gate.research_run_id,
                gate.gate_type.value,
                gate.status.value,
                gate.started_at.isoformat() if gate.started_at else "",
                gate.completed_at.isoformat() if gate.completed_at else "",
                gate.duration_ms,
                gate.configuration_version,
                gate.dataset_version,
                gate.engine_version,
                _json(gate.result),
                gate.failure_reason,
                gate.failure_class.value,
                gate.evidence_id,
                int(gate.retryable),
                gate.order_index,
            ),
        )
        self._gates[gate.gate_id] = gate
        return gate

    def start_gate(self, gate_id: str) -> ResearchGate | None:
        gate = self.get_gate(gate_id)
        if gate is None:
            return None
        updated = gate.model_copy(
            update={
                "status": GateStatus.RUNNING,
                "started_at": datetime.now(UTC),
            }
        )
        self._queue(
            _UPDATE_GATE_SQL,
            (
                updated.status.value,
                updated.started_at.isoformat() if updated.started_at else "",
                "",
                updated.failure_reason,
                updated.failure_class.value,
                "",
                gate_id,
            ),
        )
        self._gates[gate_id] = updated
        return updated

    def finish_gate(
        self,
        gate_id: str,
        *,
        status: GateStatus,
        result: dict[str, Any] | None = None,
        failure_reason: str = "",
        failure_class: FailureClass = FailureClass.UNKNOWN,
        evidence: EvidenceArtifact | None = None,
        retryable: bool | None = None,
        started_at: datetime | None = None,
    ) -> ResearchGate | None:
        gate = self.get_gate(gate_id)
        if gate is None:
            return None
        completed = datetime.now(UTC)
        start = started_at or gate.started_at
        duration = (completed - start).total_seconds() * 1000.0 if start else 0.0
        ev_id = evidence.evidence_id if evidence is not None else gate.evidence_id
        if evidence is not None:
            self.store_evidence(evidence)
        update = {
            "status": status,
            "completed_at": completed,
            "duration_ms": round(duration, 1),
            "result": result if result is not None else gate.result,
            "failure_reason": failure_reason,
            "failure_class": failure_class,
            "evidence_id": ev_id,
        }
        if retryable is not None:
            update["retryable"] = retryable
        updated = gate.model_copy(update=update)
        self._queue(
            _UPDATE_GATE_SQL,
            (
                updated.status.value,
                updated.started_at.isoformat() if updated.started_at else "",
                updated.completed_at.isoformat() if updated.completed_at else "",
                updated.failure_reason,
                updated.failure_class.value,
                updated.evidence_id,
                gate_id,
            ),
        )
        self._queue(
            _UPDATE_GATE_RESULT_SQL,
            (
                _json(updated.result),
                updated.duration_ms,
                int(updated.retryable),
                gate_id,
            ),
        )
        self._gates[gate_id] = updated
        return updated

    def block_gate(
        self,
        gate_id: str,
        *,
        reason: str,
        required: str = "",
    ) -> ResearchGate | None:
        gate = self.get_gate(gate_id)
        if gate is None:
            return None
        updated = gate.model_copy(
            update={
                "status": GateStatus.BLOCKED,
                "failure_reason": reason,
                "failure_class": FailureClass.DATA,
                "result": {**gate.result, "required": required, "reason": reason},
            }
        )
        self._queue(
            _UPDATE_GATE_SQL,
            (
                updated.status.value,
                updated.started_at.isoformat() if updated.started_at else "",
                updated.completed_at.isoformat() if updated.completed_at else "",
                updated.failure_reason,
                updated.failure_class.value,
                updated.evidence_id,
                gate_id,
            ),
        )
        self._gates[gate_id] = updated
        self._queue(
            _UPDATE_GATE_RESULT_SQL,
            (
                _json(updated.result),
                updated.duration_ms,
                int(updated.retryable),
                gate_id,
            ),
        )
        return updated

    def skip_gate(self, gate_id: str, reason: str = "") -> ResearchGate | None:
        gate = self.get_gate(gate_id)
        if gate is None:
            return None
        updated = gate.model_copy(
            update={
                "status": GateStatus.SKIPPED,
                "completed_at": datetime.now(UTC),
                "failure_reason": reason,
            }
        )
        self._queue(
            _UPDATE_GATE_SQL,
            (
                updated.status.value,
                updated.started_at.isoformat() if updated.started_at else "",
                updated.completed_at.isoformat() if updated.completed_at else "",
                updated.failure_reason,
                updated.failure_class.value,
                updated.evidence_id,
                gate_id,
            ),
        )
        self._gates[gate_id] = updated
        return updated

    def get_gate(self, gate_id: str) -> ResearchGate | None:
        cached = self._gates.get(gate_id)
        if cached is not None:
            return cached
        if not self.audit_repo._is_sqlite:
            return None
        try:
            conn = _connect(self.audit_repo)
            try:
                row = conn.execute(
                    "SELECT * FROM research_gates WHERE gate_id=?;", (gate_id,)
                ).fetchone()
            finally:
                conn.close()
            return self._gate_from_row(row) if row else None
        except Exception as e:
            logger.error("[RESEARCH_OBS] gate load failed", gate=gate_id, error=str(e))
            return None

    def list_gates(
        self,
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        limit: int = 500,
    ) -> list[ResearchGate]:
        if not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM research_gates"
        where: list[str] = []
        args: list[Any] = []
        if strategy_id:
            where.append("strategy_id=?")
            args.append(strategy_id)
        if research_run_id:
            where.append("research_run_id=?")
            args.append(research_run_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY order_index ASC, gate_id ASC LIMIT ?;"
        args.append(bounded)
        out: list[ResearchGate] = []
        try:
            conn = _connect(self.audit_repo)
            try:
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
            for r in rows:
                gate = self._gate_from_row(r)
                if gate is not None:
                    out.append(gate)
        except Exception as e:
            logger.error("[RESEARCH_OBS] gates list failed", error=str(e))
        return out

    @staticmethod
    def _gate_from_row(row: sqlite3.Row) -> ResearchGate | None:
        try:
            return ResearchGate(
                gate_id=row["gate_id"],
                strategy_id=row["strategy_id"],
                research_run_id=row["research_run_id"],
                gate_type=GateType(row["gate_type"]),
                status=GateStatus(row["status"]),
                started_at=_parse_ts(row["started_at"]),
                completed_at=_parse_ts(row["completed_at"]),
                duration_ms=float(row["duration_ms"] or 0.0),
                configuration_version=row["configuration_version"] or "",
                dataset_version=row["dataset_version"] or "",
                engine_version=row["engine_version"] or "",
                result=_read_json(row["result"]),
                failure_reason=row["failure_reason"] or "",
                failure_class=FailureClass(row["failure_class"] or "UNKNOWN"),
                evidence_id=row["evidence_id"] or "",
                retryable=bool(row["retryable"]),
                order_index=int(row["order_index"] or 0),
            )
        except Exception as e:
            logger.error("[RESEARCH_OBS] gate row decode failed", error=str(e))
            return None

    def _queue(self, sql: str, args: tuple[Any, ...]) -> None:
        try:
            if hasattr(self.audit_repo, "_queue"):
                self.audit_repo._queue.put_nowait((sql, args))
        except Exception as e:
            logger.error("[RESEARCH_OBS] queue write failed", error=str(e))

    # ==================================================================
    # Events (persisted timeline)
    # ==================================================================

    def record_event(
        self,
        strategy_id: str,
        research_run_id: str,
        event_type: str,
        message: str = "",
        payload: dict[str, Any] | None = None,
        gate_id: str = "",
        event_id: str | None = None,
    ) -> ResearchEvent:
        eid = event_id or f"EVT-{uuid.uuid4().hex[:10].upper()}"
        event = ResearchEvent(
            event_id=eid,
            strategy_id=strategy_id,
            research_run_id=research_run_id,
            gate_id=gate_id,
            event_type=event_type,
            message=message,
            payload=payload or {},
        )
        self._queue(
            _INSERT_EVENT_SQL,
            (
                event.event_id,
                event.strategy_id,
                event.research_run_id,
                event.gate_id,
                event.event_type,
                event.message,
                _json(event.payload),
                event.occurred_at.isoformat(),
            ),
        )
        return event

    def list_events(
        self,
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        if not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM research_events"
        where: list[str] = []
        args: list[Any] = []
        if strategy_id:
            where.append("strategy_id=?")
            args.append(strategy_id)
        if research_run_id:
            where.append("research_run_id=?")
            args.append(research_run_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY occurred_at ASC, id ASC LIMIT ?;"
        args.append(bounded)
        out: list[dict[str, Any]] = []
        try:
            conn = _connect(self.audit_repo)
            try:
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
            for r in rows:
                out.append(
                    {
                        "event_id": r["event_id"],
                        "strategy_id": r["strategy_id"],
                        "research_run_id": r["research_run_id"],
                        "gate_id": r["gate_id"] or "",
                        "event_type": r["event_type"],
                        "message": r["message"] or "",
                        "payload": _read_json(r["payload"]),
                        "occurred_at": r["occurred_at"] or "",
                    }
                )
        except Exception as e:
            logger.error("[RESEARCH_OBS] events list failed", error=str(e))
        return out

    # ==================================================================
    # Evidence vault (immutable)
    # ==================================================================

    def store_evidence(self, artifact: EvidenceArtifact) -> str:
        self._queue(
            _INSERT_EVIDENCE_SQL,
            (
                artifact.evidence_id,
                artifact.strategy_id,
                artifact.research_run_id,
                artifact.gate_id,
                artifact.kind.value,
                _json(artifact.content),
                artifact.content_hash,
                artifact.dataset_version,
                artifact.engine_version,
                artifact.created_at.isoformat(),
            ),
        )
        return artifact.evidence_id

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        if not self.audit_repo._is_sqlite:
            return None
        try:
            conn = _connect(self.audit_repo)
            try:
                row = conn.execute(
                    "SELECT * FROM research_evidence WHERE evidence_id=?;", (evidence_id,)
                ).fetchone()
            finally:
                conn.close()
            return self._evidence_from_row(row) if row else None
        except Exception as e:
            logger.error("[RESEARCH_OBS] evidence load failed", evidence=evidence_id, error=str(e))
            return None

    def list_evidence(
        self,
        strategy_id: str | None = None,
        research_run_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if not self.audit_repo._is_sqlite:
            return []
        bounded = max(1, min(int(limit), MAX_READ_LIMIT))
        sql = "SELECT * FROM research_evidence"
        where: list[str] = []
        args: list[Any] = []
        if strategy_id:
            where.append("strategy_id=?")
            args.append(strategy_id)
        if research_run_id:
            where.append("research_run_id=?")
            args.append(research_run_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, evidence_id ASC LIMIT ?;"
        args.append(bounded)
        out: list[dict[str, Any]] = []
        try:
            conn = _connect(self.audit_repo)
            try:
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
            for r in rows:
                ev = self._evidence_from_row(r)
                if ev is not None:
                    out.append(ev)
        except Exception as e:
            logger.error("[RESEARCH_OBS] evidence list failed", error=str(e))
        return out

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> dict[str, Any] | None:
        try:
            return {
                "evidence_id": row["evidence_id"],
                "strategy_id": row["strategy_id"],
                "research_run_id": row["research_run_id"],
                "gate_id": row["gate_id"] or "",
                "kind": row["kind"],
                "content": _read_json(row["content"]),
                "content_hash": row["content_hash"] or "",
                "dataset_version": row["dataset_version"] or "",
                "engine_version": row["engine_version"] or "",
                "created_at": row["created_at"] or "",
            }
        except Exception as e:
            logger.error("[RESEARCH_OBS] evidence row decode failed", error=str(e))
            return None

    # ==================================================================
    # Run snapshots (reproducibility)
    # ==================================================================

    def store_run_snapshot(self, run_id: str, snapshot: ResearchRunSnapshot) -> str:
        self._queue(
            _INSERT_SNAPSHOT_SQL,
            (
                run_id,
                snapshot.strategy_id,
                snapshot.strategy_version,
                snapshot.strategy_definition_hash,
                _json(snapshot.strategy_configuration),
                snapshot.dataset_version,
                snapshot.dataset_hash,
                snapshot.feature_schema_version,
                snapshot.model_version,
                snapshot.model_hash,
                snapshot.rule_matrix_version,
                snapshot.runtime_configuration_version,
                snapshot.backtest_engine_version,
                snapshot.validation_engine_version,
                str(snapshot.random_seed) if snapshot.random_seed is not None else "",
                snapshot.research_prompt_version,
                snapshot.engine_version,
                snapshot.configuration_hash,
                snapshot.fingerprint(),
                snapshot.captured_at.isoformat(),
                # CHG-0035 (v2): identity columns; empty = NOT_RECORDED
                snapshot.feature_schema_id,
                int(snapshot.feature_dimension) if snapshot.feature_dimension else 0,
                snapshot.model_id,
                snapshot.git_commit,
            ),
        )
        return snapshot.fingerprint()

    def get_run_snapshot(self, research_run_id: str) -> dict[str, Any] | None:
        if not self.audit_repo._is_sqlite:
            return None
        try:
            conn = _connect(self.audit_repo)
            try:
                row = conn.execute(
                    "SELECT * FROM research_run_snapshots WHERE research_run_id=?;",
                    (research_run_id,),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            return {
                "research_run_id": row["research_run_id"] or "",
                "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "strategy_definition_hash": row["strategy_definition_hash"] or "",
                "strategy_configuration": _read_json(row["strategy_configuration"]),
                "dataset_version": row["dataset_version"] or "",
                "dataset_hash": row["dataset_hash"] or "",
                "feature_schema_version": row["feature_schema_version"] or "",
                "model_version": row["model_version"] or "",
                "model_hash": row["model_hash"] or "",
                "rule_matrix_version": row["rule_matrix_version"] or "",
                "runtime_configuration_version": row["runtime_configuration_version"] or "",
                "backtest_engine_version": row["backtest_engine_version"] or "",
                "validation_engine_version": row["validation_engine_version"] or "",
                "random_seed": _parse_int(row["random_seed"]),
                "research_prompt_version": row["research_prompt_version"] or "",
                "engine_version": row["engine_version"] or "",
                "configuration_hash": row["configuration_hash"] or "",
                "research_hash": row["research_hash"] or "",
                "captured_at": row["captured_at"] or "",
                # CHG-0035 (v2): keys exist only when the column was migrated;
                # older rows read as NOT_RECORDED ("" / 0) — honest, never invented.
                "feature_schema_id": (
                    row["feature_schema_id"] if "feature_schema_id" in row.keys() else ""
                )
                or "",
                "feature_dimension": (
                    row["feature_dimension"] if "feature_dimension" in row.keys() else 0
                )
                or 0,
                "model_id": (row["model_id"] if "model_id" in row.keys() else "") or "",
                "git_commit": (row["git_commit"] if "git_commit" in row.keys() else "") or "",
            }
        except Exception as e:
            logger.error("[RESEARCH_OBS] snapshot load failed", run=research_run_id, error=str(e))
            return None

    # ==================================================================
    # Worker heartbeat + health (spec 29 / 30)
    # ==================================================================

    def beat(
        self,
        *,
        scope: str = "research",
        cycle_count: int = 0,
        last_cycle_start: str = "",
        last_cycle_completion: str = "",
        last_cycle_duration_ms: float = 0.0,
        last_action: str = "",
        current_job: str = "",
        current_strategy: str = "",
        current_gate: str = "",
        queued_jobs: int = 0,
        failed_jobs: int = 0,
        last_error: str = "",
        status: str = "RUNNING",
    ) -> str:
        """Persists one worker heartbeat row (upsert by scope)."""
        ts = _now()
        self._queue(
            _UPSERT_HEARTBEAT_SQL,
            (
                scope,
                ts,
                int(cycle_count),
                last_cycle_start,
                last_cycle_completion,
                round(float(last_cycle_duration_ms), 1),
                last_action,
                current_job,
                current_strategy,
                current_gate,
                int(queued_jobs),
                int(failed_jobs),
                last_error,
                status,
            ),
        )
        return ts

    def worker_health(self, scope: str = "research") -> dict[str, Any]:
        """Classifies worker health from the heartbeat (HEALTHY/DEGRADED/STUCK/FAILED)."""
        if not self.audit_repo._is_sqlite:
            return {"available": False, "health": "UNKNOWN"}
        try:
            conn = _connect(self.audit_repo)
            try:
                row = conn.execute(
                    "SELECT * FROM research_worker_heartbeat WHERE scope=?;", (scope,)
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return {"available": True, "health": WorkerHealth.IDLE.value, "heartbeat": None}
            hb = dict(row)
            now_sec = time.time()
            beat_ts = _parse_ts(hb.get("last_beat_at") or "")
            beat_age = (now_sec - beat_ts.timestamp()) if beat_ts else float("inf")
            status = str(hb.get("status") or "")
            health = WorkerHealth.HEALTHY.value
            if status == "FAILED":
                health = WorkerHealth.FAILED.value
            elif status != "RUNNING":
                health = WorkerHealth.IDLE.value
            elif beat_age > 900.0:
                health = WorkerHealth.STUCK.value
            elif beat_age > 300.0:
                health = WorkerHealth.DEGRADED.value
            out = {
                "available": True,
                "health": health,
                "heartbeat_age_sec": round(beat_age, 1) if beat_ts else None,
                "heartbeat": {
                    "scope": hb.get("scope"),
                    "last_beat_at": hb.get("last_beat_at"),
                    "cycle_count": int(hb.get("cycle_count") or 0),
                    "last_cycle_start": hb.get("last_cycle_start") or "",
                    "last_cycle_completion": hb.get("last_cycle_completion") or "",
                    "last_cycle_duration_ms": float(hb.get("last_cycle_duration_ms") or 0.0),
                    "last_action": hb.get("last_action") or "",
                    "current_job": hb.get("current_job") or "",
                    "current_strategy": hb.get("current_strategy") or "",
                    "current_gate": hb.get("current_gate") or "",
                    "queued_jobs": int(hb.get("queued_jobs") or 0),
                    "failed_jobs": int(hb.get("failed_jobs") or 0),
                    "last_error": hb.get("last_error") or "",
                    "status": status,
                    "last_cycle_at": hb.get("last_cycle_at") or "",
                },
            }
            return out
        except Exception as e:
            logger.error("[RESEARCH_OBS] worker health failed", error=str(e))
            return {"available": False, "health": "UNKNOWN"}

    # ==================================================================
    # Queue observability (spec 31)
    # ==================================================================

    def queue_snapshot(self) -> dict[str, Any]:
        """Gate-queue census: queued/running/last-error per gate type."""
        out: dict[str, Any] = {
            "available": False,
            "queued": {},
            "running": [],
            "last_errors": {},
        }
        if not self.audit_repo._is_sqlite:
            return out
        try:
            conn = _connect(self.audit_repo)
            try:
                for r in conn.execute(
                    "SELECT gate_type, status, COUNT(*) AS c FROM research_gates "
                    "GROUP BY gate_type, status;"
                ).fetchall():
                    gt = str(r["gate_type"])
                    st = str(r["status"])
                    bucket = out["queued"].setdefault(gt, {})
                    bucket[st] = int(r["c"])
                for r in conn.execute(
                    "SELECT gate_id, strategy_id, research_run_id, gate_type, status "
                    "FROM research_gates WHERE status IN ('RUNNING','QUEUED') "
                    "ORDER BY order_index ASC LIMIT 20;"
                ).fetchall():
                    out["running"].append(
                        {
                            "gate_id": r["gate_id"],
                            "strategy_id": r["strategy_id"],
                            "research_run_id": r["research_run_id"],
                            "gate_type": r["gate_type"],
                            "status": r["status"],
                        }
                    )
                for r in conn.execute(
                    "SELECT gate_type, failure_reason, COUNT(*) AS c "
                    "FROM research_gates WHERE status IN ('FAILED','ERROR','BLOCKED') "
                    "GROUP BY gate_type, failure_reason ORDER BY c DESC LIMIT 15;"
                ).fetchall():
                    gt = str(r["gate_type"])
                    out["last_errors"].setdefault(gt, []).append(
                        {"reason": str(r["failure_reason"] or ""), "count": int(r["c"])}
                    )
            finally:
                conn.close()
            out["available"] = True
            return out
        except Exception as e:
            logger.error("[RESEARCH_OBS] queue snapshot failed", error=str(e))
            return out

    # ==================================================================
    # Aggregates (heatmap / family analytics, spec 47 / 48)
    # ==================================================================

    def gate_failure_heatmap(self) -> dict[str, Any]:
        """Most common gate failures + rejection reasons across all runs."""
        out: dict[str, Any] = {"by_gate": {}, "rejection_reasons": {}}
        try:
            conn = _connect(self.audit_repo)
            try:
                total = 0
                for r in conn.execute(
                    "SELECT gate_type, COUNT(*) AS c FROM research_gates "
                    "WHERE status IN ('FAILED','ERROR') GROUP BY gate_type ORDER BY c DESC;"
                ).fetchall():
                    gt = str(r["gate_type"])
                    out["by_gate"][gt] = int(r["c"])
                    total += int(r["c"])
                out["total_failures"] = total
                reasons: dict[str, int] = {}
                for r in conn.execute("SELECT result_summary FROM research_runs;").fetchall():
                    s = _read_json(r[0])
                    lc = s.get("lifecycle", "")
                    if lc == "REJECTED":
                        for key in ("primary_failure", "reason", "rejection_reason"):
                            val = s.get(key)
                            if val:
                                reasons[str(val)] = reasons.get(str(val), 0) + 1
                out["rejection_reasons"] = dict(
                    sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)
                )
            finally:
                conn.close()
            return out
        except Exception as e:
            logger.error("[RESEARCH_OBS] heatmap failed", error=str(e))
            return out

    def family_analytics(self) -> dict[str, Any]:
        """Grouped candidate analytics by family / discovery window / tier."""
        out: dict[str, Any] = {"families": {}}
        try:
            conn = _connect(self.audit_repo)
            try:
                for r in conn.execute(
                    "SELECT context_definition, lifecycle, score, sample_count "
                    "FROM strategy_registry;"
                ).fetchall():
                    ctx = _read_json(r["context_definition"])
                    fam = str(ctx.get("fingerprint") or ctx.get("symbol") or "UNKNOWN")
                    lc = str(r["lifecycle"] or "UNKNOWN")
                    score = _read_json(r["score"])
                    bucket = out["families"].setdefault(
                        fam,
                        {"candidates": 0, "validated": 0, "rejected": 0, "scores": []},
                    )
                    bucket["candidates"] += 1
                    if lc == "VALIDATED":
                        bucket["validated"] += 1
                    elif lc == "REJECTED":
                        bucket["rejected"] += 1
                    fs = score.get("final_score")
                    if isinstance(fs, (int, float)):
                        bucket["scores"].append(float(fs))
            finally:
                conn.close()
            for _fam, bucket in out["families"].items():
                scores = bucket["scores"]
                bucket["avg_score"] = round(sum(scores) / len(scores), 3) if scores else None
                bucket["best_score"] = round(max(scores), 3) if scores else None
                bucket["pass_rate"] = (
                    round(bucket["validated"] / bucket["candidates"], 3)
                    if bucket["candidates"]
                    else 0.0
                )
                del bucket["scores"]
            return out
        except Exception as e:
            logger.error("[RESEARCH_OBS] family analytics failed", error=str(e))
            return out

    # ==================================================================
    # One-click trace (spec 12)
    # ==================================================================

    def trace(self, strategy_id: str, research_run_id: str | None = None) -> dict[str, Any]:
        """Assembles the full strategy -> run -> gates -> evidence -> score chain."""
        out: dict[str, Any] = {"strategy_id": strategy_id, "available": False}
        try:
            entry = self._registry_entry(strategy_id)
            if entry is not None:
                out["registry"] = entry
            runs = self._runs_for(strategy_id, research_run_id)
            out["runs"] = runs
            run_ids = [r["run_id"] for r in runs]
            if not run_ids and research_run_id:
                run_ids = [research_run_id]
            gates = self.list_gates(strategy_id=strategy_id, limit=500)
            out["gates"] = [g.model_dump(mode="json") for g in gates]
            out["events"] = self.list_events(strategy_id=strategy_id, limit=300)
            out["evidence"] = self.list_evidence(strategy_id=strategy_id, limit=500)
            out["snapshots"] = [self.get_run_snapshot(rid) for rid in run_ids[:5]]
            out["snapshots"] = [s for s in out["snapshots"] if s is not None]
            out["available"] = True
            return out
        except Exception as e:
            logger.error("[RESEARCH_OBS] trace failed", strategy=strategy_id, error=str(e))
            return out

    def _registry_entry(self, strategy_id: str) -> dict[str, Any] | None:
        try:
            conn = _connect(self.audit_repo)
            try:
                row = conn.execute(
                    "SELECT * FROM strategy_registry WHERE strategy_id=? "
                    "ORDER BY updated_at DESC LIMIT 1;",
                    (strategy_id,),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            out = dict(row)
            for col in (
                "backtest",
                "walkforward",
                "oos",
                "robustness",
                "score",
                "context_definition",
                "parent_strategy_ids",
                "validation_lineage",
                "retirement_reason",
                "discovery_evidence",
            ):
                out[col] = _read_json(out.get(col))
            return out
        except Exception as e:
            logger.error("[RESEARCH_OBS] registry load failed", strategy=strategy_id, error=str(e))
            return None

    def _runs_for(self, strategy_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        try:
            conn = _connect(self.audit_repo)
            try:
                if run_id:
                    rows = conn.execute(
                        "SELECT * FROM research_runs WHERE strategy_id=? AND run_id=? "
                        "ORDER BY executed_at DESC LIMIT 20;",
                        (strategy_id, run_id),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM research_runs WHERE strategy_id=? "
                        "ORDER BY executed_at DESC LIMIT 20;",
                        (strategy_id,),
                    ).fetchall()
            finally:
                conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("[RESEARCH_OBS] runs load failed", strategy=strategy_id, error=str(e))
            return []


# ======================================================================
# SQL
# ======================================================================

_INSERT_GATE_SQL = """
    INSERT INTO research_gates (
        gate_id, strategy_id, research_run_id, gate_type, status,
        started_at, completed_at, duration_ms, configuration_version,
        dataset_version, engine_version, result, failure_reason,
        failure_class, evidence_id, retryable, order_index
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(gate_id) DO NOTHING;
"""

_UPDATE_GATE_SQL = """
    UPDATE research_gates SET
        status=?, started_at=?, completed_at=?, failure_reason=?,
        failure_class=?, evidence_id=?
    WHERE gate_id=?;
"""

_UPDATE_GATE_RESULT_SQL = """
    UPDATE research_gates SET result=?, duration_ms=?, retryable=?
    WHERE gate_id=?;
"""

_INSERT_EVENT_SQL = """
    INSERT INTO research_events (
        event_id, strategy_id, research_run_id, gate_id, event_type,
        message, payload, occurred_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_id) DO NOTHING;
"""

_INSERT_EVIDENCE_SQL = """
    INSERT INTO research_evidence (
        evidence_id, strategy_id, research_run_id, gate_id, kind,
        content, content_hash, dataset_version, engine_version, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(evidence_id) DO NOTHING;
"""

_INSERT_SNAPSHOT_SQL = """
    INSERT INTO research_run_snapshots (
        research_run_id, strategy_id, strategy_version, strategy_definition_hash,
        strategy_configuration, dataset_version, dataset_hash,
        feature_schema_version, model_version, model_hash, rule_matrix_version,
        runtime_configuration_version, backtest_engine_version,
        validation_engine_version, random_seed, research_prompt_version,
        engine_version, configuration_hash, research_hash, captured_at,
        feature_schema_id, feature_dimension, model_id, git_commit
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(research_run_id) DO NOTHING;
"""

_UPSERT_HEARTBEAT_SQL = """
    INSERT INTO research_worker_heartbeat (
        scope, last_beat_at, cycle_count, last_cycle_start,
        last_cycle_completion, last_cycle_duration_ms, last_action,
        current_job, current_strategy, current_gate, queued_jobs,
        failed_jobs, last_error, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(scope) DO UPDATE SET
        last_beat_at=excluded.last_beat_at,
        cycle_count=excluded.cycle_count,
        last_cycle_start=excluded.last_cycle_start,
        last_cycle_completion=excluded.last_cycle_completion,
        last_cycle_duration_ms=excluded.last_cycle_duration_ms,
        last_action=excluded.last_action,
        current_job=excluded.current_job,
        current_strategy=excluded.current_strategy,
        current_gate=excluded.current_gate,
        queued_jobs=excluded.queued_jobs,
        failed_jobs=excluded.failed_jobs,
        last_error=excluded.last_error,
        status=excluded.status;
"""


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).astimezone(UTC)
    except Exception:
        return None


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _registry_blocked_reason(
    repo: AuditRepository, registry_entry: dict[str, Any]
) -> dict[str, Any]:
    """Resolves WHY a strategy has not moved (spec 7 / 58).

    Returns {blocked: bool, current_gate, status, reason, required}.
    A DISCOVERED strategy with no completed gates has an explicit reason:
    no validation run has started (dataset/worker state is the cause).
    """
    sid = registry_entry.get("strategy_id", "")
    lifecycle = str(registry_entry.get("lifecycle", ""))
    blocker: dict[str, Any] = {
        "blocked": False,
        "current_gate": "",
        "status": "",
        "reason": "",
        "required": "",
    }
    if not repo._is_sqlite:
        return blocker
    try:
        conn = _connect(repo)
        try:
            row = conn.execute(
                "SELECT gate_type, status, failure_reason, result "
                "FROM research_gates WHERE strategy_id=? "
                "ORDER BY order_index DESC, completed_at DESC LIMIT 1;",
                (sid,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            gt = str(row["gate_type"])
            st = str(row["status"])
            if st in ("FAILED", "ERROR", "BLOCKED"):
                blocker.update(
                    {
                        "blocked": True,
                        "current_gate": gt,
                        "status": st,
                        "reason": str(row["failure_reason"] or ""),
                        "required": str(_read_json(row["result"]).get("required") or ""),
                    }
                )
                return blocker
            if st == "RUNNING":
                blocker.update(
                    {
                        "blocked": False,
                        "current_gate": gt,
                        "status": "RUNNING",
                        "reason": "validation currently in progress",
                    }
                )
                return blocker
        if lifecycle in ("DISCOVERED", "BACKTESTING", "VALIDATING"):
            blocker.update(
                {
                    "blocked": False,
                    "current_gate": "BACKTEST",
                    "status": "NOT_STARTED",
                    "reason": "no validation run recorded for this strategy yet",
                    "required": "run /api/research/validate to start the gate chain",
                }
            )
        return blocker
    except Exception as e:
        logger.error("[RESEARCH_OBS] blocked-reason resolve failed", strategy=sid, error=str(e))
        return blocker
