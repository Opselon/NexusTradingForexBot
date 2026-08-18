"""TASK-5 60D Feature Augmentation (schema_augment.py).

WHY THIS EXISTS
---------------
The authoritative feature registry (`features/schema.py`) forward-declares
`scalp_v2` (60D) but provides NO producer: nothing in the codebase can build
a real 60D vector. TASK-5 makes 60D a REAL, versioned, causally-correct
candidate path without touching the hot-path 50D contract (INV-009).

CONTRACT
--------
    scalp_v2 = scalp_v1 (50D, UNCHANGED) + 10 NEW FEATURES (feat_50..feat_59)

The 10 additional dimensions are chosen so that EVERY one of them is:

    1. causally correct (uses only COMPLETED bars and the current tick —
       zero future information, no exit/label/outcome leakage);
    2. runtime-available (LiveEngine already holds the bar window; the
       augmentor needs only bars + tick, never the DB);
    3. replay-available (the same bars exist in historical datasets);
    4. deterministic (same inputs -> same 60D vector, exact float order);
    5. news-independent (news stays an OPTIONAL extra input — the 60D base
       must not depend on a news engine);

Each feature has: semantic definition, causal origin, mathematical formula,
source columns, normalization, missing-data behavior, expected range,
leakage analysis.

The augmentor is a PURE function over numpy arrays. It performs NO I/O and
imports NO database/runtime modules, so it can run identically in live
inference, offline training, and historical replay (spec 19).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# =============================================================================
# 60D EXTRA FEATURE CONTRACT — feat_50 .. feat_59
# =============================================================================

#: Canonical ordered names of the 10 additional dimensions.
FEATURE_NAMES_60D_EXTRA: tuple[str, ...] = (
    "regime_compression",  # feat_50
    "momentum_5_atr",  # feat_51
    "wick_imbalance_5",  # feat_52
    "volume_z_5",  # feat_53
    "range_z_5",  # feat_54
    "clv_avg_5",  # feat_55
    "session_phase_enc",  # feat_56
    "price_acceleration",  # feat_57
    "atr_trend_ratio",  # feat_58
    "direction_bias_8",  # feat_59
)

NUM_EXTRA_60D: int = len(FEATURE_NAMES_60D_EXTRA)
BASE_60D: int = 50
SCHEMA_60D: str = "scalp_v2"

#: Deterministic numeric session phase encoding (UTC-based, matches the
#: engine's session logic in scalp_features.py). All values in [-1, 1].
#: Tokyo 00-07, London 07-14, NY 13-20, overlap 13-15 (UTC).
_SESSION_ENCODING: dict[str, float] = {
    "tokyo_only": -0.75,
    "london_only": 0.25,
    "ny_only": 0.75,
    "overlap_london_ny": 1.0,
    "asia_only": -1.0,
    "overnight": 0.0,
}

#: Default values used when the bar window is too short (causal warm-up).
DEFAULTS_60D: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)

#: Feature documentation (semantic definition / formula / source / range /
#: leakage) — consumed by reports, not by math.
FEATURE_60D_DOC: dict[str, dict[str, str]] = {
    "regime_compression": {
        "semantic": "Consolidation: ratio of the most recent 10-bar range to the 50-bar range (0=no compression, 1=full expansion).",
        "formula": "range(highs[-10:], lows[-10:]) / range(highs[-50:], lows[-50:])",
        "source": "completed bars only (close/high/low)",
        "expected_range": "[0.0, 2.0] (clipped [-3,3] by the 50D sanitizer convention)",
        "missing": "default 1.0 (no compression evidence) when fewer than 10 bars",
        "leakage": "none — uses only bars up to and including the current completed bar",
    },
    "momentum_5_atr": {
        "semantic": "Normalized 5-bar momentum: cumulative log return over the last 5 COMPLETED bars scaled by ATR.",
        "formula": "(close[-1] - close[-6]) / ATR_14  (price-difference form)",
        "source": "completed closes + ATR_14",
        "expected_range": "[-5, +5] typical",
        "missing": "default 0.0 when fewer than 6 bars",
        "leakage": "none — all inputs are closed bars at decision time",
    },
    "wick_imbalance_5": {
        "semantic": "Average lower-wick vs upper-wick imbalance over the last 5 bars (rejection/concession asymmetry).",
        "formula": "mean((low_i - min(open_i,close_i)) - (max(open_i,close_i) - high_i)) / mean(range_i)",
        "source": "completed bars (open/high/low/close)",
        "expected_range": "[-1, +1]",
        "missing": "default 0.0 when fewer than 5 bars",
        "leakage": "none",
    },
    "volume_z_5": {
        "semantic": "Z-score of mean volume over the last 5 bars vs the preceding 20-bar volume distribution (participation burst).",
        "formula": "(mean(vol[-5:]) - mean(vol[-25:-5])) / (std(vol[-25:-5]) + eps)",
        "source": "completed bar tick_volume",
        "expected_range": "[-3, +3] typical",
        "missing": "default 0.0 when volume absent or fewer than 25 bars",
        "leakage": "none",
    },
    "range_z_5": {
        "semantic": "Z-score of mean 5-bar range vs the 20-bar range distribution (volatility regime burst).",
        "formula": "(mean(range[-5:]) - mean(range[-25:-5])) / (std(range[-25:-5]) + eps)",
        "source": "completed bars (high-low)",
        "expected_range": "[-3, +3] typical",
        "missing": "default 0.0 when fewer than 25 bars",
        "leakage": "none",
    },
    "clv_avg_5": {
        "semantic": "Average Close-Location-Value over the last 5 bars (where closes sit inside each bar's range).",
        "formula": "mean(((close_i - low_i) - (high_i - close_i)) / range_i)",
        "source": "completed bars",
        "expected_range": "[-1, +1]",
        "missing": "default 0.0 when fewer than 5 bars",
        "leakage": "none",
    },
    "session_phase_enc": {
        "semantic": "Deterministic UTC session phase encoding (Asia/Tokyo/London/NY/overlap) — the same hour logic the 50D session flags use, compressed to ONE dimension.",
        "formula": "mapping on hour(timestamp_utc): tokyo_only=-0.75, london_only=0.25, ny_only=0.75, overlap=1.0, asia_only=-1.0, overnight=0.0",
        "source": "current tick timestamp (UTC) — the ONLY time-intrinsic feature in the 60D set",
        "expected_range": "[-1.0, +1.0] discrete",
        "missing": "default 0.0 when timestamp absent",
        "leakage": "none — session is known at decision time",
    },
    "price_acceleration": {
        "semantic": "Momentum acceleration: (5-bar return) - (20-bar return), normalized by ATR. Positive = recently faster than the trend.",
        "formula": "((close[-1]-close[-6]) - (close[-1]-close[-21])*5/20) / ATR_14",
        "source": "completed closes + ATR_14",
        "expected_range": "[-5, +5] typical",
        "missing": "default 0.0 when fewer than 21 bars",
        "leakage": "none",
    },
    "atr_trend_ratio": {
        "semantic": "Volatility trend: current ATR_14 relative to ATR_14 five bars earlier (>1 = expanding vol regime).",
        "formula": "ATR_14(closes[-14:]) / (ATR_14(closes[-19:-5]) + eps)",
        "source": "completed bars (high/low/close)",
        "expected_range": "[0.2, 5.0] typical (clipped)",
        "missing": "default 1.0 (neutral) when fewer than 19 bars",
        "leakage": "none",
    },
    "direction_bias_8": {
        "semantic": "Signed 8-bar direction bias: (bullish bars - bearish bars)/8. Captures short-run momentum persistence the 50D lag-return trio does not average.",
        "formula": "sum(sign(close_i - open_i) for last 8 bars) / 8",
        "source": "completed bars (open/close)",
        "expected_range": "[-1, +1]",
        "missing": "default 0.0 when fewer than 8 bars",
        "leakage": "none",
    },
}

#: Grouping for ablation (spec 32): which extra features belong to which
#: semantic group, so we can test 50D vs 50D+group vs 60D.
EXTRA_GROUPS: dict[str, tuple[int, ...]] = {
    "volatility_regime": (0, 4, 8),  # regime_compression, range_z_5, atr_trend_ratio
    "momentum_acceleration": (1, 7, 9),  # momentum_5_atr, price_acceleration, direction_bias_8
    "microstructure": (2, 5),  # wick_imbalance_5, clv_avg_5
    "participation": (3,),  # volume_z_5
    "session_context": (6,),  # session_phase_enc
}


def session_phase_encoding(hour_utc: int | None) -> float:
    """Deterministic session phase for an integer UTC hour (0-23)."""
    if hour_utc is None:
        return _SESSION_ENCODING["overnight"]
    if 0 <= hour_utc < 7:
        return _SESSION_ENCODING["tokyo_only"]
    if 13 <= hour_utc < 15:
        return _SESSION_ENCODING["overlap_london_ny"]
    if 7 <= hour_utc < 13:
        return _SESSION_ENCODING["london_only"]
    if 15 <= hour_utc < 21:
        return _SESSION_ENCODING["ny_only"]
    return _SESSION_ENCODING["overnight"]


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Simple mean true range over the LAST `period` completed bars.

    True range for bar i uses the PRIOR close as reference; the window of
    bars must therefore be at least 2 wide, and closes[:-1] is the aligned
    prior-close vector for the trailing `period` bars.
    """
    n = len(closes)
    if n < 2:
        return 0.0
    hi = np.asarray(highs[-period:], dtype=np.float64)
    lo = np.asarray(lows[-period:], dtype=np.float64)
    cl = np.asarray(closes[-period - 1 : -1], dtype=np.float64)
    # hi/lo is `period` wide; cl is `period` wide when enough history exists,
    # otherwise pad to match with the first available close.
    if len(cl) < len(hi):
        pad = len(hi) - len(cl)
        cl = np.concatenate([np.full(pad, cl[0] if len(cl) else closes[0]), cl])
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - cl), np.abs(lo - cl)))
    return float(np.mean(tr)) if len(tr) else 0.0


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) < 1e-9:
        return default
    return float(num / den)


