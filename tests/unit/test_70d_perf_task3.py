"""TASK-03-70D-PARITY — feature-path purity (INV-001).

Proves the 70D feature assembly never touches a database on the hot path.
The p50/p95 timing probes (opt-in, PERF_SKIP) were moved to
_cleanup_hold_20260819 as dead skipif(True) members.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.features.features70 import assemble_70d
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from tests.helpers.liquidity_fixtures import steady_bars

#: Hot-path budget: the live tick pipeline must stay well under 100ms.
HOT_PATH_BUDGET_MS = 100.0


def _bars(n: int = 120) -> list[BarData]:
    return steady_bars(n, price=3300.0, step=0.1, t0=datetime(2026, 8, 1, 0, 0, tzinfo=UTC))


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
