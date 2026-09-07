"""Canonical Position Sizing Policy (ECON v1)
============================================

ONE source for the live RiskEngine's dynamic sizing FACTORS, so that
backtest / walk-forward / replay / promotion all reproduce the SAME sizing
economics as live execution.

Live RiskEngine.evaluate_proposal composes, in order:

    risk_pct = base_risk_pct * regime_scalar * drawdown_penalty * confidence_scalar

Every factor below is extracted VERBATIM from the live implementation
(no new constants); RiskEngine now delegates to these functions, which makes
this module the single authoritative definition. Historical sizing
(research.economics.compute_sizing) reuses the same policy object, so a
change to live sizing semantics automatically changes historical sizing.

Causality contract for historical use: equity / peak_equity are the values
known AT the trade timestamp (the running equity path BEFORE the trade).
No future state may be consulted.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

#: Live RiskEngine confidence scaling, reused verbatim (single source):
#: scalar = clip(confidence / 0.85, 0.5, 1.2).
LIVE_CONFIDENCE_DIVISOR: float = 0.85
LIVE_CONFIDENCE_MIN: float = 0.5
LIVE_CONFIDENCE_MAX: float = 1.2

#: Live RiskEngine drawdown penalty, reused verbatim:
#: dd>1% -> penalty = max(0.2, 1.0 - dd_pct * 0.2).
LIVE_DRAWDOWN_START_PCT: float = 1.0
LIVE_DRAWDOWN_FLOOR: float = 0.2
LIVE_DRAWDOWN_SLOPE: float = 0.2

#: Live RiskEngine volatility-expansion regime scalar, reused verbatim.
LIVE_VOL_EXPANSION_SCALAR: float = 0.5


class SizingPolicy(BaseModel):
    """Canonical risk-percent sizing policy — live RiskEngine semantics."""

    model_config = ConfigDict(frozen=True)

    #: Base per-trade risk percent (bootstrap RiskConfig default; live.yaml
    #: overrides to 1.0 for the live account).
    base_risk_pct: float = Field(default=0.5, gt=0.0)
    #: Engine-wide directional exposure ceiling (RiskConfig.max_allowed_lots).
    max_allowed_lots: float = Field(default=2.0, gt=0.0)

    @staticmethod
    def confidence_scalar(confidence: float) -> float:
        """Live semantics: clip(confidence / 0.85, 0.5, 1.2)."""
        c = float(confidence)
        if not math.isfinite(c):
            return 1.0
        return max(LIVE_CONFIDENCE_MIN, min(LIVE_CONFIDENCE_MAX, c / LIVE_CONFIDENCE_DIVISOR))

    @staticmethod
    def drawdown_penalty(equity: float, peak_equity: float) -> float:
        """Live semantics (BUG-252 path): >1% drawdown -> max(0.2, 1-0.2*dd%).

        Invalid peak (<=0 / non-finite) falls back to peak=equity -> no
        penalty, exactly like the live engine's honest fallback.
        """
        eq = float(equity)
        peak = float(peak_equity)
        if not math.isfinite(eq) or not math.isfinite(peak) or peak <= 0.0:
            return 1.0
        dd_pct = ((peak - eq) / peak) * 100.0
        if dd_pct <= LIVE_DRAWDOWN_START_PCT:
            return 1.0
        return max(LIVE_DRAWDOWN_FLOOR, 1.0 - dd_pct * LIVE_DRAWDOWN_SLOPE)

    @staticmethod
    def regime_scalar(regime: str | None) -> float:
        """Live semantics: VOLATILITY_EXPANSION halves trade risk."""
        return (
            LIVE_VOL_EXPANSION_SCALAR
            if str(regime or "").upper() == "VOLATILITY_EXPANSION"
            else 1.0
        )

    def effective_risk_pct(
        self,
        *,
        confidence: float,
        regime: str | None,
        equity: float,
        peak_equity: float,
    ) -> tuple[float, dict[str, float]]:
        """Base risk% composed with the live factor pipeline (order preserved).

        Live order: regime -> drawdown -> confidence. Returns the effective
        risk percent plus the factor provenance dict (observability/tests).
        """
        f_regime = self.regime_scalar(regime)
        f_dd = self.drawdown_penalty(equity, peak_equity)
        f_conf = self.confidence_scalar(confidence)
        factors = {"regime": f_regime, "drawdown": f_dd, "confidence": f_conf}
        pct = self.base_risk_pct * f_regime * f_dd * f_conf
        if not math.isfinite(pct) or pct <= 0.0:
            pct = 0.0
        return pct, factors