def compute_60d_extras(
    opens: np.ndarray | None,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray | None = None,
    hour_utc: int | None = None,
) -> list[float]:
    """Computes the 10 extra 60D features from COMPLETED bar arrays.

    Args:
        opens/highs/lows/closes: 1-D float arrays, chronological, the LAST
            element is the most recent COMPLETED bar. All inputs must already
            be finite (callers sanitize; the augmentor still guards).
        volumes: optional tick-volume array (same length). None -> volume
            features default to 0.0.
        hour_utc: integer UTC hour of the DECISION tick (0-23), or None.

    Returns:
        Exactly 10 floats in FEATURE_NAMES_60D_EXTRA order. Every value is
        finite (missing/undefined inputs collapse to their documented default,
        NEVER NaN/Inf).
    """
    n = len(closes)
    if n == 0:
        return list(DEFAULTS_60D)

    highs = np.asarray(highs, dtype=np.float64)
    lows = np.asarray(lows, dtype=np.float64)
    closes = np.asarray(closes, dtype=np.float64)
    opens_arr = np.asarray(opens, dtype=np.float64) if opens is not None else None

    atr_now = _atr(highs, lows, closes)

    def _range(arr_h: np.ndarray, arr_l: np.ndarray) -> float:
        return float(np.max(arr_h) - np.min(arr_l)) if len(arr_h) else 0.0

    # 1. regime_compression (10/50 range ratio)
    regime_compression = float(DEFAULTS_60D[0])
    if n >= 10:
        r10 = _range(highs[-10:], lows[-10:])
        r50 = _range(highs[-50:], lows[-50:])
        regime_compression = _safe_div(r10, r50, default=1.0)

    # 2. momentum_5_atr — PRICE-difference / ATR. Log-return/ATR is ~0.0003 on
    #    M5 gold (prices ~3300, small relative moves) and would be a near-dead
    #    feature; absolute move scaled by ATR is the correct M5 normalization.
    momentum_5_atr = 0.0
    if n >= 6:
        move5 = float(closes[-1] - closes[-6])
        momentum_5_atr = _safe_div(move5, max(atr_now, 1e-6))

    # 3. wick_imbalance_5
    wick_imbalance_5 = 0.0
    if n >= 5 and opens_arr is not None:
        wicks: list[float] = []
        for i in range(-5, 0):
            rng = max(highs[i] - lows[i], 1e-9)
            lower = lows[i] - min(opens_arr[i], closes[i])
            upper = max(opens_arr[i], closes[i]) - highs[i]
            wicks.append((lower - upper) / rng)
        wick_imbalance_5 = float(np.mean(wicks))

    # 4. volume_z_5
    volume_z_5 = 0.0
    if volumes is not None and n >= 25:
        vol = np.asarray(volumes, dtype=np.float64)
        recent = vol[-5:]
        ref = vol[-25:-5]
        m, s = float(np.mean(ref)), float(np.std(ref))
        volume_z_5 = _safe_div(float(np.mean(recent)) - m, s)

    # 5. range_z_5
    range_z_5 = 0.0
    if n >= 25:
        rngs = highs - lows
        m, s = float(np.mean(rngs[-25:-5])), float(np.std(rngs[-25:-5]))
        range_z_5 = _safe_div(float(np.mean(rngs[-5:])) - m, s)

    # 6. clv_avg_5
    clv_avg_5 = 0.0
    if n >= 5:
        clvs: list[float] = []
        for i in range(-5, 0):
            rng = max(highs[i] - lows[i], 1e-9)
            clvs.append(((closes[i] - lows[i]) - (highs[i] - closes[i])) / rng)
        clv_avg_5 = float(np.mean(clvs))

    # 7. session_phase_enc
    session_phase_enc = session_phase_encoding(hour_utc)

    # 8. price_acceleration — recent 5-bar move minus the 20-bar trend's
    #    per-5-bar average move, scaled by ATR (price-difference form, same
    #    rationale as momentum_5_atr).
    price_acceleration = 0.0
    if n >= 21:
        move5 = float(closes[-1] - closes[-6])
        move20 = float(closes[-1] - closes[-21])
        accel = move5 - (move20 * (5.0 / 20.0))
        price_acceleration = _safe_div(accel, max(atr_now, 1e-6))

    # 9. atr_trend_ratio
    atr_trend_ratio = 1.0
    if n >= 19:
        atr_then = _atr(highs[-19:-5], lows[-19:-5], closes[-19:-5])
        atr_trend_ratio = _safe_div(atr_now, atr_then, default=1.0)

    # 10. direction_bias_8
    direction_bias_8 = 0.0
    if n >= 8 and opens_arr is not None:
        bias = 0.0
        for i in range(-8, 0):
            if closes[i] > opens_arr[i]:
                bias += 1.0
            elif closes[i] < opens_arr[i]:
                bias -= 1.0
        direction_bias_8 = bias / 8.0

    values = [
        regime_compression,
        momentum_5_atr,
        wick_imbalance_5,
        volume_z_5,
        range_z_5,
        clv_avg_5,
        session_phase_enc,
        price_acceleration,
        atr_trend_ratio,
        direction_bias_8,
    ]
    # FINITE GUARANTEE: never emit NaN/Inf into the neural input.
    out = []
    for v in values:
        if math.isnan(v) or math.isinf(v):
            out.append(0.0 if v != DEFAULTS_60D[8] else DEFAULTS_60D[8])
        else:
            out.append(max(-3.0, min(3.0, float(v))))
    return out


