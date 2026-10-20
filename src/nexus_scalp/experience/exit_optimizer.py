"""Self-learning exit-policy optimizer (the learning half of the exit layer).

What it learns
--------------
The *geometry* of risk-free protection per market regime: at which fraction of
the planned risk (R) a trade should be allowed to lock in a cost-aware stop.

Why it can learn at all
-----------------------
The experience ledger already stores, for every closed trade: the regime it
was taken in, its MFE in R units and its realized R. That is enough to run a
counterfactual on the one decision under study — *when the stop would have
moved* — without re-running the market:

    a candidate trigger T leaves trades with MFE < T untouched (the policy
    never fired) and caps the rest at the lock value (``lock_r``), because the
    stop that fires at +lock_r did not exist for them.

The cap is deliberately WORST-CASE: it assumes a protected trade can never
keep running to its target. A trigger therefore has to earn its place by
cutting real losers without capping winners that were about to pay.

Everything is deterministic (fixed-seed bootstrap), so a re-run on the same
ledger produces the same artifact — a promotion gate can diff two runs.

Output
------
``artifacts/exit_policy.json`` — the file :mod:`exit_policy` reads at runtime:

    {"version": 3, "generated_at": "...", "grid": [...], "samples": {...},
     "regimes": {"TREND": {"trigger_r": 1.35, "lift_r": 0.42, ...}}}

The runtime treats it as an OVERLAY: a missing/malformed/stale file means the
conservative defaults, never a disabled protection path.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

#: Candidate triggers, in R units. Coarse on purpose: a scalp's exit geometry
#: should not chase noise in the third decimal.
DEFAULT_GRID: tuple[float, ...] = (0.6, 0.8, 1.0, 1.2, 1.5, 1.8, 2.2, 2.6, 3.0)

#: Value of a stopped-out protected trade, in R (cost-covered flat scratch).
DEFAULT_LOCK_R = 0.05

#: Minimum closed trades per regime before a learned trigger may be published.
MIN_SAMPLES = 30

#: Bootstrap iterations + the confidence level of the published lift.
BOOTSTRAP_ITERS = 400
CONFIDENCE = 0.90


@dataclass(frozen=True)
class ExitObservation:
    """One closed trade, reduced to what the exit decision needs."""

    regime: str
    mfe_r: float
    final_r: float

    def is_valid(self) -> bool:
        return math.isfinite(self.mfe_r) and math.isfinite(self.final_r) and self.mfe_r >= 0.0


@dataclass(frozen=True)
class RegimeRecommendation:
    regime: str
    trigger_r: float
    baseline_r: float
    expected_r: float
    lift_r: float
    lift_ci_low: float
    samples: int
    accepted: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger_r": round(self.trigger_r, 4),
            "baseline_r": round(self.baseline_r, 4),
            "expected_r": round(self.expected_r, 4),
            "lift_r": round(self.lift_r, 4),
            "lift_ci_low": round(self.lift_ci_low, 4),
            "samples": self.samples,
            "accepted": self.accepted,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExitOptimization:
    recommendations: dict[str, RegimeRecommendation]
    grid: tuple[float, ...]
    lock_r: float
    total_observations: int
    generated_at: str

    def profile(self) -> dict[str, Any]:
        return {
            "version": 3,
            "generated_at": self.generated_at,
            "lock_r": self.lock_r,
            "grid": list(self.grid),
            "total_observations": self.total_observations,
            "regimes": {
                regime: rec.as_dict() for regime, rec in sorted(self.recommendations.items())
            },
        }


def normalize_regime(regime: str | None) -> str:
    """Three buckets, mirroring the runtime policy's vocabulary."""
    key = str(regime or "").strip().upper()
    if key in {"TREND", "TRENDING", "TREND_UP", "TREND_DOWN", "EXPANSION", "VOLATILITY_EXPANSION"}:
        return "TREND"
    if key in {"RANGE", "RANGING", "MEAN_REVERSION", "COMPRESSION", "CHOP"}:
        return "RANGE"
    return "DEFAULT"


