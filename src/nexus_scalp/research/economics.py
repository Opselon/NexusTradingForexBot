"""Canonical Economic Assumptions Layer (ECON v1)
=================================================

ONE source of truth for the economic world used by backtest, walk-forward,
OOS, robustness, replay and (via composition) live risk sizing:

    signal -> confidence -> sizing decision -> execution price -> friction
           -> swap/rollover -> realized P&L -> equity -> drawdown

Design rules (economic-parity mission):
* Pure, immutable value objects + pure calculations. No I/O, no adapters,
  no clocks (everything derives from timestamps supplied by the caller).
* FAIL-CLOSED profiles: production-like validation must never silently run
  with zero friction or missing swap assumptions. Frictionless analysis is an
  explicit opt-in and is LABELLED on every result.
* No invented trading constants. Every numeric default in this module cites
  its repository evidence (data/raw probe, ledger probe, probed symbol facts,
  or the live RiskEngine's own calibrated factors, which are reused verbatim).

Evidence ledger for the defaults (probes at HEAD d57d003d, 2026-09-07):
* Spread (points, XAUUSD M1, data/raw/XAUUSD_M1.csv, 100k bars):
    p50=4  p75=12  p90=20  p95=24  max=622.
  Production-like validation default = p90 = 20.0 points.
* Slippage (points, audit_experience_outcomes, 1064 closed outcomes):
    p50..p95 = 0, max observed = 2.2.
  Production-like validation default = 2.2 points (worst observed).
* Commission: always 0.0 on this broker's deals (8341 broker deals probed).
* Latency: recorded values are measurement artifacts (max 432s outliers);
  fills in research replay are immediate, latency stays a stress dimension
  (robustness scenarios), not a P&L deduction.
* Rollover: daily server-time market break 23:00 -> 01:00 (121-minute gap
  every day across all months, no seasonal shift May-Aug 2026 -> fixed
  server offset). Spread inside the 22:30-01:15 window: p90=48, p95=87
  points vs p90=20 outside -> deterministic maintenance entry block.
* Swap: live deals carry real swap (e.g. -25.2 USD on 2.0 lots); rates vary
  by holding/direction and the sample is thin -> swap rates are REQUIRED
  EXPLICIT inputs for the production-like profile (fail-closed), never a
  guessed default.
* Instrument economics: probed XAUUSD facts reused from the replay contract
  (contract_size=100, point=0.01, tick_value=0.1, volume_min=step=0.01).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nexus_scalp.risk.sizing_policy import (
    SizingPolicy as _SizingPolicyBase,
)

# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ExecutionProfile(StrEnum):
    """Economic quality label carried on every research result.

    PRODUCTION_LIKE     - conservative friction + required swap/provenance;
                          the only profile eligible to feed promotion.
    FRICTIONLESS_RESEARCH - explicit analytical opt-in; zero friction and/or
                          unpriced sizing. Results are marked NON-PROMOTABLE.
    """

    PRODUCTION_LIKE = "PRODUCTION_LIKE"
    FRICTIONLESS_RESEARCH = "FRICTIONLESS_RESEARCH"


#: Production-like friction defaults (evidence: module docstring).
PRODUCTION_SPREAD_TICKS: float = 20.0
PRODUCTION_SLIPPAGE_TICKS: float = 2.2
#: Runaway cap for the production profile: covers the observed p95 spread
#: (24 pts) plus slippage without silently truncating evidence-based friction
#: (legacy frictionless baseline used max_slippage_ticks=5 for tiny stresses).
PRODUCTION_MAX_FRICTION_TICKS: float = 40.0

#: Free-margin clamp used by calculate_dynamic_volume (live semantics).
LIVE_MARGIN_FREE_FRACTION: float = 0.20

#: Replay account leverage (probed replay contract; live uses account truth).
REPLAY_LEVERAGE: float = 100.0


# ---------------------------------------------------------------------------
# Friction bundle (compatible superset of research.models.ExecutionAssumptions)
# ---------------------------------------------------------------------------


class FrictionAssumptions(BaseModel):
    """Per-trade friction in R space (spread/slippage) + latency metadata."""

    model_config = ConfigDict(frozen=True)

    spread_ticks: float = Field(default=0.0, ge=0.0, description="Spread added in ticks")
    slippage_ticks: float = Field(default=0.0, ge=0.0, description="Adverse slippage in ticks")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Assumed order latency")
    #: Price step (tick size) used to convert ticks to price units.
    price_tick: float = Field(default=0.01, gt=0.0)
    #: Whether entry is executed at the adverse edge of the spread (realistic).
    pay_spread: bool = Field(default=True)
    max_slippage_ticks: float = Field(default=5.0, ge=0.0)  # guard against runaway
    commission_per_lot_usd: float = Field(
        default=0.0, ge=0.0, description="Commission per lot round-turn (broker evidence: 0.0)"
    )

    @classmethod
    def production_like(cls, price_tick: float = 0.01) -> FrictionAssumptions:
        """Conservative validation friction derived from repository evidence."""
        return cls(
            spread_ticks=PRODUCTION_SPREAD_TICKS,
            slippage_ticks=PRODUCTION_SLIPPAGE_TICKS,
            price_tick=price_tick,
            max_slippage_ticks=PRODUCTION_MAX_FRICTION_TICKS,
        )

    def friction_r(self, risk_distance: float) -> float:
        """R fraction consumed by spread+slippage for one trade.

        Same semantics as research.metrics: friction in price units divided by
        the planned risk distance, capped at 0.5R; falls back to an absolute
        per-tick floor when no risk distance is recorded.
        """
        friction_ticks = min(self.spread_ticks + self.slippage_ticks, self.max_slippage_ticks)
        if friction_ticks <= 0.0:
            return 0.0
        if risk_distance > 1e-9:
            return min((friction_ticks * self.price_tick) / risk_distance, 0.5)
        return 0.01 * friction_ticks


# ---------------------------------------------------------------------------
# Swap / rollover
# ---------------------------------------------------------------------------

#: Daily maintenance window in SERVER (data) time, derived from the raw M1
#: feed: last bar 22:59, first bar of the new day 01:00 every single day.
MAINTENANCE_WINDOW_START_SERVER: str = "23:00"
MAINTENANCE_WINDOW_END_SERVER: str = "01:00"

#: Spread-evidence buffer around the hard break (spread p95=87 pts inside
#: 22:30-01:15 vs 20 outside). The window used for the entry block is the
#: hard break expanded by this buffer.
MAINTENANCE_WINDOW_BUFFER_MINUTES: int = 30

#: Broker server UTC offset. NOT DST-shifted in the observed data window
#: (May-Aug 2026 shows no seasonal gap movement). None = research data
#: timestamps ARE server time (no conversion). Live callers must supply the
#: real broker offset when they convert.
DEFAULT_SERVER_UTC_OFFSET_HOURS: float | None = None


class SwapProfile(BaseModel):
    """Canonical swap/rollover economics.

    Rates are USD per lot per rollover crossing, signed from the TRADER's
    perspective (negative = debit). They are REQUIRED EXPLICIT inputs for the
    production-like profile: the ledger proves live swap exists but the
    sample is too thin to hardcode a rate, so an unset rate fails validation
    instead of silently pricing zero swap.
    """

    model_config = ConfigDict(frozen=True)

    long_usd_per_lot_per_rollover: float | None = None
    short_usd_per_lot_per_rollover: float | None = None
    #: Server-time offset used to locate the rollover instant (data time).
    server_utc_offset_hours: float | None = DEFAULT_SERVER_UTC_OFFSET_HOURS
    #: Rollover instant in server time (day boundary where swap posts).
    rollover_instant_server: str = "00:00"

    def is_complete(self) -> bool:
        """True when both directional rates are explicitly supplied."""
        return (
            self.long_usd_per_lot_per_rollover is not None
            and self.short_usd_per_lot_per_rollover is not None
        )

    def swap_usd(
        self,
        direction: str,
        volume: float,
        entry_ts: datetime,
        exit_ts: datetime,
    ) -> float:
        """Swap cost for one held position crossing rollover instants.

        Counts server-time rollover instants strictly inside (entry, exit].
        Deterministic; timestamps must be tz-aware or naive-server consistently.
        """
        rate_long = self.long_usd_per_lot_per_rollover
        rate_short = self.short_usd_per_lot_per_rollover
        if rate_long is None or rate_short is None:
            raise ValueError(
                "SwapProfile rates unset: swap accounting requires explicit "
                "long/short rates (fail-closed; production profile validates this)."
            )
        d = str(direction).upper()
        if "BUY" in d:
            rate = float(rate_long)
        elif "SELL" in d:
            rate = float(rate_short)
        else:
            return 0.0
        if volume <= 0.0 or not math.isfinite(volume):
            return 0.0
        crossings = rollover_crossings(entry_ts, exit_ts, self.rollover_instant_server)
        return rate * float(volume) * crossings


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _server_naive(ts: datetime, offset_hours: float | None) -> datetime:
    """Converts a timestamp to naive server (data) time."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC)
    if offset_hours is None:
        return ts.replace(tzinfo=None)
    return (ts - timedelta(hours=offset_hours)).replace(tzinfo=None)


