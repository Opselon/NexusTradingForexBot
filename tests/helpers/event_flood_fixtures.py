"""Shared fixture helpers for the Agent-2 log-hygiene regression suite.

Seeds UNKNOWN_PROVENANCE orphan rows into a real ExperienceLedger the same
way the production sweep classifies them (no dispatch row + no gate signal
=> honest unknown provenance).
"""

from __future__ import annotations

from datetime import UTC, datetime


def seed_unknown_orphans(ledger, *, count: int) -> None:
    """Creates `count` executed+closed experiences with NO outcome and NO
    dispatch evidence -> evaluate_sample() == UNKNOWN_PROVENANCE."""
    from nexus_scalp.experience.models import (
        ExperienceRecord,
        FeatureSnapshot,
        StrategyContext,
    )

    base = datetime(2026, 8, 18, tzinfo=UTC)
    for i in range(count):
        rec = ExperienceRecord(
            experience_id=f"exp_orphan_{i:05d}",
            request_id=f"req_orphan_{i:05d}",
            idempotency_key=f"idem_orphan_{i:05d}",
            symbol="XAUUSD",
            timeframe="M1",
            decision_timestamp=base,
            strategy_id="strat_flood",
            strategy_version="1.0.0",
            context=StrategyContext(
                strategy_id="strat_flood",
                symbol="XAUUSD",
                session="LONDON",
                regime="TRENDING",
                volatility_regime="NORMAL",
                trend_state="BULLISH",
            ),
            feature_snapshot=FeatureSnapshot(
                feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
            ),
            action="BUY_LIMIT",
            entry_reason="SMC",
            model_probability=0.6,
            signal_confidence=0.6,
            proposed_entry=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            risk_reward_ratio=2.0,
            approved_volume=0.1,
        )
        ledger.record_experience(rec)
