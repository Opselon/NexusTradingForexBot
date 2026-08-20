"""Market Structure & Liquidity Intelligence Engine (MSLIE) — orchestrator.

The engine is the market PERCEPTION layer of the AI trading system. It
consumes raw market data (OHLC bars + volume + spread context) and produces
the structured ``MarketIntelligenceFeatureVectorV1`` consumed by AI models.

ARCHITECTURAL POSITION
----------------------
Market Data Layer -> Normalization -> Multi-Timeframe Aggregation ->
   [ Market Structure Intelligence Engine ] -> [ Liquidity Intelligence
   Engine ] -> Feature Vector Generator -> AI Models -> Decision Layer ->
   Risk Management -> Execution

The engine NEVER bypasses risk management, execution validation or the
existing AI contracts. It holds NO adapter, NO order manager, NO risk engine
(INV-002 safety contract). It is a pure perception producer.

MULTI-MONTH MARKET MEMORY
-------------------------
The engine maintains a bounded, causal memory of institutional levels:
monthly major highs/lows, weekly institutional zones, daily structure,
intraday execution context. Levels are remembered with their creation date,
event history ("Rejected", "Liquidity accumulated", "Stop sweep occurred")
and touch count.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from nexus_scalp.mslie.breakout import assess_breakout_quality
from nexus_scalp.mslie.liquidity_map import build_liquidity_map
from nexus_scalp.mslie.models import (
    LiquiditySweepEvent,
    LiquidityZone,
    MarketBias,
    MarketContext,
    MarketIntelligenceFeatureVectorV1,
    MarketMemoryLevel,
    MarketRegimeFeatures,
    SmartMoneyFeatures,
)
from nexus_scalp.mslie.regime import compute_regime_features
from nexus_scalp.mslie.smart_money import compute_smart_money_features
from nexus_scalp.mslie.sweep import detect_sweep_events
from nexus_scalp.mslie.swing import detect_swings

# =============================================================================
# VERSION
# =============================================================================

FEATURE_VECTOR_VERSION: str = "MarketIntelligenceFeatureVectorV1"
ALGORITHM_VERSION: str = "mslie-v1.0.0"

# =============================================================================
# SERVICE INTERFACE (API contract)
# =============================================================================


class IMarketStructureEngine(Protocol):
    """Market Intelligence Service Interface.

    Every consumer (UI, debug hub, future model pipeline) depends on this
    contract — never on the concrete implementation.
    """

    def analyze_market(
        self, bars: Sequence[Any], *, decision_at: datetime | None = None
    ) -> MarketIntelligenceFeatureVectorV1: ...

    def get_liquidity_map(self) -> tuple[LiquidityZone, ...]: ...

    def get_structure_state(self) -> MarketContext | None: ...

    def generate_feature_vector(self) -> MarketIntelligenceFeatureVectorV1 | None: ...

    def get_debug_status(self) -> dict[str, Any]: ...


# =============================================================================
# MARKET MEMORY
# =============================================================================


class MarketMemory:
    """Bounded, causal, thread-safe institutional-level memory.

    Levels are keyed by rounded price bucket + timeframe. Every level
    records its creation date and an event history (bounded). The memory is
    purely observational — it never influences execution directly.
    """

    MAX_LEVELS: int = 60
    MAX_EVENTS: int = 8
    TOUCH_BAND_ATR: float = 0.3

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._levels: dict[tuple[int, str], MarketMemoryLevel] = {}
        self._created_dates: dict[tuple[int, str], str] = {}
        self._events: dict[tuple[int, str], list[str]] = {}
        self._touch_counts: dict[tuple[int, str], int] = {}
        self._timestamps: dict[tuple[int, str], datetime] = {}
        self._prices: dict[tuple[int, str], float] = {}

    def observe(
        self,
        price: float,
        timeframe: str,
        *,
        atr: float,
        now: datetime,
        event: str | None = None,
    ) -> None:
        """Registers/updates a level. A level near an existing bucket is a
        touch (counted), not a new level."""
        with self._lock:
            key = (round(price / max(atr * self.TOUCH_BAND_ATR, 1e-9)), timeframe)
            band = atr * self.TOUCH_BAND_ATR
            existing = self._levels.get(key)
            if existing is None:
                # try a proximity merge against existing keys
                for k in list(self._levels):
                    if k[1] != timeframe:
                        continue
                    if abs(self._prices[k] - price) <= band:
                        key = k
                        existing = self._levels[k]
                        break
            if existing is None:
                if len(self._levels) >= self.MAX_LEVELS:
                    # evict the oldest
                    oldest = min(self._timestamps, key=self._timestamps.get)
                    del self._levels[oldest]
                    del self._created_dates[oldest]
                    del self._events[oldest]
                    del self._touch_counts[oldest]
                    del self._timestamps[oldest]
                    del self._prices[oldest]
                self._prices[key] = price
                self._created_dates[key] = now.date().isoformat()
                self._timestamps[key] = now
                self._touch_counts[key] = 0
                self._events[key] = ["Level created"]
                self._levels[key] = MarketMemoryLevel(
                    level=price,
                    created=self._created_dates[key],
                    timeframe=timeframe,
                    events=tuple(self._events[key]),
                    touch_count=0,
                    last_price=price,
                )
            else:
                self._timestamps[key] = now
                self._prices[key] = price
                self._touch_counts[key] = self._touch_counts.get(key, 0) + 1
                self._levels[key] = MarketMemoryLevel(
                    level=price,
                    created=self._created_dates[key],
                    timeframe=timeframe,
                    events=tuple(self._events.get(key, ())),
                    touch_count=self._touch_counts[key],
                    last_price=price,
                )
            if event:
                evs = self._events.setdefault(key, [])
                if len(evs) >= self.MAX_EVENTS:
                    evs.pop(0)
                if evs and evs[-1] != event:
                    evs.append(event)
                elif not evs:
                    evs.append(event)

    def levels(self) -> tuple[MarketMemoryLevel, ...]:
        with self._lock:
            out = []
            for key, lvl in self._levels.items():
                out.append(
                    MarketMemoryLevel(
                        level=lvl.level,
                        created=lvl.created,
                        timeframe=lvl.timeframe,
                        events=tuple(self._events.get(key, ())),
                        touch_count=self._touch_counts.get(key, 0),
                        last_price=self._prices.get(key),
                    )
                )
            return tuple(out)

    def snapshot_dict(self) -> list[dict[str, Any]]:
        return [lvl.to_dict() for lvl in self.levels()]


# =============================================================================
# THE ENGINE
# =============================================================================


class MarketStructureEngine:
    """Market Structure & Liquidity Intelligence Engine.

    Thread-safe perception producer. The live engine calls
    :meth:`analyze_market` on its bar-close cadence (pure numpy, no I/O, no
    DB — INV-001); the result snapshot is retained for the UI/API.
    """

    def __init__(
        self,
        *,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        timeframe_weight: float = 1.0,
        max_memory_levels: int = 60,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.timeframe_weight = timeframe_weight
        self._lock = threading.RLock()
        self.memory = MarketMemory()
        self.memory.MAX_LEVELS = max_memory_levels
        self._last_vector: MarketIntelligenceFeatureVectorV1 | None = None
        self._last_context: MarketContext | None = None
        self._last_sweeps: tuple[LiquiditySweepEvent, ...] = ()
        self._last_liquidity_map: tuple[LiquidityZone, ...] = ()
        self._last_error: str | None = None
        self._last_error_at: float | None = None
        self._last_success_at: float | None = None
        self._last_success_wall_at: float | None = None
        self._last_latency_ms: float | None = None
        self._compute_count: int = 0
        self._error_count: int = 0

    # ------------------------------------------------------------------ state

    @property
    def last_vector(self) -> MarketIntelligenceFeatureVectorV1 | None:
        with self._lock:
            return self._last_vector

    @property
    def last_latency_ms(self) -> float | None:
        with self._lock:
            return self._last_latency_ms

    # --------------------------------------------------------------- analysis

    def analyze_market(
        self,
        bars: Sequence[Any],
        *,
        decision_at: datetime | None = None,
        mid_price: float | None = None,
        atr: float | None = None,
    ) -> MarketIntelligenceFeatureVectorV1:
        """Runs the full perception pipeline over the visible bars.

        Pipeline order (matches the architectural position):
        regime -> swings -> liquidity map -> sweeps -> breakout quality ->
        smart money -> memory -> feature vector.

        Strictly causal: bars with timestamp > decision_at are invisible.
        """
        started = time.perf_counter()
        try:
            vector = self._analyze(bars, decision_at=decision_at, mid_price=mid_price, atr=atr)
            latency = (time.perf_counter() - started) * 1000.0
            with self._lock:
                self._last_vector = vector
                self._last_context = MarketContext(
                    symbol=vector.symbol,
                    timeframe=vector.timeframe,
                    regime=vector.regime,
                    bias=vector.bias,
                    structure=vector.structure,
                    confidence=vector.structure_confidence,
                    decision_at=vector.decision_at,
                    mid_price=vector.mid_price,
                    atr=atr if atr is not None else 0.0,
                )
                self._last_sweeps = (vector.last_sweep_event,) if vector.last_sweep_event else ()
                self._last_liquidity_map = vector.liquidity_map
                self._last_latency_ms = round(latency, 3)
                self._last_success_at = time.monotonic()
                self._last_success_wall_at = time.time()
                self._last_error = None
                self._compute_count += 1
            return vector
        except Exception as exc:  # pragma: no cover - defensive isolation
            with self._lock:
                self._last_error = str(exc)
                self._last_error_at = time.monotonic()
                self._error_count += 1
            raise

    def _analyze(
        self,
        bars: Sequence[Any],
        *,
        decision_at: datetime | None,
        mid_price: float | None,
        atr: float | None,
    ) -> MarketIntelligenceFeatureVectorV1:
        times = _bar_times(bars)
        if decision_at is None:
            decision_at = times[-1] if times else datetime.now(UTC)
        elif decision_at.tzinfo is None:
            decision_at = decision_at.replace(tzinfo=UTC)
        else:
            decision_at = decision_at.astimezone(UTC)
        vis = [b for b, t in zip(bars, times, strict=False) if t <= decision_at]
        if not vis:
            return self._empty_vector(decision_at)

        price = mid_price if mid_price is not None else float(vis[-1].close)

        # ---- regime ----------------------------------------------------------
        regime = compute_regime_features(vis, decision_at=decision_at)

        # ---- swings ----------------------------------------------------------
        swings_high, swings_low = detect_swings(
            vis,
            decision_at=decision_at,
            symbol=self.symbol,
            timeframe=self.timeframe,
            timeframe_weight=self.timeframe_weight,
        )

        # ---- liquidity map ----------------------------------------------------
        zones = build_liquidity_map(
            vis,
            swings_high,
            swings_low,
            decision_at=decision_at,
            mid_price=price,
            timeframe=self.timeframe,
        )
        bsl = [z for z in zones if z.side.name == "BUY_SIDE"]
        ssl = [z for z in zones if z.side.name == "SELL_SIDE"]
        nearest_bsl = bsl[0] if bsl else None
        nearest_ssl = ssl[0] if ssl else None

        # ---- sweeps -----------------------------------------------------------
        sweeps = detect_sweep_events(vis, zones, decision_at=decision_at, mid_price=price, atr=atr)
        last_sweep = sweeps[-1] if sweeps else None

        # ---- breakout quality --------------------------------------------------
        breakout = assess_breakout_quality(vis, decision_at=decision_at, breakout_level=None)

        # ---- smart money -------------------------------------------------------
        sm = compute_smart_money_features(vis, decision_at=decision_at, atr=atr, mid_price=price)

        # ---- structure + bias + confidence ------------------------------------
        structure, bias, confidence = _structure_verdict(
            regime, swings_high, swings_low, last_sweep
        )

        # ---- multi-month memory ------------------------------------------------
        atr_safe = max(atr if atr is not None else 0.0, 0.2)
        for z in zones:
            self.memory.observe(
                z.price,
                self.timeframe,
                atr=atr_safe,
                now=decision_at,
                event="Liquidity accumulated",
            )
        if last_sweep is not None:
            self.memory.observe(
                last_sweep.pool_price,
                self.timeframe,
                atr=atr_safe,
                now=decision_at,
                event="Stop sweep occurred",
            )
        memory_levels = self.memory.levels()

        return MarketIntelligenceFeatureVectorV1(
            version=FEATURE_VECTOR_VERSION,
            symbol=self.symbol,
            timeframe=self.timeframe,
            decision_at=decision_at,
            mid_price=price,
            regime=regime,
            structure=structure,
            bias=bias,
            structure_confidence=confidence,
            nearest_buy_side_liquidity=nearest_bsl,
            nearest_sell_side_liquidity=nearest_ssl,
            liquidity_map=tuple(zones),
            last_sweep_event=last_sweep,
            breakout_quality=breakout,
            smart_money=sm,
            memory=memory_levels,
            swing_count_high=len(swings_high),
            swing_count_low=len(swings_low),
        )

    def _empty_vector(self, decision_at: datetime) -> MarketIntelligenceFeatureVectorV1:
        return MarketIntelligenceFeatureVectorV1(
            version=FEATURE_VECTOR_VERSION,
            symbol=self.symbol,
            timeframe=self.timeframe,
            decision_at=decision_at,
            mid_price=0.0,
            regime=MarketRegimeFeatures(
                trend_direction=0.0,
                trend_strength=0.0,
                volatility_state=0.0,
                ranging_probability=0.5,
                expansion_probability=0.25,
                compression_probability=0.25,
                regime_label="INSUFFICIENT_HISTORY",
            ),
            structure="UNKNOWN",
            bias=MarketBias.NEUTRAL,
            structure_confidence=0.0,
            nearest_buy_side_liquidity=None,
            nearest_sell_side_liquidity=None,
            smart_money=SmartMoneyFeatures(
                order_block_type=0.0,
                order_block_strength=0.0,
                fvg_count=0.0,
                fvg_strength=0.0,
                displacement_strength=0.0,
                inducement_levels=0.0,
                premium_discount_position=0.0,
                last_mitigated_order_block=0.0,
            ),
        )

    # -------------------------------------------------------------- interface

    def get_liquidity_map(self) -> tuple[LiquidityZone, ...]:
        with self._lock:
            return self._last_liquidity_map

    def get_structure_state(self) -> MarketContext | None:
        with self._lock:
            return self._last_context

    def generate_feature_vector(self) -> MarketIntelligenceFeatureVectorV1 | None:
        with self._lock:
            return self._last_vector

    def get_debug_status(self) -> dict[str, Any]:
        """Canonical debug payload for the UI / debug snapshot."""
        with self._lock:
            vector = self._last_vector
            status = "ONLINE" if vector is not None else "STANDBY"
            if self._last_error is not None:
                status = "DEGRADED"
            last_update = (
                datetime.fromtimestamp(self._last_success_wall_at, tz=UTC).isoformat()
                if self._last_success_wall_at is not None
                else None
            )
            return {
                "available": vector is not None,
                "status": status,
                "engine_status": {
                    "market_structure_engine": "ONLINE" if vector is not None else "STANDBY",
                    "liquidity_engine": "ACTIVE" if vector is not None else "STANDBY",
                    "feature_generator": "RUNNING" if vector is not None else "IDLE",
                    "last_update": last_update,
                    "latency_ms": self._last_latency_ms,
                    "compute_count": self._compute_count,
                    "error_count": self._error_count,
                    "last_error": self._last_error,
                },
                "market_context": self._last_context.to_dict() if self._last_context else None,
                "liquidity_map": [z.to_dict() for z in self._last_liquidity_map],
                "last_sweep": (self._last_sweeps[-1].to_dict() if self._last_sweeps else None),
                "feature_vector": vector.to_dict() if vector else None,
                "algorithm_version": ALGORITHM_VERSION,
            }


# =============================================================================
# STRUCTURE VERDICT
# =============================================================================


def _structure_verdict(
    regime: MarketRegimeFeatures,
    swings_high: Sequence[Any],
    swings_low: Sequence[Any],
    last_sweep: LiquiditySweepEvent | None,
) -> tuple[str, MarketBias, float]:
    """Derives structure / bias / confidence from regime + swing geometry.

    BULLISH: higher highs + higher lows with an up-trend regime; BEARISH:
    lower highs + lower lows with a down-trend regime; else RANGING. A
    reversal sweep flips the bias when the regime agrees.
    """
    if not swings_high or not swings_low:
        if regime.regime_label == "TRENDING":
            if regime.trend_direction > 0:
                return "BULLISH", MarketBias.BULLISH, 55.0
            if regime.trend_direction < 0:
                return "BEARISH", MarketBias.BEARISH, 55.0
        return "RANGING", MarketBias.NEUTRAL, 40.0

    last_high = swings_high[-1]
    last_low = swings_low[-1]
    # compare the last two highs / lows for HH/HL/LH/LL geometry
    prev_high = swings_high[-2] if len(swings_high) >= 2 else None
    prev_low = swings_low[-2] if len(swings_low) >= 2 else None

    hh = prev_high is not None and last_high.price > prev_high.price
    hl = prev_low is not None and last_low.price > prev_low.price
    lh = prev_high is not None and last_high.price < prev_high.price
    ll = prev_low is not None and last_low.price < prev_low.price

    structure = "RANGING"
    bias = MarketBias.NEUTRAL
    confidence = 45.0
    if hh and hl:
        structure = "BULLISH"
        bias = MarketBias.BULLISH
        confidence = 60.0 + 20.0 * (regime.trend_strength / 100.0)
    elif lh and ll:
        structure = "BEARISH"
        bias = MarketBias.BEARISH
        confidence = 60.0 + 20.0 * (regime.trend_strength / 100.0)
    elif hh or hl:
        structure = "BULLISH" if regime.trend_direction >= 0 else "RANGING"
        bias = MarketBias.BULLISH if regime.trend_direction >= 0 else MarketBias.NEUTRAL
        confidence = 50.0 + 15.0 * (regime.trend_strength / 100.0)
    elif lh or ll:
        structure = "BEARISH" if regime.trend_direction <= 0 else "RANGING"
        bias = MarketBias.BEARISH if regime.trend_direction <= 0 else MarketBias.NEUTRAL
        confidence = 50.0 + 15.0 * (regime.trend_strength / 100.0)

    # reversal sweep: a sweep AGAINST the structure followed by rejection
    # flips the bias toward the rejection side (regime-agreed only).
    if last_sweep is not None and last_sweep.after_event_state.name == "REVERSAL":
        if last_sweep.direction == "SELL_SIDE" and bias != MarketBias.BULLISH:
            structure = "BULLISH"
            bias = MarketBias.BULLISH
            confidence = max(confidence, 55.0)
        elif last_sweep.direction == "BUY_SIDE" and bias != MarketBias.BEARISH:
            structure = "BEARISH"
            bias = MarketBias.BEARISH
            confidence = max(confidence, 55.0)

    confidence = round(max(0.0, min(100.0, confidence)), 2)
    return structure, bias, confidence


def _bar_times(bars: Sequence[Any]) -> list[datetime]:
    out: list[datetime] = []
    for b in bars:
        t = b.timestamp
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        else:
            t = t.astimezone(UTC)
        out.append(t)
    return out