def in_maintenance_window(
    ts: datetime,
    *,
    start_server: str = MAINTENANCE_WINDOW_START_SERVER,
    end_server: str = MAINTENANCE_WINDOW_END_SERVER,
    buffer_minutes: int = MAINTENANCE_WINDOW_BUFFER_MINUTES,
    server_utc_offset_hours: float | None = DEFAULT_SERVER_UTC_OFFSET_HOURS,
) -> bool:
    """True when `ts` (server/data time) sits in the nightly maintenance break
    expanded by the spread-evidence buffer. Window may cross midnight."""
    srv = _server_naive(ts, server_utc_offset_hours)
    minutes = srv.hour * 60 + srv.minute

    def _m(hhmm: str) -> int:
        hh, mm = _parse_hhmm(hhmm)
        return hh * 60 + mm

    start = _m(start_server) - buffer_minutes
    end = _m(end_server) + buffer_minutes
    if start <= end:
        return start <= minutes <= end
    # crosses midnight
    return minutes >= start or minutes <= end


def rollover_crossings(
    entry_ts: datetime,
    exit_ts: datetime,
    rollover_instant_server: str = "00:00",
    *,
    server_utc_offset_hours: float | None = DEFAULT_SERVER_UTC_OFFSET_HOURS,
) -> int:
    """Number of daily rollover instants strictly inside (entry, exit].

    The rollover instant is the server-time day boundary (default 00:00
    server) — the point where MT5 posts the daily swap.
    """
    if exit_ts <= entry_ts:
        return 0
    e = _server_naive(entry_ts, server_utc_offset_hours)
    x = _server_naive(exit_ts, server_utc_offset_hours)
    hh, mm = _parse_hhmm(rollover_instant_server)
    day0 = e.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if day0 <= e:
        day0 += timedelta(days=1)
    count = 0
    cur = day0
    while cur <= x:
        count += 1
        cur += timedelta(days=1)
    return count


