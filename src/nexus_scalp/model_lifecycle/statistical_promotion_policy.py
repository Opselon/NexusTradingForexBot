"""Statistical promotion policy (P0) — explicit, config-driven, fail-closed.

The shadow promotion gate MUST quantify uncertainty over the paired delta it
already computes (delta_r = shadow_r - champion_r per outcome-resolved record).
Every threshold below is a POLICY value declared here and only here (no hidden
magic numbers at call sites); operators override through the YAML
``learning.shadow.statistical_promotion`` section.

Contract (mirrors ``model_lifecycle.learning_config`` conventions):
* pure declarative pydantic model — never authoritative runtime state;
* fail-closed semantics: a promotion decision requires sample_count >=
  ``minimum_sample_size`` AND mean_delta > 0 AND ci_lower > 0 computed by the
  deterministic paired bootstrap in ``shadow.bootstrap``;
* missing/malformed statistical evidence is a VETO — never a pass.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["StatisticalPromotionPolicy", "resolve_policy"]


class StatisticalPromotionPolicy(BaseModel):
    """Explicit uncertainty-aware promotion policy (P0)."""

    #: Minimum outcome-resolved paired samples before the bootstrap CI is
    #: considered evidence at all. 100 is the conservative default: with
    #: scalping R-multiples (heavy tails, occasional ±1R losses) an interval
    #: from fewer trades is dominated by sampling noise. The historical value
    #: (30) is retained ONLY as the shadow-run sample floor (governance
    #: shadow_sample_floor), NOT as statistical evidence sufficiency.
    minimum_sample_size: int = Field(default=100, ge=1)

    #: Bootstrap replicate count. 10000 gives a stable 95% percentile interval
    #: for the mean at this sample scale; bounded runtime via chunked resampling.
    bootstrap_resamples: int = Field(default=10_000, ge=100)

    #: Two-sided confidence level for the delta CI. 95% is the repo's
    #: quantitative standard (walk-forward / calibration reporting bands).
    confidence_level: float = Field(default=0.95, gt=0.0, lt=1.0)

    #: Require the estimated advantage itself to be strictly positive (the CI
    #: can exclude 0 only when the point estimate is on the same side).
    require_positive_mean_delta: bool = Field(default=True)


def resolve_policy(
    policy: StatisticalPromotionPolicy | None = None,
    *,
    cfg: object | None = None,
) -> StatisticalPromotionPolicy:
    """Resolves the policy from an optional YAML config fragment.

    ``cfg`` is the raw ``learning.shadow.statistical_promotion`` mapping (or
    None). Unknown keys are ignored (forward-compatible YAML); invalid values
    raise pydantic ValidationError — a misconfigured statistical gate must
    fail loudly, never silently relax to defaults it never declared.
    """
    if policy is not None:
        return policy
    data: dict[str, object] = {}
    if cfg is not None:
        get = getattr(cfg, "get", None)
        if callable(get):
            for key in StatisticalPromotionPolicy.model_fields:
                val = get(key)
                if val is not None:
                    data[key] = val
    return StatisticalPromotionPolicy(**data)  # type: ignore[arg-type]
