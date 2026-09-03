"""Mission item 3: synthetic 500x evaluate_probabilities micro-bench."""
import statistics
import time
from datetime import UTC, datetime, timedelta

import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import FeatureVector
from nexus_scalp.signals.policy import SignalPolicy


def make_fv():
    return FeatureVector(
        symbol="XAUUSD",
        timestamp_utc=datetime.now(UTC).isoformat(),
        live_tick_displacement=0.5,
        log_return_m1=0.0,
        atr_m1=2.0,
        upper_wick_ratio=0.1,
        lower_wick_ratio=0.1,
        body_to_range_ratio=0.8,
        is_doji=False,
        is_hammer_pinbar=False,
        is_shooting_star=False,
        is_engulfing_bullish=False,
        is_engulfing_bearish=False,
        close_location_value=0.5,
        consecutive_momentum_count=1.0,
        dist_to_swing_high_20=2.0,
        dist_to_swing_low_20=2.0,
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
        lag_1_atr_ratio=1.0,
        lag_1_volume_z=0.0,
        lag_1_clv=0.0,
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
        tenkan_sen=2000.1,
        kijun_sen=2000.1,
        senkou_span_a=2000.1,
        senkou_span_b=2000.1,
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
        support_zone_dist=3.0,
        resistance_zone_dist=3.0,
        trend_strength=0.0,
        consolidation_ratio=1.0,
        htf_h1_atr_ratio=1.0,
        htf_h4_atr_ratio=1.0,
        feat_ob_valid_bos=0.0,
        feat_ob_equilibrium_ratio=0.0,
        feat_ob_liquidity_swept=0.0,
        feat_ob_fib_50_60_alignment=0.0,
    )


pol = SignalPolicy()
ms = []
reasons = {}
base = datetime.now(UTC)
for k in range(500):
    fv = make_fv()
    t0 = time.perf_counter()
    p = pol.evaluate_probabilities(
        probabilities=torch.tensor([0.6, 0.2, 0.2, 0.0]),
        current_tick=TickData(
            symbol="XAUUSD",
            timestamp=base + timedelta(milliseconds=k * 50),
            bid=2000.0 + 1e-9 * k,
            ask=2000.2 + 1e-9 * k,
        ),
        feature_vector=fv,
        regime_state=None,
    )
    ms.append((time.perf_counter() - t0) * 1000)
    r = p.reason_code.split(" (")[0][:44]
    reasons[r] = reasons.get(r, 0) + 1

ms.sort()
n = len(ms)
print(f"synthetic 500x evaluate: p50={ms[n // 2]:.3f}ms p95={ms[int(n * 0.95)]:.3f}ms "
      f"mean={statistics.fmean(ms):.3f}ms")
print("dominant reasons:", sorted(reasons.items(), key=lambda kv: -kv[1])[:4])
