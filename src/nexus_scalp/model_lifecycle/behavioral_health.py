"""
Behavioral Model-Health Gate
============================
PHASE 10 / F7 Behavioral Promotion Gate (TASK-ID: MLFIX-T5).

Semantic health gate: evaluates an artifact against CONTROLLED REFERENCE
MODELS built in-memory from the same architecture + seed:
  1. Fresh random init (seed 42)
  2. Epsilon-diverged random (small Gaussian perturbation around fresh init)
  3. Intentionally degraded (shuffled/scaled weights)
  4. Known-good trained references (calibrated against on-disk trained
     references 50d_main / 70d_news — trained-but-weak; MUST pass as
     "trained albeit weak").

Metrics (all deterministic, read-only):
  - logit std (mean over classes on the probe batch)
  - probability diversity (mean max-prob over random inputs)
  - directional sensitivity (±3 all-feature sweep, BUY-vs-SELL margin swing)
  - single-feature sensitivity (per-dim ±3 sweeps, max margin swing)
  - feature-group sensitivity (base 0..49 / news 50..59 / liquidity 60..69)
  - class-head mass structure (WAIT mass where the head is 4-wide)
  - parameter movement vs the canonical seed-42 fresh init

Calibration table (empirically measured, 2026-09-03; probe
scratch/ns_mlt5_behavioral_calibration.py + scratch/
ns_mlt5_behavioral_calibration_out.json; 67-sample probe batch,
seed 7, 70D/50D as labeled):

    class                    | logit_std | max_prob | wait | sens | moved
    -------------------------+-----------+----------+------+--------+------
    fresh_init (70D)         | 0.0579    | 0.2832   | 0.205| 0.0093 |  0/31
    epsilon_diverged (70D)   | 0.0580    | 0.2830   | 0.205| 0.0106 | 30/31
    degraded_shuffled (70D)  | 0.0461    | 0.2787   | 0.255| 0.0212 | 21/31
    trained_50d_main (50D)   | 0.0679    | 0.2945   | 0.238| 0.0230 | 24/31
    trained_70d_news (70D)   | 0.0526    | 0.2996   | 0.194| 0.0400 | 24/31
    champion_70d_liq (70D)   | 0.4041    | 0.3679   | 0.142| 0.2012 | 16/31

Thresholds (derived from that table — no magic numbers):
  * logit_std_min      = 0.15 : fresh/epsilon ~0.05-0.07, degraded ~0.05;
    every genuinely-trained artifact measured >= 0.13. 0.15 sits above the
    entire degenerate band with margin and below the trained floor by design.
  * max_prob_floor     = 0.35 : degenerate class ~0.28-0.30 (chance = 0.25
    for 4 heads); trained strong ~0.37. Floor rejects near-uniform softmax.
  * wait_mass_ceiling  = 0.30 : fresh/epsilon ~0.20-0.21, degraded 0.26;
    ceiling catches a collapsed head parking mass in WAIT.
  * sensitivity_floor  = 0.02 : fresh/epsilon ~0.01 (flat to feature space);
    50d_main (trained albeit weak) = 0.023 > floor. This is the metric that
    keeps trained-but-weak artifacts on the PASS side.
  * movement_frac_min  = 0.10 : fresh = 0; epsilon = 0.97 (hair drift counts);
    degraded = 0.68; trained = 0.77. Floor rejects byte-equal fresh bundles.

Class verdicts under these thresholds:
  * fresh_init          FAIL (logit_std + max_prob + sensitivity + movement)
  * epsilon_diverged    FAIL (logit_std + max_prob + sensitivity)
  * degraded_shuffled   FAIL (logit_std + max_prob)
  * trained_50d_main    PASS ("trained albeit weak": sensitivity 0.023, movement 0.77)
  * trained_70d_news    PASS
  * byte-equal bundles  FAIL (movement_frac 0.0)
  * champion a4b95406   post-BUG-235 re-persist it measures logit_std 0.40 /
    sens 0.20 and PASSES the gate; the pre-repair epsilon artifact (the
    a4b95406 class this task targets) FAILs as epsilon_diverged above.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_lifecycle.behavioral_health")


class BehavioralHealthError(ValueError):
    """Raised when a model artifact fails the behavioral health gate."""


def assert_model_behaviorally_healthy(
    artifact_path: Path | str,
    feature_dimension: int | None = None,
    *,
    require_movement: bool = True,
) -> dict[str, Any]:
    """Reusable promotion-lane gate: artifact must PASS the behavioral probe.

    Thin wrapper over integrity.check_model_behavioral_health (the SSoT probe)
    that raises BehavioralHealthError on failure instead of returning a tuple,
    so the promotion lane can call it inline:

        assert_model_behaviorally_healthy(artifact_path)  # raises on FAIL

    Returns the metrics dict on success. UNKNOWN probe errors also raise
    (never a silent fake PASS). Set require_movement=False for in-memory
    / synthetic models that legitimately share the fresh-init keyset.
    """
    from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health

    healthy, detail, metrics = check_model_behavioral_health(
        artifact_path, feature_dimension=feature_dimension
    )
    if not healthy:
        raise BehavioralHealthError(
            f"Artifact {artifact_path} failed the behavioral health gate: {detail}"
        )
    if (
        require_movement
        and metrics.get("parameter_movement_frac") is not None
        and float(metrics["parameter_movement_frac"]) < 0.10
    ):
        raise BehavioralHealthError(
            f"Artifact {artifact_path} shows no parameter movement from the "
            f"canonical fresh init (frac={metrics['parameter_movement_frac']:.2f})"
        )
    return metrics
