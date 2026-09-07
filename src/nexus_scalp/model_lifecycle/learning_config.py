"""Learning Loop Configuration (Learning-Loop Closure, Phases 1 / 6 / 10).

Config-driven, FAIL-CLOSED learning lifecycle controls. EVERY switch defaults
to False/disabled: no self-learning, no automatic shadow attach and no online
fine-tune persistence may run merely because a worker exists. Operators opt in
explicitly per subsystem after the corresponding safety gates are verified.

Contract (mirrors `nexus_scalp.configuration.config` conventions):
* Pure declarative pydantic model — never authoritative runtime state.
* Consumers read it from the bootstrap AppConfig (and the runtime snapshot
  when wired); values are validated ranges, never raw YAML dicts.
* Thresholds that were previously hardcoded (retrain interval bars, buffer
  floor, experience floor, worker interval) are HERE and only here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from nexus_scalp.model_lifecycle.statistical_promotion_policy import (
    StatisticalPromotionPolicy,
)


class RetrainConfig(BaseModel):
    """Controlled (offline) retraining triggers from the experience ledger."""

    enabled: bool = False
    #: Minimum verified experiences in the ledger before a cycle may train.
    min_new_experiences: int = Field(default=50, ge=1)
    #: Minimum seconds between two controlled training cycles (per worker).
    min_interval_seconds: float = Field(default=300.0, ge=1.0)
    #: Bounded concurrency — more than one concurrent controlled training is
    #: refused regardless of configuration.
    max_concurrent_trainings: int = Field(default=1, ge=1, le=1)


class OnlineFinetuneConfig(BaseModel):
    """Online fine-tune persistence gate (Phase 10 decision — Option A).

    The async online fine-tune path is the ONLY path that can rewrite the
    LIVE serving artifact outside the governed promotion transaction
    (LiveEngine._trigger_async_online_fine_tune ->
    _save_model_weights_atomic(bundle.artifact_path)). Its quality gate does
    not substitute for promotion: no OOS/WF/robustness evidence, no shadow
    evaluation, no operator approval token, no governance event on the
    artifact write itself.

    enabled=False (the default and the shipped configuration) blocks the
    dispatch entirely: the champion artifact is immutable between governed
    promotions. Re-enabling requires an explicit operator decision recorded
    against DEC-0006 and a redesign that stages accepted candidates as
    governed Challengers (Option B) — until that lands, keep False.
    """

    enabled: bool = False
    #: Rolling-buffer floor before a fine-tune may even be considered
    #: (was hardcoded 300 bars in the engine).
    min_buffer_bars: int = Field(default=300, ge=32)
    #: Retrain window in completed bars (was hardcoded 50).
    interval_bars: int = Field(default=50, ge=1)


class ShadowConfig(BaseModel):
    """Automatic shadow-attachment controls (Phase 7 / 8)."""

    enabled: bool = False
    #: Minimum shadow decisions before finalize is attempted (shadow worker).
    finalize_after_decisions: int = Field(default=30, ge=1)
    #: Minimum shadow samples required by the promotion evaluation.
    #: NOTE: this is the RUN-LEVEL sample floor (governance shadow_sample_floor).
    #: Statistical EVIDENCE sufficiency is governed separately by
    #: ``statistical_promotion.minimum_sample_size`` (P0) — the historical 30
    #: is deliberately no longer the promotion-evidence threshold.
    min_shadow_samples: int = Field(default=30, ge=1)
    #: P0 statistical promotion policy (bootstrap CI on the paired delta).
    statistical_promotion: StatisticalPromotionPolicy = StatisticalPromotionPolicy()


class PromotionConfig(BaseModel):
    """Promotion policy hook (Phase 9).

    Promotion itself stays manual and operator-gated end-to-end
    (governance.engine.ModelGovernanceEngine + promotion transaction).
    This section only records whether the *automatic* pipeline may prepare
    promotion evidence; the APPROVED -> CHAMPION transition can never be
    automated and has no switch here by design.
    """

    enabled: bool = False


class LearningConfig(BaseModel):
    """Root `learning:` section — every subsystem disabled by default."""

    enabled: bool = False
    retrain: RetrainConfig = RetrainConfig()
    online_finetune: OnlineFinetuneConfig = OnlineFinetuneConfig()
    shadow: ShadowConfig = ShadowConfig()
    promotion: PromotionConfig = PromotionConfig()
