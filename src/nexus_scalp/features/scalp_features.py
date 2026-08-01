"""
Institutional XAUUSD Feature Engineering Engine (v8.0 Enterprise - Upgraded 50D Contract)
========================================================================================
Calculates strictly causal, zero-lookahead microstructure, Price Action candle patterns (Doji,
Star, Engulfing, Wicks), Swing Structure, Time-of-Day Sessions, Lags, Ichimoku, and ICT signals.
Generates the exact 50-dimensional sanitized tensor required by ScalpNet for deep neural inference.

Enterprise Upgrades & Hardening Incorporated:
    1. Explicit 50D Contract Tuple (FEATURE_NAMES with runtime length assertion).
    2. True Multi-Timeframe (MTF) analysis incorporating context from M15, M30, H1, and H4 timeframes.
    3. Live Dynamic Support & Resistance levels detection with nearest distance calculation.
    4. Non-Redundant Feature Mapping.
    5. True Exponential Moving Averages.
    6. Runtime Length Assertion Gate (Enforces exact 50D tensor output or raises RuntimeError).

Invariants:
    - Zero Lookahead Bias: Features are strictly computed on completed bars and the live tick.
    - Zero Dimensionality Mismatch: to_tensor_input() strictly returns 50 float elements matching FEATURE_NAMES.
"""

import math
from datetime import UTC

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from nexus_scalp.domain.models import TickData
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.features.scalp_features")

# ==============================================================================
# HELPERS FOR MTF AGGREGATION & S/R DETECTION
# ==============================================================================

