"""Deterministic 70D golden corpus builder (TASK-03-70D-PARITY, brief 36).

The corpus contains engineered market scenarios covering:
  trending / ranging / high volatility / low volatility
  News ON / OFF
  Liquidity active / inactive (BSL, SSL, EQH, EQL, sweep, no sweep, HTF confluence)

For each scenario the module stores: timestamp, schema, 70D vector, news
status, liquidity status. All downstream parity tests use this corpus: if the
dataset builder, replay and inference adapter produce the SAME vector for the
SAME (scenario, timestamp), the contract holds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from nexus_scalp.features.features70 import (
    FeatureSourceState,
    assemble_70d,
    news_10d_from_context,
)
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from tests.helpers.liquidity_fixtures import (
    bar,
    ramp_bars,
    steady_bars,
    sweep_pool_bars,
    swing_high_bars,
    swing_low_bars,
)


def _to_rows(bars: list[BarData], t0: datetime) -> list[dict[str, Any]]:
    rows = []
    for _i, b in enumerate(bars):
        rows.append(
            {
                "time": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "tick_volume": b.tick_volume,
            }
        )
    return rows


def _compute_70d(
    bars: list[BarData],
    *,
    news10: list[float] | None = None,
    news_available: bool = True,
    use_htf: bool = True,
) -> dict[str, Any]:
    """Computes the canonical 70D vector for one scenario (pure, causal)."""
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    last = bars[-1]
    tick_ts = last.timestamp
    from nexus_scalp.domain.models import TickData

    tick = TickData(
        symbol="XAUUSD",
        timestamp=tick_ts,
        bid=last.close,
        ask=last.close + 0.20,
        volume=last.tick_volume,
    )
    fv = engine.compute_from_bars(bars, tick)
    x50 = fv.to_tensor_input()
    liquid = compute_liquidity_features(
        bars,
        decision_at=tick_ts,
        mid_price=float(last.close),
        atr=fv.atr_m1,
        use_htf=use_htf,
    )
    liq10 = list(liquid.as_vector())
    if news10 is None:
        n10 = [0.0] * 10
        n_avail = False
        n_status = FeatureSourceState.FEATURE_DISABLED
    else:
        n10 = news10
        n_avail = True
        n_status = FeatureSourceState.FEATURE_AVAILABLE

    snap = assemble_70d(
        base50=x50,
        news10=n10,
        liquidity10=liq10,
        symbol="XAUUSD",
        timeframe="M1",
        timestamp_utc=tick_ts,
        news_available=n_avail,
        liquidity_available=True,
        news_status=n_status,
        liquidity_status=FeatureSourceState.FEATURE_AVAILABLE,
    )
    return {
        "timestamp": tick_ts,
        "schema_id": snap.schema_id,
        "dimension": len(snap.feature_vector),
        "feature_vector": snap.vector,
        "news_status": snap.news_status.value,
        "liquidity_status": snap.liquidity_status.value,
        "news_enabled": n_avail,
        "schema_hash": snap.schema_hash(),
    }


def build_70d_golden_corpus() -> dict[str, dict[str, Any]]:
    """Builds the golden corpus (deterministic, no randomness)."""
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    corpus: dict[str, dict[str, Any]] = {}

    # --- trending (steady ramp) -------------------------------------------------
    corpus["trending_up"] = _compute_70d(ramp_bars(120, 3300.0, 0.4, t0), news10=None)
    # --- ranging (steady flat) ---------------------------------------------------
    corpus["ranging"] = _compute_70d(steady_bars(120, price=3300.0, step=0.0), news10=None)
    # --- high volatility (big swings) -------------------------------------------
    bars_hi = []
    for i in range(120):
        c = 3300.0 + (12.0 if i % 2 else -12.0) * (i % 5)
        bars_hi.append(bar(i, t0, c - 2.0, c + 8.0, c - 8.0, c, vol=300))
    corpus["high_volatility"] = _compute_70d(bars_hi, news10=None)
    # --- low volatility ----------------------------------------------------------
    corpus["low_volatility"] = _compute_70d(
        steady_bars(120, price=3300.0, step=0.0, atr_units=0.05), news10=None
    )
    # --- News ON (non-zero context) ----------------------------------------------
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
        "news_state": 2.0,  # HIGH_IMPACT
    }
    n10 = news_10d_from_context(news_ctx)
    corpus["news_on_ramp"] = _compute_70d(
        ramp_bars(120, 3300.0, 0.3, t0), news10=n10, news_available=True
    )
    # --- News OFF ----------------------------------------------------------------
    corpus["news_off_ramp"] = _compute_70d(
        ramp_bars(120, 3300.0, 0.3, t0), news10=None, news_available=False
    )
    # --- Liquidity active: BSL above / SSL below (swing pools) -------------------
    corpus["liquidity_bsl_ssl"] = _compute_70d(
        swing_high_bars(60, high_price=3310.0, base=3300.0)
        + swing_low_bars(60, low_price=3290.0, base=3300.0)
    )
    # --- EQH / EQL clusters -------------------------------------------------------
    eqh_bars = steady_bars(80, price=3300.0)
    for j in range(3):
        i = 40 + j * 5
        eqh_bars.append(bar(i, t0, 3300.0, 3302.0, 3299.0, 3301.0, vol=150))
    corpus["eqh_cluster"] = _compute_70d(eqh_bars)
    # --- sweep scenario -----------------------------------------------------------
    corpus["sweep"] = _compute_70d(
        sweep_pool_bars(3310.0, 3300.0, pool_index=58, sweep_index=63, n_total=66, side="bsl")
    )
    # --- no sweep ----------------------------------------------------------------
    corpus["no_sweep"] = _compute_70d(steady_bars(120, price=3300.0, step=0.02))
    # --- HTF confluence -----------------------------------------------------------
    corpus["htf_confluence"] = _compute_70d(ramp_bars(200, 3300.0, 0.25, t0), use_htf=True)

    return corpus


GOLDEN_CORPUS = build_70d_golden_corpus()


def corpus_scenario_names() -> list[str]:
    return sorted(GOLDEN_CORPUS.keys())
