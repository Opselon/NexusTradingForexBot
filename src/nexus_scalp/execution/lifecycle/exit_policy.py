"""Deterministic, volatility-aware exit protection policy.

The live defect this module repairs
-----------------------------------
A freshly opened position was being declared risk-free almost immediately: the
breakeven (BE) trigger fired at a fraction of the planned risk, the stop moved
to (or near) the entry, and an ordinary pullback converted winners into
scratches before the move developed. The old trigger — a flat ``$15`` floor
with a ``0.15R`` ceiling — is not anchored to the instrument's noise, so it
protects risk the trade's own thesis had already earned back.

What it is
----------
A pure, deterministic decision layer placed between the protection engine's
broker-geometry guards and the per-ticket protection state. It answers one
question: *given this tick's evidence, may the position go risk-free now, and
at what stop level?*

Design rules (each one kills a concrete failure mode)
------------------------------------------------------
1. Structural, not monetary. The trigger is expressed in R units (the
   position's own planned-risk distance) and in ATR of the instrument, never
   in raw USD: $15 is a fortune on 0.01 lots and noise on 2.0 lots.
2. Never inside the market's own noise. ``be_min_atr_multiplier`` requires the
   move to exceed a fraction of the session ATR before protection tightens.
3. Cost-aware lock. The locked stop must sit BEYOND the estimated round-trip
   cost (spread + commission) plus a positive buffer. A "breakeven" that
   realizes nothing or a loss is a loser wearing a winner's mask.
4. Spike persistence. MFE (best profit ever seen) is not authority on its own:
   current profit must still be within ``spike_hold_allowance_r`` of the
   trigger, so a wick that already collapsed cannot de-risk the trade.
5. Minimum dwell. A position younger than ``min_hold_seconds`` is not a
   developed move, whatever its floating profit says.
6. Laddered de-risk, not a cliff. At ``partial_de_risk_r`` the stop is lifted
   to ``-partial_de_risk_stop_r`` (half the open risk gone, room to run kept);
   full risk-free stays expensive until the thesis advances.
7. Regime awareness. Trend/expansion regimes ask for more (moves continue),
   range regimes for less (moves revert).
8. Fail-closed. Without a learned profile the hard-wired conservative
   defaults apply; a HOLD decision never disables protection — it leaves the
   original stop in force and hands the tick to the deterministic chain.

No broker I/O, no DB I/O, no clock reads: everything is passed in, so the
module is replayable and unit-testable (INV-001: pure decision authority).

The optimizer (``experience/exit_optimizer.py``) writes the learned per-regime
geometry consumed by :meth:`ExitProtectionPolicy.apply_learned_profile` — that
is the self-learning half of this layer.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus_scalp.domain.enums import OrderType
from nexus_scalp.domain.models import Position, SymbolInfo

logger = logging.getLogger("nexus_scalp.execution.exit_policy")

#: Learned policy artifact names, newest first. A malformed/missing file means
#: "defaults", never a crash and never a disabled protection path.
LEARNED_POLICY_FILES: tuple[str, ...] = ("exit_policy.json", "exit_policy_v2.json")


def artifacts_dir() -> Path:
    """Resolve the artifact root (env override keeps tests hermetic)."""
    override = os.environ.get("NEXUS_ARTIFACTS_DIR")
    return Path(override) if override else Path("artifacts")


@dataclass(frozen=True)
class ExitPolicyConfig:
    """Every numeric rule in one frozen place (auditable, tunable)."""

    #: Absolute R floor before full risk-free is allowed at all.
    be_min_r: float = 1.0
    #: Alternative floor: the move must exceed this many ATRs.
    be_min_atr_multiplier: float = 0.8
    #: Sanity ceiling so an absurd ATR cannot postpone protection forever.
    be_max_r: float = 3.0

    #: Regime-specific R floors.
    trend_regime_floor_r: float = 1.2
    range_regime_floor_r: float = 0.7
    default_regime_floor_r: float = 0.9

    #: Ladder: when MFE reaches this R, lift the stop to -partial_de_risk_stop_r.
    partial_de_risk_r: float = 0.6
    partial_de_risk_stop_r: float = 0.45

    #: Lock = entry +/- (cost distance + buffer + this fraction of 1R).
    lock_buffer_pips: float = 0.6
    lock_extra_r: float = 0.10

    #: Round-trip cost estimate used when the caller has no commission figure.
    round_trip_cost_usd_per_lot: float = 1.10

    #: The ladder's first step must clear this fraction of the ATR noise floor
    #: too: de-risking into a wick that dwarfs the stop is just a slower loss.
    partial_atr_fraction: float = 0.75

    #: Dwell time before ANY tightening is allowed.
    min_hold_seconds: float = 12.0
    #: Current profit may be at most this far below the trigger (persistence).
    spike_hold_allowance_r: float = 0.15


class ExitAction:
    """Decision vocabulary (stable strings: audit + telemetry + tests)."""

    HOLD = "HOLD"
    PARTIAL_DE_RISK = "PARTIAL_DE_RISK"
    BREAKEVEN_LOCK = "BREAKEVEN_LOCK"


@dataclass(frozen=True)
class ExitPlan:
    """One decision with the geometry that justified it."""

    action: str
    trigger_r: float = 0.0
    new_sl: float | None = None
    reason: str = ""
    evidence: dict[str, float] = field(default_factory=dict)

    @property
    def should_modify(self) -> bool:
        return self.action in (ExitAction.PARTIAL_DE_RISK, ExitAction.BREAKEVEN_LOCK)


_REGIME_ALIASES: dict[str, str] = {
    "TREND": "trend",
    "TRENDING": "trend",
    "TREND_UP": "trend",
    "TREND_DOWN": "trend",
    "EXPANSION": "trend",
    "VOLATILITY_EXPANSION": "trend",
    "RANGE": "range",
    "RANGING": "range",
    "MEAN_REVERSION": "range",
    "COMPRESSION": "range",
    "CHOP": "range",
}


def normalize_regime(regime: str | None) -> str:
    """Map the platform's regime vocabulary onto the policy's three buckets."""
    key = str(regime or "").strip().upper()
    return _REGIME_ALIASES.get(key, "default")


class ExitProtectionPolicy:
    """Pure exit-protection decision engine (no I/O, no clock)."""

    def __init__(
        self,
        config: ExitPolicyConfig | None = None,
        learned: dict[str, float] | None = None,
    ) -> None:
        self.cfg = config or ExitPolicyConfig()
        self._learned: dict[str, float] = {}
        self.apply_learned_profile(learned)

    # -- learning ------------------------------------------------------- #
    def apply_learned_profile(self, learned: dict[str, float] | None) -> None:
        """Overlay a learned per-regime trigger table (values validated)."""
        if not learned:
            return
        for regime, value in learned.items():
            try:
                trigger = float(value)
            except (TypeError, ValueError):
                continue
            if 0.4 <= trigger <= 5.0:
                self._learned[normalize_regime(regime)] = trigger

    def learned_trigger_r(self, regime: str) -> float | None:
        return self._learned.get(normalize_regime(regime))

    def trigger_floor_r(self, regime: str, r_unit: float, atr_price: float) -> tuple[float, dict[str, float]]:
        """Resolve the R floor for this ticket and expose its provenance."""
        bucket = normalize_regime(regime)
        cfg = self.cfg
        regime_floor = {
            "trend": cfg.trend_regime_floor_r,
            "range": cfg.range_regime_floor_r,
        }.get(bucket, cfg.default_regime_floor_r)

        atr_floor_r = 0.0
        if atr_price > 0.0 and r_unit > 0.0:
            atr_floor_r = (cfg.be_min_atr_multiplier * atr_price) / r_unit

        learned = self._learned.get(bucket)
        base = max(cfg.be_min_r, regime_floor, atr_floor_r)
        if learned is not None:
            base = max(base, learned)
        floor_r = min(cfg.be_max_r, base)
        return floor_r, {
            "regime_floor_r": regime_floor,
            "atr_floor_r": round(atr_floor_r, 4),
            "r_unit_price": round(r_unit, 6),
            "learned_r": round(learned, 4) if learned is not None else 0.0,
        }

    # -- geometry helpers ------------------------------------------------ #
    @staticmethod
    def r_unit_price(position: Position, risk_usd: float, contract_size: float) -> float:
        """Price distance corresponding to 1R, or 0.0 without provenance."""
        notional = position.volume * max(contract_size, 1e-9)
        if risk_usd <= 0.0 or notional <= 0.0:
            return 0.0
        return abs(risk_usd / notional)

    @staticmethod
    def _is_buy(position: Position) -> bool:
        return position.type == OrderType.BUY

    def _sl_is_beyond(self, position: Position, candidate: float) -> bool:
        """True when ``candidate`` is strictly tighter than the current stop.

        A zero stop carries no protective geometry at all, so ANY finite
        candidate is an improvement over it — returning False here would let
        a stop-less ticket sit unprotected forever under the
        "already applied" guard.
        """
        current = float(position.sl or 0.0)
        if current <= 0.0:
            return True
        return candidate > current if self._is_buy(position) else candidate < current

    def de_risk_stop_price(self, position: Position, r_unit: float) -> float:
        """Stop at ``-partial_de_risk_stop_r`` R (half the open risk removed)."""
        offset = self.cfg.partial_de_risk_stop_r * r_unit
        return (
            position.price_open - offset
            if self._is_buy(position)
            else position.price_open + offset
        )

    def lock_price(
        self,
        position: Position,
        contract_size: float,
        spread_price: float,
        commission_usd: float,
        symbol_info: SymbolInfo | None = None,
        r_unit: float = 0.0,
    ) -> float | None:
        """Cost-aware risk-free stop: entry + round-trip cost + buffer."""
        notional = position.volume * max(contract_size, 1e-9)
        if notional <= 0.0:
            return None
        spread_usd = max(spread_price, 0.0) * notional
        cost_usd = (
            commission_usd
            if commission_usd > 0.0
            else self.cfg.round_trip_cost_usd_per_lot * max(position.volume, 1e-9)
        ) + spread_usd
        cost_price = cost_usd / notional

        pip = 0.10
        if symbol_info is not None and symbol_info.point > 0.0:
            digits = int(getattr(symbol_info, "digits", 2) or 2)
            pip = symbol_info.point * (10.0 if digits in (3, 5) else 1.0)
        buffer_price = self.cfg.lock_buffer_pips * pip + self.cfg.lock_extra_r * r_unit

        distance = cost_price + buffer_price
        if distance <= 0.0:
            return None
        price = (
            position.price_open + distance
            if self._is_buy(position)
            else position.price_open - distance
        )
        digits = int(getattr(symbol_info, "digits", 2) or 2) if symbol_info else 2
        return round(price, digits)

    # -- the decision ----------------------------------------------------- #
    def plan(
        self,
        position: Position,
        risk_usd: float,
        atr_price: float,
        contract_size: float,
        spread_price: float = 0.0,
        commission_usd: float = 0.0,
        hold_seconds: float = 0.0,
        mfe_usd: float | None = None,
        peak_win_usd: float | None = None,
        regime: str = "",
        symbol_info: SymbolInfo | None = None,
    ) -> ExitPlan:
        """Decide whether this ticket may tighten, and to where."""
        cfg = self.cfg
        effective_risk = float(risk_usd)
        if effective_risk <= 0.0 and position.sl and position.sl > 0.0:
            # Restart gap: `_initial_risks` is rebuilt from live state, but the
            # position's own stop distance is the SAME risk the trade was
            # opened with — recover 1R from it instead of refusing to protect.
            notional = position.volume * max(contract_size, 1e-9)
            effective_risk = abs(position.price_open - float(position.sl)) * notional
        r_unit = self.r_unit_price(position, effective_risk, contract_size)
        if r_unit <= 0.0:
            return ExitPlan(
                action=ExitAction.HOLD,
                reason="no planned-risk provenance — original stop stands",
                evidence={"risk_usd": float(risk_usd)},
            )

        peak_usd = float(mfe_usd if mfe_usd is not None else (peak_win_usd or 0.0))
        risk_usd = effective_risk
        floor_r, evidence = self.trigger_floor_r(regime, r_unit, atr_price)
        trigger_usd = floor_r * risk_usd
        current_usd = float(position.profit)

        if hold_seconds < cfg.min_hold_seconds:
            return ExitPlan(
                ExitAction.HOLD,
                trigger_r=floor_r,
                reason="within minimum dwell",
                evidence={**evidence, "hold_s": float(hold_seconds)},
            )

        # The first ladder step obeys the noise floor as well: when the ATR is
        # many times the planned risk, a 0.6R "de-risk" would only guarantee a
        # slower stop-out, so it stays blocked.
        atr_floor_r = float(evidence.get("atr_floor_r", 0.0) or 0.0)
        partial_floor_r = max(cfg.partial_de_risk_r, cfg.partial_atr_fraction * atr_floor_r)
        partial_usd = partial_floor_r * risk_usd
        if peak_usd < partial_usd:
            return ExitPlan(
                ExitAction.HOLD,
                trigger_r=floor_r,
                reason="MFE below de-risk threshold",
                evidence={
                    **evidence,
                    "mfe_usd": peak_usd,
                    "partial_usd": round(partial_usd, 2),
                    "trigger_usd": round(trigger_usd, 2),
                },
            )

        # Persistence: the CURRENT profit must still be within the allowance of
        # whatever the trade has actually earned (capped by the full trigger) —
        # a wick that already collapsed is not authority to tighten.
        reference_usd = min(peak_usd, trigger_usd)
        if current_usd < reference_usd - cfg.spike_hold_allowance_r * risk_usd:
            return ExitPlan(
                ExitAction.HOLD,
                trigger_r=floor_r,
                reason="profit collapsed below spike allowance",
                evidence={**evidence, "pnl_usd": current_usd, "reference_usd": round(reference_usd, 2)},
            )

        # Ladder step 1: partial de-risk below the full trigger.
        if peak_usd < trigger_usd:
            candidate = self.de_risk_stop_price(position, r_unit)
            if not self._sl_is_beyond(position, candidate):
                return ExitPlan(
                    ExitAction.HOLD,
                    trigger_r=floor_r,
                    reason="partial de-risk already applied",
                    evidence=evidence,
                )
            return ExitPlan(
                ExitAction.PARTIAL_DE_RISK,
                trigger_r=floor_r,
                new_sl=candidate,
                reason="partial de-risk: stop lifted to -0.45R",
                evidence={**evidence, "mfe_usd": peak_usd},
            )

        lock = self.lock_price(
            position,
            contract_size,
            spread_price,
            commission_usd,
            symbol_info=symbol_info,
            r_unit=r_unit,
        )
        if lock is None:
            return ExitPlan(
                ExitAction.HOLD,
                trigger_r=floor_r,
                reason="no valid cost-aware lock (degenerate notional)",
                evidence=evidence,
            )
        if not self._sl_is_beyond(position, lock):
            return ExitPlan(
                ExitAction.HOLD,
                trigger_r=floor_r,
                reason="stop already at/beyond the cost-aware lock",
                evidence=evidence,
            )
        return ExitPlan(
            ExitAction.BREAKEVEN_LOCK,
            trigger_r=floor_r,
            new_sl=lock,
            reason=f"cost-aware risk-free lock ({floor_r:.2f}R)",
            evidence={**evidence, "mfe_usd": peak_usd, "pnl_usd": current_usd},
        )


# ---------------------------------------------------------------------------
# Learned-profile loading (fail-closed: any problem -> defaults)
# ---------------------------------------------------------------------------


def _validate_learned(payload: Any) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    regimes = payload.get("regimes", payload)
    if not isinstance(regimes, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in regimes.items():
        value = raw.get("trigger_r") if isinstance(raw, dict) else raw
        if value is None or isinstance(value, bool):
            continue
        try:
            trigger = float(value)
        except (TypeError, ValueError):
            continue
        if 0.4 <= trigger <= 5.0:
            out[str(key)] = trigger
    return out


def load_learned_profile(root: Path | None = None) -> dict[str, float]:
    """Read the optimizer's artifact; any failure means 'no learned data'."""
    base = root or artifacts_dir()
    for name in LEARNED_POLICY_FILES:
        path = base / name
        try:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            learned = _validate_learned(payload)
            if learned:
                logger.info(
                    "exit policy learned profile loaded",
                    extra={"path": str(path), "regimes": sorted(learned)},
                )
                return learned
        except Exception as err:
            logger.warning("exit policy artifact ignored: %s (%s)", path, err)
    return {}


def build_policy(root: Path | None = None) -> ExitProtectionPolicy:
    """Policy with the learned overlay applied (single construction point)."""
    return ExitProtectionPolicy(learned=load_learned_profile(root))
