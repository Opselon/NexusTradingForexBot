"""Trading Quality Metric Suite — economic trading metrics vs classification.

ML-BT-001 (STREAM I — Backtest / Forward Test), owner AGENT-BACKTEST.

PURPOSE
A model with 65% classification accuracy can lose money if its losses are
larger than its wins, while a model with 40% accuracy can be highly profitable
with a positive R-expectancy. Classification metrics (accuracy / precision /
recall / F1 from ``model_generation.validation.confusion_and_class_metrics``)
say nothing about the *size* of wins vs losses. This module owns the economic
half of that separation: given a list of executed trades it computes the
institutional trading-quality numbers that gate model promotion.

CONTRACT
* ``TradeRecord`` is the input unit — one CLOSED trade with its realized
  R-multiple, optional USD PnL, risk distance (stop width in price) and
  friction already paid (spread + slippage in ticks, commission in USD).
  Callers that only have R-multiples pass ``realized_r`` alone; every other
  field is optional and degrades gracefully.
* ``calculate_economic_metrics(trades)`` is PURE and DETERMINISTIC: same
  trades in, same numbers out, no I/O, no randomness. It never reads the
  future — metrics are aggregate statistics over the supplied sequence.
* ``compute_slippage_decay()`` re-prices the SAME trade sequence under added
  tick friction and returns the expectancy degradation curve. It mirrors the
  canonical friction model in ``research.metrics._friction_sensitivity`` /
  ``research.backtest``: per-trade R is reduced by
  ``min(friction_frac, FRICTION_R_CAP)`` where
  ``friction_frac = (friction_ticks * price_tick) / risk_distance``. The cap
  is the friction model's own theoretical ceiling: a single trade can never
  lose more than 0.5R to friction, so MAX measurable degradation is 0.5R.
* Empty input or any non-finite value raises ``ValueError`` (ABORT_CONDITIONS)
  — a silent 0.0 expectancy from an empty list is exactly the false-negative
  that would mask a broken evaluation.

NON_GOALS
Does NOT replace classification metrics; ``confusion_and_class_metrics`` keeps
owning accuracy/F1. This module provides trading metrics ALONGSIDE them
(task NON_GOALS). It does not re-price under canonical sizing — that is
``research.metrics.compute_sized_economic_pnl`` (ECON v1) — and it never
mutates the recorded evidence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, model_validator

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.models import ExecutionAssumptions

logger = get_logger("nexus_scalp.research.trading_metrics")

#: Canonical per-trade friction ceiling, IDENTICAL to the R-clamp used by
#: research.metrics._friction_sensitivity / research.backtest compute_backtest
#: (min(friction_frac, 0.5)). A single trade can never lose more than 0.5R to
#: spread+slippage, so the MAXIMUM measurable expectancy degradation is 0.5R.
#: Raising this constant is a contract change that must be re-audited.
FRICTION_R_CAP: float = 0.5

#: Slippage grid for compute_slippage_decay(): [0, 1, 2, 3, 5] extra ticks,
#: exactly the grid named in the ML-BT-001 implementation plan.
DEFAULT_SLIPPAGE_GRID_TICKS: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 5.0)

#: Profit factor reported when gross loss is ~0 and there is gross profit.
#: Capped so a single lucky trade cannot report PF 1e9 (same convention as
#: experience.evaluator: gross_loss==0 -> min(gross_profit, 99)).
PROFIT_FACTOR_INF_CAP: float = 99.0

#: Rounding used on every exported float so the JSON report is byte-stable
#: across platforms (deterministic comparisons in downstream gates).
_METRIC_PRECISION: int = 6

_EMPTY = "trades must be a non-empty sequence of closed trades"
_NON_FINITE = "trade contains non-finite value (trade_index={idx}, field={field}, value={value})"


class TradeRecord(BaseModel):
    """One CLOSED trade — the input unit of the trading quality suite.

    ``realized_r`` is the only required field: callers replaying an R-multiple
    sequence (the common backtest case) pass it alone. ``realized_pnl_usd``,
    ``risk_distance``, ``commission_usd`` and the friction fields are optional
    and are used to compute the friction-adjusted view when present.
    """

    model_config = {"frozen": True}

    trade_id: str = Field(default="", description="Stable id / provenance key")
    realized_r: float = Field(..., description="Realized R-multiple after costs")
    realized_pnl_usd: float | None = Field(
        default=None, description="Realized USD PnL (None = R-only evaluation)"
    )
    #: Stop width in price — the R denominator. When known, extra tick
    #: friction converts to R exactly (price_tick * ticks / risk_distance).
    risk_distance: float | None = Field(default=None, ge=0.0)
    #: Friction ALREADY paid on this trade (recorded, not simulated).
    spread_ticks_paid: float = Field(default=0.0, ge=0.0)
    slippage_ticks_paid: float = Field(default=0.0, ge=0.0)
    commission_usd: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def _validate_finite(self) -> TradeRecord:
        for field in ("realized_r", "commission_usd", "spread_ticks_paid", "slippage_ticks_paid"):
            value = getattr(self, field)
            if not math.isfinite(value):
                raise ValueError(_NON_FINITE.format(idx=-1, field=field, value=value))
        if self.realized_pnl_usd is not None and not math.isfinite(self.realized_pnl_usd):
            raise ValueError(
                _NON_FINITE.format(idx=-1, field="realized_pnl_usd", value=self.realized_pnl_usd)
            )
        if self.risk_distance is not None and not math.isfinite(self.risk_distance):
            raise ValueError(
                _NON_FINITE.format(idx=-1, field="risk_distance", value=self.risk_distance)
            )
        return self


class SlippageDecayPoint(BaseModel):
    """One point of the expectancy-vs-friction degradation curve."""

    model_config = {"frozen": True}

    added_ticks: float = Field(..., ge=0.0, description="Extra adverse friction applied (ticks)")
    friction_r_per_trade: float = Field(
        ..., description="Mean R deducted per trade at this friction level"
    )
    expectancy_r: float = Field(..., description="Expectancy after this friction")
    degradation_r: float = Field(..., description="Expectancy lost vs the zero-friction baseline")
    degradation_pct: float = Field(..., ge=0.0, description="Relative degradation of the baseline")


class EconomicMetrics(BaseModel):
    """Institutional trading-quality report for one trade sequence.

    Every field is a plain serializable scalar/list so the report drops
    straight into a benchmark JSON artifact and stays comparable across
    candidates. ``classification_metrics`` carries the F1/accuracy half
    ALONGSIDE (NON_GOALS: separation, not replacement).
    """

    total_trades: int = Field(..., ge=0)
    win_rate: float = Field(..., ge=0.0, le=1.0)
    wins: int = Field(..., ge=0)
    losses: int = Field(..., ge=0)
    breakevens: int = Field(..., ge=0)

    expectancy_r: float = Field(...)
    avg_win_r: float = Field(...)
    avg_loss_r: float = Field(...)
    #: Canonical profit factor = gross profit / gross loss. >1 profitable.
    #: ``None`` when gross loss is ~0 (reported via profit_factor_capped).
    profit_factor: float | None = Field(default=None, ge=0.0)
    profit_factor_capped: float = Field(..., ge=0.0, description="PF with the no-loss cap applied")

    #: Max drawdown of the cumulative R equity curve, positive magnitude.
    max_drawdown_r: float = Field(..., ge=0.0)
    max_drawdown_usd: float = Field(default=0.0, ge=0.0)
    #: Peak-to-valley depth as a fraction of the peak at the valley point.
    max_drawdown_pct: float = Field(default=0.0, ge=0.0)
    #: Calmar = annualized return / max drawdown. Uses the supplied
    #: ``annualization_factor`` (trades per year); None when drawdown is 0.
    calmar_ratio: float | None = Field(default=None)

    #: Turnover: total absolute R traded (sum |R|) and mean |R| per trade.
    turnover_r: float = Field(..., ge=0.0)
    avg_abs_r: float = Field(..., ge=0.0)

    #: Statistical significance of the mean R (t-stat over the sample).
    expectancy_t_stat: float = Field(default=0.0)
    expectancy_std_r: float = Field(default=0.0)

    #: Friction-adjusted expectancy at the canonical recorded friction, and
    #: the mean R actually deducted per trade (0 when no risk distance).
    net_expectancy_r_after_friction: float = Field(default=0.0)
    friction_r_per_trade: float = Field(default=0.0)

    slippage_decay: list[SlippageDecayPoint] = Field(default_factory=list)

    #: Optional classification half (accuracy/F1) kept alongside, never
    #: replaced. Populated by attach_classification_metrics().
    classification_metrics: dict[str, Any] | None = Field(default=None)

    #: Provenance: what friction assumptions / annualization were used.
    assumptions: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers (pure, no allocation beyond the R arrays)
# ---------------------------------------------------------------------------


def _validate(trades: Sequence[TradeRecord]) -> list[TradeRecord]:
    """ABORT_CONDITIONS: empty or non-finite input raises ValueError."""
    if trades is None:
        raise ValueError(_EMPTY)
    ordered = list(trades)
    if not ordered:
        raise ValueError(_EMPTY)
    for idx, t in enumerate(ordered):
        if not isinstance(t, TradeRecord):
            raise ValueError(f"trades[{idx}] is not a TradeRecord (got {type(t).__name__})")
        for field in ("realized_r", "commission_usd", "spread_ticks_paid", "slippage_ticks_paid"):
            value = getattr(t, field)
            if not math.isfinite(value):
                raise ValueError(_NON_FINITE.format(idx=idx, field=field, value=value))
        if t.realized_pnl_usd is not None and not math.isfinite(t.realized_pnl_usd):
            raise ValueError(
                _NON_FINITE.format(idx=idx, field="realized_pnl_usd", value=t.realized_pnl_usd)
            )
        if t.risk_distance is not None and not math.isfinite(t.risk_distance):
            raise ValueError(
                _NON_FINITE.format(idx=idx, field="risk_distance", value=t.risk_distance)
            )
    return ordered


def _round(value: float) -> float:
    out = round(float(value), _METRIC_PRECISION)
    # NEVER emit -0.0 (breaks byte-stable comparisons).
    return 0.0 if out == 0.0 else out


def _equity_curve(r_values: np.ndarray) -> np.ndarray:
    """Cumulative R equity curve."""
    return np.cumsum(r_values)


def _drawdown_walk(r_values: np.ndarray) -> tuple[float, float]:
    """Peak-to-valley walk matching the repo oracle exactly.

    Returns ``(max_drawdown, peak_at_valley)``.

    Semantics (byte-identical to ``research.metrics.drawdown_metrics``
    (metrics.py:40-83) and ``shadow.comparison._max_drawdown``): the equity
    starts at 0, each trade is booked first, and only then may the running peak
    rise to the new cumulative high — and the peak is FLOORED at the 0 starting
    equity::

        peak_t = max(0, cum_0, ..., cum_t)      # running high, 0-floored
        dd_t   = peak_t - cum_t

    The 0 floor comes from the oracle's ``peak = 0.0`` initialisation plus
    ``if cum > peak`` — the peak is never lowered below the starting equity.

    Consequences:

    * a trade that sets a new equity high contributes ZERO drawdown (the peak
      catches up to ``cum``) — a rising curve is drawdown-free;
    * for a sequence that never went positive the peak stays 0, so the drawdown
      equals the TOTAL loss (``[-1,-2,-3]`` -> 6.0R) — the honest,
      self-consistent number.

    Two implementation traps (both are why naive formulas disagree with the
    oracle on the first failing sequence):

    * ``np.maximum.accumulate(cum)`` WITHOUT the ``np.maximum(cum, 0.0)`` floor
      anchors the peak at the first (negative) cumulative value and reports a
      drawdown SMALLER than the total loss:
        ``[-1,-2,-3] -> cum [-1,-3,-6] -> peak -1 -> dd 5.0`` (oracle: 6.0)
    * a strictly left-aligned peak (``0..t-1``) reports a drawdown on a
      sequence whose equity never declined:
        ``[2,-1,3,-1,-1] -> dd 4.0`` (oracle: 2.0)
    """
    cum = _equity_curve(r_values)
    peak = np.maximum.accumulate(np.maximum(cum, 0.0))
    dd = peak - cum
    valley_idx = int(np.argmax(dd))
    return float(dd[valley_idx]), float(peak[valley_idx])


def _drawdown_r(r_values: np.ndarray) -> float:
    """Max peak-to-valley drawdown of the cumulative R curve (positive)."""
    if r_values.size == 0:
        return 0.0
    return _drawdown_walk(r_values)[0]


def _drawdown_usd(pnl_values: np.ndarray | None) -> float:
    """Same peak convention on the USD PnL curve."""
    if pnl_values is None or pnl_values.size == 0:
        return 0.0
    return _drawdown_walk(pnl_values)[0]


def _profit_factor(r_values: np.ndarray) -> tuple[float | None, float]:
    """Canonical PF = gross profit / gross loss, with the no-loss cap.

    Returns (unbounded_or_None, capped_report_value).
    """
    gross_profit = float(np.sum(r_values[r_values > 0.0]))
    gross_loss = float(np.abs(np.sum(r_values[r_values < 0.0])))
    if gross_loss > 1e-9:
        return gross_profit / gross_loss, min(gross_profit / gross_loss, PROFIT_FACTOR_INF_CAP)
    if gross_profit > 0.0:
        return None, PROFIT_FACTOR_INF_CAP
    return None, 1.0


def _t_stat(r_values: np.ndarray) -> tuple[float, float]:
    n = r_values.size
    if n < 2:
        return 0.0, 0.0
    std = float(np.std(r_values, ddof=1))
    if std <= 1e-9:
        return 0.0, std
    mean = float(np.mean(r_values))
    return mean / (std / math.sqrt(n)), std


def _friction_r_per_trade(
    ordered: list[TradeRecord],
    assumptions: ExecutionAssumptions | None,
    extra_ticks: float = 0.0,
) -> float:
    """Mean R deducted per trade for the given extra adverse friction.

    Mirrors research.metrics._friction_sensitivity exactly: per-trade
    ``frac = (friction_ticks * price_tick) / risk_distance`` clamped at
    ``FRICTION_R_CAP``; trades with no recorded risk distance pay the linear
    per-tick fallback ``0.01 * friction_ticks``.

    Vectorised: the per-trade deduction is computed once for ALL trades in a
    single pass (this is the hot path of the 5-level slippage decay curve and
    of the canonical-friction view in calculate_economic_metrics).
    """
    if assumptions is None or not ordered:
        return 0.0
    friction_ticks = float(assumptions.spread_ticks + assumptions.slippage_ticks + extra_ticks)
    if friction_ticks <= 0.0:
        return 0.0
    tick = float(assumptions.price_tick)
    if tick <= 0.0:
        return 0.0

    risk = np.asarray(
        [
            (t.risk_distance if (t.risk_distance is not None and t.risk_distance > 1e-9) else 0.0)
            for t in ordered
        ],
        dtype=float,
    )
    # Trades WITH a usable risk distance: frac = ticks * price_tick / risk,
    # clamped at the 0.5R ceiling. Vectorised min() over the array.
    has_risk = risk > 1e-9
    frac = np.full_like(risk, 0.01 * friction_ticks)  # linear per-tick fallback
    if bool(np.any(has_risk)):
        frac[has_risk] = np.minimum((friction_ticks * tick) / risk[has_risk], FRICTION_R_CAP)
    return float(np.mean(frac))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def calculate_economic_metrics(
    trades: Sequence[TradeRecord],
    assumptions: ExecutionAssumptions | None = None,
    *,
    annualization_factor: float | None = 250.0,
    classification_metrics: dict[str, Any] | None = None,
) -> EconomicMetrics:
    """Compute the institutional trading-quality metrics for closed trades.

    Pure / deterministic / no I/O. Raises ``ValueError`` on empty input or any
    non-finite value (ABORT_CONDITIONS) — never returns a silent 0.0 report
    that would masquerade as a break-even evaluation.

    Parameters
    ----------
    trades:
        Closed trades, in decision/exit order (order matters for drawdown).
    assumptions:
        ExecutionAssumptions used for the friction-adjusted view. ``None``
        means R-only evaluation: no friction re-pricing is attempted and
        ``net_expectancy_r_after_friction`` equals ``expectancy_r``.
    annualization_factor:
        Trades per year, used for the Calmar ratio. ``None`` skips Calmar.
    classification_metrics:
        The accuracy/F1 half, attached ALONGSIDE (NON_GOALS: separation, not
        replacement). Use ``attach_classification_metrics`` instead when the
        classification pass runs after the economic pass.
    """
    ordered = _validate(trades)
    n = len(ordered)
    r_values = np.asarray([float(t.realized_r) for t in ordered], dtype=float)

    wins = int(np.sum(r_values > 0.0))
    losses = int(np.sum(r_values < 0.0))
    breakevens = n - wins - losses

    expectancy_r = float(np.mean(r_values))
    avg_win_r = float(np.mean(r_values[r_values > 0.0])) if wins else 0.0
    avg_loss_r = float(np.mean(r_values[r_values < 0.0])) if losses else 0.0

    pf_raw, pf_capped = _profit_factor(r_values)
    max_dd_r = _drawdown_r(r_values)

    pnl_values: np.ndarray | None = None
    if all(t.realized_pnl_usd is not None for t in ordered):
        pnl_values = np.asarray([float(t.realized_pnl_usd) for t in ordered], dtype=float)  # type: ignore[misc]
    max_dd_usd = _drawdown_usd(pnl_values)

    # Peak-relative drawdown depth, using the same running peak as
    # _drawdown_r. Institutional convention: depth = dd / peak at the valley.
    #
    # Only meaningful for a POSITIVE peak. When the sequence never went
    # positive (or the peak at the valley is <= 0) there is no positive equity
    # base to measure against, so the honest bound is 1.0 ("peak fully
    # depleted") — never 0.0, which would misreport a losing sequence as
    # drawdown-free. max_drawdown_r stays the authoritative number.
    max_dd_pct = 0.0
    if max_dd_r > 0.0:
        _dd, peak_at_valley = _drawdown_walk(r_values)
        if peak_at_valley > 0.0:
            max_dd_pct = max_dd_r / peak_at_valley
        else:
            max_dd_pct = 1.0

    # Calmar: annualized R return / max drawdown R.
    calmar: float | None = None
    if annualization_factor is not None and annualization_factor > 0.0 and max_dd_r > 0.0 and n > 0:
        annualized_r = expectancy_r * float(annualization_factor)
        calmar = annualized_r / max_dd_r

    t_stat, std_r = _t_stat(r_values)
    turnover_r = float(np.sum(np.abs(r_values)))

    friction_r = _friction_r_per_trade(ordered, assumptions)
    net_expectancy = expectancy_r - friction_r

    # NOTE: the decay curve is computed ONCE here and is deliberately NOT
    # recomputed by callers. calculate_economic_metrics already returns the
    # full slippage_decay list on the report, so a caller that needs the curve
    # should read `metrics.slippage_decay` rather than calling
    # compute_slippage_decay(trades) again — that would re-price the sequence 5
    # more times (once per grid level) for an identical result.
    decay = compute_slippage_decay(ordered, assumptions=assumptions)

    assumption_provenance: dict[str, Any] = {
        "friction_model": "research.metrics._friction_sensitivity parity (min(frac, 0.5))",
        "friction_r_cap": FRICTION_R_CAP,
        "annualization_factor": annualization_factor,
    }
    if assumptions is not None:
        assumption_provenance["execution_assumptions"] = assumptions.model_dump()

    return EconomicMetrics(
        total_trades=n,
        win_rate=_round(wins / n) if n else 0.0,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        expectancy_r=_round(expectancy_r),
        avg_win_r=_round(avg_win_r),
        avg_loss_r=_round(avg_loss_r),
        profit_factor=_round(pf_raw) if pf_raw is not None else None,
        profit_factor_capped=_round(pf_capped),
        max_drawdown_r=_round(max_dd_r),
        max_drawdown_usd=_round(max_dd_usd),
        max_drawdown_pct=_round(max_dd_pct),
        calmar_ratio=_round(calmar) if calmar is not None else None,
        turnover_r=_round(turnover_r),
        avg_abs_r=_round(turnover_r / n) if n else 0.0,
        expectancy_t_stat=_round(t_stat),
        expectancy_std_r=_round(std_r),
        net_expectancy_r_after_friction=_round(net_expectancy),
        friction_r_per_trade=_round(friction_r),
        slippage_decay=decay,
        classification_metrics=classification_metrics,
        assumptions=assumption_provenance,
    )


def compute_slippage_decay(
    trades: Sequence[TradeRecord],
    *,
    assumptions: ExecutionAssumptions | None = None,
    grid_ticks: Sequence[float] | None = None,
) -> list[SlippageDecayPoint]:
    """Expectancy degradation curve across added adverse tick friction.

    Re-prices the SAME trade sequence under ``[0, 1, 2, 3, 5]`` extra ticks
    (the ML-BT-001 plan grid; override with ``grid_ticks``) and reports, per
    level: the mean R deducted, the post-friction expectancy, the absolute
    degradation and the relative degradation of the zero-friction baseline.

    The per-trade R deduction is the CANONICAL friction model
    (``min(friction_frac, FRICTION_R_CAP)``) so the curve is directly
    comparable with ``research.metrics._friction_sensitivity`` and the OOS
    robustness gates. A single trade can never lose more than ``FRICTION_R_CAP``
    R to friction, so MAX measurable degradation is ``FRICTION_R_CAP``.

    Trades with no recorded ``risk_distance`` fall back to the linear
    ``0.01 * friction_ticks`` per-tick charge. When ``assumptions is None``
    no friction re-pricing is possible and the curve is flat (each point
    reports the unchanged expectancy) — the caller is expected to supply
    ExecutionAssumptions for a meaningful decay curve.
    """
    ordered = _validate(trades)
    grid = (
        list(DEFAULT_SLIPPAGE_GRID_TICKS) if grid_ticks is None else [float(g) for g in grid_ticks]
    )
    if not grid:
        raise ValueError("grid_ticks must be a non-empty sequence of tick levels")

    r_values = np.asarray([float(t.realized_r) for t in ordered], dtype=float)
    baseline_expectancy = float(np.mean(r_values))
    can_price_friction = assumptions is not None

    # NOTE on semantics: degradation_pct is a RELATIVE degradation of the
    # zero-friction baseline, and this repo already owns a canonical, numerically
    # stable relative-degradation helper — research.metrics.compute_relative_degradation
    # (metrics.py:86, BUG-140 Phase 6): (in_sample - out_of_sample) / |in_sample|
    # with an epsilon guard and a clip_max. We reuse it instead of inventing a
    # second ratio so the decay curve cannot disagree with the OOS gate family.
    from nexus_scalp.research.metrics import compute_relative_degradation

    curve: list[SlippageDecayPoint] = []
    for level in grid:
        friction_r = _friction_r_per_trade(ordered, assumptions, extra_ticks=level)
        expectancy = baseline_expectancy - friction_r
        degradation = baseline_expectancy - expectancy
        # degradation_pct is the relative degradation of the baseline, so
        # in_sample = the zero-friction expectancy, out_of_sample = stressed.
        # Unsigned magnitude (this field is ge=0): friction can only reduce
        # expectancy; the signed OOS-vs-IS ratio stays in the gate family.
        degradation_pct = abs(
            compute_relative_degradation(
                baseline_expectancy, expectancy, epsilon=1e-12, clip_max=1e9
            )
        )
        curve.append(
            SlippageDecayPoint(
                added_ticks=_round(level),
                friction_r_per_trade=_round(friction_r),
                expectancy_r=_round(expectancy),
                degradation_r=_round(degradation),
                degradation_pct=_round(degradation_pct),
            )
        )
    if not can_price_friction:
        logger.debug(
            "[TRADING_METRICS] event=NO_ASSUMPTIONS_SLIPPAGE_FLAT trades=%d grid=%s",
            len(ordered),
            grid,
        )
    return curve


def attach_classification_metrics(
    metrics: EconomicMetrics,
    classification_metrics: dict[str, Any],
) -> EconomicMetrics:
    """Return a copy with the classification half attached ALONGSIDE.

    NON_GOALS: this module does not compute accuracy/F1 (that is
    ``model_generation.validation.confusion_and_class_metrics``); it only
    carries the two halves in ONE comparable report object.
    """
    return metrics.model_copy(update={"classification_metrics": dict(classification_metrics)})


def economic_metrics_to_report(metrics: EconomicMetrics) -> dict[str, Any]:
    """Canonical JSON-serializable report (model_benchmark_report compatible).

    ``classification`` and ``economic`` sit side by side under
    ``metrics`` so the benchmark report keeps the two families separate
    but comparable per candidate.
    """
    payload = metrics.model_dump(mode="json")
    classification = payload.pop("classification_metrics", None)
    return {
        "metrics": {
            "classification": classification,
            "economic": payload,
        },
        "metric_families_separated": True,
        "contract": {
            "module": "nexus_scalp.research.trading_metrics",
            "task": "ML-BT-001",
            "friction_r_cap": FRICTION_R_CAP,
            "profit_factor_definition": "gross_profit / gross_loss (no-loss -> cap)",
            "max_drawdown_definition": "peak-to-valley of cumulative R equity curve",
        },
    }