# ---------------------------------------------------------------------------
# Instrument + sizing policy (live RiskEngine semantics, shared)
# ---------------------------------------------------------------------------


class InstrumentEconomics(BaseModel):
    """Broker symbol facts needed to price size and P&L.

    Defaults are the probed XAUUSD facts (replay execution contract).
    """

    model_config = ConfigDict(frozen=True)

    contract_size: float = Field(default=100.0, gt=0.0)
    point: float = Field(default=0.01, gt=0.0)
    tick_value: float = Field(default=0.1, gt=0.0)
    volume_min: float = Field(default=0.01, gt=0.0)
    volume_max: float = Field(default=10.0, gt=0.0)
    volume_step: float = Field(default=0.01, gt=0.0)
    #: Account leverage used by the margin clamp in replay/backtest contexts
    #: (live uses AccountInfo.leverage truth).
    leverage: float = Field(default=REPLAY_LEVERAGE, gt=0.0)


class SizingPolicy(_SizingPolicyBase):
    """Canonical risk-percent sizing policy — live RiskEngine semantics.

    Re-exported from ``nexus_scalp.risk.sizing_policy`` (the single source
    that the live RiskEngine itself consumes); kept as a subclass for
    backward-compatible imports from the economics layer.
    """


class SizingDecision(BaseModel):
    """One causal sizing decision (observability + parity tests)."""

    model_config = ConfigDict(frozen=True)

    volume: float = Field(default=0.0, ge=0.0)
    risk_pct_effective: float = Field(default=0.0, ge=0.0)
    risk_usd: float = Field(default=0.0, ge=0.0)
    equity_used: float = Field(default=0.0, ge=0.0)
    peak_equity_used: float = Field(default=0.0, ge=0.0)
    reason: str = Field(default="")
    factors: dict[str, float] = Field(default_factory=dict)