def augment_50d_to_60d(
    features50: list[float] | np.ndarray,
    extras: list[float] | np.ndarray,
) -> list[float]:
    """Concatenates the 50D vector with the 10 extra features.

    Raises:
        ValueError: (a) the 50D base is not exactly 50 floats, or (b) the
        extras are not exactly 10 floats. A 60D vector must NEVER be built
        silently from a wrong base/extra width (INV-009).
    """
    base = list(features50)
    extra = list(extras)
    if len(base) != BASE_60D:
        raise ValueError(
            f"augment_50d_to_60d: base vector must be exactly {BASE_60D}D, got {len(base)}"
        )
    if len(extra) != NUM_EXTRA_60D:
        raise ValueError(
            f"augment_50d_to_60d: extras must be exactly {NUM_EXTRA_60D}D, got {len(extra)}"
        )
    return base + extra


def validate_60d_vector(vector: list[float] | np.ndarray, context: str = "") -> list[float]:
    """Validates a 60D vector: exactly 60 floats, all finite (spec 10)."""
    vec = list(vector)
    if len(vec) != BASE_60D + NUM_EXTRA_60D:
        raise ValueError(
            f"60D contract violation{f' in {context}' if context else ''}: "
            f"expected {BASE_60D + NUM_EXTRA_60D}, got {len(vec)}"
        )
    for i, v in enumerate(vec):
        if not math.isfinite(v):
            raise ValueError(
                f"60D contract violation{f' in {context}' if context else ''}: "
                f"non-finite value at index {i}"
            )
    return vec


