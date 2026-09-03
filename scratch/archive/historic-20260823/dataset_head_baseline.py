"""
Deterministic Research Dataset Builder
======================================
PHASE 09B consumes the immutable Phase 08 experience ledger (NOT a parallel
trade database) and produces a causally-safe `ResearchDataset`.

Guarantees (spec 5 / 6 / 7):
  * Every sample preserves decision_timestamp, experience_id, symbol, timeframe,
    strategy_id/version, feature_schema_id/dimension, regime, session, context,
    risk, execution and outcome provenance.
  * Causal ordering is preserved: samples are sorted by decision_timestamp.
  * Only EXECUTED + CLOSED experiences enter research.
  * Leakage guard: `build(as_of=...)` never includes samples whose DECISION
    happened at or after `as_of` (future outcomes can never be used).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import ExperienceRecord
from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import ResearchDataset, ResearchSample

logger = get_logger("nexus_scalp.research.dataset")


def _sample_id(rec: ExperienceRecord) -> str:
    key = rec.idempotency_key or rec.experience_id
    return f"rs_{hashlib.sha256(key.encode()).hexdigest()[:16]}"


class ResearchDatasetBuilder:
    """Builds deterministic research datasets from the experience ledger."""

    def __init__(self, ledger: ExperienceLedger) -> None:
        self.ledger = ledger

    def _to_sample(self, rec: ExperienceRecord) -> ResearchSample:
        ctx = rec.context
        return ResearchSample(
            sample_id=_sample_id(rec),
            experience_id=rec.experience_id,
            idempotency_key=rec.idempotency_key,
            decision_timestamp=rec.decision_timestamp,
            outcome_timestamp=rec.outcome_timestamp or rec.decision_timestamp,
            symbol=rec.symbol,
            timeframe=rec.timeframe,
            strategy_id=rec.strategy_id,
            strategy_version=rec.strategy_version,
            feature_schema_id=rec.feature_schema_id,
            feature_dimension=rec.feature_dimension,
            regime=ctx.regime,
            session=ctx.session,
            volatility_regime=ctx.volatility_regime,
            trend_state=ctx.trend_state,
            feature_hash=rec.feature_hash,
            context_fingerprint=ctx.confluence_fingerprint,
            entry_price=rec.proposed_entry,
            stop_loss=rec.stop_loss,
            take_profit=rec.take_profit,
            direction=rec.action,
            realized_r=rec.realized_r_multiple,
            realized_pnl_usd=rec.realized_pnl_usd,
            risk_distance=rec.planned_risk_distance,
            holding_duration_sec=rec.behavior.duration_sec,
            mae_r=rec.behavior.mae_r,
            mfe_r=rec.behavior.mfe_r,
            exit_reason=rec.exit_reason,
        )

    def build(self, dataset_id: str | None = None) -> ResearchDataset:
        """
        Builds the full research dataset from all closed experiences, causally
        ordered by decision_timestamp.
        """
        samples: list[ResearchSample] = []
        strategy_ids = self.ledger.list_strategy_ids()
        seen: set[str] = set()
        for sid in strategy_ids:
            for rec in self.ledger.get_experiences_for_strategy(sid, limit=10000):
                if not (rec.is_executed and rec.is_closed):
                    continue
                if rec.idempotency_key in seen:
                    continue
                seen.add(rec.idempotency_key)
                samples.append(self._to_sample(rec))
        samples.sort(key=lambda s: s.decision_timestamp)
        return self._dataset(dataset_id, samples)

    def build_for_strategy(
        self,
        strategy_id: str,
        dataset_id: str | None = None,
        as_of: datetime | None = None,
    ) -> ResearchDataset:
        """
        Builds a causally-safe dataset for one strategy family.

        When `as_of` is given, only samples whose DECISION happened strictly
        BEFORE `as_of` are included (spec 7 leakage guard). This lets the
        walk-forward / OOS pipeline construct train / validation splits that can
        never peek into the future.
        """
        records = self.ledger.get_experiences_for_strategy(strategy_id, limit=10000)
        samples: list[ResearchSample] = []
        for rec in records:
            if not (rec.is_executed and rec.is_closed):
                continue
            if as_of is not None and rec.decision_timestamp >= as_of:
                continue
            samples.append(self._to_sample(rec))
        samples.sort(key=lambda s: s.decision_timestamp)
        return self._dataset(dataset_id, samples)

    def _dataset(self, dataset_id: str | None, samples: list[ResearchSample]) -> ResearchDataset:
        did = dataset_id or _dataset_id(samples)
        source_range: dict[str, str] = {}
        if samples:
            source_range = {
                "start": samples[0].decision_timestamp.isoformat(),
                "end": samples[-1].decision_timestamp.isoformat(),
            }
        schema_ids = sorted({s.feature_schema_id for s in samples})
        return ResearchDataset(
            dataset_id=did,
            created_at=datetime.now(UTC),
            samples=samples,
            source_range=source_range,
            schema_ids=schema_ids,
        )


def _dataset_id(samples: list[ResearchSample]) -> str:
    if not samples:
        return f"ds_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    digest = hashlib.sha256()
    for s in samples:
        digest.update(f"{s.idempotency_key}|{int(s.decision_timestamp.timestamp())}".encode())
    return f"ds_{digest.hexdigest()[:16]}"


def dataset_provenance(dataset: ResearchDataset) -> dict[str, Any]:
    """Ligible lineage summary for a dataset (spec 26: research data versioning)."""
    schemas: dict[str, int] = {}
    for s in dataset.samples:
        schemas[s.feature_schema_id] = schemas.get(s.feature_schema_id, 0) + 1
    return {
        "dataset_id": dataset.dataset_id,
        "sample_count": len(dataset.samples),
        "source_range": dataset.source_range,
        "schema_ids": dataset.schema_ids,
        "schema_distribution": schemas,
        "strategy_ids": sorted({s.strategy_id for s in dataset.samples}),
    }
