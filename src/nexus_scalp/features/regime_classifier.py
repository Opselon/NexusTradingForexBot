"""
Institutional Microstructure Market Regime Engine (Module 1)
===========================================================

Production-Grade microstructure regime classifier for XAUUSD scalping.

Key properties
--------------
- Strict O(1) hot-path for rolling metrics:
    * 5m Realized Volatility (RV) via running sum of squared log-returns.
    * Tick velocity via ring timestamps.
    * OFI via running sum (and Level2 OBI fallback with fixed top-K depth).
- Constant memory footprint via bounded deques (ring buffers).
- Graceful degradation:
    Level2 DOM -> OBI; else Level1 tick-delta fallback.
- Regime Hysteresis / Debounce:
    Prevents regime oscillation (esp. around spread thresholds) by enforcing:
        * minimum hold time
        * enter/exit thresholds (Schmitt trigger)
        * probability margin for switching

Output
------
MarketRegimeState is frozen (immutable) and includes:
- regime_type, probability
- ofi, rv_5m, tick_velocity
- spread
- recommended_execution_type
- decision_reason (for debugging/policy)
"""

from __future__ import annotations

import math
from collections import deque
from datetime import UTC
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from nexus_scalp.domain.models import TickData
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.features.regime_classifier")


# -----------------------------
# Enums
# -----------------------------


class RegimeType(StrEnum):
    MACRO_NEWS_FREEZE = "MACRO_NEWS_FREEZE"
    HIGH_SPREAD_CHOP = "HIGH_SPREAD_CHOP"
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    TRENDING_MOMENTUM = "TRENDING_MOMENTUM"
    RANGING_MEAN_REVERSION = "RANGING_MEAN_REVERSION"


class RecommendedExecutionType(StrEnum):
    FREEZE_ALL = "FREEZE_ALL"
    LIMIT_ONLY = "LIMIT_ONLY"
    PASSIVE_LIMIT = "PASSIVE_LIMIT"
    IOC_MARKET = "IOC_MARKET"
    HYBRID_LIMIT_STOP = "HYBRID_LIMIT_STOP"


class RegimeReason(StrEnum):
    WARMUP = "WARMUP"
    MACRO_NEWS = "MACRO_NEWS"
    SPREAD_SCHMITT = "SPREAD_SCHMITT"
    RV_SPIKE = "RV_SPIKE"
    TICK_DENSITY = "TICK_DENSITY"
    OFI_TREND_ALIGN = "OFI_TREND_ALIGN"
    DEFAULT_RANGE = "DEFAULT_RANGE"
    HYSTERESIS_HOLD = "HYSTERESIS_HOLD"
    HYSTERESIS_MARGIN = "HYSTERESIS_MARGIN"


# -----------------------------
# State Model
# -----------------------------


