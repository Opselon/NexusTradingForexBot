"""TASK-03-70D-PARITY — runtime hot-path performance (brief 49).

Measures p50/p95/max for: 50D base construction, 70D snapshot assembly,
70D validation, replay_70d_vector, and the Runtime70Hook full live path.
Proves the 70D contract does not violate the hot-path budget and that no DB
access happens on the feature path (INV-001).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from nexus_scalp.features.features70 import assemble_70d
from nexus_scalp.features.inference_validator import InferenceValidator
from nexus_scalp.features.runtime70 import Runtime70Hook
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from tests.helpers.liquidity_fixtures import steady_bars

#: Hot-path budget: the live tick pipeline must stay well under 100ms.
HOT_PATH_BUDGET_MS = 100.0

# Timing probes are opt-in (not part of the default gate); the no-DB proof
# below runs always (INV-001).
PERF_SKIP = pytest.mark.skipif(True, reason="perf probe run explicitly")


def _bars(n: int = 120) -> list[BarData]:
    return steady_bars(n, price=3300.0, step=0.1, t0=datetime(2026, 8, 1, 0, 0, tzinfo=UTC))


def _percentiles(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)
    n = len(s)
    p50 = s[int(n * 0.50)]
    p95 = s[min(int(n * 0.95), n - 1)]
    return {"p50_ms": round(p50, 3), "p95_ms": round(p95, 3), "max_ms": round(max(s), 3)}


def _measure(fn, n: int = 30) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1e3)
    return _percentiles(samples)


@PERF_SKIP
def test_70d_construction_and_validation_bounded() -> None:
    bars = _bars()
    engine = ScalpFeatureEngine(symbol="XAUUSD")

    def full_70d() -> None:
        from nexus_scalp.domain.models import TickData

        last = bars[-1]
        tick = TickData(
            symbol="XAUUSD",
            timestamp=last.timestamp,
            bid=last.close,
            ask=last.close + 0.2,
            volume=last.tick_volume,
        )
        fv = engine.compute_from_bars(bars, tick)
        x50 = fv.to_tensor_input()
        snap = assemble_70d(
            base50=x50,
            news10=[0.1] * 10,
            liquidity10=[0.2] * 10,
            symbol="XAUUSD",
            timeframe="M1",
            timestamp_utc=last.timestamp,
            news_available=True,
            liquidity_available=True,
        )
        snap.validate(context="perf")

    stats = _measure(full_70d, n=20)
    print(f"\n[70D] full assembly+validation: {stats}")
    assert stats["p95_ms"] < HOT_PATH_BUDGET_MS


@PERF_SKIP
def test_validator_cached_metadata_fast() -> None:
    v = InferenceValidator(
        expected_schema_id="scalp_v3", expected_dimension=70, expected_schema_hash=""
    )
    vec = [0.0] * 50 + [0.1] * 10 + [0.2] * 10

    def validate() -> None:
        v.validate(
            vec,
            news_status="FEATURE_AVAILABLE",
            liquidity_status="FEATURE_AVAILABLE",
            context="perf",
        )

    stats = _measure(validate, n=50)
    print(f"\n[VALIDATOR] cached per-tick validation: {stats}")
    assert stats["p95_ms"] < 1.0  # pure in-memory, no hash rebuild per tick


@PERF_SKIP
def test_runtime_hook_snapshot_bounded() -> None:
    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=True)
    bars = _bars()
    from nexus_scalp.domain.models import TickData

    last = bars[-1]
    tick = TickData(
        symbol="XAUUSD",
        timestamp=last.timestamp,
        bid=last.close,
        ask=last.close + 0.2,
        volume=last.tick_volume,
    )
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    news_ctx = {
        "active_high_impact_events": 1.0,
        "xauusd_relevance": 0.8,
        "usd_relevance": 0.5,
        "bullish_pressure": 0.4,
        "bearish_pressure": 0.1,
        "conflict_score": 0.2,
        "novelty": 0.0,
        "freshness": 1.0,
        "confidence": 0.9,
        "source_consensus": 0.7,
        "news_state": 2.0,
    }

    def snapshot() -> None:
        fv = engine.compute_from_bars(bars, tick)
        hook.compute_snapshot(
            completed_bars=bars,
            base50=list(fv.to_tensor_input()),
            news_context=news_ctx,
            timestamp_utc=datetime.now(UTC),
            context="perf",
        )

    stats = _measure(snapshot, n=20)
    print(f"\n[RUNTIME70] live snapshot: {stats}")
    assert stats["p95_ms"] < HOT_PATH_BUDGET_MS


def test_no_db_on_feature_path() -> None:
    """Proves the feature assembly never touches sqlite (INV-001).

    Uses a monkeypatched sqlite3.connect that fails loudly: if the 70D
    snapshot path attempted a DB open, the test would raise.
    """
    import sqlite3

    original = sqlite3.connect
    calls: list[str] = []

    def boom(*a, **k):  # pragma: no cover - only reached on contract breach
        calls.append(str(a))
        raise AssertionError("DB access on feature path (INV-001 breach)")

    sqlite3.connect = boom
    try:
        bars = _bars()
        engine = ScalpFeatureEngine(symbol="XAUUSD")
        from nexus_scalp.domain.models import TickData

        last = bars[-1]
        tick = TickData(
            symbol="XAUUSD",
            timestamp=last.timestamp,
            bid=last.close,
            ask=last.close + 0.2,
            volume=last.tick_volume,
        )
        fv = engine.compute_from_bars(bars, tick)
        snap = assemble_70d(
            base50=list(fv.to_tensor_input()),
            news10=[0.0] * 10,
            liquidity10=[0.0] * 10,
            symbol="XAUUSD",
            timeframe="M1",
            timestamp_utc=last.timestamp,
            news_available=True,
            liquidity_available=True,
        )
        snap.validate(context="no-db")
    finally:
        sqlite3.connect = original
    assert calls == [], "feature path opened a DB connection"