def observations_from_records(records: Iterable[Any]) -> list[ExitObservation]:
    """Adapt experience records (or plain dicts) into observations."""
    out: list[ExitObservation] = []
    for record in records:
        if isinstance(record, dict):
            get = record.get
            regime = get("regime") or ""
            mfe_r = get("mfe_r", get("mfe_R", 0.0))
            final_r = get("final_r", get("realized_r_multiple", 0.0))
            closed = get("is_closed", True)
        else:
            context = getattr(record, "context", None)
            behavior = getattr(record, "behavior", None)
            regime = getattr(context, "regime", "") if context is not None else ""
            mfe_r = getattr(behavior, "mfe_r", 0.0) if behavior is not None else 0.0
            final_r = getattr(record, "realized_r_multiple", 0.0)
            closed = getattr(record, "is_closed", False)
        if not closed:
            continue
        observation = ExitObservation(
            regime=normalize_regime(regime),
            mfe_r=float(mfe_r or 0.0),
            final_r=float(final_r or 0.0),
        )
        if observation.is_valid():
            out.append(observation)
    return out


def counterfactual_r(
    observation: ExitObservation,
    trigger_r: float,
    lock_r: float,
    dip_fraction: float = 0.25,
) -> float:
    """Expected R if the stop had been allowed to tighten at ``trigger_r``.

    Three populations, one rule each:

    * ``MFE < trigger`` — the policy never fired: the realized R stands.
    * ``MFE >= trigger`` and the trade closed NEGATIVE — the stop was sitting
      at +``lock_r`` when price returned, so the round-trip is recovered.
    * ``MFE >= trigger`` and the trade closed positive — it may simply have
      run to its exit, but it also exposed the position to a pullback that
      would now stop it at ``lock_r``. That optionality loss is charged as
      ``dip_fraction`` of the distance between the exit and the lock. The
      fraction is ESTIMATED FROM THE SAME LEDGER (see ``estimate_dip_fraction``)
      rather than assumed, and the default is deliberately unflattering.
    """
    if observation.mfe_r < trigger_r:
        return observation.final_r
    if observation.final_r < lock_r:
        return lock_r
    return observation.final_r - dip_fraction * (observation.final_r - lock_r)


def estimate_dip_fraction(
    observations: Sequence[ExitObservation], giveback_r: float = 0.5
) -> float:
    """Share of winning trades that gave back at least ``giveback_r`` of their MFE.

    This is the empirical chance that a locked stop would have been hit while
    the trade was still going to finish green — the cost side of tightening.
    Clamped so a degenerate ledger cannot drive the model to either extreme.
    """
    winners = [o for o in observations if o.final_r > 0.0]
    if not winners:
        return 0.25
    dipped = sum(1 for o in winners if (o.mfe_r - o.final_r) >= giveback_r)
    return min(0.60, max(0.05, dipped / len(winners)))


def expected_r(
    observations: Sequence[ExitObservation],
    trigger_r: float,
    lock_r: float,
    dip_fraction: float = 0.25,
) -> float:
    if not observations:
        return 0.0
    return sum(counterfactual_r(o, trigger_r, lock_r, dip_fraction) for o in observations) / len(
        observations
    )


def _expected_r_arrays(mfe, final, trigger_r: float, lock_r: float, dip_fraction: float):
    """Vectorised counterfactual mean — identical arithmetic, no per-row call."""
    if mfe.size == 0:
        return 0.0
    reached = mfe >= trigger_r
    recovered = np.where(final <= lock_r, lock_r, final - dip_fraction * (final - lock_r))
    values = np.where(reached, np.where(final < 0.0, lock_r, recovered), final)
    return float(values.mean())


def _bootstrap_lift_ci(
    observations: Sequence[ExitObservation],
    chosen: float,
    baseline: float,
    lock_r: float,
    seed: int = 20260101,
    dip_fraction: float = 0.25,
) -> tuple[float, float]:
    """Percentile bootstrap CI of (mean R at ``chosen`` minus mean R at ``baseline``).

    Resampling is done on index arrays against pre-extracted columns: the
    estimator is the same, the allocation is not — a 3k-row ledger fits its CI
    in milliseconds instead of seconds, which is what makes this usable inside
    a nightly job rather than a notebook.
    """
    if not observations:
        return 0.0, 0.0
    mfe = np.fromiter((o.mfe_r for o in observations), dtype=float, count=len(observations))
    final = np.fromiter((o.final_r for o in observations), dtype=float, count=len(observations))
    rng = np.random.default_rng(seed)
    n = mfe.size
    lifts = np.empty(BOOTSTRAP_ITERS, dtype=float)
    for index in range(BOOTSTRAP_ITERS):
        pick = rng.integers(0, n, size=n)
        lifts[index] = _expected_r_arrays(
            mfe[pick], final[pick], chosen, lock_r, dip_fraction
        ) - _expected_r_arrays(mfe[pick], final[pick], baseline, lock_r, dip_fraction)
    lo = float(np.quantile(lifts, (1.0 - CONFIDENCE) / 2.0))
    hi = float(np.quantile(lifts, (1.0 + CONFIDENCE) / 2.0))
    return lo, hi


