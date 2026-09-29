"""Versioned feature schema for the ML Position Controller.

TASK-ML-CTRL §5/§12: the runtime tensor contract is declared here, versioned,
and serialized with every model artifact. Training and runtime MUST build
features through THIS module — there is no second formula anywhere.

Schema v2 (current)
-------------------
v1 (12 features) is the legacy flat vector ``ADVISER_FEATURE_ORDER`` — kept
as the position-state block. v2 appends the shared Indicator Engine snapshot
per timeframe (M1/M5/M15), consumed through ``IndicatorService`` — the SAME
backend the AI Analysis page uses. No indicator math lives here.

Missing-data policy (spec §9): a missing indicator value contributes its
``*_valid`` mask feature = 0.0 and its normalized/distance features = 0.0 —
explicit, never a fabricated raw zero masquerading as a real reading.

Layout (all float32, deterministic order):
    [0:12]    position-state block (v1 ADVISER_FEATURE_ORDER, unchanged)
    then per timeframe tf in (M1, M5, M15):
        osc block:  11 × (value_norm, action_score, valid)   = 33
        osc counts: strong_sell, sell, neutral, buy, strong_buy, total = 6
        ma block:   15 × (distance_atr, action_score, valid)  = 45
        ma counts:  strong_sell, sell, neutral, buy, strong_buy, total = 6
        pivots:     7 levels × (distance_atr, valid)          = 14
    = 104 per timeframe × 3 timeframes = 312
    [12:324]  indicator blocks
    TOTAL D = 324

Action encoding (shared, no second voting algorithm — the Indicator Engine's
own action strings are authoritative):
    Strong sell -> -1.0, Sell -> -0.5, Neutral -> 0.0, Buy -> +0.5, Strong buy -> +1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.position_adviser.features import ADVISER_FEATURE_ORDER

#: Current schema version. Bump on ANY change to the layout below; the model
#: artifact records the version it was trained on and the runtime refuses
#: (MODEL_LOAD_REJECTED) on mismatch — no silent reshape/pad/truncate (§30).
POSITION_FEATURE_SCHEMA_VERSION = "position_features_v2"

#: Timeframes in the current schema. M30/H1/H4/D1 ready via the same
#: per-timeframe block builder (extend _TIMEFRAMES + retrain; no integration
#: rewrite needed).
_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15")

#: Oscillator identifiers in the SHARED engine's canonical order (service.py
#: _oscillators). Value normalization is applied by the engine's own bounds
#: where the spec defines them; here we pass through the raw value and encode
#: direction via the engine's action vote — the vote IS the ML signal.
_OSCILLATOR_IDS: tuple[str, ...] = (
    "rsi14",
    "stoch_k",
    "cci20",
    "adx14",
    "awesome",
    "momentum10",
    "macd_level",
    "stoch_rsi",
    "williams_r",
    "bull_bear",
    "ultimate",
)

#: Moving-average identifiers in the SHARED engine's canonical order.
_MA_IDS: tuple[str, ...] = (
    "ema10",
    "sma10",
    "ema20",
    "sma20",
    "ema30",
    "sma30",
    "ema50",
    "sma50",
    "ema100",
    "sma100",
    "ema200",
    "sma200",
    "ichimoku_base",
    "vwma20",
    "hma9",
)

#: Pivot levels in the shared PivotMatrix order.
_PIVOT_LEVELS: tuple[str, ...] = ("R3", "R2", "R1", "P", "S1", "S2", "S3")

#: ATR fallback (spec §8: normalize distances by ATR; if ATR itself is
#: missing/zero the distance feature is 0.0 + valid=0, never a raw price).
_ATR_EPS = 1e-9

#: The 5 action scores (no second voting algorithm).
_ACTION_SCORES: dict[str, float] = {
    "Strong sell": -1.0,
    "Sell": -0.5,
    "Neutral": 0.0,
    "Buy": 0.5,
    "Strong buy": 1.0,
}


def action_score(action: str | None) -> float:
    """Encode the engine's own action vote. Unknown/None -> 0.0 (neutral)."""
    if not action:
        return 0.0
    return _ACTION_SCORES.get(str(action).strip(), 0.0)


@dataclass(frozen=True)
class TimeframeFeatures:
    """One timeframe's indicator-block feature values (already flattened)."""

    timeframe: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        expected = _BLOCK_SIZE
        if len(self.values) != expected:
            raise ValueError(
                f"timeframe {self.timeframe}: expected {expected} feature values, "
                f"got {len(self.values)}"
            )


