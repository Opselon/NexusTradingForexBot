"""
Training Dataset Builder
========================
PHASE 10 deterministic, causally-safe training dataset construction
(spec 7 / 8 / 10 / 38).

The builder consumes the immutable Phase 08 experience ledger (NEVER raw
database rows) and produces a deterministic `TrainingDataset` artifact that:

* preserves sample identity, decision timestamp, feature schema + vector,
  label, strategy context, regime, symbol, timeframe, session, sample weight
  and provenance back to the source experience;
* represents ALL outcome classes (wins, losses, neutral, rejected decisions,
  bad-execution and large-loss cases) so the model never trains on winners
  only (spec 8);
* enforces strict temporal causality - `as_of` never leaks future outcomes;
* has a deterministic identity: the same input data + configuration yields the
  same `dataset_id` (spec 7 / 13).

Labelling contract (spec 9): labels are derived from the EXISTING model
contract (TripleBarrier 3-class 0=NO_TRADE / 1=BUY / 2=SELL), never invented.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ExperienceRecord
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.model_lifecycle.models import TrainingDataset, TrainingDatasetRow
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.dataset")

#: Label mapping matches the TripleBarrierLabeler + WalkForwardTrainer contract.
LABEL_MAP: dict[str, int] = {
    ActionType.NO_TRADE.value: 0,
    ActionType.BUY_MARKET.value: 1,
    ActionType.SELL_MARKET.value: 2,
}


def resolve_schema(schema_id: str | None = None):
    """Resolves a feature schema id (defaults to the active schema)."""

    return FEATURE_SCHEMAS.resolve(schema_id)


class TrainingDatasetBuilder:
    """Builds deterministic training datasets from the experience ledger."""

    def __init__(self, ledger: ExperienceLedger) -> None:
        self.ledger = ledger

    def _label_for(self, rec: ExperienceRecord) -> int:
        """Resolves the training label from the recorded decision action."""
        return LABEL_MAP.get(str(rec.action), 0)

    def _row_from_record(
        self,
        rec: ExperienceRecord,
        label: int,
        dataset_id: str,
        include_no_trade: bool,
        weight_no_trade: float,
    ) -> TrainingDatasetRow | None:
        """Builds one typed row preserving full provenance (spec 7)."""
        values = rec.feature_snapshot.values
        dim = rec.feature_dimension
        if not values:
            # No feature snapshot available: cannot train on this sample.
            return None
        if len(values) != dim:
            logger.warning(
                "[TRAINING_DATASET] sample schema mismatch, skipped",
                experience_id=rec.experience_id,
                declared=dim,
                actual=len(values),
            )
            return None

        sample_id = f"ts_{hashlib.sha256(rec.idempotency_key.encode()).hexdigest()[:16]}"

        weight = 1.0
        if label == 0:
            if not include_no_trade:
                return None
            weight = weight_no_trade

        return TrainingDatasetRow(
            sample_id=sample_id,
            experience_id=rec.experience_id,
            idempotency_key=rec.idempotency_key,
            decision_timestamp=rec.decision_timestamp,
            feature_schema_id=rec.feature_schema_id,
            feature_dimension=dim,
            feature_vector=values,
            label=label,
            label_str=LABEL_STR.get(label, str(label)),
            strategy_id=rec.strategy_id,
            strategy_version=rec.strategy_version,
            regime=rec.context.regime,
            symbol=rec.symbol,
            timeframe=rec.timeframe,
            session=rec.context.session,
            sample_weight=weight,
            outcome_r=rec.realized_r_multiple,
            is_executed=rec.is_executed,
            is_closed=rec.is_closed,
            exit_reason=rec.exit_reason,
        )

    def build(
        self,
        include_no_trade: bool = True,
        weight_no_trade: float = 0.25,
        as_of: datetime | None = None,
        only_executed: bool = True,
        dataset_version: str = "1.0.0",
        config: dict[str, Any] | None = None,
    ) -> TrainingDataset:
        """
        Builds the deterministic training dataset.

        Parameters:
            include_no_trade: include NO_TRADE decisions (label 0) as negative
                examples. Losses and wins are ALWAYS included.
            weight_no_trade: sample weight applied to NO_TRADE rows (they are
                the dominant class in scalping data).
            as_of: causality wall - only decisions strictly BEFORE this
                timestamp enter the dataset (no future leakage).
            only_executed: when True, only executed experiences with outcomes
                are eligible as labeled training examples.

        Label contract: NO_TRADE=0 / BUY=1 / SELL=2 (TripleBarrier contract).
        """
        cfg = {
            "include_no_trade": include_no_trade,
            "weight_no_trade": weight_no_trade,
            "as_of": as_of.isoformat() if as_of else "",
            "only_executed": only_executed,
            "dataset_version": dataset_version,
        }
        if config:
            cfg.update(config)

        rows: list[TrainingDatasetRow] = []
        source_experience_ids: list[str] = []
        strategy_ids = self.ledger.list_strategy_ids()
        for sid in strategy_ids:
            for rec in self.ledger.get_experiences_for_strategy(sid, limit=10000):
                if as_of is not None and rec.decision_timestamp >= as_of:
                    continue
                label = self._label_for(rec)
                if rec.is_executed and not rec.is_closed:
                    continue  # open positions are not labeled evidence yet
                if only_executed and not rec.is_executed:
                    # A never-executed decision only enters the dataset as a
                    # NO_TRADE / rejected decision sample when explicitly wanted.
                    if not (include_no_trade and label == 0):
                        continue

                row = self._row_from_record(rec, label, "", include_no_trade, weight_no_trade)
                if row is None:
                    continue
                rows.append(row)
                source_experience_ids.append(rec.experience_id)

        rows.sort(key=lambda r: r.decision_timestamp)
        dataset_id = _dataset_id(rows, cfg)
        source_range: dict[str, str] = {}
        if rows:
            source_range = {
                "start": rows[0].decision_timestamp.isoformat(),
                "end": rows[-1].decision_timestamp.isoformat(),
            }
        return TrainingDataset(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            feature_schema_id=resolve_schema().schema_id,
            feature_dimension=resolve_schema().dimension,
            created_at=datetime.now(UTC),
            rows=rows,
            source_experience_ids=source_experience_ids,
            source_range=source_range,
            config_hash=hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16],
        )


#: Human label names mirroring the trainer's inverse label map.
LABEL_STR: dict[int, str] = {
    0: ActionType.NO_TRADE.value,
    1: ActionType.BUY_MARKET.value,
    2: ActionType.SELL_MARKET.value,
}


def _dataset_id(rows: list[TrainingDatasetRow], cfg: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(cfg, sort_keys=True).encode())
    for r in rows:
        digest.update(f"{r.idempotency_key}|{int(r.decision_timestamp.timestamp())}".encode())
    return f"td_{digest.hexdigest()[:16]}"


def validate_no_future_leakage(
    dataset: TrainingDataset, as_of: datetime, label: str = "training dataset"
) -> None:
    """Hard invariant: no decision in the dataset is at/after `as_of`."""
    for r in dataset.rows:
        if r.decision_timestamp >= as_of:
            raise ValueError(
                f"Leakage violation in {label}: sample {r.sample_id} decision "
                f"{r.decision_timestamp.isoformat()} is not strictly before as_of "
                f"{as_of.isoformat()}"
            )
