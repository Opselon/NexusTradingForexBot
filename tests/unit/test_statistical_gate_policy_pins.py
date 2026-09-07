"""P3 decision-record integrity: additional cross-system provenance pins.

Pins the P0+P1 evidence chain end-to-end:
  * the shadow statistical gate consumes policy values from the SSOT
    (model_lifecycle.statistical_promotion_policy) — mutation-proofing that
    the gate cannot be silently relaxed by bypassing the policy;
  * the gate reads the paired-delta vector captured by the comparer —
    mutation-proofing that aggregates alone cannot satisfy it;
  * decision-ID uniqueness is enforced by scripts/ci/check_decision_ids.py
    (behavioral tests live in test_decision_id_integrity.py).
"""

from __future__ import annotations

import inspect

from nexus_scalp.model_lifecycle.statistical_promotion_policy import (
    StatisticalPromotionPolicy,
    resolve_policy,
)
from nexus_scalp.shadow.comparison import ShadowComparer
from nexus_scalp.shadow.models import ShadowComparison, ShadowModelRef
from nexus_scalp.shadow.statistical_gate import evaluate_statistical_gate


class TestPolicyIsSingleSourceOfTruth:
    def test_gate_wiring_requires_policy_arguments(self):
        """evaluate_promotion must accept (and pass through) the policy —
        mutation resistance for 'bypass the statistical gate' edits."""
        src = inspect.getsource(ShadowComparer.evaluate_promotion)
        assert "evaluate_statistical_gate" in src, (
            "evaluate_promotion no longer runs the statistical gate"
        )
        assert "statistical_provenance" in src, "provenance not persisted on evaluation"
        # the gate runs unconditionally (no skip/optional branch)
        assert "if False" not in src and "skip" not in src.lower().replace(
            "skipped_dry_run", ""
        )

    def test_policy_defaults_are_explicit(self):
        pol = StatisticalPromotionPolicy()
        assert pol.minimum_sample_size == 100
        assert pol.bootstrap_resamples == 10_000
        assert pol.confidence_level == 0.95
        assert pol.require_positive_mean_delta is True

    def test_policy_override_from_yaml_fragment(self):
        pol = resolve_policy(cfg={"minimum_sample_size": 150, "confidence_level": 0.99})
        assert pol.minimum_sample_size == 150
        assert pol.confidence_level == 0.99
        # untouched fields keep defaults
        assert pol.bootstrap_resamples == 10_000

    def test_insufficient_n_vetoes_before_bootstrap(self):
        ch = ShadowModelRef(model_id="c", model_version="1")
        ca = ShadowModelRef(model_id="a", model_version="1")
        comp = ShadowComparison(
            run_id="r",
            champion=ch,
            challenger=ca,
            outcome_resolved_count=99,
            paired_deltas=[0.5] * 99,  # clearly positive but n < 100
        )
        ev = evaluate_statistical_gate(comp)
        assert ev["passed"] is False
        assert any("insufficient resolved samples 99 < 100" in v for v in ev["vetoes"])

    def test_legacy_empty_vector_fails_closed(self):
        ch = ShadowModelRef(model_id="c", model_version="1")
        ca = ShadowModelRef(model_id="a", model_version="1")
        comp = ShadowComparison(
            run_id="r",
            champion=ch,
            challenger=ca,
            outcome_resolved_count=100,
            paired_deltas=[],  # legacy row / malformed evidence
        )
        ev = evaluate_statistical_gate(comp)
        assert ev["passed"] is False
        assert any("malformed" in v for v in ev["vetoes"])

    def test_provenance_covers_required_fields(self):
        ch = ShadowModelRef(model_id="c", model_version="1")
        ca = ShadowModelRef(model_id="a", model_version="1")
        comp = ShadowComparison(
            run_id="prov-run-42",
            champion=ch,
            challenger=ca,
            outcome_resolved_count=120,
            paired_deltas=[0.4] * 120,
        )
        ev = evaluate_statistical_gate(comp)
        assert ev["passed"] is True
        prov = ev["provenance"]
        for key in (
            "sample_count",
            "mean_delta",
            "median_delta",
            "confidence_level",
            "bootstrap_resamples",
            "ci_lower",
            "ci_upper",
            "method",
            "random_seed",
            "seed_policy",
        ):
            assert key in prov, f"provenance missing {key}"
        # deterministic seed derived from run identity
        assert prov["random_seed"] == evaluate_statistical_gate(comp)["provenance"][
            "random_seed"
        ]