class MarketRegimeState(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timestamp_utc: str

    regime_type: RegimeType
    regime_probability: float = Field(..., ge=0.0, le=1.0)

    order_flow_imbalance: float = Field(..., ge=-1.0, le=1.0)
    realized_volatility_5m: float
    tick_velocity_per_sec: float

    current_spread_usd: float
    is_macro_news_active: bool

    recommended_execution_type: RecommendedExecutionType
    reason: RegimeReason


# -----------------------------
# Classifier
# -----------------------------


class MarketRegimeClassifier:
    """..."""

    # Activity/market-stress ordinal used by the hysteresis gate to decide
    # whether a candidate switch is an ESCALATION (into a more active/special
    # regime) or a de-escalation (back toward neutral). Higher = more active /
    # more intervention. RANGING_MEAN_REVERSION is the neutral baseline (0).
    _REGIME_ACTIVITY: ClassVar[dict[RegimeType, int]] = {
        RegimeType.RANGING_MEAN_REVERSION: 0,
        RegimeType.TRENDING_MOMENTUM: 1,
        RegimeType.VOLATILITY_EXPANSION: 2,
        RegimeType.HIGH_SPREAD_CHOP: 3,
        RegimeType.MACRO_NEWS_FREEZE: 3,
    }

    def __init__(
        self,
        symbol: str = "XAUUSD",
        # Rolling window
        rolling_seconds: int = 300,
        max_ticks_buffer: int = 6000,
        # Spread Schmitt-trigger thresholds (hysteresis band).
        # Calibrated for XAUUSD from 100k real M1 bars (2026-05-01..2026-08-17):
        #   spread_usd p50=$0.04, p90=$0.20, p95=$0.24, p99=$0.34, max=$6.22.
        # Enter chop (FREEZE_ALL guard) when spread >= $0.25 (≈p97 of normal Gold),
        # exit only when spread <= $0.18 (hysteresis band of $0.07). This makes
        # HIGH_SPREAD_CHOP reachable during genuine spread widening without firing
        # on routine $0.04-0.24 quiet-session spreads. See BUG-132.
        spread_chop_enter_usd: float = 0.25,
        spread_chop_exit_usd: float = 0.18,
        # Volatility thresholds (PRICE-based only — tick_velocity removed as a
        # volatility proxy, see BUG-132 / VOLATILITY_EXPANSION below).
        # Calibrated from real XAUUSD 5-min realized vol (sqrt sum sq log-ret):
        #   rv_5m p50=0.00062, p75=0.00091, p90=0.00128, p95=0.00160, p99=0.00250.
        # Enter VOLATILITY_EXPANSION at p~P90 (0.0013), exit at p~P75 (0.0010).
        rv_expand_enter: float = 0.0013,
        rv_expand_exit: float = 0.0010,
        # Tick-VELOCITY is feed-activity, NOT volatility (see cal. evidence).
        # Retained as a *context* field + a secondary, high-bar VOLATILITY trigger
        # for the rare case of a price-driven burst the rv_5m ring missed. It is
        # intentionally set far above any observed XAUUSD feed rate (p99=13.9/s,
        # max=32/s) so it does NOT drive classification on normal data.
        tick_vel_expand_enter: float = 20.0,
        tick_vel_expand_exit: float = 15.0,
        # Trend thresholds. Calibrated from real XAUUSD 5-min aggregate:
        #   |cumulative 5-min return| p50=0.00044, p75=0.00081, p90=0.00131,
        #   p95=0.00171, p99=0.00292. Require genuine directional displacement
        #   >= p~P85 (0.0010) AND a realized-vol floor >= p~P55 (0.0004).
        ofi_trend_threshold: float = 0.40,
        price_trend_threshold: float = 0.0010,  # ~0.10% 5m cumulative price displacement
        rv_trend_floor: float = 0.0004,
        # Hysteresis timing and confidence margin
        min_regime_hold_sec: float = 4.0,  # Fast 4s hold window
        switch_prob_margin: float = 0.10,  # require new_prob >= old_prob + margin
        # Warmup
        min_ticks_for_stats: int = 15,
        depth_top_k: int = 5,
    ) -> None:
        self.symbol = symbol

        self.rolling_seconds = int(rolling_seconds)
        self.min_ticks_for_stats = int(min_ticks_for_stats)

        # Hysteresis parameters
        self.min_regime_hold_sec = float(min_regime_hold_sec)
        self.switch_prob_margin = float(switch_prob_margin)

        # Spread Schmitt trigger
        self.spread_chop_enter = float(spread_chop_enter_usd)
        self.spread_chop_exit = float(spread_chop_exit_usd)

        # Volatility thresholds
        self.rv_expand_enter = float(rv_expand_enter)
        self.rv_expand_exit = float(rv_expand_exit)

        # Tick density thresholds
        self.tick_vel_expand_enter = float(tick_vel_expand_enter)
        self.tick_vel_expand_exit = float(tick_vel_expand_exit)

        # Trend rule
        self.ofi_trend_threshold = float(ofi_trend_threshold)
        self.price_trend_threshold = float(price_trend_threshold)
        self.rv_trend_floor = float(rv_trend_floor)

        self.depth_top_k = int(depth_top_k)

        # Fixed ring buffers (constant memory)
        self._ts: deque[float] = deque(maxlen=max_ticks_buffer)
        self._log_ret: deque[float] = deque(maxlen=max_ticks_buffer)
        self._ofi: deque[float] = deque(maxlen=max_ticks_buffer)

        # Running sums (O(1))
        self._sum_ret: float = 0.0
        self._sum_sq_ret: float = 0.0
        self._sum_ofi: float = 0.0

        # Previous tick cache
        self._prev_bid: float = 0.0
        self._prev_ask: float = 0.0
        self._prev_mid: float = 0.0

        # Hysteresis stable regime cache
        self._stable_regime: RegimeType | None = None
        self._stable_prob: float = 0.0
        self._stable_since_sec: float = 0.0
        self._last_logged_regime: RegimeType | None = None

        # Last computed metrics (cached each classify_tick) for observability.
        self._last_spread: float = 0.0
        self._last_rv_5m: float = 0.0
        self._last_tick_vel: float = 0.0
        self._last_ofi: float = 0.0

    # -------------------------
    # Public API
    # -------------------------

    def classify_tick(
        self,
        current_tick: TickData,
        is_macro_news_window: bool = False,
        level2_depth: dict[str, list[tuple[float, float]]] | None = None,
    ) -> MarketRegimeState:
        now_utc = current_tick.timestamp
        now_sec = float(now_utc.timestamp())

        bid = float(current_tick.bid)
        ask = float(current_tick.ask)
        mid = (bid + ask) * 0.5

        # Spread rounding: keep 2 decimals to match your logs, but compute comparisons on raw
        spread = ask - bid
        spread_usd = round(spread, 2)

        # Cold start init
        if self._prev_mid <= 0.0:
            self._prev_bid, self._prev_ask, self._prev_mid = bid, ask, mid

        # 1) O(1) compute log return + OFI
        log_ret = math.log(mid / self._prev_mid) if self._prev_mid > 0.0 and mid > 0.0 else 0.0
        ofi = self._compute_ofi(bid, ask, float(getattr(current_tick, "volume", 1.0)), level2_depth)

        self._push(now_sec, log_ret, ofi)
        self._evict(now_sec)

        # Update prev cache
        self._prev_bid, self._prev_ask, self._prev_mid = bid, ask, mid

        # 2) Warmup guard
        n = len(self._ts)
        if n < self.min_ticks_for_stats:
            return self._state(
                now_utc=now_utc,
                regime=RegimeType.RANGING_MEAN_REVERSION,
                prob=0.50,
                norm_ofi=0.0,
                rv_5m=0.0,
                tick_velocity=1.0,
                spread_usd=spread_usd,
                is_macro_news=is_macro_news_window,
                exec_type=RecommendedExecutionType.PASSIVE_LIMIT,
                reason=RegimeReason.WARMUP,
                log_transition=False,
            )

        # 3) Strict O(1) metrics
        elapsed = max(self._ts[-1] - self._ts[0], 1.0)
        tick_velocity = round(n / elapsed, 2)

        rv_5m = math.sqrt(max(self._sum_sq_ret, 0.0))
        avg_ofi = self._sum_ofi / max(n, 1)
        norm_ofi = float(math.tanh(avg_ofi))  # [-1, +1]

        # 4) Candidate regime decision (rules + Schmitt triggers)
        cand_regime, cand_prob, cand_exec, cand_reason = self._candidate_regime(
            is_macro_news=is_macro_news_window,
            spread=spread,
            rv_5m=rv_5m,
            tick_velocity=tick_velocity,
            norm_ofi=norm_ofi,
        )

        # 5) Apply hysteresis gate -> stable regime
        stable_regime, stable_prob, stable_exec, stable_reason = self._apply_hysteresis(
            now_sec=now_sec,
            candidate_regime=cand_regime,
            candidate_prob=cand_prob,
            candidate_exec=cand_exec,
            candidate_reason=cand_reason,
        )

        # 6) Log on stable transitions only
        if stable_regime != self._last_logged_regime:
            logger.info(
                "[REGIME TRANSITION]",
                symbol=self.symbol,
                previous_regime=self._last_logged_regime.value
                if self._last_logged_regime
                else "INIT",
                new_regime=stable_regime.value,
                prob=f"{stable_prob * 100:.1f}%",
                ofi=f"{norm_ofi:+.2f}",
                rv_5m=f"{rv_5m:.6f}",
                spread=f"${spread_usd:.2f}",
                tick_vel=tick_velocity,
                reason=stable_reason.value,
            )
            self._last_logged_regime = stable_regime

        # Cache last computed metrics for decision_diagnostics() observability.
        self._last_spread = float(spread_usd)
        self._last_rv_5m = float(rv_5m)
        self._last_tick_vel = float(tick_velocity)
        self._last_ofi = float(norm_ofi)

        return self._state(
            now_utc=now_utc,
            regime=stable_regime,
            prob=stable_prob,
            norm_ofi=norm_ofi,
            rv_5m=rv_5m,
            tick_velocity=tick_velocity,
            spread_usd=spread_usd,
            is_macro_news=is_macro_news_window,
            exec_type=stable_exec,
            reason=stable_reason,
            log_transition=False,
        )

    # -------------------------
    # O(1) ring ops
    # -------------------------

    def _push(self, now_sec: float, log_ret: float, ofi: float) -> None:
        self._ts.append(now_sec)
        self._log_ret.append(log_ret)
        self._ofi.append(ofi)

        self._sum_ret += log_ret
        self._sum_sq_ret += log_ret * log_ret
        self._sum_ofi += ofi

    def _evict(self, now_sec: float) -> None:
        cutoff = now_sec - self.rolling_seconds
        while self._ts and self._ts[0] < cutoff:
            self._ts.popleft()

            old_ret = self._log_ret.popleft()
            old_ofi = self._ofi.popleft()

            self._sum_ret -= old_ret
            self._sum_sq_ret -= old_ret * old_ret
            self._sum_ofi -= old_ofi

    # -------------------------
    # Candidate regime logic
    # -------------------------

    def _candidate_regime(
        self,
        *,
        is_macro_news: bool,
        spread: float,
        rv_5m: float,
        tick_velocity: float,
        norm_ofi: float,
    ) -> tuple[RegimeType, float, RecommendedExecutionType, RegimeReason]:

        # A) Macro news -> always freeze
        if is_macro_news:
            return (
                RegimeType.MACRO_NEWS_FREEZE,
                0.99,
                RecommendedExecutionType.FREEZE_ALL,
                RegimeReason.MACRO_NEWS,
            )

        # B) Spread schmitt-trigger -> HIGH_SPREAD_CHOP with hysteresis band
        # If currently in HIGH_SPREAD_CHOP, require spread <= exit to leave.
        in_chop = self._stable_regime == RegimeType.HIGH_SPREAD_CHOP
        if (not in_chop and spread >= self.spread_chop_enter) or (
            in_chop and spread >= self.spread_chop_exit
        ):
            # probability increases as spread exceeds enter threshold
            # clamp [0.80, 0.99]
            over = max(0.0, spread - self.spread_chop_enter)
            prob = min(0.99, 0.80 + over * 0.50)
            return (
                RegimeType.HIGH_SPREAD_CHOP,
                prob,
                RecommendedExecutionType.FREEZE_ALL,
                RegimeReason.SPREAD_SCHMITT,
            )

        # C) Volatility expansion schmitt-trigger (rv and tick density)
        in_expand = self._stable_regime == RegimeType.VOLATILITY_EXPANSION

        rv_trigger = (
            (rv_5m >= self.rv_expand_enter) if not in_expand else (rv_5m >= self.rv_expand_exit)
        )
        tv_trigger = (
            (tick_velocity >= self.tick_vel_expand_enter)
            if not in_expand
            else (tick_velocity >= self.tick_vel_expand_exit)
        )

        if rv_trigger or tv_trigger:
            # probability based on normalized exceedance
            rv_ratio = (rv_5m / self.rv_expand_enter) if self.rv_expand_enter > 0 else 1.0
            tv_ratio = (
                (tick_velocity / self.tick_vel_expand_enter)
                if self.tick_vel_expand_enter > 0
                else 1.0
            )
            score = max(rv_ratio, tv_ratio)
            prob = min(0.95, 0.55 + (min(score, 2.5) - 1.0) * 0.25)
            reason = RegimeReason.RV_SPIKE if rv_ratio >= tv_ratio else RegimeReason.TICK_DENSITY
            return (
                RegimeType.VOLATILITY_EXPANSION,
                prob,
                RecommendedExecutionType.HYBRID_LIMIT_STOP,
                reason,
            )

        # D) Trending momentum: strong OFI and enough RV floor
        cum_ret_abs = abs(self._sum_ret)
        has_price_trend = cum_ret_abs >= self.price_trend_threshold
        has_ofi_trend = abs(norm_ofi) >= self.ofi_trend_threshold

        if (has_price_trend or has_ofi_trend) and rv_5m >= self.rv_trend_floor:
            prob = min(0.95, 0.60 + max(cum_ret_abs * 200.0, abs(norm_ofi) * 0.35))
            return (
                RegimeType.TRENDING_MOMENTUM,
                float(prob),
                RecommendedExecutionType.IOC_MARKET,
                RegimeReason.OFI_TREND_ALIGN,
            )

        # E) Default range / mean reversion
        # Higher prob when OFI neutral and RV low
        if self.rv_expand_enter > 0:
            rv_norm = min(rv_5m / self.rv_expand_enter, 1.0)
        else:
            rv_norm = 0.0
        prob = max(0.60, 1.0 - abs(norm_ofi) - rv_norm * 0.6)
        return (
            RegimeType.RANGING_MEAN_REVERSION,
            float(prob),
            RecommendedExecutionType.PASSIVE_LIMIT,
            RegimeReason.DEFAULT_RANGE,
        )

    def decision_diagnostics(self) -> dict[str, object]:
        """Auditable snapshot of the live classifier inputs and which decision
        conditions are currently firing. Used by the debug/telemetry UI so an
        operator can see WHY a regime was selected (not just the final label).

        All thresholds are the active (possibly recalibrated) instance values,
        so this reflects the exact logic that produced the last stable regime.
        """
        cum_ret_abs = abs(self._sum_ret)
        in_chop = self._stable_regime == RegimeType.HIGH_SPREAD_CHOP
        in_expand = self._stable_regime == RegimeType.VOLATILITY_EXPANSION
        return {
            "thresholds": {
                "spread_chop_enter_usd": self.spread_chop_enter,
                "spread_chop_exit_usd": self.spread_chop_exit,
                "rv_expand_enter": self.rv_expand_enter,
                "rv_expand_exit": self.rv_expand_exit,
                "tick_vel_expand_enter": self.tick_vel_expand_enter,
                "tick_vel_expand_exit": self.tick_vel_expand_exit,
                "price_trend_threshold": self.price_trend_threshold,
                "ofi_trend_threshold": self.ofi_trend_threshold,
                "rv_trend_floor": self.rv_trend_floor,
            },
            "state": {
                "stable_regime": self._stable_regime.value if self._stable_regime else None,
                "in_chop": in_chop,
                "in_expand": in_expand,
                "sum_ret_abs": round(cum_ret_abs, 6),
            },
            "conditions": {
                # Each key is True when that branch would (or did) fire given the
                # current rolling metrics + stable-regime Schmitt state.
                "macro_news_freeze": False,  # only set by caller; not derivable here
                "high_spread_chop": (
                    (not in_chop and self._last_spread >= self.spread_chop_enter)
                    or (in_chop and self._last_spread >= self.spread_chop_exit)
                ),
                "volatility_expansion_rv": (
                    (self._last_rv_5m >= self.rv_expand_enter)
                    if not in_expand
                    else (self._last_rv_5m >= self.rv_expand_exit)
                ),
                "volatility_expansion_tickvel": (
                    (self._last_tick_vel >= self.tick_vel_expand_enter)
                    if not in_expand
                    else (self._last_tick_vel >= self.tick_vel_expand_exit)
                ),
                "trending_price": cum_ret_abs >= self.price_trend_threshold
                and self._last_rv_5m >= self.rv_trend_floor,
                "trending_ofi": abs(self._last_ofi) >= self.ofi_trend_threshold
                and self._last_rv_5m >= self.rv_trend_floor,
                "ranging_default": True,  # the fallback branch
            },
        }

    # -------------------------
    # Hysteresis gate
    # -------------------------

    def _apply_hysteresis(
        self,
        *,
        now_sec: float,
        candidate_regime: RegimeType,
        candidate_prob: float,
        candidate_exec: RecommendedExecutionType,
        candidate_reason: RegimeReason,
    ) -> tuple[RegimeType, float, RecommendedExecutionType, RegimeReason]:

        # First regime
        if self._stable_regime is None:
            self._stable_regime = candidate_regime
            self._stable_prob = candidate_prob
            self._stable_since_sec = now_sec
            return candidate_regime, candidate_prob, candidate_exec, candidate_reason

        # Same regime -> refresh prob and keep
        if candidate_regime == self._stable_regime:
            self._stable_prob = candidate_prob
            return candidate_regime, candidate_prob, candidate_exec, candidate_reason

        # Enforce minimum hold time
        held_for = now_sec - self._stable_since_sec
        if held_for < self.min_regime_hold_sec:
            # Keep current stable regime; reason indicates hold
            return (
                self._stable_regime,
                self._stable_prob,
                self._exec_for(self._stable_regime),
                RegimeReason.HYSTERESIS_HOLD,
            )

        # Require confidence margin to switch.
        # We only demand the margin when ESCALATING into a MORE ACTIVE / special
        # regime than the current one (e.g. RANGING -> TRENDING/VOLATILITY, or
        # TRENDING -> VOLATILITY). This prevents a single noisy tick from flipping
        # the regime into a high-intervention state. De-escalation back toward the
        # neutral RANGING regime is gated instead by the Schmitt exit bands inside
        # the candidate logic plus min_regime_hold_sec, NOT by a probability margin
        # that RANGING (ranged ~0.60-0.90) could never satisfy.
        # The old code required the margin for ALL switches out of a safe regime,
        # which made TRENDING_MEAN_REVERSION absorbing: once entered it could never
        # leave because the candidate RANGING prob was never >= stable_prob+margin.
        # See BUG-132.
        # UNSAFE regimes (CHOP/NEWS) are intentionally NOT covered by the margin:
        # when the market normalizes we must ALWAYS be able to relax the FREEZE_ALL
        # guard immediately (safety-critical — never get stuck frozen). The
        # `not is_current_unsafe` guard below handles that.
        is_current_unsafe = self._stable_regime in (
            RegimeType.HIGH_SPREAD_CHOP,
            RegimeType.MACRO_NEWS_FREEZE,
        )
        if not is_current_unsafe:
            escalating = (
                self._REGIME_ACTIVITY[candidate_regime] > self._REGIME_ACTIVITY[self._stable_regime]
            )
            if escalating and candidate_prob < (self._stable_prob + self.switch_prob_margin):
                return (
                    self._stable_regime,
                    self._stable_prob,
                    self._exec_for(self._stable_regime),
                    RegimeReason.HYSTERESIS_MARGIN,
                )

        # Switch accepted
        self._stable_regime = candidate_regime
        self._stable_prob = candidate_prob
        self._stable_since_sec = now_sec
        return candidate_regime, candidate_prob, candidate_exec, candidate_reason

    def _exec_for(self, regime: RegimeType) -> RecommendedExecutionType:
        if regime in (RegimeType.MACRO_NEWS_FREEZE, RegimeType.HIGH_SPREAD_CHOP):
            return RecommendedExecutionType.FREEZE_ALL
        if regime == RegimeType.VOLATILITY_EXPANSION:
            return RecommendedExecutionType.HYBRID_LIMIT_STOP
        if regime == RegimeType.TRENDING_MOMENTUM:
            return RecommendedExecutionType.IOC_MARKET
        return RecommendedExecutionType.PASSIVE_LIMIT

    # -------------------------
    # OFI computation (Level2 -> OBI, else Level1)
    # -------------------------

    def _compute_ofi(
        self,
        bid: float,
        ask: float,
        volume: float,
        level2_depth: dict[str, list[tuple[float, float]]] | None,
    ) -> float:
        # Level2 OBI (fixed top-K, effectively constant time)
        if level2_depth:
            bids = level2_depth.get("bids") or []
            asks = level2_depth.get("asks") or []
            if bids and asks:
                # top-K only
                tb = 0.0
                ta = 0.0
                for _, v in bids[: self.depth_top_k]:
                    tb += float(v)
                for _, v in asks[: self.depth_top_k]:
                    ta += float(v)
                tot = tb + ta
                if tot > 0.0:
                    return (tb - ta) / tot

        # Level1 fallback: directional volume impulse
        vol_eff = max(volume, 1.0)
        norm_vol = math.tanh(math.log1p(vol_eff) / 3.0)  # [0,1]

        if bid > self._prev_bid:
            return 1.0 * norm_vol
        if ask < self._prev_ask:
            return -1.0 * norm_vol

        mid = (bid + ask) * 0.5
        if mid > self._prev_mid:
            return 0.5 * norm_vol
        if mid < self._prev_mid:
            return -0.5 * norm_vol
        return 0.0

    # -------------------------
    # State builder
    # -------------------------

    def _state(
        self,
        *,
        now_utc,
        regime: RegimeType,
        prob: float,
        norm_ofi: float,
        rv_5m: float,
        tick_velocity: float,
        spread_usd: float,
        is_macro_news: bool,
        exec_type: RecommendedExecutionType,
        reason: RegimeReason,
        log_transition: bool,
    ) -> MarketRegimeState:
        # Ensure UTC isoformat stable
        ts = now_utc.astimezone(UTC).isoformat()

        return MarketRegimeState(
            symbol=self.symbol,
            timestamp_utc=ts,
            regime_type=regime,
            regime_probability=round(float(prob), 2),
            order_flow_imbalance=round(float(norm_ofi), 2),
            realized_volatility_5m=round(float(rv_5m), 6),
            tick_velocity_per_sec=float(tick_velocity),
            current_spread_usd=float(spread_usd),
            is_macro_news_active=bool(is_macro_news),
            recommended_execution_type=exec_type,
            reason=reason,
        )
