"""Setup Detector v2 — "Hunter" Precision Setup Classification (PHASE 15D).

Builds on the proven 50D feature vector (ScalpFeatureEngine) — the SMC /
momentum / mean-reversion signals are ALREADY computed causally there. This
module turns those signals into EXPLAINABLE setup classifications with a
quality score, so the sample maker can label high-precision setups only.

Setup taxonomy (12+ types):
    SMC (smart money)      : LIQUIDITY_SWEEP, ORDER_BLOCK, FVG, BREAK_OF_STRUCTURE,
                             CHoCH, OTE_PULLBACK
    Momentum               : TREND_CONTINUATION, BREAKOUT_PULLBACK, IMPULSE
    Mean-reversion         : RANGING_FADE, OVERSOLD_BOUNCE, COMPRESSION_BREAK
    Session                : LONDON_BREAKOUT, NY_OPEN_SWEEP

Each setup carries:
    setup_id (deterministic), setup_type, quality (0..1), confidence factors
    (per-signal contributions), filters (spread/atr/session constraints) and
    compatible strategy hints.

Contract: PURE + CAUSAL — uses only the current row + prior rows (never future).
Every setup decision is explainable via its `factors` dict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_generation.setup_detector")

#: Registry of all setup types (for validation + docs).
SETUP_TYPES: tuple[str, ...] = (
    "LIQUIDITY_SWEEP",
    "ORDER_BLOCK",
    "FVG",
    "BREAK_OF_STRUCTURE",
    "CHOCH",
    "OTE_PULLBACK",
    "TREND_CONTINUATION",
    "BREAKOUT_PULLBACK",
    "IMPULSE",
    "RANGING_FADE",
    "OVERSOLD_BOUNCE",
    "COMPRESSION_BREAK",
    "LONDON_BREAKOUT",
    "NY_OPEN_SWEEP",
)

#: Hunter minimum quality: setups below this are discarded (UNKNOWN).
HUNTER_MIN_QUALITY: float = 0.55


@dataclass(frozen=True)
class SetupDetection:
    """One detected setup — deterministic + explainable."""

    setup_id: str
    setup_type: str
    quality: float  # 0..1 hunter confidence
    factors: dict[str, float] = field(default_factory=dict)  # per-signal contributions
    filters: dict[str, Any] = field(default_factory=dict)  # constraints that must hold
    compatible_strategies: list[str] = field(default_factory=list)
    version: str = "2.0.0"

    def to_contract(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "setup_type": self.setup_type,
            "quality": round(self.quality, 4),
            "factors": self.factors,
            "filters": self.filters,
            "compatible_strategies": self.compatible_strategies,
            "version": self.version,
        }


def _f(v: Any, default: float = 0.0) -> float:
    """Coerce a feature value to float (None-safe)."""
    try:
        x = float(v)
        return x if math.isfinite(x) else default  # NaN/Inf -> default
    except (TypeError, ValueError):
        return default


def _quality(*terms: float, weights: tuple[float, ...]) -> float:
    """Hunter quality = WEIGHTED GEOMETRIC MEAN of factor terms, clipped [0,1].

    Geometric (multiplicative) aggregation is deliberately selective: a setup
    scores high ONLY when EVERY signal is strong. A single weak factor drags
    the whole quality toward 0 — this is the "hunter" precision philosophy
    (all conditions must line up; partial setups are thrown away).
    """
    if not terms or not weights or len(terms) != len(weights):
        return 0.0
    total_w = sum(weights)
    if total_w <= 0:
        return 0.0
    # clip terms to [0,1] first (signed features use abs() upstream)
    clipped = [max(0.0, min(1.0, t)) for t in terms]
    log_sum = 0.0
    for t, w in zip(clipped, weights, strict=False):
        if t <= 0.0:
            return 0.0  # any zero factor -> no setup (hunter selectivity)
        log_sum += w * math.log(t)
    q = math.exp(log_sum / total_w)
    return max(0.0, min(1.0, q))


def _make_id(setup_type: str, row: dict[str, Any], ts: Any) -> str:
    import hashlib

    payload = f"{setup_type}|{ts}|{row.get('close', '')}|{row.get('atr_m1', row.get('atr', ''))}"
    return f"setup_{hashlib.sha256(payload.encode()).hexdigest()[:12]}"


class SetupDetector:
    """Detects hunter setups from a labeled feature frame (row-by-row, causal)."""

    def __init__(self, min_quality: float = HUNTER_MIN_QUALITY) -> None:
        self.min_quality = min_quality

    # ------------------------------------------------------------------
    # Signal helpers (read from the 50D feature vector fields)
    # ------------------------------------------------------------------

    @staticmethod
    def _sig(row: dict[str, Any], name: str, feat_idx: int | None = None) -> float:
        """Reads a named feature or feat_<idx> from the row."""
        if name in row:
            return _f(row[name])
        if feat_idx is not None:
            return _f(row.get(f"feat_{feat_idx}"))
        return 0.0

    # ------------------------------------------------------------------
    # Setups
    # ------------------------------------------------------------------

    def _detect_liquidity_sweep(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        sweep = self._sig(row, "feat_ob_liquidity_swept", 48)
        bare_sweep = self._sig(row, "liquidity_sweep_signal", 15)
        stop_hunt = self._sig(row, "stop_hunt_depth", 14)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)
        session_ok = self._session_ok(row)

        # sweep strength: combines the SMC sweep flag + stop-hunt depth.
        # Session is a BONUS multiplier (1.15x in a preferred session), never a
        # hard gate — a deep sweep still hunts outside session windows.
        q = _quality(
            abs(sweep),
            (abs(bare_sweep) + 1.0) / 2.0,
            min(abs(stop_hunt) / atr, 1.0),
            weights=(0.4, 0.3, 0.3),
        )
        if session_ok:
            q = min(1.0, q * 1.15)
        if q < self.min_quality:
            return None
        direction = "BUY" if sweep >= 0 or bare_sweep >= 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("LIQUIDITY_SWEEP", row, ts),
            setup_type="LIQUIDITY_SWEEP",
            quality=q,
            factors={
                "ob_swept": round(_f(sweep), 4),
                "sweep_signal": round(_f(bare_sweep), 4),
                "stop_hunt_depth_atr": round(abs(stop_hunt) / atr, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4), "session_ok": session_ok},
            compatible_strategies=["hunter_sweep_v1", "hunter_london_v1"],
        )

    def _detect_order_block(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        ob_type = self._sig(row, "order_block_type", 27)
        ob_equil = self._sig(row, "feat_ob_equilibrium_ratio", 47)
        ob_bos = self._sig(row, "feat_ob_valid_bos", 46)
        ob_fib = self._sig(row, "feat_ob_fib_50_60_alignment", 49)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if ob_type == 0 and ob_bos == 0:
            return None
        q = _quality(
            min(abs(ob_type), 1.0),
            abs(ob_bos),
            1.0 - min(abs(ob_equil - 0.5) * 2.0, 1.0),
            weights=(0.4, 0.4, 0.2),
        )
        # Fib 50-60 alignment is a REFINEMENT bonus, never a hard requirement:
        # a valid OB+BOS with no fib info still hunts (floor keeps geomean alive).
        if ob_fib > 0:
            q = min(1.0, q * (0.85 + 0.15 * abs(ob_fib)))
        if q < self.min_quality:
            return None
        # OB type: 1=bullish (buy), -1/2=bearish (sell)
        direction = "BUY" if ob_type > 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("ORDER_BLOCK", row, ts),
            setup_type="ORDER_BLOCK",
            quality=q,
            factors={
                "ob_type": round(_f(ob_type), 4),
                "valid_bos": round(_f(ob_bos), 4),
                "equilibrium_ratio": round(_f(ob_equil), 4),
                "fib_50_60": round(_f(ob_fib), 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_ob_v1", "hunter_smc_v1"],
        )

    def _detect_fvg(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        fvg = self._sig(row, "fvg_sig", 26)
        clv = self._sig(row, "close_location_value", 6)
        htf = self._sig(row, "htf_h4_trend", 40)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if fvg == 0:
            return None
        q = _quality(
            min(abs(fvg), 1.0),
            abs(clv),
            (htf + 1.0) / 2.0,
            weights=(0.4, 0.3, 0.3),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if fvg > 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("FVG", row, ts),
            setup_type="FVG",
            quality=q,
            factors={
                "fvg_sig": round(_f(fvg), 4),
                "clv": round(clv, 4),
                "htf_h4_trend": round(htf, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_fvg_v1", "hunter_smc_v1"],
        )

    def _detect_bos(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        bos = self._sig(row, "feat_ob_valid_bos", 46)
        breakout = self._sig(row, "breakout_sig", 29)
        displacement = self._sig(row, "norm_displacement", 8)
        htf = self._sig(row, "htf_h4_trend", 40)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if bos == 0 and breakout == 0:
            return None
        q = _quality(
            min(abs(bos), 1.0),
            min(abs(breakout), 1.0),
            min(abs(displacement), 1.0),
            (htf + 1.0) / 2.0,
            weights=(0.4, 0.3, 0.15, 0.15),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if (bos >= 0 or breakout >= 0) else "SELL"
        return SetupDetection(
            setup_id=_make_id("BREAK_OF_STRUCTURE", row, ts),
            setup_type="BREAK_OF_STRUCTURE",
            quality=q,
            factors={
                "valid_bos": round(_f(bos), 4),
                "breakout_sig": round(_f(breakout), 4),
                "displacement": round(displacement, 4),
                "htf_h4_trend": round(htf, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_bos_v1", "hunter_trend_v1"],
        )

    def _detect_choch(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        choch = self._sig(row, "choch_sig", 28)
        clv = self._sig(row, "close_location_value", 6)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if choch == 0:
            return None
        q = _quality(min(abs(choch), 1.0), abs(clv), weights=(0.7, 0.3))
        if q < self.min_quality:
            return None
        direction = "BUY" if choch > 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("CHOCH", row, ts),
            setup_type="CHOCH",
            quality=q,
            factors={
                "choch_sig": round(_f(choch), 4),
                "clv": round(clv, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_choch_v1", "hunter_reversal_v1"],
        )

    def _detect_ote_pullback(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        ob_fib = self._sig(row, "feat_ob_fib_50_60_alignment", 49)
        ob_equil = self._sig(row, "feat_ob_equilibrium_ratio", 47)
        htf = self._sig(row, "htf_h4_trend", 40)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        # OTE: price pulled back into 50-61.8% of the impulse leg, with OB alignment
        if ob_fib == 0 and ob_equil == 0:
            return None
        in_zone = min(abs(ob_fib), 1.0)  # 1.0 when price inside fib 50-60 zone
        q = _quality(in_zone, abs(ob_equil), (htf + 1.0) / 2.0, weights=(0.5, 0.25, 0.25))
        if q < self.min_quality:
            return None
        direction = "BUY" if htf >= 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("OTE_PULLBACK", row, ts),
            setup_type="OTE_PULLBACK",
            quality=q,
            factors={
                "fib_50_60": round(_f(ob_fib), 4),
                "equilibrium": round(_f(ob_equil), 4),
                "htf_h4_trend": round(htf, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_ote_v1", "hunter_pullback_v1"],
        )

    def _detect_trend_continuation(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        momentum = self._sig(row, "consecutive_momentum_count", 7)
        htf_h1 = self._sig(row, "htf_h1_momentum", 41)
        htf_h4 = self._sig(row, "htf_h4_trend", 40)
        dist_ema21 = self._sig(row, "dist_to_ema_21", 35)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        q = _quality(
            min(abs(momentum), 1.0),
            (htf_h1 + 1.0) / 2.0,
            (htf_h4 + 1.0) / 2.0,
            min(abs(dist_ema21) * 2.0, 1.0),
            weights=(0.4, 0.25, 0.2, 0.15),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if (momentum >= 0 and htf_h1 >= 0) else "SELL"
        return SetupDetection(
            setup_id=_make_id("TREND_CONTINUATION", row, ts),
            setup_type="TREND_CONTINUATION",
            quality=q,
            factors={
                "momentum": round(_f(momentum), 4),
                "htf_h1": round(htf_h1, 4),
                "htf_h4": round(htf_h4, 4),
                "dist_ema21": round(dist_ema21, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4), "trend_aligned": True},
            compatible_strategies=["hunter_trend_v1", "hunter_momentum_v1"],
        )

    def _detect_breakout_pullback(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        breakout = self._sig(row, "breakout_sig", 29)
        pullback_ok = abs(self._sig(row, "dist_to_swing_high_20", 10)) > 0.05
        htf = self._sig(row, "htf_h4_trend", 40)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if breakout == 0:
            return None
        q = _quality(
            min(abs(breakout), 1.0),
            1.0 if pullback_ok else 0.0,
            (htf + 1.0) / 2.0,
            weights=(0.5, 0.3, 0.2),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if breakout > 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("BREAKOUT_PULLBACK", row, ts),
            setup_type="BREAKOUT_PULLBACK",
            quality=q,
            factors={
                "breakout_sig": round(_f(breakout), 4),
                "pullback_room": round(
                    min(abs(self._sig(row, "dist_to_swing_high_20", 10)), 1.0), 4
                ),
                "htf_h4_trend": round(htf, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_breakout_v1", "hunter_pullback_v1"],
        )

    def _detect_impulse(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        displacement = self._sig(row, "norm_displacement", 8)
        vol_z = self._sig(row, "lag_1_volume_z", 24)
        momentum = self._sig(row, "consecutive_momentum_count", 7)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        q = _quality(
            min(abs(displacement), 1.0),
            min(abs(vol_z) / 2.0, 1.0),
            min(abs(momentum), 1.0),
            weights=(0.4, 0.3, 0.3),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if (displacement >= 0 and momentum >= 0) else "SELL"
        return SetupDetection(
            setup_id=_make_id("IMPULSE", row, ts),
            setup_type="IMPULSE",
            quality=q,
            factors={
                "displacement": round(displacement, 4),
                "volume_z": round(vol_z, 4),
                "momentum": round(momentum, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_impulse_v1"],
        )

    def _detect_ranging_fade(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        compression = self._sig(row, "price_compression_flag_ratio", 12)
        clv = self._sig(row, "close_location_value", 6)
        htf_h4 = self._sig(row, "htf_h4_trend", 40)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if compression == 0:
            return None
        # Range fade: price at range edge (|CLV| high) inside compression, HTF flat
        q = _quality(
            min(abs(compression), 1.0),
            min(abs(clv), 1.0),
            1.0 - (htf_h4 + 1.0) / 2.0,
            weights=(0.4, 0.35, 0.25),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if clv < 0 else "SELL"  # fade the edge
        return SetupDetection(
            setup_id=_make_id("RANGING_FADE", row, ts),
            setup_type="RANGING_FADE",
            quality=q,
            factors={
                "compression": round(compression, 4),
                "clv": round(clv, 4),
                "htf_h4_flat": round(1.0 - (htf_h4 + 1.0) / 2.0, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_range_v1"],
        )

    def _detect_oversold_bounce(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        rsi = self._sig(row, "norm_rsi", 34)  # normalized: (RSI-50)/16.66
        lower_wick = self._sig(row, "lower_wick_ratio", 1)
        pinbar = self._sig(row, "pinbar_sig", 4)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if rsi >= 0:
            return None  # not oversold
        q = _quality(
            min(abs(rsi) / 2.0, 1.0),
            min(lower_wick * 1.5, 1.0),
            min(abs(pinbar), 1.0),
            weights=(0.4, 0.3, 0.3),
        )
        if q < self.min_quality:
            return None
        return SetupDetection(
            setup_id=_make_id("OVERSOLD_BOUNCE", row, ts),
            setup_type="OVERSOLD_BOUNCE",
            quality=q,
            factors={
                "norm_rsi": round(rsi, 4),
                "lower_wick_ratio": round(lower_wick, 4),
                "pinbar": round(pinbar, 4),
                "direction": 1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_reversal_v1"],
        )

    def _detect_compression_break(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        compression = self._sig(row, "price_compression_flag_ratio", 12)
        breakout = self._sig(row, "breakout_sig", 29)
        displacement = self._sig(row, "norm_displacement", 8)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        # Compression -> breakout: tight range expanding with displacement
        q = _quality(
            min(abs(compression), 1.0),
            min(abs(breakout), 1.0),
            min(abs(displacement), 1.0),
            weights=(0.4, 0.35, 0.25),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if (breakout >= 0 or displacement >= 0) else "SELL"
        return SetupDetection(
            setup_id=_make_id("COMPRESSION_BREAK", row, ts),
            setup_type="COMPRESSION_BREAK",
            quality=q,
            factors={
                "compression": round(compression, 4),
                "breakout_sig": round(breakout, 4),
                "displacement": round(displacement, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4)},
            compatible_strategies=["hunter_compression_v1"],
        )

    # ------------------------------------------------------------------
    # Session-aware setups
    # ------------------------------------------------------------------

    @staticmethod
    def _session_ok(row: dict[str, Any]) -> bool:
        """Hunter prefers London/NY overlap sessions (feature flags)."""
        london = _f(row.get("session_london"), 0.0)
        ny = _f(row.get("session_ny"), 0.0)
        return bool(london or ny)

    def _detect_london_breakout(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        london = self._sig(row, "session_london", 17)
        breakout = self._sig(row, "breakout_sig", 29)
        displacement = self._sig(row, "norm_displacement", 8)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if london == 0:
            return None
        q = _quality(
            min(abs(breakout), 1.0),
            min(abs(displacement), 1.0),
            1.0,
            weights=(0.5, 0.3, 0.2),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if (breakout >= 0 or displacement >= 0) else "SELL"
        return SetupDetection(
            setup_id=_make_id("LONDON_BREAKOUT", row, ts),
            setup_type="LONDON_BREAKOUT",
            quality=q,
            factors={
                "session_london": 1.0,
                "breakout_sig": round(breakout, 4),
                "displacement": round(displacement, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4), "session": "LONDON"},
            compatible_strategies=["hunter_london_v1"],
        )

    def _detect_ny_open_sweep(self, row: dict[str, Any], ts: Any) -> SetupDetection | None:
        ny = self._sig(row, "session_ny", 18)
        sweep = self._sig(row, "feat_ob_liquidity_swept", 48)
        stop_hunt = self._sig(row, "stop_hunt_depth", 14)
        atr = max(_f(row.get("atr_m1") or row.get("atr")), 1e-6)

        if ny == 0:
            return None
        q = _quality(
            min(abs(sweep), 1.0),
            min(abs(stop_hunt) / atr, 1.0),
            1.0,
            weights=(0.5, 0.3, 0.2),
        )
        if q < self.min_quality:
            return None
        direction = "BUY" if sweep >= 0 else "SELL"
        return SetupDetection(
            setup_id=_make_id("NY_OPEN_SWEEP", row, ts),
            setup_type="NY_OPEN_SWEEP",
            quality=q,
            factors={
                "session_ny": 1.0,
                "ob_swept": round(sweep, 4),
                "stop_hunt_atr": round(abs(stop_hunt) / atr, 4),
                "direction": 1.0 if direction == "BUY" else -1.0,
            },
            filters={"min_atr": round(atr, 4), "session": "NY"},
            compatible_strategies=["hunter_sweep_v1"],
        )

    # ------------------------------------------------------------------
    # Main entry: detect all setups for one row
    # ------------------------------------------------------------------

    def detect(self, row: dict[str, Any], timestamp: Any = None) -> list[SetupDetection]:
        """Returns ALL setups detected for this row (sorted by quality desc).

        Pure + causal: only row + prior context is used; no future info.
        """
        ts = timestamp or row.get("timestamp") or row.get("time")
        detectors = (
            self._detect_liquidity_sweep,
            self._detect_order_block,
            self._detect_fvg,
            self._detect_bos,
            self._detect_choch,
            self._detect_ote_pullback,
            self._detect_trend_continuation,
            self._detect_breakout_pullback,
            self._detect_impulse,
            self._detect_ranging_fade,
            self._detect_oversold_bounce,
            self._detect_compression_break,
            self._detect_london_breakout,
            self._detect_ny_open_sweep,
        )
        out: list[SetupDetection] = []
        for det in detectors:
            try:
                sd = det(row, ts)
                if sd is not None:
                    out.append(sd)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("[SETUP] detector %s failed", det.__name__, error=str(e))
        out.sort(key=lambda s: s.quality, reverse=True)
        return out

    def best_setup(self, row: dict[str, Any], timestamp: Any = None) -> SetupDetection | None:
        """The single best (highest-quality) setup, or None."""
        dets = self.detect(row, timestamp)
        return dets[0] if dets else None


#: Convenience validator for tests/docs.
def validate_setup_type(setup_type: str) -> bool:
    return setup_type in SETUP_TYPES