def compute_sizing(
    *,
    policy: SizingPolicy,
    instrument: InstrumentEconomics,
    equity: float,
    peak_equity: float,
    entry: float,
    stop_loss: float,
    confidence: float,
    regime: str | None,
    risk_engine: Any = None,
) -> SizingDecision:
    """Canonical causal sizing decision.

    Delegates broker math (step floor, tier caps, margin clamp, min-lot
    rules) to the LIVE RiskEngine.calculate_dynamic_volume so backtest and
    live cannot drift. `risk_engine` may be injected for tests; by default a
    throwaway RiskEngine with matching max_allowed_lots is constructed.

    Causality contract: equity/peak_equity are the values known AT the trade
    timestamp (running equity path before this trade). No future state.
    """
    from nexus_scalp.risk.risk_engine import RiskEngine

    sl_distance = abs(float(entry) - float(stop_loss))
    engine = risk_engine
    if engine is None:
        from nexus_scalp.configuration.config import RiskConfig

        engine = RiskEngine(
            config=RiskConfig(risk_per_trade_pct=policy.base_risk_pct),
            max_allowed_lots=policy.max_allowed_lots,
        )
    pct, factors = policy.effective_risk_pct(
        confidence=confidence,
        regime=regime,
        equity=equity,
        peak_equity=peak_equity,
    )
    from nexus_scalp.domain.models import AccountInfo, SymbolInfo

    # Invalid equity fails closed exactly like the live engine: a
    # non-positive/non-finite account state can never produce volume.
    eq = float(equity)
    if not math.isfinite(eq) or eq <= 0.0:
        return SizingDecision(
            volume=0.0,
            risk_pct_effective=pct,
            risk_usd=0.0,
            equity_used=max(0.0, eq) if math.isfinite(eq) else 0.0,
            peak_equity_used=max(0.0, float(peak_equity)) if math.isfinite(peak_equity) else 0.0,
            reason="INVALID_EQUITY",
            factors=factors,
        )
    account = AccountInfo(
        login=0,
        trade_mode=0,
        leverage=int(instrument.leverage),
        balance=equity,
        equity=equity,
        margin=0.0,
        margin_free=equity,
    )
    symbol_info = SymbolInfo(
        symbol="BACKTEST",
        digits=2,
        point=instrument.point,
        tick_size=instrument.point,
        tick_value=instrument.tick_value,
        volume_min=instrument.volume_min,
        volume_max=instrument.volume_max,
        volume_step=instrument.volume_step,
        stops_level=0,
        freeze_level=0,
        trade_contract_size=instrument.contract_size,
    )
    volume, reason = engine.calculate_dynamic_volume(
        entry=entry,
        sl=stop_loss,
        account=account,
        symbol_info=symbol_info,
        risk_pct=pct,
    )
    risk_usd = volume * instrument.contract_size * sl_distance
    return SizingDecision(
        volume=volume,
        risk_pct_effective=pct,
        risk_usd=risk_usd,
        equity_used=equity,
        peak_equity_used=peak_equity,
        reason=reason,
        factors=factors,
    )


# ---------------------------------------------------------------------------
# Aggregate assumptions object
# ---------------------------------------------------------------------------


