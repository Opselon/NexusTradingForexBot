"""
Experience Ledger Implementation
================================
Manages immutable recording, deduplication, retrieval, and updating of
ExperienceRecords for Phase 08 Experience Intelligence.
"""

import hashlib
import json
from datetime import datetime

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import ExperienceRecord, StrategyContext
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.experience.ledger")


class ExperienceLedger:
    """
    Append-oriented, immutable Experience Ledger maintaining historical trade experiences.
    """

    def __init__(self, audit_repo: AuditRepository) -> None:
        self.audit_repo = audit_repo

    def record_experience(self, record: ExperienceRecord) -> bool:
        """
        Records a new immutable experience into the ledger via async queue.
        Enforces deduplication using the deterministic idempotency_key.
        """
        if not self.audit_repo._is_sqlite:
            return False

        # Build raw dict payload
        record_dict = json.loads(record.model_dump_json())

        query = """
            INSERT INTO audit_experiences
            (experience_id, request_id, execution_id, decision_id, idempotency_key,
             symbol, timeframe, strategy_id, strategy_version, decision_timestamp, outcome_timestamp,
             action, entry_reason, model_probability, signal_confidence, proposed_entry, stop_loss,
             take_profit, risk_reward_ratio, approved_volume, is_executed, is_closed, exit_reason,
             realized_pnl_usd, realized_r_multiple, mae_points, mfe_points, mae_usd, mfe_usd,
             holding_duration_seconds, feature_hash, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING;
        """

        outcome_ts_str = record.outcome_timestamp.isoformat() if record.outcome_timestamp else None

        args = (
            record.experience_id,
            record.request_id,
            record.execution_id,
            record.decision_id,
            record.idempotency_key,
            record.symbol,
            record.timeframe,
            record.strategy_id,
            record.strategy_version,
            record.decision_timestamp.isoformat(),
            outcome_ts_str,
            record.action,
            record.entry_reason,
            record.model_probability,
            record.signal_confidence,
            record.proposed_entry,
            record.stop_loss,
            record.take_profit,
            record.risk_reward_ratio,
            record.approved_volume,
            1 if record.is_executed else 0,
            1 if record.is_closed else 0,
            record.exit_reason,
            record.realized_pnl_usd,
            record.realized_r_multiple,
            record.mae_points,
            record.mfe_points,
            record.mae_usd,
            record.mfe_usd,
            record.holding_duration_seconds,
            record.feature_hash,
            json.dumps(record_dict),
        )

        try:
            self.audit_repo._queue.put_nowait((query, args))
            logger.debug(
                "Experience record queued",
                experience_id=record.experience_id,
                strategy_id=record.strategy_id,
            )
            return True
        except Exception as e:
            logger.error("Failed to queue experience record", error=str(e))
            return False

    def update_experience_outcome(
        self,
        idempotency_key: str,
        outcome_timestamp: datetime,
        is_executed: bool,
        is_closed: bool,
        exit_reason: str,
        realized_pnl_usd: float,
        realized_r_multiple: float,
        mae_points: float = 0.0,
        mfe_points: float = 0.0,
        mae_usd: float = 0.0,
        mfe_usd: float = 0.0,
        holding_duration_seconds: float = 0.0,
    ) -> bool:
        """
        Causally updates post-trade outcomes for an existing recorded experience.
        Guarantees temporal causality (outcome_timestamp >= decision_timestamp).
        """
        if not self.audit_repo._is_sqlite:
            return False

        query = """
            UPDATE audit_experiences
            SET outcome_timestamp = ?,
                is_executed = ?,
                is_closed = ?,
                exit_reason = ?,
                realized_pnl_usd = ?,
                realized_r_multiple = ?,
                mae_points = ?,
                mfe_points = ?,
                mae_usd = ?,
                mfe_usd = ?,
                holding_duration_seconds = ?
            WHERE idempotency_key = ?;
        """

        args = (
            outcome_timestamp.isoformat(),
            1 if is_executed else 0,
            1 if is_closed else 0,
            exit_reason,
            float(realized_pnl_usd),
            float(realized_r_multiple),
            float(mae_points),
            float(mfe_points),
            float(mae_usd),
            float(mfe_usd),
            float(holding_duration_seconds),
            idempotency_key,
        )

        try:
            self.audit_repo._queue.put_nowait((query, args))
            logger.debug("Experience outcome update queued", idempotency_key=idempotency_key)
            return True
        except Exception as e:
            logger.error("Failed to queue experience outcome update", error=str(e))
            return False

    def get_experiences_for_strategy(
        self,
        strategy_id: str,
        limit: int = 500,
        before_timestamp: datetime | None = None,
    ) -> list[ExperienceRecord]:
        """
        Retrieves causally valid historical experience records for a specific strategy.
        Optionally filters by `before_timestamp` to prevent lookahead leakage.
        """
        if not self.audit_repo._is_sqlite:
            return []

        import sqlite3

        records = []
        try:
            with sqlite3.connect(self.audit_repo._db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                if before_timestamp is not None:
                    query = """
                        SELECT payload FROM audit_experiences
                        WHERE strategy_id = ? AND decision_timestamp < ?
                        ORDER BY decision_timestamp DESC LIMIT ?;
                    """
                    cursor = conn.execute(query, (strategy_id, before_timestamp.isoformat(), limit))
                else:
                    query = """
                        SELECT payload FROM audit_experiences
                        WHERE strategy_id = ?
                        ORDER BY decision_timestamp DESC LIMIT ?;
                    """
                    cursor = conn.execute(query, (strategy_id, limit))

                for row in cursor.fetchall():
                    raw_payload = row["payload"]
                    if raw_payload:
                        data = json.loads(raw_payload)
                        records.append(ExperienceRecord.model_validate(data))
        except Exception as e:
            logger.error(
                "Failed to retrieve experiences for strategy",
                strategy_id=strategy_id,
                error=str(e),
            )

        return records

    @staticmethod
    def compute_feature_hash(feature_vector_50d: list[float]) -> str:
        """Computes deterministic SHA256 string fingerprint for 50D feature vector."""
        formatted = ",".join(f"{v:.6f}" for v in feature_vector_50d)
        return hashlib.sha256(formatted.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def generate_strategy_id(context: StrategyContext) -> str:
        """Generates deterministic strategy fingerprint ID from context properties."""
        raw_key = (
            f"{context.symbol}_{context.timeframe}_{context.session}_"
            f"{context.regime}_{context.volatility_regime}_{context.trend_state}_"
            f"{context.confluence_fingerprint}"
        )
        return f"strat_{hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:12]}"