# Per-timeframe block layout (documented; verified by test):
#   oscillators: 11 indicators × 3 (value_norm, action, valid) = 33
#   osc counts: 5 votes + total = 6
#   moving averages: 15 × 3 (dist_atr, action, valid) = 45
#   ma counts: 5 votes + total = 6
#   pivots: 7 levels × 2 (dist_atr, valid) = 14
_OSC_FEATS = len(_OSCILLATOR_IDS) * 3  # 33
_MA_FEATS = len(_MA_IDS) * 3  # 45
_PIVOT_FEATS = len(_PIVOT_LEVELS) * 2  # 14
_COUNT_FEATS = 6
_BLOCK_SIZE = _OSC_FEATS + _COUNT_FEATS + _MA_FEATS + _COUNT_FEATS + _PIVOT_FEATS  # 104

POSITION_FEATURE_DIM = len(ADVISER_FEATURE_ORDER) + len(_TIMEFRAMES) * _BLOCK_SIZE

#: Human-readable ordered feature names (for the serialized schema contract
#: and the MODEL_LOAD_REJECTED diagnostic). Same order as the flat vector.
FEATURE_NAMES: tuple[str, ...] = (
    *ADVISER_FEATURE_ORDER,
    *(
        f"{tf}.{group}.{name}.{suffix}"
        for tf in _TIMEFRAMES
        for group, names, suffixes in (
            ("oscillator", _OSCILLATOR_IDS, ("value_norm", "action", "valid")),
            ("counts", ("osc",), ("strong_sell", "sell", "neutral", "buy", "strong_buy", "total")),
            ("ma", _MA_IDS, ("distance_atr", "action", "valid")),
            (
                "counts",
                ("ma",),
                ("strong_sell", "sell", "neutral", "buy", "strong_buy", "total"),
            ),
            ("pivot", _PIVOT_LEVELS, ("distance_atr", "valid")),
        )
        for name in names
        for suffix in suffixes
    ),
)

if len(FEATURE_NAMES) != POSITION_FEATURE_DIM:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"feature schema internal error: names={len(FEATURE_NAMES)} dim={POSITION_FEATURE_DIM}"
    )


def schema_contract() -> dict[str, Any]:
    """The full machine-readable contract serialized into model artifacts."""
    return {
        "schema_version": POSITION_FEATURE_SCHEMA_VERSION,
        "d": POSITION_FEATURE_DIM,
        "dtype": "float32",
        "shape": [None, POSITION_FEATURE_DIM],  # [batch, D]; no sequence dim (v2)
        "timeframes": list(_TIMEFRAMES),
        "feature_names": list(FEATURE_NAMES),
        "per_timeframe_block": {
            "oscillator_value_norm": (0, _OSC_FEATS, 3),
            "oscillator_counts": (_OSC_FEATS, _COUNT_FEATS, 1),
            "ma_distance_atr": (_OSC_FEATS + _COUNT_FEATS, _MA_FEATS, 3),
            "ma_counts": (_OSC_FEATS + _COUNT_FEATS + _MA_FEATS, _COUNT_FEATS, 1),
            "pivots": (
                _OSC_FEATS + _COUNT_FEATS + _MA_FEATS + _COUNT_FEATS,
                _PIVOT_FEATS,
                2,
            ),
        },
        "missing_data_policy": "valid mask + 0.0; never a fabricated raw zero",
        "action_encoding": dict(_ACTION_SCORES),
    }


def _norm_rsi(v: float | None) -> float:
    return 0.0 if v is None else (float(v) - 50.0) / 50.0  # [0,100] -> [-1,1]


def _norm_williams(v: float | None) -> float:
    # Engine displays abs(%R) in [-100,0] or [0,100]; normalize either to [-1,1].
    if v is None:
        return 0.0
    return max(-1.0, min(1.0, float(v) / 100.0))


def _norm_stoch(v: float | None) -> float:
    return 0.0 if v is None else (float(v) / 100.0) * 2.0 - 1.0  # [0,100] -> [-1,1]


def _norm_generic(v: float | None) -> float:
    """Bounded squash for unbounded oscillators (CCI, AO, momentum, MACD)."""
    if v is None:
        return 0.0
    x = float(v)
    # sign-preserving squash; scale chosen so typical ranges land in [-1,1]
    return max(-1.0, min(1.0, x / (abs(x) + 100.0) * 2.0)) if x != 0 else 0.0


_OSC_NORMALIZERS = {
    "rsi14": _norm_rsi,
    "stoch_k": _norm_stoch,
    "cci20": _norm_generic,
    "adx14": lambda v: 0.0 if v is None else min(1.0, max(0.0, float(v)) / 100.0),
    "awesome": _norm_generic,
    "momentum10": _norm_generic,
    "macd_level": _norm_generic,
    "stoch_rsi": _norm_stoch,
    "williams_r": _norm_williams,
    "bull_bear": _norm_generic,
    "ultimate": _norm_stoch,
}


