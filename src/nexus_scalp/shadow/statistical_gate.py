"""Statistical promotion evidence (P0) — uncertainty-aware promotion gate.

P0 finding (verified): ``ShadowComparer.evaluate_promotion`` decided
eligibility from raw point deltas (``mean_delta_r``/``expectancy_delta``) and a
sample-count floor (30). A small lucky sample with a positive point delta
passed every veto. This module adds the missing evidence layer:

    paired-difference bootstrap CI over delta_r = shadow_r - champion_r

The statistical gate is ADDITIVE: all existing vetoes (drawdown, OOS,
robustness, calibration, strategy regressions, tail, invalid-rate) still
apply. Fail-closed: when the bootstrap cannot run or the comparison inputs are
malformed, the gate VETOES with an explicit reason — "unknown" is never
transformed into "pass".

Provenance (sample_count / mean / median / CI / resamples / confidence /
seed / method) is returned for persistence on the PromotionEvaluation.
"""

from __future__ import annotations

import math
from typing import Any

from nexus_scalp.model_lifecycle.statistical_promotion_policy import (
    StatisticalPromotionPolicy,
    resolve_policy,
)
from nexus_scalp.shadow.bootstrap import BootstrapError, bootstrap_mean_ci, derive_seed_from_run_id
from nexus_scalp.shadow.models import ShadowComparison

__all__ = ["evaluate_statistical_gate", "statistical_evidence_summary"]


def evaluate_statistical_gate(
    comparison: ShadowComparison,
    *,
    policy: StatisticalPromotionPolicy | None = None,
    cfg: object | None = None,
) -> dict[str, Any]:
    """Runs the uncertainty-aware statistical gate on a shadow comparison.

    Returns the evidence dict:
        ``passed``       — bool gate verdict (see fail-closed contract below)
        ``vetoes``       — list[str] explicit veto reasons (empty when passed)
        ``provenance``   — dict persisted next to the promotion evaluation
        ``reasons``      — informational notes

    Fail-closed contract — the gate passes ONLY when ALL of:
      * outcome_resolved_count >= policy.minimum_sample_size
      * the bootstrap CI computed cleanly on the paired deltas
      * mean_delta > 0 (when require_positive_mean_delta)
      * ci_lower > 0  (the CI supports the required direction)

    Any malformed input (non-finite delta, mismatched payload lengths)
    yields passed=False with an explicit veto reason.
    """
    pol = resolve_policy(policy, cfg=cfg)
    provenance: dict[str, object] = {
        "policy_minimum_sample_size": pol.minimum_sample_size,
        "policy_bootstrap_resamples": pol.bootstrap_resamples,
        "policy_confidence_level": pol.confidence_level,
    }
    vetoes: list[str] = []
    reasons: list[str] = []

    n = int(comparison.outcome_resolved_count)
    if n < pol.minimum_sample_size:
        vetoes.append(
            f"statistical gate: insufficient resolved samples {n} < {pol.minimum_sample_size}"
        )
        return _finish(False, vetoes, reasons, provenance)

    deltas = _extract_paired_deltas(comparison)
    if deltas is None:
        vetoes.append(
            "statistical gate: paired delta evidence malformed/unavailable "
            "(delta_r payload missing or mismatched with resolved records)"
        )
        return _finish(False, vetoes, reasons, provenance)
    if len(deltas) != n:
        vetoes.append(
            f"statistical gate: input lengths mismatched: {len(deltas)} deltas != {n} resolved"
        )
        return _finish(False, vetoes, reasons, provenance)

    seed = derive_seed_from_run_id(comparison.run_id)
    try:
        result = bootstrap_mean_ci(
            deltas,
            resamples=pol.bootstrap_resamples,
            confidence_level=pol.confidence_level,
            seed=seed,
        )
    except BootstrapError as e:
        vetoes.append(f"statistical gate: bootstrap failed: {e}")
        return _finish(False, vetoes, reasons, provenance)

    ci_lower = float(result["ci_lower"])
    ci_upper = float(result["ci_upper"])
    mean_delta = float(result["mean_delta"])
    provenance.update(
        {
            "sample_count": int(result["sample_count"]),
            "mean_delta": mean_delta,
            "median_delta": float(result["median_delta"]),
            "confidence_level": float(result["confidence_level"]),
            "bootstrap_resamples": int(result["resamples"]),
            "ci_lower": ci_lower,
            "ci_upper": ci_upper,
            "method": str(result["method"]),
            "random_seed": int(seed),
            "seed_policy": "BLAKE2b(run_id) deterministic — no global RNG",
        }
    )

    if pol.require_positive_mean_delta and not mean_delta > 0.0:
        vetoes.append(f"statistical gate: mean delta {mean_delta:.4f}R not positive")
    if not ci_lower > 0.0:
        vetoes.append(
            f"statistical gate: CI lower bound {ci_lower:.4f}R does not exceed 0 "
            f"(CI [{ci_lower:.4f}, {ci_upper:.4f}]R, "
            f"{pol.confidence_level:.0%} confidence, n={len(deltas)})"
        )

    passed = not vetoes
    if passed:
        reasons.append(
            f"statistical evidence: n={len(deltas)}, mean {mean_delta:.4f}R, "
            f"{pol.confidence_level:.0%} CI [{ci_lower:.4f}, {ci_upper:.4f}]R > 0"
        )
    return _finish(passed, vetoes, reasons, provenance)


def _extract_paired_deltas(comparison: ShadowComparison) -> list[float] | None:
    """Extracts the paired delta vector the comparison was built from.

    Source: the per-record ``delta_r`` values (CHG-0046 D3 pairing) that
    ``ShadowComparer.compare`` now snapshots onto ``comparison.paired_deltas``
    at aggregation time. Legacy comparisons persisted before this field carry
    an empty list — the statistical gate then vetoes fail-closed instead of
    fabricating a vector from aggregates.
    """
    payload = getattr(comparison, "paired_deltas", None)
    if not isinstance(payload, (list, tuple)) or not payload:
        return None
    out: list[float] = []
    for v in payload:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f):
            return None
        out.append(f)
    return out


def statistical_evidence_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    """Compact summary for logs/UI (no fabricated numbers)."""
    prov = evidence.get("provenance") or {}
    assert isinstance(prov, dict)
    vetoes = evidence.get("vetoes") or []
    assert isinstance(vetoes, list)
    return {
        "passed": bool(evidence.get("passed")),
        "sample_count": prov.get("sample_count"),
        "ci_lower": prov.get("ci_lower"),
        "ci_upper": prov.get("ci_upper"),
        "vetoes": list(vetoes),
    }


def _finish(
    passed: bool,
    vetoes: list[str],
    reasons: list[str],
    provenance: dict[str, object],
) -> dict[str, object]:
    return {
        "passed": passed,
        "vetoes": vetoes,
        "reasons": reasons,
        "provenance": provenance,
    }
