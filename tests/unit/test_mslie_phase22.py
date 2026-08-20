"""MSLIE unit tests — Market Structure & Liquidity Intelligence Engine.

TEST-MS LIE-01..NN: swing detection, regime features, liquidity map, sweep
detection, breakout quality, smart money, market memory, no-future-leakage,
determinism, feature vector contract, engine interface.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass

import pytest

sys.path.insert(0, "src")

from nexus_scalp.mslie import (  # noqa: E402
    MarketBias,
    MarketIntelligenceFeatureVectorV1,
    MarketStructureEngine,
    SweepState,
    compute_regime_features,
    detect_swings,
    build_liquidity_map,
    detect_sweep_events,
    assess_breakout_quality,
    compute_smart_money_features,
)
from nexus_scalp.mslie.models import LiquidityRank, ZoneSide  # noqa: E402


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 100


def _make_bars(n: int = 120, *, drift: float = 0.0, start_price: float = 2000.0) -> list[_Bar]:
    bars: list[_Bar] = []
    t = datetime(2025, 3, 1, 0, 0, tzinfo=UTC)
    price = start_price
    for i in range(n):
        o = price
        c = price + drift + (0.4 if i % 2 else -0.2)
        h = max(o, c) + 0.8
        l = min(o, c) - 0.8
        bars.append(_Bar(t, o, h, l, c, 100 + (i % 7) * 20))
        price = c
        t += timedelta(minutes=1)
    return bars


def _trending_bars(n: int = 160) -> list[_Bar]:
    bars = _make_bars(n // 2, drift=0.0)
    bars += _make_bars(n // 2, drift=0.7, start_price=bars[-1].close)
    # fix timestamps to be continuous
    t = bars[0].timestamp
    for b in bars:
        b.timestamp = t
        t += timedelta(minutes=1)
    return bars


def _sweep_bars() -> list[_Bar]:
    bars = _make_bars(150, drift=0.0)
    # a sweep: dip below the recent low then reclaim hard
    last = bars[-1]
    bars.append(_Bar(last.timestamp + timedelta(minutes=1), last.close, last.close + 0.5, 1980.0, 1985.0, 500))
    bars.append(_Bar(bars[-1].timestamp + timedelta(minutes=1), 1985.0, 1995.0, 1984.0, 1993.0, 400))
    bars.append(_Bar(bars[-1].timestamp + timedelta(minutes=1), 1993.0, 2005.0, 1992.0, 2002.0, 600))
    return bars


# =============================================================================
# REGIME
# =============================================================================


class TestRegime:
    def test_trending_regime_detected(self) -> None:
        bars = _trending_bars()
        r = compute_regime_features(bars)
        assert r.trend_direction > 0.5
        assert r.trend_strength > 40.0
        assert r.regime_label in ("TRENDING", "EXPANSION", "MIXED")

    def test_ranging_regime(self) -> None:
        bars = _make_bars(120, drift=0.0)
        r = compute_regime_features(bars)
        assert r.regime_label in ("RANGING", "MIXED", "COMPRESSION")

    def test_insufficient_history_honest_defaults(self) -> None:
        bars = _make_bars(4)
        r = compute_regime_features(bars)
        assert r.regime_label == "INSUFFICIENT_HISTORY"
        assert 0.0 <= r.ranging_probability <= 1.0

    def test_all_probabilities_sum_to_one(self) -> None:
        r = compute_regime_features(_trending_bars())
        total = r.ranging_probability + r.expansion_probability + r.compression_probability
        assert abs(total - 1.0) < 1e-6

    def test_no_future_leakage_regime(self) -> None:
        bars = _trending_bars()
        decision = bars[100].timestamp
        r = compute_regime_features(bars, decision_at=decision)
        # the vector must be identical to computing on the truncated series
        r2 = compute_regime_features(bars[:101], decision_at=decision)
        assert r.to_dict() == r2.to_dict()


# =============================================================================
# SWINGS
# =============================================================================


class TestSwings:
    def test_detects_swing_highs_and_lows(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        assert isinstance(highs, list)
        assert isinstance(lows, list)
        # swings are confirmed only (timestamps <= decision)
        for s in highs + lows:
            assert s.timestamp <= bars[-1].timestamp

    def test_swing_scores_bounded(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        for s in highs + lows:
            assert 0.0 <= s.strength_score <= 100.0
            assert 0.0 <= s.importance_score <= 100.0
            assert s.type.name in ("HIGH", "LOW")

    def test_no_future_leakage_swings(self) -> None:
        bars = _trending_bars()
        decision = bars[100].timestamp
        h1, l1 = detect_swings(bars, decision_at=decision, symbol="XAUUSD", timeframe="M1")
        h2, l2 = detect_swings(bars[:101], decision_at=decision, symbol="XAUUSD", timeframe="M1")
        assert [s.to_dict() for s in h1] == [s.to_dict() for s in h2]
        assert [s.to_dict() for s in l1] == [s.to_dict() for s in l2]

    def test_swing_ids_unique(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        ids = [s.id for s in highs + lows]
        assert len(ids) == len(set(ids))


# =============================================================================
# LIQUIDITY MAP
# =============================================================================


class TestLiquidityMap:
    def test_zones_built_with_sides(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
        assert len(zones) > 0
        sides = {z.side for z in zones}
        assert ZoneSide.BUY_SIDE in sides or ZoneSide.SELL_SIDE in sides

    def test_zone_ranks_valid(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
        for z in zones:
            assert z.rank in (LiquidityRank.LOW, LiquidityRank.MEDIUM, LiquidityRank.HIGH, LiquidityRank.EXTREME)
            assert 0.0 <= z.strength_score <= 100.0
            assert z.distance_from_price >= 0.0

    def test_bounded_zone_count(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
        assert len(zones) <= 12

    def test_nearest_bsl_above_ssl_below(self) -> None:
        bars = _trending_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        price = bars[-1].close
        zones = build_liquidity_map(bars, highs, lows, mid_price=price)
        for z in zones:
            if z.side == ZoneSide.BUY_SIDE:
                assert z.price >= price
            else:
                assert z.price <= price


# =============================================================================
# SWEEPS
# =============================================================================


class TestSweeps:
    def test_sweep_detected_with_state(self) -> None:
        bars = _sweep_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
        events = detect_sweep_events(bars, zones, mid_price=bars[-1].close)
        assert len(events) >= 1
        ev = events[-1]
        assert ev.after_event_state in (SweepState.REVERSAL, SweepState.CONTINUATION, SweepState.UNCERTAIN)
        assert 0.0 <= ev.confidence <= 100.0

    def test_no_sweep_on_flat_market(self) -> None:
        bars = _make_bars(120, drift=0.0)
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
        events = detect_sweep_events(bars, zones, mid_price=bars[-1].close)
        # a flat market has no displacement -> no confident events
        assert all(ev.confidence < 60.0 for ev in events)

    def test_sweep_events_chronological(self) -> None:
        bars = _sweep_bars()
        highs, lows = detect_swings(bars, symbol="XAUUSD", timeframe="M1")
        zones = build_liquidity_map(bars, highs, lows, mid_price=bars[-1].close)
        events = detect_sweep_events(bars, zones, mid_price=bars[-1].close)
        ts = [e.timestamp for e in events]
        assert ts == sorted(ts)


# =============================================================================
# BREAKOUT QUALITY
# =============================================================================


class TestBreakout:
    def test_real_and_fake_complement(self) -> None:
        bars = _trending_bars()
        q = assess_breakout_quality(bars)
        if q is not None:
            assert abs((q.real_breakout_probability + q.fake_breakout_probability) - 1.0) < 1e-6

    def test_none_without_breakout(self) -> None:
        bars = _make_bars(120, drift=0.0)
        q = assess_breakout_quality(bars)
        # flat series: no close beyond the range -> no breakout verdict
        assert q is None or q.real_breakout_probability < 0.95

    def test_all_sub_scores_bounded(self) -> None:
        bars = _trending_bars()
        q = assess_breakout_quality(bars)
        if q is not None:
            for v in (q.closing_strength, q.volume_support, q.momentum_support, q.retest_confirmation, q.structure_confirmation):
                assert 0.0 <= v <= 1.0


# =============================================================================
# SMART MONEY
# =============================================================================


class TestSmartMoney:
    def test_features_finite_bounded(self) -> None:
        bars = _trending_bars()
        sm = compute_smart_money_features(bars)
        assert -1.0 <= sm.order_block_type <= 1.0
        assert 0.0 <= sm.order_block_strength <= 1.0
        assert 0.0 <= sm.fvg_count <= 6.0
        assert -3.0 <= sm.fvg_strength <= 3.0
        assert -3.0 <= sm.displacement_strength <= 3.0
        assert -1.0 <= sm.premium_discount_position <= 1.0
        assert -3.0 <= sm.last_mitigated_order_block <= 3.0

    def test_no_future_leakage_smart_money(self) -> None:
        bars = _trending_bars()
        decision = bars[100].timestamp
        a = compute_smart_money_features(bars, decision_at=decision)
        b = compute_smart_money_features(bars[:101], decision_at=decision)
        assert a.to_dict() == b.to_dict()


# =============================================================================
# ENGINE / FEATURE VECTOR / INTERFACE
# =============================================================================


class TestEngine:
    def test_analyze_market_returns_contract(self) -> None:
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        v = eng.analyze_market(_trending_bars())
        assert isinstance(v, MarketIntelligenceFeatureVectorV1)
        assert v.version == "MarketIntelligenceFeatureVectorV1"
        assert v.symbol == "XAUUSD"
        assert v.timeframe == "M1"
        assert v.bias in (MarketBias.BULLISH, MarketBias.BEARISH, MarketBias.NEUTRAL)
        assert 0.0 <= v.structure_confidence <= 100.0

    def test_interface_methods(self) -> None:
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        bars = _trending_bars()
        v = eng.analyze_market(bars)
        assert eng.generate_feature_vector() is v
        assert eng.get_structure_state() is not None
        assert isinstance(eng.get_liquidity_map(), tuple)
        status = eng.get_debug_status()
        assert status["status"] in ("ONLINE", "STANDBY", "DEGRADED")
        assert status["engine_status"]["market_structure_engine"] in ("ONLINE", "STANDBY")
        assert status["feature_vector"]["version"] == v.version

    def test_engine_deterministic(self) -> None:
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        bars = _trending_bars()
        v1 = eng.analyze_market(bars)
        v2 = eng.analyze_market(bars)
        assert v1.regime.to_dict() == v2.regime.to_dict()
        assert [z.to_dict() for z in v1.liquidity_map] == [z.to_dict() for z in v2.liquidity_map]

    def test_no_future_leakage_engine(self) -> None:
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        bars = _trending_bars()
        decision = bars[100].timestamp
        v_full = eng.analyze_market(bars, decision_at=decision)
        eng2 = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        v_trunc = eng2.analyze_market(bars[:101], decision_at=decision)
        assert v_full.regime.to_dict() == v_trunc.regime.to_dict()

    def test_market_memory_grows_and_bounds(self) -> None:
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1", max_memory_levels=8)
        for i in range(20):
            bars = _make_bars(60, start_price=2000.0 + i * 5)
            eng.analyze_market(bars)
        levels = eng.memory.levels()
        assert len(levels) <= 8
        for lvl in levels:
            assert lvl.events  # every level has an event history

    def test_latency_telemetry(self) -> None:
        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        eng.analyze_market(_trending_bars())
        assert eng.last_latency_ms is not None
        assert eng.last_latency_ms >= 0.0
        status = eng.get_debug_status()
        assert status["engine_status"]["compute_count"] >= 1
        assert status["engine_status"]["last_update"] is not None

    def test_to_dict_json_safe(self) -> None:
        import json

        eng = MarketStructureEngine(symbol="XAUUSD", timeframe="M1")
        v = eng.analyze_market(_trending_bars())
        json.dumps(v.to_dict())  # must not raise
        json.dumps(eng.get_debug_status())  # must not raise