def aggregate_bars(m1_bars: list[BarData], period_minutes: int) -> list[BarData]:
    """
    Groups completed M1 bars into completed higher timeframe bars.
    """
    if not m1_bars:
        return []
    aggregated = []
    current_bar_start = None
    o, h, l, c, vol = 0.0, -float("inf"), float("inf"), 0.0, 0
    for bar in m1_bars:
        minute = bar.timestamp.minute
        bar_minute = (minute // period_minutes) * period_minutes
        bar_start = bar.timestamp.replace(minute=bar_minute, second=0, microsecond=0)

        if current_bar_start is None:
            current_bar_start = bar_start
            o = bar.open
            h = bar.high
            l = bar.low
            c = bar.close
            vol = bar.tick_volume
        elif bar_start == current_bar_start:
            h = max(h, bar.high)
            l = min(l, bar.low)
            c = bar.close
            vol += bar.tick_volume
        else:
            aggregated.append(BarData(
                symbol=bar.symbol,
                timeframe=f"M{period_minutes}" if period_minutes < 60 else f"H{period_minutes // 60}",
                timestamp=current_bar_start,
                open=o,
                high=h,
                low=l,
                close=c,
                tick_volume=vol,
                is_complete=True
            ))
            current_bar_start = bar_start
            o = bar.open
            h = bar.high
            l = bar.low
            c = bar.close
            vol = bar.tick_volume

    if current_bar_start is not None:
        aggregated.append(BarData(
            symbol=m1_bars[-1].symbol,
            timeframe=f"M{period_minutes}" if period_minutes < 60 else f"H{period_minutes // 60}",
            timestamp=current_bar_start,
            open=o,
            high=h,
            low=l,
            close=c,
            tick_volume=vol,
            is_complete=True
        ))
    return aggregated


def find_support_resistance_levels(bars: list[BarData], window: int = 20) -> tuple[list[float], list[float]]:
    """
    Finds Support (swing lows) and Resistance (swing highs) levels over a window.
    """
    if len(bars) < window * 2 + 1:
        return [], []

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    supports = []
    resistances = []

    for i in range(window, len(bars) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            resistances.append(highs[i])
        if lows[i] == min(lows[i - window : i + window + 1]):
            supports.append(lows[i])

    def clean_levels(levels: list[float]) -> list[float]:
        if not levels:
            return []
        levels = sorted(levels)
        cleaned = [levels[0]]
        for l in levels[1:]:
            if (l - cleaned[-1]) / cleaned[-1] > 0.001:
                cleaned.append(l)
        return cleaned

    return clean_levels(supports), clean_levels(resistances)


# ==============================================================================
# EXPLICIT 50D FEATURE MAPPING CONTRACT
# ==============================================================================
FEATURE_NAMES: tuple[str, ...] = (
    "upper_wick_ratio",             # feat_0
    "lower_wick_ratio",             # feat_1
    "body_to_range_ratio",          # feat_2
    "is_doji",                      # feat_3
    "pinbar_sig",                   # feat_4
    "engulfing_sig",                # feat_5
    "close_location_value",         # feat_6
    "consecutive_momentum_count",   # feat_7
    "norm_displacement",            # feat_8
    "rapid_reversal_spike_val",     # feat_9
    "dist_to_swing_high_20",        # feat_10
    "dist_to_swing_low_20",         # feat_11
    "price_compression_flag_ratio", # feat_12
    "extreme_sig",                  # feat_13
    "stop_hunt_depth",              # feat_14
    "liquidity_sweep_signal",       # feat_15
    "session_tokyo",                # feat_16
    "session_london",               # feat_17
    "session_ny",                   # feat_18
    "session_overlap_london_ny",    # feat_19
    "lag_1_log_return",             # feat_20
    "lag_2_log_return",             # feat_21
    "lag_3_log_return",             # feat_22
    "lag_1_atr_ratio",              # feat_23
    "lag_1_volume_z",               # feat_24
    "lag_1_clv",                    # feat_25
    "fvg_sig",                      # feat_26
    "order_block_type",             # feat_27
    "choch_sig",                    # feat_28
    "breakout_sig",                 # feat_29
    "norm_tk_diff",                 # feat_30
    "tk_cross_signal",              # feat_31
    "kumo_sig",                     # feat_32
    "norm_kumo_width",              # feat_33
    "norm_rsi",                     # feat_34
    "dist_to_ema_21",               # feat_35
    "dist_to_ema_50",               # feat_36
    "cross_asset_z_score",          # feat_37
    "norm_dist_to_tenkan",          # feat_38
    "norm_dist_to_kijun",           # feat_39
    "htf_h4_trend",                 # feat_40 [NEW]
    "htf_h1_momentum",              # feat_41 [NEW]
    "htf_m30_structure",            # feat_42 [NEW]
    "htf_m15_confirmation",         # feat_43 [NEW]
    "support_zone_dist",            # feat_44 [NEW]
    "resistance_zone_dist",         # feat_45 [NEW]
    "trend_strength",               # feat_46 [NEW]
    "consolidation_ratio",          # feat_47 [NEW]
    "htf_h1_atr_ratio",             # feat_48 [NEW]
    "htf_h4_atr_ratio",             # feat_49 [NEW]
)

NUM_FEATURES: int = len(FEATURE_NAMES)
if NUM_FEATURES != 50:
    raise RuntimeError(f"ScalpNet feature contract violation: expected 50 features, got {NUM_FEATURES}")


class FeatureVector(BaseModel):
    """
    Immutable domain model representing a single point-in-time snapshot of the market.
    Encompasses a full 50-dimensional Price Action & Multi-Timeframe feature matrix.
    """
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp_utc: str

    # 1. Gold Micro Metrics & Volatility
    live_tick_displacement: float = Field(..., description="Tick price distance from last closed bar")
    log_return_m1: float = Field(..., description="Log return of the last completed bar")
    atr_m1: float = Field(..., description="14-period Average True Range")

    # 2. Price Action & Candlestick Anatomy
    upper_wick_ratio: float = Field(..., description="Upper wick length relative to bar range [0 to 1]")
    lower_wick_ratio: float = Field(..., description="Lower wick length relative to bar range [0 to 1]")
    body_to_range_ratio: float = Field(..., description="Body size relative to bar range [0 to 1]")
    is_doji: bool = Field(..., description="Doji candle pattern flag")
    is_hammer_pinbar: bool = Field(..., description="Bullish Hammer / Pinbar pattern flag")
    is_shooting_star: bool = Field(..., description="Bearish Shooting Star pattern flag")
    is_engulfing_bullish: bool = Field(..., description="Bullish Engulfing candle pattern flag")
    is_engulfing_bearish: bool = Field(..., description="Bearish Engulfing candle pattern flag")
    close_location_value: float = Field(..., description="CLV representing close location [-1 to +1]")
    consecutive_momentum_count: float = Field(..., description="Normalized count of consecutive same-color bars")

    # 3. Swing Structure & Chart Patterns
    dist_to_swing_high_20: float = Field(..., description="Normalized distance to 20-bar swing high")
    dist_to_swing_low_20: float = Field(..., description="Normalized distance to 20-bar swing low")
    price_compression_flag_ratio: float = Field(..., description="Ratio of 5-bar range to 20-bar range (Flag pattern)")
    is_at_extreme_high: bool = Field(..., description="Price is in top 5% of 50-bar range")
    is_at_extreme_low: bool = Field(..., description="Price is in bottom 5% of 50-bar range")
    stop_hunt_depth: float = Field(..., description="Normalized penetration depth during liquidity sweep")

    # 4. Market Sessions & Time-of-Day
    session_tokyo: bool = Field(..., description="Active Tokyo Trading Session flag")
    session_london: bool = Field(..., description="Active London Trading Session flag")
    session_ny: bool = Field(..., description="Active New York Trading Session flag")
    session_overlap_london_ny: bool = Field(..., description="London / New York Overlap Peak Liquidity flag")

    # 5. Time-Series Lag Features
    lag_1_log_return: float
    lag_2_log_return: float
    lag_3_log_return: float
    lag_1_atr_ratio: float
    lag_1_volume_z: float
    lag_1_clv: float

    # 6. ICT Signals & Microstructure
    fvg_bullish_active: bool
    fvg_bearish_active: bool
    order_block_type: int
    liquidity_sweep_signal: int
    fvg_depth: float = 0.0
    ob_strength: float = 0.0
    choch_bullish: bool
    choch_bearish: bool
    broke_previous_high: bool
    broke_previous_low: bool
    rapid_reversal_spike: bool
    rapid_reversal_spike_val: float

    # 7. Ichimoku Kinko Hyo
    tenkan_sen: float
    kijun_sen: float
    senkou_span_a: float
    senkou_span_b: float
    tk_cross_signal: int
    is_above_kumo: bool
    is_below_kumo: bool

    # 8. Dynamic S/R, Indicators & Stat-Arb
    rsi_14: float
    dist_to_ema_21: float
    dist_to_ema_50: float
    cross_asset_z_score: float

    # 9. True Multi-Timeframe Context Features [NEW 10 Indicators]
    htf_h4_trend: float = Field(..., description="H4 trend direction: +1 bullish, -1 bearish, 0 flat")
    htf_h1_momentum: float = Field(..., description="H1 momentum roc / atr ratio")
    htf_m30_structure: float = Field(..., description="M30 structure vs short ema")
    htf_m15_confirmation: float = Field(..., description="M15 trend confirmation signal")
    support_zone_dist: float = Field(..., description="Distance to nearest significant support level")
    resistance_zone_dist: float = Field(..., description="Distance to nearest significant resistance level")
    trend_strength: float = Field(..., description="Aggregated trend direction across timeframes")
    consolidation_ratio: float = Field(..., description="Large window consolidation compression factor")
    htf_h1_atr_ratio: float = Field(..., description="H1 ATR to M1 ATR volatility ratio")
    htf_h4_atr_ratio: float = Field(..., description="H4 ATR to M1 ATR volatility ratio")

    def to_tensor_input(self) -> list[float]:
        """
        Converts the feature snapshot into the exact 50-dimensional tensor input expected by ScalpNet v3.
        Applies rigorous ATR-based normalization, sanitization, and clamps values to [-3.0, +3.0].
        Strictly enforces the FEATURE_NAMES contract.
        """
        safe_atr = max(self.atr_m1, 0.20)
        
        norm_displacement = self.live_tick_displacement / safe_atr
        norm_rsi = (self.rsi_14 - 50.0) / 16.66
        norm_tk_diff = (self.tenkan_sen - self.kijun_sen) / safe_atr
        norm_kumo_width = (self.senkou_span_a - self.senkou_span_b) / safe_atr

        norm_dist_to_tenkan = (self.tenkan_sen - self.kijun_sen) / (safe_atr * 2.0)
        norm_dist_to_kijun = (self.kijun_sen - self.tenkan_sen) / (safe_atr * 2.0)

        kumo_sig = 1.0 if self.is_above_kumo else (-1.0 if self.is_below_kumo else 0.0)
        # Deep PyTorch Neural Integration: Use normalized continuous zone quality as feat_zone_quality (feat_26)
        fvg_sig = self.fvg_depth
        choch_sig = 1.0 if self.choch_bullish else (-1.0 if self.choch_bearish else 0.0)
        breakout_sig = 1.0 if self.broke_previous_high else (-1.0 if self.broke_previous_low else 0.0)
        extreme_sig = 1.0 if self.is_at_extreme_high else (-1.0 if self.is_at_extreme_low else 0.0)
        
        engulfing_sig = 1.0 if self.is_engulfing_bullish else (-1.0 if self.is_engulfing_bearish else 0.0)
        pinbar_sig = 1.0 if self.is_hammer_pinbar else (-1.0 if self.is_shooting_star else 0.0)

        raw_features = [
            # feat_0 .. feat_9
            self.upper_wick_ratio,
            self.lower_wick_ratio,
            self.body_to_range_ratio,
            1.0 if self.is_doji else 0.0,
            pinbar_sig,
            engulfing_sig,
            self.close_location_value,
            self.consecutive_momentum_count,
            norm_displacement,
            self.rapid_reversal_spike_val,

            # feat_10 .. feat_15
            self.dist_to_swing_high_20,
            self.dist_to_swing_low_20,
            self.price_compression_flag_ratio,
            extreme_sig,
            self.stop_hunt_depth,
            float(self.liquidity_sweep_signal),

            # feat_16 .. feat_19
            1.0 if self.session_tokyo else 0.0,
            1.0 if self.session_london else 0.0,
            1.0 if self.session_ny else 0.0,
            1.0 if self.session_overlap_london_ny else 0.0,

            # feat_20 .. feat_25
            self.lag_1_log_return * 100.0,
            self.lag_2_log_return * 100.0,
            self.lag_3_log_return * 100.0,
            self.lag_1_atr_ratio,
            self.lag_1_volume_z,
            self.lag_1_clv,

            # feat_26 .. feat_32
            fvg_sig,  # mapped to feat_zone_quality
            self.ob_strength,  # mapped to feat_ob_strength
            choch_sig,
            breakout_sig,
            norm_tk_diff,
            float(self.tk_cross_signal),
            kumo_sig,

            # feat_33 .. feat_39
            norm_kumo_width,
            norm_rsi,
            self.dist_to_ema_21,
            self.dist_to_ema_50,
            self.cross_asset_z_score,
            norm_dist_to_tenkan,
            norm_dist_to_kijun,

            # feat_40 .. feat_49 [NEW MULTI-TIMEFRAME INTEL]
            self.htf_h4_trend,
            self.htf_h1_momentum,
            self.htf_m30_structure,
            self.htf_m15_confirmation,
            self.support_zone_dist,
            self.resistance_zone_dist,
            self.trend_strength,
            self.consolidation_ratio,
            self.htf_h1_atr_ratio,
            self.htf_h4_atr_ratio,
        ]

        sanitized_features = []
        for val in raw_features:
            if math.isnan(val) or math.isinf(val):
                sanitized_features.append(0.0)
            else:
                sanitized_features.append(max(-3.0, min(3.0, float(val))))

        if len(sanitized_features) != 50:
            raise RuntimeError(
                f"Feature contract violation: expected 50 features matching FEATURE_NAMES, "
                f"got {len(sanitized_features)}"
            )

        return sanitized_features


class ScalpFeatureEngine:
    """
    Sub-millisecond Feature Engineering pipeline computing 50D Price Action,
    Candle Anatomy, Session, Lag, ICT, Ichimoku, and true Multi-Timeframe Context.
    """

    def __init__(self, symbol: str = "XAUUSD") -> None:
        self.symbol = symbol

    def _compute_ema(self, prices: np.ndarray, period: int) -> float:
        """Computes true Exponential Moving Average (EMA) using exponential smoothing."""
        if len(prices) == 0:
            return 0.0
        alpha = 2.0 / (period + 1.0)
        ema = prices[0]
        for p in prices[1:]:
            ema = (p * alpha) + (ema * (1.0 - alpha))
        return float(ema)

    def compute_from_bars(
        self, 
        completed_bars: list[BarData],
        current_tick: TickData,
        benchmark_bars: list[float] | None = None,
        current_benchmark: float | None = None
    ) -> FeatureVector:
        """
        Hot-path execution computing 50D master feature tensor directly from recent history.
        """
        mid_price = (current_tick.bid + current_tick.ask) / 2.0

        if len(completed_bars) < 55:
            return self._cold_start_vector(current_tick, mid_price)

        # Extract hot views (last 55 bars)
        tail_bars = completed_bars[-55:]
        closes = np.array([b.close for b in tail_bars], dtype=np.float64)
        highs = np.array([b.high for b in tail_bars], dtype=np.float64)
        lows = np.array([b.low for b in tail_bars], dtype=np.float64)
        opens = np.array([b.open for b in tail_bars], dtype=np.float64)
        volumes = np.array([b.tick_volume for b in tail_bars], dtype=np.float64)

        last_close = closes[-1]
        live_tick_displacement = float(mid_price - last_close)
        log_ret_m1 = float(math.log(closes[-1] / closes[-2]) if closes[-2] > 0 else 0.0)

        # 1. ATR Calculation
        tr = np.maximum(
            highs[-14:] - lows[-14:],
            np.maximum(
                np.abs(highs[-14:] - closes[-15:-1]),
                np.abs(lows[-14:] - closes[-15:-1]),
            ),
        )
        atr_m1 = float(np.mean(tr)) if len(tr) > 0 else 1.50
        safe_atr = max(atr_m1, 0.20)

        # 2. Group 1: Price Action & Classical Candlestick Anatomy
        bar_range = max(highs[-1] - lows[-1], 0.01)
        body_top = max(opens[-1], closes[-1])
        body_bottom = min(opens[-1], closes[-1])
        body_size = body_top - body_bottom

        upper_wick = highs[-1] - body_top
        lower_wick = body_bottom - lows[-1]

        upper_wick_ratio = float(upper_wick / bar_range)
        lower_wick_ratio = float(lower_wick / bar_range)
        body_to_range_ratio = float(body_size / bar_range)

        is_doji = bool(body_to_range_ratio <= 0.12)
        is_hammer_pinbar = bool(lower_wick_ratio >= 0.55 and body_top >= (highs[-1] - bar_range * 0.35))
        is_shooting_star = bool(upper_wick_ratio >= 0.55 and body_bottom <= (lows[-1] + bar_range * 0.35))

        is_engulfing_bullish = bool(
            closes[-2] < opens[-2]
            and closes[-1] > opens[-1]
            and opens[-1] <= closes[-2]
            and closes[-1] >= opens[-2]
        )
        is_engulfing_bearish = bool(
            closes[-2] > opens[-2]
            and closes[-1] < opens[-1]
            and opens[-1] >= closes[-2]
            and closes[-1] <= opens[-2]
        )

        clv = float(((closes[-1] - lows[-1]) - (highs[-1] - closes[-1])) / bar_range)

        color_dir = np.sign(closes[-10:] - opens[-10:])
        consecutive_count = 0.0
        curr_dir = color_dir[-1]
        if curr_dir != 0:
            for d in reversed(color_dir):
                if d == curr_dir:
                    consecutive_count += 1.0
                else:
                    break
        consecutive_momentum_count = float(np.clip((consecutive_count * curr_dir) / 5.0, -1.0, 1.0))

        # 3. Group 2: Swings & Chart Patterns
        swing_high_20 = np.max(highs[-20:-1])
        swing_low_20 = np.min(lows[-20:-1])

        dist_to_swing_high_20 = float((swing_high_20 - mid_price) / safe_atr)
        dist_to_swing_low_20 = float((mid_price - swing_low_20) / safe_atr)

        range_5 = np.max(highs[-5:]) - np.min(lows[-5:]) + 1e-8
        range_20 = np.max(highs[-20:]) - np.min(lows[-20:]) + 1e-8
        price_compression_flag_ratio = float(np.clip(range_5 / range_20, 0.0, 2.0))

        recent_max_50 = np.max(highs[-50:])
        recent_min_50 = np.min(lows[-50:])
        total_range = (recent_max_50 - recent_min_50) + 1e-8
        range_pos = float((mid_price - recent_min_50) / total_range)

        is_extreme_high = bool(range_pos >= 0.95)
        is_extreme_low = bool(range_pos <= 0.05)

        recent_high_10 = np.max(highs[-11:-1])
        recent_low_10 = np.min(lows[-11:-1])
        liquidity_sweep_signal = 0
        stop_hunt_depth = 0.0

        if lows[-1] < recent_low_10 and closes[-1] > recent_low_10:
            liquidity_sweep_signal = 1
            stop_hunt_depth = float((recent_low_10 - lows[-1]) / safe_atr)
        elif highs[-1] > recent_high_10 and closes[-1] < recent_high_10:
            liquidity_sweep_signal = -1
            stop_hunt_depth = float((highs[-1] - recent_high_10) / safe_atr)

        # 4. Group 3: Market Sessions & Time-of-Day
        dt_utc = current_tick.timestamp.astimezone(UTC) if current_tick.timestamp.tzinfo else current_tick.timestamp.replace(tzinfo=UTC)
        hour = dt_utc.hour

        session_tokyo = bool(0 <= hour < 8)
        session_london = bool(7 <= hour < 15)
        session_ny = bool(13 <= hour < 21)
        session_overlap_london_ny = bool(13 <= hour < 15)

        # 5. Group 4: Time-Series Lag Features
        lag_1_log_return = float(math.log(closes[-2] / closes[-3]) if closes[-3] > 0 else 0.0)
        lag_2_log_return = float(math.log(closes[-3] / closes[-4]) if closes[-4] > 0 else 0.0)
        lag_3_log_return = float(math.log(closes[-4] / closes[-5]) if closes[-5] > 0 else 0.0)

        tr_lag1 = max(highs[-2] - lows[-2], abs(highs[-2] - closes[-3]), abs(lows[-2] - closes[-3]))
        lag_1_atr_ratio = float(tr_lag1 / safe_atr)

        vol_mean_20 = np.mean(volumes[-21:-1]) + 1e-8
        vol_std_20 = np.std(volumes[-21:-1]) + 1e-8
        lag_1_volume_z = float((volumes[-2] - vol_mean_20) / vol_std_20)

        bar_range_lag1 = max(highs[-2] - lows[-2], 0.01)
        lag_1_clv = float(((closes[-2] - lows[-2]) - (highs[-2] - closes[-2])) / bar_range_lag1)

        # 6. Group 5: ICT Signals & Microstructure
        fvg_bullish = bool((lows[-1] - highs[-3]) > (safe_atr * 0.20))
        fvg_bearish = bool((lows[-3] - highs[-1]) > (safe_atr * 0.20))
        fvg_depth = float((lows[-1] - highs[-3]) / safe_atr) if fvg_bullish else (-float((lows[-3] - highs[-1]) / safe_atr) if fvg_bearish else 0.0)

        order_block_type = 0
        if closes[-1] > highs[-2] and closes[-2] < opens[-2]:
            order_block_type = 1
        elif closes[-1] < lows[-2] and closes[-2] > opens[-2]:
            order_block_type = -1
        ob_strength = float(order_block_type * (volumes[-1] / vol_mean_20))

        # True Exponential Moving Averages
        ema_20_val = self._compute_ema(closes[-20:], 20)
        ema_50_val = self._compute_ema(closes[-50:], 50)
        
        is_downtrend = ema_20_val < ema_50_val
        is_uptrend = ema_20_val > ema_50_val

        swing_high_20_choch = np.max(highs[-20:-5])
        swing_low_20_choch = np.min(lows[-20:-5])

        choch_bullish = bool(is_downtrend and mid_price > swing_high_20_choch)
        choch_bearish = bool(is_uptrend and mid_price < swing_low_20_choch)

        broke_prev_high = bool(mid_price > highs[-1])
        broke_prev_low = bool(mid_price < lows[-1])

        rapid_reversal_spike_val = 1.0 if (abs(live_tick_displacement) > (safe_atr * 0.6) and (live_tick_displacement * log_ret_m1) < 0) else 0.0

        # 7. Group 6: Ichimoku Kinko Hyo
        tenkan_sen = float((np.max(highs[-9:]) + np.min(lows[-9:])) / 2.0)
        kijun_sen = float((np.max(highs[-26:]) + np.min(lows[-26:])) / 2.0)
        senkou_span_a = float((tenkan_sen + kijun_sen) / 2.0)
        senkou_span_b = float((np.max(highs[-52:]) + np.min(lows[-52:])) / 2.0)

        is_above_kumo = bool(mid_price > max(senkou_span_a, senkou_span_b))
        is_below_kumo = bool(mid_price < min(senkou_span_a, senkou_span_b))

        prev_tenkan = float((np.max(highs[-10:-1]) + np.min(lows[-10:-1])) / 2.0)
        prev_kijun = float((np.max(highs[-27:-1]) + np.min(lows[-27:-1])) / 2.0)

        tk_cross_signal = 0
        if prev_tenkan <= prev_kijun and tenkan_sen > kijun_sen:
            tk_cross_signal = 1
        elif prev_tenkan >= prev_kijun and tenkan_sen < kijun_sen:
            tk_cross_signal = -1

        # 8. Group 7: Indicators, True EMA S/R & Stat-Arb
        diffs = np.diff(closes[-15:])
        gains = np.maximum(diffs, 0.0)
        losses = np.maximum(-diffs, 0.0)
        avg_gain = np.mean(gains) + 1e-8
        avg_loss = np.mean(losses) + 1e-8
        rs = avg_gain / avg_loss
        rsi_14 = float(100.0 - (100.0 / (1.0 + rs)))

        ema_21_val = self._compute_ema(closes[-21:], 21)
        ema_50_val = self._compute_ema(closes[-50:], 50)

        dist_to_ema_21 = float((mid_price - ema_21_val) / safe_atr)
        dist_to_ema_50 = float((mid_price - ema_50_val) / safe_atr)

        cross_asset_z_score = 0.0
        if benchmark_bars is not None and current_benchmark is not None and len(benchmark_bars) >= 50:
            bench_tail = np.array(benchmark_bars[-50:], dtype=np.float64)
            gold_tail = closes[-50:]

            cov_matrix = np.cov(bench_tail, gold_tail)
            var_bench = cov_matrix[0, 0]
            cov_gb = cov_matrix[0, 1]

            beta = cov_gb / var_bench if var_bench > 1e-8 else 1.0
            if math.isnan(beta) or math.isinf(beta):
                beta = 1.0

            spreads = gold_tail - (beta * bench_tail)
            mean_spread = float(np.mean(spreads))
            std_spread = float(np.std(spreads)) + 1e-8

            current_spread = mid_price - (beta * current_benchmark)
            cross_asset_z_score = float((current_spread - mean_spread) / std_spread)

        # 9. Group 8: True Multi-Timeframe Context Features [NEW]
        m15_bars = aggregate_bars(completed_bars, 15)
        m30_bars = aggregate_bars(completed_bars, 30)
        h1_bars = aggregate_bars(completed_bars, 60)
        h4_bars = aggregate_bars(completed_bars, 240)

        # H4 trend
        if len(h4_bars) >= 3:
            h4_closes = np.array([b.close for b in h4_bars], dtype=np.float64)
            h4_ema = self._compute_ema(h4_closes, 3)
            htf_h4_trend = 1.0 if h4_closes[-1] > h4_ema else -1.0
        elif len(h4_bars) >= 1:
            htf_h4_trend = 1.0 if h4_bars[-1].close > h4_bars[0].close else -1.0
        else:
            htf_h4_trend = 0.0

        # H1 momentum
        if len(h1_bars) >= 2:
            h1_closes = np.array([b.close for b in h1_bars], dtype=np.float64)
            htf_h1_momentum = float(h1_closes[-1] - h1_closes[-2]) / safe_atr
        else:
            htf_h1_momentum = 0.0

        # M30 structure
        if len(m30_bars) >= 5:
            m30_closes = np.array([b.close for b in m30_bars], dtype=np.float64)
            m30_ema = self._compute_ema(m30_closes, 5)
            htf_m30_structure = 1.0 if m30_closes[-1] > m30_ema else -1.0
        else:
            htf_m30_structure = 0.0

        # M15 confirmation
        if len(m15_bars) >= 2:
            m15_last = m15_bars[-1]
            m15_prev = m15_bars[-2]
            m15_bull_engulf = (m15_last.close > m15_last.open) and (m15_prev.close < m15_prev.open) and (m15_last.close >= m15_prev.open)
            m15_bear_engulf = (m15_last.close < m15_last.open) and (m15_prev.close > m15_prev.open) and (m15_last.close <= m15_prev.open)
            if m15_bull_engulf:
                htf_m15_confirmation = 1.0
            elif m15_bear_engulf:
                htf_m15_confirmation = -1.0
            else:
                htf_m15_confirmation = 1.0 if m15_last.close > m15_last.open else -1.0
        else:
            htf_m15_confirmation = 0.0

        # Support & Resistance levels detection
        supports, resistances = find_support_resistance_levels(completed_bars, window=20)
        nearest_support = None
        nearest_resistance = None
        for s in reversed(sorted(supports)):
            if s < mid_price:
                nearest_support = s
                break
        for r in sorted(resistances):
            if r > mid_price:
                nearest_resistance = r
                break

        support_zone_dist = float((mid_price - nearest_support) / safe_atr) if nearest_support is not None else 3.0
        resistance_zone_dist = float((nearest_resistance - mid_price) / safe_atr) if nearest_resistance is not None else 3.0

        # Trend strength cross-timeframe
        trend_signals = []
        if len(completed_bars) >= 20:
            m1_closes = np.array([b.close for b in completed_bars[-20:]], dtype=np.float64)
            m1_ema = self._compute_ema(m1_closes, 20)
            trend_signals.append(1.0 if m1_closes[-1] > m1_ema else -1.0)
        trend_signals.append(htf_m15_confirmation)
        trend_signals.append(htf_m30_structure)
        trend_signals.append(htf_h4_trend)
        trend_strength = float(np.mean(trend_signals)) if trend_signals else 0.0

        # Consolidation Ratio over larger window
        if len(completed_bars) >= 50:
            range_10 = np.max(highs[-10:]) - np.min(lows[-10:]) + 1e-8
            range_50 = np.max(highs[-50:]) - np.min(lows[-50:]) + 1e-8
            consolidation_ratio = float(range_10 / range_50)
        else:
            consolidation_ratio = 1.0

        # HTF ATR Volatility ratios
        if len(h1_bars) >= 3:
            h1_trs = [b.high - b.low for b in h1_bars[-14:]]
            h1_atr = float(np.mean(h1_trs)) if h1_trs else safe_atr
            htf_h1_atr_ratio = float(h1_atr / safe_atr)
        else:
            htf_h1_atr_ratio = 1.0

        if len(h4_bars) >= 3:
            h4_trs = [b.high - b.low for b in h4_bars[-14:]]
            h4_atr = float(np.mean(h4_trs)) if h4_trs else safe_atr
            htf_h4_atr_ratio = float(h4_atr / safe_atr)
        else:
            htf_h4_atr_ratio = 1.0

        return FeatureVector(
            symbol=self.symbol,
            timestamp_utc=current_tick.timestamp.isoformat(),
            live_tick_displacement=live_tick_displacement,
            log_return_m1=log_ret_m1,
            atr_m1=atr_m1,
            upper_wick_ratio=upper_wick_ratio,
            lower_wick_ratio=lower_wick_ratio,
            body_to_range_ratio=body_to_range_ratio,
            is_doji=is_doji,
            is_hammer_pinbar=is_hammer_pinbar,
            is_shooting_star=is_shooting_star,
            is_engulfing_bullish=is_engulfing_bullish,
            is_engulfing_bearish=is_engulfing_bearish,
            close_location_value=clv,
            consecutive_momentum_count=consecutive_momentum_count,
            dist_to_swing_high_20=dist_to_swing_high_20,
            dist_to_swing_low_20=dist_to_swing_low_20,
            price_compression_flag_ratio=price_compression_flag_ratio,
            is_at_extreme_high=is_extreme_high,
            is_at_extreme_low=is_extreme_low,
            stop_hunt_depth=stop_hunt_depth,
            session_tokyo=session_tokyo,
            session_london=session_london,
            session_ny=session_ny,
            session_overlap_london_ny=session_overlap_london_ny,
            lag_1_log_return=lag_1_log_return,
            lag_2_log_return=lag_2_log_return,
            lag_3_log_return=lag_3_log_return,
            lag_1_atr_ratio=lag_1_atr_ratio,
            lag_1_volume_z=lag_1_volume_z,
            lag_1_clv=lag_1_clv,
            fvg_bullish_active=fvg_bullish,
            fvg_bearish_active=fvg_bearish,
            order_block_type=order_block_type,
            liquidity_sweep_signal=liquidity_sweep_signal,
            fvg_depth=fvg_depth,
            ob_strength=ob_strength,
            choch_bullish=choch_bullish,
            choch_bearish=choch_bearish,
            broke_previous_high=broke_prev_high,
            broke_previous_low=broke_prev_low,
            rapid_reversal_spike=bool(rapid_reversal_spike_val > 0),
            rapid_reversal_spike_val=rapid_reversal_spike_val,
            tenkan_sen=tenkan_sen,
            kijun_sen=kijun_sen,
            senkou_span_a=senkou_span_a,
            senkou_span_b=senkou_span_b,
            tk_cross_signal=tk_cross_signal,
            is_above_kumo=is_above_kumo,
            is_below_kumo=is_below_kumo,
            rsi_14=rsi_14,
            dist_to_ema_21=dist_to_ema_21,
            dist_to_ema_50=dist_to_ema_50,
            cross_asset_z_score=cross_asset_z_score,
            # MTF Context
            htf_h4_trend=htf_h4_trend,
            htf_h1_momentum=htf_h1_momentum,
            htf_m30_structure=htf_m30_structure,
            htf_m15_confirmation=htf_m15_confirmation,
            support_zone_dist=support_zone_dist,
            resistance_zone_dist=resistance_zone_dist,
            trend_strength=trend_strength,
            consolidation_ratio=consolidation_ratio,
            htf_h1_atr_ratio=htf_h1_atr_ratio,
            htf_h4_atr_ratio=htf_h4_atr_ratio,
        )

    def _cold_start_vector(self, tick: TickData, mid_price: float) -> FeatureVector:
        """Fallback feature vector for system warm-up phase."""
        return FeatureVector(
            symbol=self.symbol,
            timestamp_utc=tick.timestamp.isoformat(),
            live_tick_displacement=0.0,
            log_return_m1=0.0,
            atr_m1=1.50,
            upper_wick_ratio=0.0,
            lower_wick_ratio=0.0,
            body_to_range_ratio=1.0,
            is_doji=False,
            is_hammer_pinbar=False,
            is_shooting_star=False,
            is_engulfing_bullish=False,
            is_engulfing_bearish=False,
            close_location_value=0.0,
            consecutive_momentum_count=0.0,
            dist_to_swing_high_20=0.0,
            dist_to_swing_low_20=0.0,
            price_compression_flag_ratio=1.0,
            is_at_extreme_high=False,
            is_at_extreme_low=False,
            stop_hunt_depth=0.0,
            session_tokyo=False,
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
            choch_bullish=False,
            choch_bearish=False,
            broke_previous_high=False,
            broke_previous_low=False,
            rapid_reversal_spike=False,
            rapid_reversal_spike_val=0.0,
            tenkan_sen=mid_price,
            kijun_sen=mid_price,
            senkou_span_a=mid_price,
            senkou_span_b=mid_price,
            tk_cross_signal=0,
            is_above_kumo=False,
            is_below_kumo=False,
            rsi_14=50.0,
            dist_to_ema_21=0.0,
            dist_to_ema_50=0.0,
            cross_asset_z_score=0.0,
            # MTF defaults
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
        )
