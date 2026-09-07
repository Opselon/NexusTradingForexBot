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

#: LIVE CONFIDENCE FACTOR (ECON v1, updated to the P0 phase-3 contract):
#: the RiskEngine no longer levers on RAW model confidence — confidence now
#: routes through the CALIBRATED multiplier
#: (model_lifecycle.confidence_calibration.confidence_to_risk_multiplier):
#:   state != CALIBRATED  -> 1.0 exactly (flat sizing)
#:   CALIBRATED           -> linear in [0.25, 1.0]
#: i.e. confidence can only DE-RISK, never lever up. The historical
#: raw-softmax constants (divisor 0.85, clip [0.5, 1.2]) are retired; they
#: remain defined ONLY for regression references in old artifacts.
LIVE_CONFIDENCE_DIVISOR: float = 0.85  # retired raw path (kept for provenance)
LIVE_CONFIDENCE_MIN: float = 0.5  # retired raw path (kept for provenance)
LIVE_CONFIDENCE_MAX: float = 1.2  # retired raw path (kept for provenance)
#: Canonical calibrated-confidence multiplier bounds (single source:
#: model_lifecycle.confidence_calibration; mirrored here for the pure policy).
LIVE_CONF_MULTIPLIER_MIN: float = 0.25
LIVE_CONF_MULTIPLIER_MAX: float = 1.0

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
        """Canonical confidence factor (calibrated-confidence contract).

        The live RiskEngine now consumes the CALIBRATED multiplier bounded
        [0.25, 1.0] — confidence can only de-risk, never lever. When no
        calibrated state is supplied the factor is the flat 1.0 (the same
        NOT_CALIBRATED fallback the live engine applies). The historical
        raw-softmax clip(conf/0.85, 0.5, 1.2) is RETIRED: a raw softmax must
        not lever money-at-risk.
        """
        c = float(confidence)
        if not math.isfinite(c) or c < 0.0:
            return 1.0  # unknown/degraded confidence -> flat sizing
        c = min(1.0, c)
        span = LIVE_CONF_MULTIPLIER_MAX - LIVE_CONF_MULTIPLIER_MIN
        return LIVE_CONF_MULTIPLIER_MIN + span * c

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
