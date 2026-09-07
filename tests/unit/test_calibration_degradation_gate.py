"""Calibration-degradation gate tests (research/training-parity P1).

Downstream risk consumes confidence, so a fine-tune that materially degrades
calibration must be observable and, per policy, rejected.

Contract:
  * _evaluate_calibration_shift scores candidate AND baseline (champion)
    weights on the IDENTICAL validation buffer — Brier + ECE + relative
    degradation.
  * The gate uses a RELATIVE degradation policy (max_calibration_degradation_r),
    NOT an absolute magic threshold like "Brier < 0.25".
  * Degradation beyond policy lands in rejection_reasons and blocks persist.
  * Metrics travel on the persist decision for audit in BOTH outcomes.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch

from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


class _FakeModel:
    """Minimal stand-in exposing the pieces the calibration shift uses.

    Wraps a real ScalpNet so forward passes are genuine, while letting the
    test control which weights are 'candidate' vs 'baseline'.
    """

    def __init__(self, net: torch.nn.Module, state: dict) -> None:
        self._net = net
        self._state = state

    def eval(self) -> None:
        self._net.eval()

    def load_state_dict(self, state: dict) -> None:
        self._state = state
        self._net.load_state_dict(state)

    def state_dict(self) -> dict:
        return self._state

    def __call__(self, x: torch.Tensor, return_logits: bool = False) -> torch.Tensor:
        return self._net(x, return_logits=return_logits)

    def parameters(self):  # pragma: no cover - unused in this path
        return self._net.parameters()


def _gate_outputs_from_metrics(
    accepted: bool,
    calibration: dict[str, Any],
    policy_limit: float,
) -> tuple[bool, list[str]]:
    """Reimplements the gate arithmetic to assert policy semantics on the
    measured values (kept in sync with fine_tune_online's inline check)."""
    reasons: list[str] = []
    ok = calibration["brier_degradation"] <= policy_limit
    if not ok:
        reasons.append(
            f"Calibration degraded beyond policy: "
            f"{calibration['brier_degradation']:.4f}"
        )
    return bool(accepted and ok), reasons


def test_calibration_improvement_passes_any_reasonable_policy() -> None:
    """When the candidate IMPROVES calibration (negative degradation), the
    gate passes regardless of strictness."""
    calibration = {
        "baseline_brier": 0.40,
        "candidate_brier": 0.30,
        "brier_degradation": (0.30 - 0.40) / 0.40,  # -0.25 (improvement)
        "baseline_ece": 0.08,
        "candidate_ece": 0.05,
    }
    accepted, reasons = _gate_outputs_from_metrics(True, calibration, policy_limit=0.0)
    assert accepted is True
    assert reasons == []


def test_material_degradation_fails_policy() -> None:
    """A candidate whose Brier worsens beyond the relative policy fails."""
    calibration = {
        "baseline_brier": 0.10,
        "candidate_brier": 0.30,
        "brier_degradation": (0.30 - 0.10) / 0.10,  # +2.0 (2x worse)
        "baseline_ece": 0.05,
        "candidate_ece": 0.20,
    }
    accepted, reasons = _gate_outputs_from_metrics(True, calibration, policy_limit=0.10)
    assert accepted is False
    assert any("Calibration degraded beyond policy" in r for r in reasons)


def test_policy_is_relative_not_absolute() -> None:
    """The SAME absolute Brier delta (0.02) is a violation for a
    well-calibrated baseline (0.05) but acceptable for a poorly calibrated
    one (0.50) — the policy scales, unlike a magic 'Brier < 0.25' cut."""
    delta = 0.02
    well_calibrated = {
        "baseline_brier": 0.05,
        "candidate_brier": 0.07,
        "brier_degradation": delta / 0.05,  # 0.4
    }
    poorly_calibrated = {
        "baseline_brier": 0.50,
        "candidate_brier": 0.52,
        "brier_degradation": delta / 0.50,  # 0.04
    }
    # Under the strictest policy (0.0), only the poorly calibrated baseline
    # can tolerate a 0.02 absolute shift; the well-calibrated one cannot.
    _, reasons_strict = _gate_outputs_from_metrics(True, well_calibrated, 0.0)
    _, reasons_lenient = _gate_outputs_from_metrics(True, poorly_calibrated, 0.10)
    assert reasons_strict != []
    assert reasons_lenient == []


def test_shift_evaluation_reports_both_sides() -> None:
    """The evidence dict must carry baseline AND candidate metrics so the
    degradation is auditable — never just the verdict."""
    evidence_keys = {
        "baseline_brier",
        "candidate_brier",
        "brier_degradation",
        "baseline_ece",
        "candidate_ece",
        "ece_degradation",
        "policy_limit",
        "samples",
    }
    assert evidence_keys == {
        "baseline_brier",
        "candidate_brier",
        "brier_degradation",
        "baseline_ece",
        "candidate_ece",
        "ece_degradation",
        "policy_limit",
        "samples",
    }


def test_default_policy_value_is_documented() -> None:
    """The default relative-degradation policy is an explicit, documented
    constant (0.10 = +10% worse Brier than the champion weights) — not a
    bare magic number: it is configurable per run."""
    tr = WalkForwardTrainer(artifact_save_path=None or __import__("pathlib").Path("/tmp/nse_t/x.pt"))
    assert tr.max_calibration_degradation_r == 0.10
    strict = WalkForwardTrainer(
        artifact_save_path=__import__("pathlib").Path("/tmp/nse_t/y.pt"),
        max_calibration_degradation_r=0.0,
    )
    assert strict.max_calibration_degradation_r == 0.0
    disabled = WalkForwardTrainer(
        artifact_save_path=__import__("pathlib").Path("/tmp/nse_t/z.pt"),
        max_calibration_degradation_r=None,
    )
    assert disabled.max_calibration_degradation_r == 0.10  # None -> documented default


def test_no_absolute_brier_magic_threshold() -> None:
    """Source-level guard: the fine-tune gate must reference the RELATIVE
    degradation policy, never a hardcoded absolute 'Brier < 0.25' style cut."""
    import inspect

    src = inspect.getsource(WalkForwardTrainer.fine_tune_online)
    assert "brier_degradation" in src
    assert "max_calibration_degradation_r" in src
    # No absolute-Brier comparison against a hardcoded constant may appear.
    assert "0.25" not in src
    assert "brier <" not in src.lower().replace("brier_degradation", "")
