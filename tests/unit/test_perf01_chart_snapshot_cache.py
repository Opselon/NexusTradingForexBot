"""PERF-01: chart-snapshot cache on the live hot path.

Guards the PERF-01 optimization: SMC overlays + 900-bar payload are computed
ONLY when the completed-bar series changes (new bar) or on a 10s cadence,
never on every tick. The cache key is the last completed bar timestamp, so
between ticks the UI snapshot is untouched (identical payload, no recompute).

Behavioral invariants preserved:
  * First tick after construction always publishes a snapshot.
  * A bar close (new completed bar) always republishes.
  * Between ticks the server-state payload is NOT rewritten (cache hit),
    but the last known snapshot remains readable.
  * The 10s cadence still refreshes the forming bar.
"""

from __future__ import annotations

import time

import pytest

from nexus_scalp.features.scalp_features import FeatureVector


class _FakeAggregator:
    """Minimal aggregator double: one completed bar + a forming bar."""

    def __init__(self, bars) -> None:
        self._completed = list(bars)

    def get_completed_bars(self) -> list:
        return list(self._completed)

    def get_current_forming_bar(self) -> object | None:
        return None


class _FakeServerState:
    """Records every update_live_visuals call."""

    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []
        self.bars: list = []
        self.overlays: dict = {}

    def update_live_visuals(self, bars, overlays) -> None:
        self.calls.append((bars, overlays))
        self.bars = bars
        self.overlays = overlays


class _FakePolicy:
    def extract_live_chart_overlays(self, completed_bars, atr_val) -> dict:
        return {
            "rectangles": [{"id": f"ob_{len(completed_bars)}"}],
            "bos_lines": [],
            "midlines": [],
            "liq_markers": [],
        }


class _FakeEngine:
    """LiveEngine-shaped object exercising just the snapshot-cache block."""

    def __init__(self, bars) -> None:
        self.aggregator = _FakeAggregator(bars)
        self.signal_policy = _FakePolicy()
        self.server_state = _FakeServerState()
        self._last_chart_snapshot_key: object = None
        self._last_chart_snapshot_bars: list | None = None
        self._last_chart_snapshot_overlays: dict | None = None
        self._last_chart_snapshot_time: float = 0.0
        self.liquidity_governor = None
        self.bars = bars

    def _publish(self) -> None:
        """Mirror of the live-engine per-tick overlay block."""
        completed_bars = self.aggregator.get_completed_bars()
        fv = FeatureVector(
            symbol="XAUUSD",
            timestamp_utc="2026-08-20T00:00:00+00:00",
            live_tick_displacement=0.0,
            log_return_m1=0.0,
            atr_m1=1.5,
            upper_wick_ratio=0.1,
            lower_wick_ratio=0.1,
            body_to_range_ratio=0.5,
            is_doji=False,
            is_hammer_pinbar=False,
            is_shooting_star=False,
            is_engulfing_bullish=False,
            is_engulfing_bearish=False,
            close_location_value=0.5,
            consecutive_momentum_count=0.0,
            dist_to_swing_high_20=1.0,
            dist_to_swing_low_20=1.0,
            price_compression_flag_ratio=1.0,
            is_at_extreme_high=False,
            is_at_extreme_low=False,
            stop_hunt_depth=0.0,
            session_tokyo=True,
            session_london=False,
            session_ny=False,
            session_overlap_london_ny=False,
            lag_1_log_return=0.0,
            lag_2_log_return=0.0,
            lag_3_log_return=0.0,
            lag_1_atr_ratio=0.5,
            lag_1_volume_z=0.0,
            lag_1_clv=0.5,
            fvg_bullish_active=False,
            fvg_bearish_active=False,
            order_block_type=0,
            liquidity_sweep_signal=0,
            fvg_depth=0.0,
            ob_strength=0.0,
            choch_bullish=False,
            choch_bearish=False,
            broke_previous_high=False,
            broke_previous_low=False,
            rapid_reversal_spike=False,
            rapid_reversal_spike_val=0.0,
            tenkan_sen=2000.0,
            kijun_sen=2000.0,
            senkou_span_a=2000.0,
            senkou_span_b=2000.0,
            tk_cross_signal=0,
            is_above_kumo=False,
            is_below_kumo=False,
            rsi_14=50.0,
            dist_to_ema_21=0.0,
            dist_to_ema_50=0.0,
            cross_asset_z_score=0.0,
            htf_h4_trend=0.0,
            htf_h1_momentum=0.0,
            htf_m30_structure=0.0,
            htf_m15_confirmation=0.0,
            support_zone_dist=0.0,
            resistance_zone_dist=0.0,
            trend_strength=0.0,
            consolidation_ratio=1.0,
            htf_h1_atr_ratio=1.0,
            htf_h4_atr_ratio=1.0,
            feat_ob_valid_bos=0.0,
            feat_ob_equilibrium_ratio=0.5,
            feat_ob_liquidity_swept=0.0,
            feat_ob_fib_50_60_alignment=0.0,
        )
        if getattr(self, "server_state", None) is not None:
            snapshot_key = completed_bars[-1].timestamp if completed_bars else None
            if (
                self._last_chart_snapshot_key is None
                or snapshot_key != self._last_chart_snapshot_key
                or (time.time() - self._last_chart_snapshot_time) >= 10.0
            ):
                real_overlays = self.signal_policy.extract_live_chart_overlays(
                    completed_bars=completed_bars, atr_val=fv.atr_m1
                )
                bars_list = [{"time": b.timestamp.isoformat()} for b in completed_bars[-900:]]
                self._last_chart_snapshot_key = snapshot_key
                self._last_chart_snapshot_bars = bars_list
                self._last_chart_snapshot_overlays = real_overlays
                self._last_chart_snapshot_time = time.time()
                self.server_state.update_live_visuals(bars_list, real_overlays)


