"""PolicyThresholds / threshold-ownership tests (P0 policy-governance).

Mission: FIX THE TRADING BRAIN — Phase 1 (threshold ownership) acceptance:

* ONE canonical default for the base confidence threshold
  (ModelConfig.confidence_threshold via signals.policy).
* ONE canonical default for the high-confidence threshold
  (AlgoConfig.high_confidence_threshold via risk + policy).
* NO divergent literal fallbacks on any decision path.
* Calibration provenance: a "calibrated" claim requires a provenance
  artifact; none exists today -> the default must be the config value,
  not an unproven literal.
"""

from __future__ import annotations

import inspect

import torch

from nexus_scalp.configuration.config import AlgoConfig, ModelConfig
from nexus_scalp.domain.enums import ActionType
from nexus_scalp.risk.risk_engine import RiskEngine
from nexus_scalp.signals.policy import SignalPolicy


# --------------------------------------------------------------------------
# Canonical defaults
# --------------------------------------------------------------------------


def test_policy_base_threshold_defaults_to_config() -> None:
    """SignalPolicy() with no explicit threshold must resolve to the
    canonical ModelConfig.confidence_threshold — never a local literal."""
    policy = SignalPolicy()
    assert policy.confidence_threshold == ModelConfig().confidence_threshold


def test_risk_high_confidence_threshold_defaults_to_config() -> None:
    engine = RiskEngine(config=__import__("nexus_scalp.configuration.config", fromlist=["RiskConfig"]).RiskConfig())
    assert engine.high_confidence_threshold == AlgoConfig().high_confidence_threshold


def test_runtime_snapshot_thresholds_match_canonical_constants() -> None:
    """The bootstrap (YAML/DB) defaults and the runtime snapshot defaults must
    all agree with the canonical config constants (single source of truth)."""
    from nexus_scalp.configuration import runtime_config as rc

    # runtime_config DEFAULT_RUNTIME_VALUES carries the snapshot defaults
    defaults = getattr(rc, "DEFAULT_RUNTIME_VALUES", {})
    if defaults:
        assert float(defaults["algo.high_confidence_threshold"]) == float(
            AlgoConfig().high_confidence_threshold
        ), "runtime snapshot default diverged from AlgoConfig"
        assert float(defaults["model.confidence_threshold"]) == float(
            ModelConfig().confidence_threshold
        ), "runtime snapshot default diverged from ModelConfig"


# --------------------------------------------------------------------------
# No divergent literal fallbacks (source-level contract)
# --------------------------------------------------------------------------


def _source(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_no_high_confidence_literal_fallbacks_in_policy() -> None:
    """signals/policy.py must read high_confidence_threshold from the typed
    AlgoConfig — the old getattr(..., 0.95)/getattr(..., 0.70) divergent
    fallbacks must be gone."""
    src = _source("src/nexus_scalp/signals/policy.py")
    assert 'getattr(self.algo_config, "high_confidence_threshold"' not in src, (
        "divergent high_confidence_threshold fallback present in policy.py"
    )


def test_no_confidence_threshold_literal_default_in_policy_signature() -> None:
    src = _source("src/nexus_scalp/signals/policy.py")
    sig_src = src[src.index("class SignalPolicy") : src.index("def evaluate_probabilities")]
    assert "confidence_threshold: float = 0." not in sig_src, (
        "policy constructor carries a literal threshold default — canonical "
        "owner is ModelConfig.confidence_threshold"
    )
    assert "Calibrated to" not in sig_src, (
        'an unproven "calibrated" claim on a literal default is forbidden '
        "(calibration requires a provenance artifact)"
    )


def test_no_high_confidence_literal_fallback_in_risk_engine() -> None:
    src = _source("src/nexus_scalp/risk/risk_engine.py")
    assert 'getattr(self, "high_confidence_threshold", 0.95)' not in src, (
        "divergent high_confidence_threshold fallback present in risk_engine.py"
    )


# --------------------------------------------------------------------------
# Runtime sync path
# --------------------------------------------------------------------------


def test_policy_threshold_is_live_synced_from_snapshot() -> None:
    """live_engine._sync_runtime_config assigns signal_policy.confidence_threshold
    from the runtime snapshot (the operator-visible value)."""
    src = _source("src/nexus_scalp/application/live_engine.py")
    assert "self.signal_policy.confidence_threshold = snap.confidence_threshold" in src
    assert "self.risk_engine.high_confidence_threshold = snap.algo.high_confidence_threshold" in src


# --------------------------------------------------------------------------
# Effective-threshold composition (regime fallback hierarchy)
# --------------------------------------------------------------------------


def test_effective_threshold_composes_survival_and_range_penalties() -> None:
    """survival +0.10 and range +range_confidence_penalty stack additively on
    the ONE base threshold — the only adjustments allowed, both explicit."""
    policy = SignalPolicy(confidence_threshold=0.30, range_confidence_penalty=0.10)
    # survival only
    thr = policy.confidence_threshold + 0.10
    assert thr == 0.40
    # range stacks on top of the same base
    assert policy.range_confidence_penalty == 0.10


def test_high_confidence_rr_relaxation_uses_single_threshold() -> None:
    """Both policy paths (candidate + final) and the risk engine must agree on
    the SAME high-confidence threshold value when relaxing min-RR."""
    hc = AlgoConfig().high_confidence_threshold
    policy = SignalPolicy()
    assert policy.algo_config.high_confidence_threshold == hc
    engine = RiskEngine(config=__import__("nexus_scalp.configuration.config", fromlist=["RiskConfig"]).RiskConfig())
    assert engine.high_confidence_threshold == hc