def negative_rate_at(
    observations: Sequence[ExitObservation], trigger_r: float
) -> tuple[int, float]:
    """Trades that reached ``trigger_r`` but still closed at a loss (share)."""
    reached = [o for o in observations if o.mfe_r >= trigger_r]
    if not reached:
        return 0, 0.0
    losers = sum(1 for o in reached if o.final_r < 0.0)
    return len(reached), losers / len(reached)


def choose_trigger(
    observations: Sequence[ExitObservation],
    grid: Sequence[float] = DEFAULT_GRID,
    *,
    target_negative_rate: float = 0.25,
    min_trigger: float = 0.8,
    max_trigger: float = 2.5,
) -> tuple[float, str]:
    """Smallest floor at which a protected trade is usually NOT a round-trip.

    The statistic is the ledger's own round-trip curve: among trades that
    reached ``T`` R of MFE, what share still closed negative? If that share is
    high, a stop at ``T`` buys nothing — the move reverses anyway — and the
    floor should move up. The first grid level whose bucket falls under the
    target becomes the recommendation; if no level qualifies, the book offers
    no evidence for tightening and the baseline stands.
    """
    candidates = [t for t in sorted(grid) if min_trigger <= t <= max_trigger]
    for trigger in candidates:
        reached, rate = negative_rate_at(observations, trigger)
        if reached >= 10 and rate <= target_negative_rate:
            return trigger, (
                f"{rate:.0%} of {reached} trades reaching {trigger:.1f}R closed negative "
                f"(target {target_negative_rate:.0%})"
            )
    return 1.0, "no MFE level met the round-trip target — baseline kept"


def optimize(
    observations: Sequence[ExitObservation],
    grid: Sequence[float] = DEFAULT_GRID,
    *,
    baseline_trigger_r: float = 1.0,
    lock_r: float = DEFAULT_LOCK_R,
    min_samples: int = MIN_SAMPLES,
    seed: int = 20260101,
    dip_fraction: float | None = None,
    target_negative_rate: float = 0.25,
) -> ExitOptimization:
    """Learn the per-regime floor from the ledger's round-trip curve."""
    buckets: dict[str, list[ExitObservation]] = {}
    for observation in observations:
        buckets.setdefault(observation.regime, []).append(observation)

    recommendations: dict[str, RegimeRecommendation] = {}
    for regime, rows in buckets.items():
        dip = estimate_dip_fraction(rows) if dip_fraction is None else dip_fraction
        baseline_r = expected_r(rows, baseline_trigger_r, lock_r, dip)
        if len(rows) < min_samples:
            recommendations[regime] = RegimeRecommendation(
                regime=regime,
                trigger_r=baseline_trigger_r,
                baseline_r=baseline_r,
                expected_r=baseline_r,
                lift_r=0.0,
                lift_ci_low=0.0,
                samples=len(rows),
                accepted=False,
                reason=f"insufficient sample ({len(rows)} < {min_samples})",
            )
            continue

        trigger, why = choose_trigger(rows, grid, target_negative_rate=target_negative_rate)
        expected = expected_r(rows, trigger, lock_r, dip)
        lift = expected - baseline_r
        changed = abs(trigger - baseline_trigger_r) > 1e-9
        # The CI is evidence FOR a change; computing it when nothing changed
        # would cost the whole budget for a number nobody reads.
        ci_low, _ci_high = (
            _bootstrap_lift_ci(rows, trigger, baseline_trigger_r, lock_r, seed, dip)
            if changed
            else (0.0, 0.0)
        )
        recommendations[regime] = RegimeRecommendation(
            regime=regime,
            trigger_r=trigger,
            baseline_r=baseline_r,
            expected_r=expected,
            lift_r=lift,
            lift_ci_low=ci_low,
            samples=len(rows),
            accepted=changed,
            reason=why,
        )

    return ExitOptimization(
        recommendations=recommendations,
        grid=tuple(grid),
        lock_r=lock_r,
        total_observations=len(observations),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def write_profile(path: Path | str, optimization: ExitOptimization) -> Path:
    """Atomically publish the learned profile (readers never see a half file)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = optimization.profile()
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def load_observations_from_ledger(ledger: Any, limit: int = 20000) -> list[ExitObservation]:
    """Read the experience ledger through its public API (no SQL here)."""
    records = ledger.list_all_experiences(limit=limit)
    return observations_from_records(records)
