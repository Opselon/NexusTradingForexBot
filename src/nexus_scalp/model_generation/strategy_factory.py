"""Strategy Factory v2 — "Hunter" Strategy Library (PHASE 15D).

A hunter strategy = setup compatibility + entry filters + risk rules + session
windows. The factory is DETERMINISTIC: given a setup detection + row context,
it returns an EXPLAINABLE entry decision (GO / NO_GO) with the configured
risk (RR floor, ATR-based stop/TP, session gate).

Strategies registered (hunter family):
    hunter_sweep_v1, hunter_ob_v1, hunter_fvg_v1, hunter_bos_v1,
    hunter_choch_v1, hunter_ote_v1, hunter_trend_v1, hunter_momentum_v1,
    hunter_breakout_v1, hunter_impulse_v1, hunter_range_v1,
    hunter_reversal_v1, hunter_compression_v1, hunter_london_v1, hunter_smc_v1

Each carries: setup_types, min_quality, rr_floor, max_spread_atr, atr_stop_mult,
atr_tp_mult, session_gate (None = any), regime_ok, direction_alignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexus_scalp.model_generation.setup_detector import SetupDetection

HUNTER_VERSION = "2.0.0"


@dataclass(frozen=True)
class HunterStrategy:
    """Deterministic hunter strategy definition."""

    strategy_id: str
    setup_types: tuple[str, ...]
    min_quality: float = 0.60
    rr_floor: float = 1.8  # minimum reward:risk
    max_spread_atr: float = 0.35  # spread must be <= 0.35 * ATR
    atr_stop_mult: float = 1.0  # stop = atr * mult
    atr_tp_mult: float = 1.8  # TP = atr * mult (RR ~ 1.8)
    session_gate: str | None = None  # "LONDON" / "NY" / None
    regime_ok: tuple[str, ...] = ("TRENDING", "RANGING")
    direction_alignment: bool = True  # require setup direction == strategy bias
    version: str = HUNTER_VERSION

    def to_contract(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "setup_types": list(self.setup_types),
            "min_quality": self.min_quality,
            "rr_floor": self.rr_floor,
            "max_spread_atr": self.max_spread_atr,
            "atr_stop_mult": self.atr_stop_mult,
            "atr_tp_mult": self.atr_tp_mult,
            "session_gate": self.session_gate,
            "regime_ok": list(self.regime_ok),
            "direction_alignment": self.direction_alignment,
        }


#: Registry of all hunter strategies.
HUNTER_STRATEGIES: dict[str, HunterStrategy] = {
    "hunter_sweep_v1": HunterStrategy(
        strategy_id="hunter_sweep_v1",
        setup_types=("LIQUIDITY_SWEEP", "NY_OPEN_SWEEP"),
        min_quality=0.62,
        rr_floor=2.0,
        max_spread_atr=0.30,
        atr_stop_mult=0.9,
        atr_tp_mult=2.0,
    ),
    "hunter_ob_v1": HunterStrategy(
        strategy_id="hunter_ob_v1",
        setup_types=("ORDER_BLOCK",),
        min_quality=0.60,
        rr_floor=1.8,
        max_spread_atr=0.35,
        atr_stop_mult=1.0,
        atr_tp_mult=1.8,
    ),
    "hunter_fvg_v1": HunterStrategy(
        strategy_id="hunter_fvg_v1",
        setup_types=("FVG",),
        min_quality=0.58,
        rr_floor=1.8,
        max_spread_atr=0.35,
        atr_stop_mult=0.9,
        atr_tp_mult=1.8,
    ),
    "hunter_bos_v1": HunterStrategy(
        strategy_id="hunter_bos_v1",
        setup_types=("BREAK_OF_STRUCTURE",),
        min_quality=0.62,
        rr_floor=2.0,
        max_spread_atr=0.30,
        atr_stop_mult=1.0,
        atr_tp_mult=2.2,
    ),
    "hunter_choch_v1": HunterStrategy(
        strategy_id="hunter_choch_v1",
        setup_types=("CHOCH",),
        min_quality=0.60,
        rr_floor=2.0,
        max_spread_atr=0.35,
        atr_stop_mult=1.0,
        atr_tp_mult=2.0,
    ),
    "hunter_ote_v1": HunterStrategy(
        strategy_id="hunter_ote_v1",
        setup_types=("OTE_PULLBACK",),
        min_quality=0.62,
        rr_floor=2.2,
        max_spread_atr=0.30,
        atr_stop_mult=0.8,
        atr_tp_mult=2.2,
    ),
    "hunter_trend_v1": HunterStrategy(
        strategy_id="hunter_trend_v1",
        setup_types=("TREND_CONTINUATION", "BREAK_OF_STRUCTURE"),
        min_quality=0.60,
        rr_floor=1.8,
        max_spread_atr=0.40,
        atr_stop_mult=1.2,
        atr_tp_mult=2.0,
    ),
    "hunter_momentum_v1": HunterStrategy(
        strategy_id="hunter_momentum_v1",
        setup_types=("IMPULSE", "TREND_CONTINUATION"),
        min_quality=0.58,
        rr_floor=1.5,
        max_spread_atr=0.40,
        atr_stop_mult=1.0,
        atr_tp_mult=1.6,
    ),
    "hunter_breakout_v1": HunterStrategy(
        strategy_id="hunter_breakout_v1",
        setup_types=("BREAKOUT_PULLBACK", "COMPRESSION_BREAK"),
        min_quality=0.60,
        rr_floor=2.0,
        max_spread_atr=0.35,
        atr_stop_mult=1.0,
        atr_tp_mult=2.0,
    ),
    "hunter_impulse_v1": HunterStrategy(
        strategy_id="hunter_impulse_v1",
        setup_types=("IMPULSE",),
        min_quality=0.62,
        rr_floor=1.5,
        max_spread_atr=0.35,
        atr_stop_mult=1.1,
        atr_tp_mult=1.8,
    ),
    "hunter_range_v1": HunterStrategy(
        strategy_id="hunter_range_v1",
        setup_types=("RANGING_FADE",),
        min_quality=0.58,
        rr_floor=1.8,
        max_spread_atr=0.30,
        atr_stop_mult=0.9,
        atr_tp_mult=1.6,
        regime_ok=("RANGING",),
    ),
    "hunter_reversal_v1": HunterStrategy(
        strategy_id="hunter_reversal_v1",
        setup_types=("OVERSOLD_BOUNCE", "CHOCH"),
        min_quality=0.60,
        rr_floor=2.0,
        max_spread_atr=0.30,
        atr_stop_mult=0.9,
        atr_tp_mult=1.8,
    ),
    "hunter_compression_v1": HunterStrategy(
        strategy_id="hunter_compression_v1",
        setup_types=("COMPRESSION_BREAK",),
        min_quality=0.58,
        rr_floor=1.8,
        max_spread_atr=0.35,
        atr_stop_mult=1.0,
        atr_tp_mult=1.8,
    ),
    "hunter_london_v1": HunterStrategy(
        strategy_id="hunter_london_v1",
        setup_types=("LONDON_BREAKOUT", "LIQUIDITY_SWEEP"),
        min_quality=0.62,
        rr_floor=2.0,
        max_spread_atr=0.30,
        atr_stop_mult=1.0,
        atr_tp_mult=2.2,
        session_gate="LONDON",
    ),
    "hunter_smc_v1": HunterStrategy(
        strategy_id="hunter_smc_v1",
        setup_types=(
            "ORDER_BLOCK",
            "FVG",
            "BREAK_OF_STRUCTURE",
            "LIQUIDITY_SWEEP",
            "OTE_PULLBACK",
        ),
        min_quality=0.62,
        rr_floor=2.0,
        max_spread_atr=0.32,
        atr_stop_mult=1.0,
        atr_tp_mult=2.0,
    ),
}

DEFAULT_HUNTER_STRATEGY = "hunter_smc_v1"


def get_strategy(strategy_id: str) -> HunterStrategy:
    if strategy_id not in HUNTER_STRATEGIES:
        raise KeyError(f"Unknown hunter strategy {strategy_id!r}")
    return HUNTER_STRATEGIES[strategy_id]


@dataclass(frozen=True)
class EntryDecision:
    """Deterministic GO / NO_GO decision for one strategy + setup + row."""

    strategy_id: str
    setup: SetupDetection
    decision: str  # "GO" / "NO_GO"
    reasons: tuple[str, ...] = ()
    stop_distance: float | None = None
    tp_distance: float | None = None
    direction: str | None = None
    risk_fraction: float = 0.005  # default 0.5% risk per trade

    def to_contract(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "setup_id": self.setup.setup_id,
            "setup_type": self.setup.setup_type,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "stop_distance": self.stop_distance,
            "tp_distance": self.tp_distance,
            "direction": self.direction,
            "risk_fraction": self.risk_fraction,
        }


class StrategyFactory:
    """Evaluates a row + setup against hunter strategies (deterministic)."""

    def __init__(self, strategies: dict[str, HunterStrategy] | None = None) -> None:
        self.strategies = strategies or HUNTER_STRATEGIES

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        setup: SetupDetection,
        row: dict[str, Any],
        strategy_id: str | None = None,
    ) -> EntryDecision:
        """Evaluate one setup against its compatible strategies.

        Returns the best GO decision (highest RR) or a NO_GO decision.
        """
        candidates: list[EntryDecision] = []
        for sid, strat in self.strategies.items():
            if strategy_id is not None and sid != strategy_id:
                continue
            if setup.setup_type not in strat.setup_types:
                continue
            dec = self._evaluate_one(strat, setup, row)
            candidates.append(dec)

        if not candidates:
            return EntryDecision(
                strategy_id=strategy_id or DEFAULT_HUNTER_STRATEGY,
                setup=setup,
                decision="NO_GO",
                reasons=("NO_COMPATIBLE_STRATEGY",),
            )
        # prefer GO with highest RR; else the first NO_GO with fullest reasons
        gos = [c for c in candidates if c.decision == "GO"]
        if gos:
            return max(
                gos, key=lambda c: (c.tp_distance or 0.0) / max(c.stop_distance or 1e-9, 1e-9)
            )
        return candidates[0]

    def _evaluate_one(
        self, strat: HunterStrategy, setup: SetupDetection, row: dict[str, Any]
    ) -> EntryDecision:
        reasons: list[str] = []

        if setup.quality < strat.min_quality:
            reasons.append(f"QUALITY_BELOW_FLOOR({setup.quality:.2f}<{strat.min_quality})")

        atr = max(float(row.get("atr_m1") or row.get("atr") or 0.0), 1e-6)
        spread = float(row.get("spread", 0.0) or 0.0)
        if spread / atr > strat.max_spread_atr:
            reasons.append(f"SPREAD_TOO_WIDE({spread / atr:.3f}>{strat.max_spread_atr})")

        regime = str(row.get("regime", "UNKNOWN")).upper()
        if regime not in strat.regime_ok:
            reasons.append(f"REGIME_NOT_OK({regime})")

        if strat.session_gate:
            session_hit = self._session_hit(row, strat.session_gate)
            if not session_hit:
                reasons.append(f"SESSION_GATE({strat.session_gate})")

        if strat.direction_alignment and setup.factors.get("direction", 0) == 0:
            reasons.append("NO_DIRECTION_ALIGNMENT")

        stop_dist = setup.factors.get("stop_hunt_depth_atr") or 0.0
        if stop_dist == 0.0:
            stop_dist = atr * strat.atr_stop_mult
        tp_dist = stop_dist * (strat.atr_tp_mult / strat.atr_stop_mult)
        if tp_dist / max(stop_dist, 1e-9) < strat.rr_floor:
            reasons.append(f"RR_BELOW_FLOOR({tp_dist / max(stop_dist, 1e-9):.2f}<{strat.rr_floor})")

        if reasons:
            return EntryDecision(
                strategy_id=strat.strategy_id,
                setup=setup,
                decision="NO_GO",
                reasons=tuple(reasons),
                stop_distance=round(stop_dist, 4),
                tp_distance=round(tp_dist, 4),
                direction=("BUY" if setup.factors.get("direction", 0) > 0 else "SELL"),
            )

        return EntryDecision(
            strategy_id=strat.strategy_id,
            setup=setup,
            decision="GO",
            reasons=("HUNTER_QUALIFIED",),
            stop_distance=round(stop_dist, 4),
            tp_distance=round(tp_dist, 4),
            direction=("BUY" if setup.factors.get("direction", 0) > 0 else "SELL"),
        )

    @staticmethod
    def _session_hit(row: dict[str, Any], session: str) -> bool:
        key = f"session_{session.lower()}"
        if key in row:
            return bool(float(row[key]))
        # fallback feature indices: session_london=17, session_ny=18
        idx = {"LONDON": 17, "NY": 18}.get(session.upper())
        if idx is not None:
            return bool(float(row.get(f"feat_{idx}", 0.0)))
        return False


def best_strategy_for(setup: SetupDetection) -> str:
    """Picks the most specific strategy for a setup (first compatible + highest floor)."""
    best = None
    best_floor = -1.0
    for sid, strat in HUNTER_STRATEGIES.items():
        if setup.setup_type in strat.setup_types and strat.min_quality > best_floor:
            best = sid
            best_floor = strat.min_quality
    return best or DEFAULT_HUNTER_STRATEGY