class EconomicAssumptions(BaseModel):
    """The complete economic world of one research run.

    identity() must fully reconstruct how any number in the result was
    produced (artifact provenance requirement).
    """

    model_config = ConfigDict(frozen=True)

    profile: ExecutionProfile = Field(default=ExecutionProfile.PRODUCTION_LIKE)
    friction: FrictionAssumptions = Field(default_factory=FrictionAssumptions.production_like)
    swap: SwapProfile = Field(default_factory=SwapProfile)
    instrument: InstrumentEconomics = Field(default_factory=InstrumentEconomics)
    sizing: SizingPolicy = Field(default_factory=SizingPolicy)
    #: Equity base for the historical sizing path (modeled input; live uses
    #: the real account equity path). Explicit, never silently fixed-size.
    starting_equity_usd: float = Field(default=10_000.0, gt=0.0)
    #: Deterministic maintenance-window entry policy (config-gated live).
    block_entries_in_maintenance_window: bool = Field(default=True)

    @classmethod
    def production_like(
        cls,
        *,
        swap: SwapProfile | None = None,
        instrument: InstrumentEconomics | None = None,
        starting_equity_usd: float = 10_000.0,
    ) -> EconomicAssumptions:
        """Production-like profile: conservative friction, explicit swap."""
        return cls(
            profile=ExecutionProfile.PRODUCTION_LIKE,
            friction=FrictionAssumptions.production_like(
                price_tick=(instrument or InstrumentEconomics()).point
            ),
            swap=swap or SwapProfile(),
            instrument=instrument or InstrumentEconomics(),
            starting_equity_usd=starting_equity_usd,
        )

    @classmethod
    def frictionless_research(
        cls,
        *,
        instrument: InstrumentEconomics | None = None,
        starting_equity_usd: float = 10_000.0,
    ) -> EconomicAssumptions:
        """EXPLICIT frictionless analytical mode (never the default)."""
        return cls(
            profile=ExecutionProfile.FRICTIONLESS_RESEARCH,
            friction=FrictionAssumptions(price_tick=(instrument or InstrumentEconomics()).point),
            swap=SwapProfile(),
            instrument=instrument or InstrumentEconomics(),
            starting_equity_usd=starting_equity_usd,
        )

    def is_promotable_economics(self) -> bool:
        """Production-like economics require complete swap assumptions."""
        return self.profile == ExecutionProfile.PRODUCTION_LIKE and self.swap.is_complete()

    def provenance(self) -> dict[str, Any]:
        """Full reconstruction identity for artifacts/promotion evidence."""
        return {
            "economic_assumptions_version": "ECON_V1",
            "execution_profile": self.profile.value,
            "friction": self.friction.model_dump(),
            "swap": self.swap.model_dump(),
            "instrument": self.instrument.model_dump(),
            "sizing": self.sizing.model_dump(),
            "starting_equity_usd": self.starting_equity_usd,
            "block_entries_in_maintenance_window": self.block_entries_in_maintenance_window,
            "swap_complete": self.swap.is_complete(),
            "promotable_economics": self.is_promotable_economics(),
        }

    def execution_cost_usd(
        self,
        *,
        direction: str,
        volume: float,
        risk_distance: float,
        entry: float,
    ) -> float:
        """Explicit USD execution cost decomposition for one trade.

        Friction is modeled in R space (metrics.py semantics); this converts
        the SAME friction R into USD at the planned risk of the sized trade
        and adds the per-lot commission. Spread/slippage debit only.
        """
        f_r = self.friction.friction_r(risk_distance)
        cost = f_r * volume * self.instrument.contract_size * risk_distance
        cost += self.friction.commission_per_lot_usd * max(0.0, volume)
        _ = (direction, entry)  # symmetric spread model; kept for future asymmetry
        return cost


def normalize_assumptions(
    assumptions: Any,
) -> EconomicAssumptions:
    """Backward-compatible normalization for engine constructors.

    * None                          -> PRODUCTION_LIKE (fail-closed default).
    * EconomicAssumptions           -> as-is.
    * legacy ExecutionAssumptions   -> explicit caller choice: the friction
      values travel unchanged and the run is LABELLED FRICTIONLESS_RESEARCH
      (an explicit construction is an explicit analytical opt-in).
    """
    if assumptions is None:
        return EconomicAssumptions.production_like()
    from nexus_scalp.research.models import ExecutionAssumptions

    if isinstance(assumptions, EconomicAssumptions):
        return assumptions
    if isinstance(assumptions, ExecutionAssumptions):
        return EconomicAssumptions(
            profile=ExecutionProfile.FRICTIONLESS_RESEARCH,
            friction=FrictionAssumptions(
                spread_ticks=assumptions.spread_ticks,
                slippage_ticks=assumptions.slippage_ticks,
                latency_ms=assumptions.latency_ms,
                price_tick=assumptions.price_tick,
                pay_spread=assumptions.pay_spread,
                max_slippage_ticks=assumptions.max_slippage_ticks,
            ),
        )
    raise TypeError(
        f"Unsupported assumptions type: {type(assumptions)!r}; expected "
        "EconomicAssumptions or research.models.ExecutionAssumptions."
    )