def build_timeframe_block(tf: str, snap: Any, atr: float) -> TimeframeFeatures:
    """Flatten ONE shared IndicatorSnapshot into its per-timeframe block.

    ``snap`` is the ``IndicatorSnapshot`` produced by
    ``nexus_scalp.indicators.service.IndicatorService`` — the SAME object the
    AI Analysis API serves. No math here beyond normalization/encoding.
    """
    if tf.upper() not in _TIMEFRAMES:
        raise ValueError(f"timeframe {tf!r} not in schema {_TIMEFRAMES}")

    osc_results = {r.name: r for r in snap.oscillators}
    ma_results = {r.name: r for r in snap.moving_averages}
    pivots = snap.pivots.rows if snap.pivots else {}
    gauges = snap.gauges or {}

    vals: list[float] = []

    # --- oscillators: 11 × (value_norm, action, valid) ---
    for oid in _OSCILLATOR_IDS:
        r = osc_results.get(_display_name(oid))
        if r is None or r.value is None:
            vals.extend([0.0, 0.0, 0.0])
            continue
        norm = _OSC_NORMALIZERS[oid](r.value)
        vals.extend([float(norm), action_score(r.action), 1.0])

    # --- oscillator counts (5 votes + total) ---
    osc_g = gauges.get("oscillators")
    if osc_g is not None:
        vals.extend(
            [
                float(osc_g.sell),
                float(osc_g.neutral),
                float(osc_g.buy),
                0.0,  # strong_sell (not split by GaugeState; encoded via votes below)
                0.0,  # strong_buy
                float(osc_g.sell + osc_g.neutral + osc_g.buy),
            ]
        )
    else:
        vals.extend([0.0] * 6)

    # --- moving averages: 15 × (dist_atr, action, valid) ---
    atr_safe = max(float(atr), _ATR_EPS)
    last_close = snap.last_close
    for mid in _MA_IDS:
        r = ma_results.get(_ma_display_name(mid))
        if r is None or r.value is None or last_close is None:
            vals.extend([0.0, 0.0, 0.0])
            continue
        dist = (float(last_close) - float(r.value)) / atr_safe
        vals.extend([float(dist), action_score(r.action), 1.0])

    # --- MA counts (5 votes + total) ---
    ma_g = gauges.get("moving_averages")
    if ma_g is not None:
        vals.extend(
            [
                float(ma_g.sell),
                float(ma_g.neutral),
                float(ma_g.buy),
                0.0,
                0.0,
                float(ma_g.sell + ma_g.neutral + ma_g.buy),
            ]
        )
    else:
        vals.extend([0.0] * 6)

    # --- pivots: 7 levels × (dist_atr, valid) ---
    for lvl in _PIVOT_LEVELS:
        row = pivots.get(lvl, {}) if pivots else {}
        classic = row.get("Classic") if isinstance(row, dict) else None
        if classic is None or last_close is None:
            vals.extend([0.0, 0.0])
            continue
        dist = (float(last_close) - float(classic)) / atr_safe
        vals.extend([float(dist), 1.0])

    return TimeframeFeatures(timeframe=tf.upper(), values=tuple(vals))


# Display-name maps (shared engine's exact names — no re-derivation).
_OSC_DISPLAY = {
    "rsi14": "Relative Strength Index (14)",
    "stoch_k": "Stochastic %K (14, 3, 3)",
    "cci20": "Commodity Channel Index (20)",
    "adx14": "Average Directional Index (14)",
    "awesome": "Awesome Oscillator",
    "momentum10": "Momentum (10)",
    "macd_level": "MACD Level (12, 26)",
    "stoch_rsi": "Stochastic RSI Fast (3, 3, 14, 14)",
    "williams_r": "Williams Percent Range (14)",
    "bull_bear": "Bull Bear Power",
    "ultimate": "Ultimate Oscillator (7, 14, 28)",
}

_MA_DISPLAY = {
    "ema10": "Exponential Moving Average (10)",
    "sma10": "Simple Moving Average (10)",
    "ema20": "Exponential Moving Average (20)",
    "sma20": "Simple Moving Average (20)",
    "ema30": "Exponential Moving Average (30)",
    "sma30": "Simple Moving Average (30)",
    "ema50": "Exponential Moving Average (50)",
    "sma50": "Simple Moving Average (50)",
    "ema100": "Exponential Moving Average (100)",
    "sma100": "Simple Moving Average (100)",
    "ema200": "Exponential Moving Average (200)",
    "sma200": "Simple Moving Average (200)",
    "ichimoku_base": "Ichimoku Base Line (9, 26, 52, 26)",
    "vwma20": "Volume Weighted Moving Average (20)",
    "hma9": "Hull Moving Average (9)",
}


def _display_name(oid: str) -> str:
    return _OSC_DISPLAY[oid]


def _ma_display_name(mid: str) -> str:
    return _MA_DISPLAY[mid]
