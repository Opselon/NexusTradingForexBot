"""TASK-4 regression helpers: ledger seeding with authoritative broker outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.models import (
    ExecutionContext,
    ExperienceOutcome,
    ExperienceRecord,
    FeatureSnapshot,
    OutcomeDecomposition,
    PositionBehavior,
    StrategyContext,
)


def make_record(
    key: str,
    ts: datetime | None = None,
    strategy_id: str = "strat_fam",
    session: str = "LONDON",
    regime: str = "RANGING_MEAN_REVERSION",
    vol_regime: str = "NORMAL",
    trend: str = "BULLISH",
    schema_id: str = "scalp_v1",
    dimension: int = 50,
) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=f"exp_{key}",
        request_id=f"req_{key}",
        idempotency_key=key,
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=ts or datetime(2024, 1, 1, tzinfo=UTC),
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id=strategy_id,
            symbol="XAUUSD",
            session=session,
            regime=regime,
            volatility_regime=vol_regime,
            trend_state=trend,
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id=schema_id, feature_dimension=dimension, values=[0.0] * dimension
        ),
        action="BUY_MARKET",
        entry_reason="SMC",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )


def make_outcome(
    record: ExperienceRecord,
    r: float,
    broker_source: str = "BROKER_DEALS",
    exit_reason: str = "TP",
) -> ExperienceOutcome:
    """Outcome with authoritative broker reconstruction (default)."""
    pnl = r * 100.0
    return ExperienceOutcome(
        idempotency_key=record.idempotency_key,
        execution_id=f"tk_{record.idempotency_key}",
        outcome_timestamp=record.decision_timestamp + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=pnl,
        realized_r_multiple=r,
        approved_volume=0.1,
        behavior=PositionBehavior(
            mfe_r=0.5, mae_r=0.2, mae_points=2.0, mfe_points=5.0, duration_sec=300.0
        ),
        execution=ExecutionContext(),
        decomposition=OutcomeDecomposition(final_outcome_r=r),
        broker_outcome={
            "ticket": f"tk_{record.idempotency_key}",
            "gross_profit": pnl,
            "net_pnl_usd": pnl,
            "volume": 0.1,
            "entry_price": 2000.0,
            "exit_price": 2020.0,
            "reconstruction_source": broker_source,
        },
    )


def seed_experiences(
    ledger: ExperienceLedger,
    repo,
    count: int,
    prefix: str = "t4",
    r_values: list[float] | None = None,
    broker_source: str = "BROKER_DEALS",
    base: datetime | None = None,
) -> list[ExperienceRecord]:
    """Records N decisions + authoritative outcomes."""
    base = base or datetime(2024, 1, 1, tzinfo=UTC)
    records: list[ExperienceRecord] = []
    for i in range(count):
        rec = make_record(f"{prefix}{i}", ts=base + timedelta(hours=i))
        r = r_values[i] if r_values and i < len(r_values) else (0.3 if i % 2 else 0.15)
        ledger.record_experience(rec)
        ledger.record_outcome(make_outcome(rec, r, broker_source=broker_source))
        records.append(rec)
    repo._queue.join()
    return records