def feature_quality_report(
    frame: Any,
    schema_id: str = SCHEMA_60D,
) -> dict[str, Any]:
    """Per-feature quality audit for a dataset frame (spec 5).

    For every feat_* column in the frame reports: missing %, NaN %, Inf %,
    unique count, variance, min, max, quantiles (0/25/50/75/100), plus
    detector flags: DEAD (zero variance), NEAR_CONSTANT (<1e-6 variance),
    OUTLIER_DOMINATED (max > 3*std above the 99th percentile) and duplicate
    detection (columns whose values are identical to another column).

    The report is pure computation — it does not mutate the frame.
    """

    if frame is None or frame.is_empty():
        return {"schema_id": schema_id, "error": "EMPTY_FRAME", "features": {}}

    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    report: dict[str, Any] = {"schema_id": schema_id, "total_rows": frame.height, "features": {}}

    qs = [0.0, 0.25, 0.5, 0.75, 1.0]
    for col in feat_cols:
        s = frame[col].to_numpy().astype(np.float64)
        finite = np.isfinite(s)
        missing_pct = round(100.0 * (1.0 - finite.mean()), 4)
        nan_pct = round(100.0 * float(np.isnan(s).mean()), 4)
        inf_pct = round(100.0 * float(np.isinf(s).mean()), 4)
        f = s[finite]
        unique = len(np.unique(f)) if len(f) else 0
        var = float(np.var(f)) if len(f) else 0.0
        quantiles = [float(v) for v in np.quantile(f, qs)] if len(f) else [0.0] * 5

        flags: list[str] = []
        if unique <= 1:
            flags.append("DEAD_FEATURE")
        elif var < 1e-6:
            flags.append("NEAR_CONSTANT_FEATURE")
        if len(f):
            p99 = float(np.quantile(f, 0.99))
            sd = float(np.std(f))
            if sd > 1e-9 and abs(p99) > 3.0 * sd + 1e-6:
                flags.append("OUTLIER_DOMINATED")

        report["features"][col] = {
            "missing_pct": missing_pct,
            "nan_pct": nan_pct,
            "inf_pct": inf_pct,
            "unique_count": unique,
            "variance": round(var, 6),
            "min": round(float(f.min()), 6) if len(f) else None,
            "max": round(float(f.max()), 6) if len(f) else None,
            "quantiles": [round(q, 6) for q in quantiles],
            "flags": flags,
        }

    # duplicate detection (pairwise identical column values)
    dup_groups: list[list[str]] = []
    seen: set[str] = set()
    for i, c1 in enumerate(feat_cols):
        if c1 in seen:
            continue
        group = [c1]
        for c2 in feat_cols[i + 1 :]:
            if np.array_equal(frame[c1].to_numpy(), frame[c2].to_numpy()):
                group.append(c2)
                seen.add(c2)
        if len(group) > 1:
            dup_groups.append(group)
    report["duplicate_groups"] = dup_groups
    report["feature_count"] = len(feat_cols)
    return report