def _make_bars(n: int) -> list:
    from datetime import UTC, datetime, timedelta

    from nexus_scalp.market_data.bar_aggregator import BarData

    start = datetime(2026, 8, 20, tzinfo=UTC)
    return [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=start + timedelta(minutes=i),
            open=2000.0 + i,
            high=2001.0 + i,
            low=1999.0 + i,
            close=2000.5 + i,
            tick_volume=10 + i,
            is_complete=True,
        )
        for i in range(n)
    ]


def test_perf01_first_tick_publishes() -> None:
    bars = _make_bars(60)
    eng = _FakeEngine(bars)
    eng._publish()
    assert len(eng.server_state.calls) == 1
    assert eng.server_state.bars
    assert eng.server_state.overlays


def test_perf01_no_recompute_between_ticks() -> None:
    bars = _make_bars(60)
    eng = _FakeEngine(bars)
    eng._publish()
    n_calls = len(eng.server_state.calls)
    # Same completed-bar series => cache hit => no extra publish.
    for _ in range(5):
        eng._publish()
    assert len(eng.server_state.calls) == n_calls
    # The cached snapshot is still available.
    assert eng._last_chart_snapshot_bars is not None
    assert eng._last_chart_snapshot_overlays is not None


def test_perf01_new_bar_republishes() -> None:
    from datetime import timedelta

    bars = _make_bars(60)
    eng = _FakeEngine(bars)
    eng._publish()
    n_calls = len(eng.server_state.calls)
    # Simulate a bar close: last completed bar advances.
    eng.aggregator._completed.append(_make_bars(61)[-1])  # a real BarData with a later timestamp
    eng._publish()
    assert len(eng.server_state.calls) == n_calls + 1


def test_perf01_ten_second_cadence_refreshes() -> None:
    bars = _make_bars(60)
    eng = _FakeEngine(bars)
    eng._publish()
    n_calls = len(eng.server_state.calls)
    # Force the 10s cadence to expire without a new bar.
    eng._last_chart_snapshot_time = time.time() - 11.0
    eng._publish()
    assert len(eng.server_state.calls) == n_calls + 1


def test_perf01_no_server_state_is_noop() -> None:
    bars = _make_bars(60)
    eng = _FakeEngine(bars)
    eng.server_state = None
    eng._publish()  # must not raise and must not publish
    assert eng._last_chart_snapshot_bars is None
