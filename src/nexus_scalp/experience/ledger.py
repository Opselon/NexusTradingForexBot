"""
Immutable Experience Ledger
===========================
Phase 08 long-term memory persistence.

Storage model (three tables, all owned by the existing `AuditRepository`; no
second persistence system is introduced):

    audit_experiences            immutable decision rows, UNIQUE(idempotency_key)
    audit_experience_outcomes    append-only outcome events, UNIQUE(idempotency_key)
    audit_experience_corrections additive correction events

CRITICAL INVARIANTS
-------------------
1. A decision row is written exactly once (`ON CONFLICT DO NOTHING`) and is
   NEVER updated. Outcomes live in their own table, which is why duplicate
   broker close callbacks, reconnect replays and startup replays cannot inflate
   evidence.
2. Retrieval merges decision + outcome server-side and reconstructs a fully
   typed `ExperienceRecord`, so evaluators always see the real outcome. (The
   first Phase 08 revision updated only scalar columns while retrieval read the
   frozen JSON payload, which silently produced zero closed samples - see
   agents/bugs.md BUG-008.)
3. Retrieval is always bounded (LIMIT) and always causally filtered
   (`decision_timestamp < before_timestamp`) when a decision time is supplied.
4. Raw rows are never deleted. Corrections are additive events.
5. The ledger never touches the model artifact and never imports torch.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import (
    CANONICAL_FEATURE_DIMENSION,
    CANONICAL_FEATURE_SCHEMA_ID,
    ExperienceCorrection,
    ExperienceOutcome,
    ExperienceRecord,
    StrategyContext,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.ledger")

#: Hard cap on any single retrieval so a corrupt caller cannot scan the table.
MAX_RETRIEVAL_LIMIT: int = 2000

_INSERT_EXPERIENCE_SQL = """
    INSERT INTO audit_experiences
    (experience_id, request_id, execution_id, decision_id, idempotency_key,
     correction_of, record_version, symbol, timeframe, strategy_id, strategy_version,
     decision_timestamp, action, entry_reason, model_probability, signal_confidence,
     proposed_entry, stop_loss, take_profit, risk_reward_ratio, min_rr_policy,
     feature_schema_id, feature_dimension, feature_hash,
     model_id, model_version, config_version, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(idempotency_key) DO NOTHING;
"""

_INSERT_OUTCOME_SQL = """
    INSERT INTO audit_experience_outcomes
    (idempotency_key, execution_id, outcome_timestamp, is_executed, is_closed,
     exit_reason, realized_pnl_usd, realized_r_multiple, approved_volume,
     mae_points, mfe_points, mae_usd, mfe_usd, mae_r, mfe_r,
     holding_duration_seconds, slippage_points, execution_latency_ms,
     strategy_quality, entry_quality, execution_quality, management_quality,
     exit_quality, behavioral_flags, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(idempotency_key) DO NOTHING;
"""

#: BUG-046 repair path: UPDATEs ONLY the derived outcome layer (never the
#: immutable decision row). Used exclusively by the historical outcome repair
#: job to replace a zero/corrupt realized result with broker-reconstructed
#: truth. Idempotent by key; the payload carries repair provenance.
_REPAIR_OUTCOME_SQL = """
    UPDATE audit_experience_outcomes SET
        execution_id = ?,
        outcome_timestamp = ?,
        is_executed = ?,
        is_closed = ?,
        exit_reason = ?,
        realized_pnl_usd = ?,
        realized_r_multiple = ?,
        approved_volume = ?,
        mae_points = ?,
        mfe_points = ?,
        mae_usd = ?,
        mfe_usd = ?,
        mae_r = ?,
        mfe_r = ?,
        holding_duration_seconds = ?,
        slippage_points = ?,
        execution_latency_ms = ?,
        strategy_quality = ?,
        entry_quality = ?,
        execution_quality = ?,
        management_quality = ?,
        exit_quality = ?,
        behavioral_flags = ?,
        payload = ?
    WHERE idempotency_key = ?
"""

#: Merged projection used by every retrieval path. LEFT JOIN keeps decisions
#: that have no outcome yet (rejected / still open) visible to forensics.
_SELECT_MERGED = """
    SELECT e.payload AS decision_payload, o.payload AS outcome_payload
    FROM audit_experiences e
    LEFT JOIN audit_experience_outcomes o
        ON o.idempotency_key = e.idempotency_key
"""


class ExperienceLedger:
    """
    Append-only experience store.

    Writes go through the AuditRepository background queue so the live tick path
    never performs disk I/O. Reads use short-lived read-only connections with a
    bounded timeout.
    """

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo
        #: Counters exposed for observability and tests.
        self.recorded_count: int = 0
        self.duplicate_count: int = 0
        self.rejected_count: int = 0
        self.outcome_count: int = 0

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record_experience(self, record: ExperienceRecord) -> bool:
        """
        Queues an immutable decision experience.

        Returns True when the row was queued. Deduplication is enforced by the
        UNIQUE constraint on `idempotency_key`, so a replayed event is a no-op
        at the database level rather than a second learning sample.
        """
        if not self.audit_repo._is_sqlite:
            return False

        payload = record.model_dump_json()
        args = (
            record.experience_id,
            record.request_id,
            record.execution_id,
            record.decision_id,
            record.idempotency_key,
            record.correction_of,
            record.record_version,
            record.symbol,
            record.timeframe,
            record.strategy_id,
            record.strategy_version,
            record.decision_timestamp.isoformat(),
            record.action,
            record.entry_reason,
            record.model_probability,
            record.signal_confidence,
            record.proposed_entry,
            record.stop_loss,
            record.take_profit,
            record.risk_reward_ratio,
            record.min_rr_policy,
            record.feature_snapshot.feature_schema_id,
            record.feature_snapshot.feature_dimension,
            record.feature_snapshot.feature_hash,
            record.provenance.model_id,
            record.provenance.model_version,
            record.provenance.config_version,
            payload,
        )

        try:
            self.audit_repo._queue.put_nowait((_INSERT_EXPERIENCE_SQL, args))
            self.recorded_count += 1
            logger.debug(
                "[EXPERIENCE] RECORDED",
                experience_id=record.experience_id,
                strategy_id=record.strategy_id,
                feature_schema=record.feature_snapshot.feature_schema_id,
                feature_dim=record.feature_snapshot.feature_dimension,
            )
            return True
        except Exception as e:
            self.rejected_count += 1
            logger.error("[EXPERIENCE] INVALID queue failure", error=str(e))
            return False

    def record_outcome(self, outcome: ExperienceOutcome) -> bool:
        """
        Queues an append-only outcome event for an existing decision.

        Idempotent by construction: a second close callback for the same
        `idempotency_key` is discarded by the UNIQUE constraint, so realised
        PnL can never be double-counted.
        """
        if not self.audit_repo._is_sqlite:
            return False

        d = outcome.decomposition
        b = outcome.behavior
        args = (
            outcome.idempotency_key,
            outcome.execution_id,
            outcome.outcome_timestamp.isoformat(),
            1 if outcome.is_executed else 0,
            1 if outcome.is_closed else 0,
            outcome.exit_reason,
            float(outcome.realized_pnl_usd),
            float(outcome.realized_r_multiple),
            float(outcome.approved_volume),
            float(b.mae_points),
            float(b.mfe_points),
            float(b.mae_usd),
            float(b.mfe_usd),
            float(b.mae_r),
            float(b.mfe_r),
            float(b.duration_sec),
            float(outcome.execution.slippage_points),
            float(outcome.execution.latency_ms),
            float(d.strategy_quality),
            float(d.entry_quality),
            float(d.execution_quality),
            float(d.position_management_quality),
            float(d.exit_quality),
            ",".join(f.value for f in outcome.behavioral_flags),
            outcome.model_dump_json(),
        )

        try:
            self.audit_repo._queue.put_nowait((_INSERT_OUTCOME_SQL, args))
            self.outcome_count += 1
            logger.debug(
                "[EXPERIENCE] OUTCOME RECORDED",
                idempotency_key=outcome.idempotency_key,
                realized_r=round(outcome.realized_r_multiple, 3),
                flags=[f.value for f in outcome.behavioral_flags],
            )
            return True
        except Exception as e:
            logger.error("[EXPERIENCE] OUTCOME queue failure", error=str(e))
            return False

    def repair_outcome(self, outcome: ExperienceOutcome, repair_reason: str = "") -> bool:
        """
        BUG-046: corrects a previously-recorded OUTCOME (derived layer only).

        The immutable decision row in `audit_experiences` is NEVER modified.
        This updates the outcome row (unique on idempotency_key) with
        broker-reconstructed truth and stamps repair provenance into the
        payload. Idempotent: repairing the same key twice converges to the
        same value; it never duplicates rows or double-counts PnL.

        Returns True when the repair write was queued.
        """
        if not self.audit_repo._is_sqlite:
            return False

        d = outcome.decomposition
        b = outcome.behavior
        args = (
            outcome.execution_id,
            outcome.outcome_timestamp.isoformat(),
            1 if outcome.is_executed else 0,
            1 if outcome.is_closed else 0,
            outcome.exit_reason,
            float(outcome.realized_pnl_usd),
            float(outcome.realized_r_multiple),
            float(outcome.approved_volume),
            float(b.mae_points),
            float(b.mfe_points),
            float(b.mae_usd),
            float(b.mfe_usd),
            float(b.mae_r),
            float(b.mfe_r),
            float(b.duration_sec),
            float(outcome.execution.slippage_points),
            float(outcome.execution.latency_ms),
            float(d.strategy_quality),
            float(d.entry_quality),
            float(d.execution_quality),
            float(d.position_management_quality),
            float(d.exit_quality),
            ",".join(f.value for f in outcome.behavioral_flags),
            outcome.model_dump_json(),
            outcome.idempotency_key,
        )
        try:
            self.audit_repo._queue.put_nowait((_REPAIR_OUTCOME_SQL, args))
            logger.info(
                "[BROKER_OUTCOME_REPAIR] event=REPAIRED",
                idempotency_key=outcome.idempotency_key,
                realized_r=round(outcome.realized_r_multiple, 4),
                realized_pnl=round(outcome.realized_pnl_usd, 2),
                reason=repair_reason or "",
            )
            return True
        except Exception as e:
            logger.error("[EXPERIENCE] OUTCOME repair queue failure", error=str(e))
            return False

    def record_correction(self, correction: ExperienceCorrection) -> bool:
        """
        Appends a correction event. Historical rows are never overwritten - the
        correction is evidence in its own right.
        """
        if not self.audit_repo._is_sqlite:
            return False

        query = """
            INSERT INTO audit_experience_corrections
            (correction_id, idempotency_key, corrected_at, reason, field_name,
             old_value, new_value)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        args = (
            correction.correction_id,
            correction.idempotency_key,
            correction.corrected_at.isoformat(),
            correction.reason,
            correction.field_name,
            correction.old_value,
            correction.new_value,
        )
        try:
            self.audit_repo._queue.put_nowait((query, args))
            logger.info(
                "[EXPERIENCE] CORRECTION recorded",
                idempotency_key=correction.idempotency_key,
                field=correction.field_name,
                reason=correction.reason,
            )
            return True
        except Exception as e:
            logger.error("[EXPERIENCE] CORRECTION queue failure", error=str(e))
            return False

    def build_correction(
        self,
        idempotency_key: str,
        reason: str,
        field_name: str,
        old_value: str = "",
        new_value: str = "",
    ) -> ExperienceCorrection:
        """Factory producing a correction event with a unique id."""
        return ExperienceCorrection(
            correction_id=f"corr_{uuid.uuid4().hex[:12]}",
            idempotency_key=idempotency_key,
            reason=reason,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
        )

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def _connect(self, timeout: float = 5.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self.audit_repo._db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _merge_row(row: sqlite3.Row) -> ExperienceRecord | None:
        """
        Rebuilds a typed `ExperienceRecord` from the merged decision + outcome
        payloads. Legacy revision-1 payloads are migrated by the model
        validator, so old rows remain readable.
        """
        raw_decision = row["decision_payload"]
        if not raw_decision:
            return None
        try:
            record = ExperienceRecord.model_validate(json.loads(raw_decision))
        except Exception as e:
            logger.error("[EXPERIENCE] INVALID payload skipped", error=str(e))
            return None

        raw_outcome = row["outcome_payload"] if "outcome_payload" in row.keys() else None
        if not raw_outcome:
            return record
        try:
            outcome = ExperienceOutcome.model_validate(json.loads(raw_outcome))
        except Exception as e:
            logger.error(
                "[EXPERIENCE] INVALID outcome payload skipped",
                idempotency_key=record.idempotency_key,
                error=str(e),
            )
            return record
        if outcome.outcome_timestamp < record.decision_timestamp:
            # Defensive: an outcome that predates its decision is not evidence.
            logger.error(
                "[EXPERIENCE] CAUSALITY_REJECTED outcome precedes decision",
                idempotency_key=record.idempotency_key,
            )
            return record
        return record.with_outcome(outcome)

    def _query_records(
        self, where: str, args: tuple[Any, ...], limit: int
    ) -> list[ExperienceRecord]:
        """Runs a bounded merged query and returns typed records."""
        if not self.audit_repo._is_sqlite:
            return []

        bounded = max(1, min(int(limit), MAX_RETRIEVAL_LIMIT))
        sql = f"{_SELECT_MERGED} WHERE {where} ORDER BY e.decision_timestamp DESC LIMIT ?;"
        records: list[ExperienceRecord] = []
        try:
            conn = self._connect()
            try:
                for row in conn.execute(sql, (*args, bounded)).fetchall():
                    merged = self._merge_row(row)
                    if merged is not None:
                        records.append(merged)
            finally:
                conn.close()
        except Exception as e:
            logger.error("[EXPERIENCE] retrieval failed", error=str(e))
            return []
        return records

    def get_experiences_for_strategy(
        self,
        strategy_id: str,
        limit: int = 500,
        before_timestamp: datetime | None = None,
    ) -> list[ExperienceRecord]:
        """
        Bounded, causally-filtered retrieval for one strategy family.

        When `before_timestamp` is supplied, only experiences whose DECISION
        happened strictly earlier are returned - future outcomes can never
        influence a past decision.
        """
        if before_timestamp is not None:
            return self._query_records(
                "e.strategy_id = ? AND e.decision_timestamp < ?",
                (strategy_id, before_timestamp.isoformat()),
                limit,
            )
        return self._query_records("e.strategy_id = ?", (strategy_id,), limit)

    def get_experiences_for_symbol(
        self,
        symbol: str,
        limit: int = 200,
        before_timestamp: datetime | None = None,
    ) -> list[ExperienceRecord]:
        """Bounded generalized retrieval used for hierarchical similarity matching."""
        if before_timestamp is not None:
            return self._query_records(
                "e.symbol = ? AND e.decision_timestamp < ?",
                (symbol, before_timestamp.isoformat()),
                limit,
            )
        return self._query_records("e.symbol = ?", (symbol,), limit)

    def get_experience_by_key(self, idempotency_key: str) -> ExperienceRecord | None:
        """Fetches a single merged experience by its idempotency key."""
        rows = self._query_records("e.idempotency_key = ?", (idempotency_key,), 1)
        return rows[0] if rows else None

    def get_experiences_by_order_id(
        self, request_id: str, limit: int = 20
    ) -> list[ExperienceRecord]:
        """
        Phase 14 POSITION_STATE correlation fallback: retrieves decision
        experiences carrying a given request_id in ANY identifier column
        (request_id, decision_id, execution_id, experience_id).

        Used when the order manager lost its in-memory ticket->request_id map
        (restart / reconciliation) but the immutable ledger still holds the
        originating decision. Never fabricates an identity: the caller logs
        which fallback matched.
        """
        if not request_id:
            return []
        return self._query_records(
            "e.request_id = ? OR e.decision_id = ? OR e.execution_id = ? OR e.experience_id = ?",
            (request_id, request_id, request_id, request_id),
            limit,
        )

    def has_outcome(self, idempotency_key: str) -> bool:
        """True when an outcome event already exists for this experience."""
        if not self.audit_repo._is_sqlite:
            return False
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM audit_experience_outcomes WHERE idempotency_key = ? LIMIT 1;",
                    (idempotency_key,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception as e:
            logger.error("[EXPERIENCE] outcome lookup failed", error=str(e))
            return False

    def count_recent_entries_for_strategy(
        self, strategy_id: str, before_timestamp: datetime, window_seconds: float
    ) -> int:
        """
        Counts executed entries in the same strategy family inside a trailing
        window. Used for the objective REENTRY_OVERTRADING measurement.
        """
        if not self.audit_repo._is_sqlite:
            return 0
        from datetime import timedelta

        window_start = before_timestamp - timedelta(seconds=max(1.0, window_seconds))
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM audit_experiences e
                    WHERE e.strategy_id = ?
                      AND e.decision_timestamp >= ?
                      AND e.decision_timestamp < ?;
                    """,
                    (
                        strategy_id,
                        window_start.isoformat(),
                        before_timestamp.isoformat(),
                    ),
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception as e:
            logger.error("[EXPERIENCE] reentry count failed", error=str(e))
            return 0

    def list_strategy_ids(self, limit: int = 5000) -> list[str]:
        """Distinct strategy families present in the immutable ledger."""
        if not self.audit_repo._is_sqlite:
            return []
        try:
            conn = self._connect(timeout=10.0)
            try:
                rows = conn.execute(
                    "SELECT DISTINCT strategy_id FROM audit_experiences LIMIT ?;",
                    (max(1, int(limit)),),
                ).fetchall()
                return [r["strategy_id"] for r in rows if r["strategy_id"]]
            finally:
                conn.close()
        except Exception as e:
            logger.error("[EXPERIENCE] strategy id enumeration failed", error=str(e))
            return []

    def count_experiences(self) -> int:
        """Total immutable decision rows."""
        if not self.audit_repo._is_sqlite:
            return 0
        try:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) FROM audit_experiences;").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception:
            return 0

    def get_schema_distribution(self) -> dict[str, int]:
        """
        Feature-schema census across the ledger.

        Proves at runtime that historical schemas are preserved rather than
        rewritten when the live contract widens.
        """
        if not self.audit_repo._is_sqlite:
            return {}
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT feature_schema_id, feature_dimension, COUNT(*) AS n
                    FROM audit_experiences
                    GROUP BY feature_schema_id, feature_dimension;
                    """
                ).fetchall()
                return {
                    f"{r['feature_schema_id']}/{r['feature_dimension']}D": int(r["n"]) for r in rows
                }
            finally:
                conn.close()
        except Exception as e:
            logger.error("[EXPERIENCE] schema census failed", error=str(e))
            return {}

    # ------------------------------------------------------------------
    # Deterministic identity helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_feature_hash(
        feature_values: list[float],
        feature_schema_id: str = CANONICAL_FEATURE_SCHEMA_ID,
    ) -> str:
        """
        Deterministic fingerprint of a feature snapshot.

        The schema id and dimension are folded into the digest so an identical
        numeric prefix under a different schema cannot collide with a 50D
        record.
        """
        formatted = ",".join(f"{float(v):.6f}" for v in feature_values)
        raw = f"{feature_schema_id}:{len(feature_values)}:{formatted}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def generate_strategy_id(context: StrategyContext) -> str:
        """
        Deterministic strategy-family fingerprint.

        Deliberately built from BOUNDED context tokens only (symbol, timeframe,
        session, regime, volatility bucket, trend state, setup type, confluence
        digest) so that experiences aggregate into families instead of creating
        one strategy per unique float vector.
        """
        raw_key = (
            f"{context.symbol}|{context.timeframe}|{context.session}|"
            f"{context.regime}|{context.volatility_regime}|{context.trend_state}|"
            f"{context.setup_type}|{context.confluence_fingerprint}|{context.parameter_hash}"
        )
        return f"strat_{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:12]}"

    @staticmethod
    def canonical_feature_dimension() -> int:
        """Current live feature contract dimensionality."""
        return CANONICAL_FEATURE_DIMENSION
