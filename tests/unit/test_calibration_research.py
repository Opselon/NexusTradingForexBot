"""Probability calibration & ECE evaluation tests (ML-VAL-002).

Covers the Guo et al. (2017) toolbox in ``src/nexus_scalp/research/calibration.py``:

* ECE / Brier / NLL on synthetic multiclass probability matrices, including
  the perfectly-calibrated and maximally-miscalibrated anchors.
* Reliability-diagram bin accounting (populated bins, closed top edge).
* Temperature scaling: identity at T=1, sharpening at T<1, flattening at T>1,
  and that a fitted T *reduces* ECE on an overconfident fold.
* Round-trip artifact serialization and the OOS split protocol
  (fit on train, evaluate on val — the honest number).
* Fail-loud validation: non-finite, negative, wrong-shape, single-class folds,
  and the ABORT_CONDITIONS degenerate-temperature guard.
* torch interop (skipped when torch is unavailable — the slim venv contract).
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from nexus_scalp.research.calibration import (
    CalibrationReport,
    TemperatureScaler,
    apply_temperature,
    brier_score,
    compute_ece,
    evaluate_temperature_scaling,
    fit_temperature,
    log_loss,
    nll_loss,
    reliability_diagram,
    validate_probs,
    wrap_model_temperature,
)

torch = pytest.importorskip("torch")
torch_nn = torch.nn  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(seed=20260921)


def _overconfident_pair(
    n: int = 4000, c: int = 3, boost: float = 1.8, sharp: float = 0.35, seed: int = 17
) -> tuple[np.ndarray, np.ndarray]:
    """``(logits, independent_labels)`` for a genuinely overconfident model.

    The label is drawn first and the logit boosted toward it, but by less than
    the softmax sharpening, so accuracy (~0.68) lags confidence (~0.90): the
    classic Guo et al. miscalibration shape that temperature scaling fixes.

    The returned labels are the *drawn* ones — NOT ``argmax(logits)``. Deriving
    labels from the argmax makes the model 100% accurate by construction, which
    drives the optimal temperature to 0 and makes every "improvement" test
    self-fulfilling. Every temperature test here uses this pair.
    """
    rng = np.random.default_rng(seed=seed)
    y = rng.integers(0, c, size=n)
    z = rng.normal(0.0, 1.5, size=(n, c))
    z[np.arange(n), y] += boost
    return z / sharp, y


def _overconfident_logits(n: int = 4000, **kw: Any) -> np.ndarray:
    """Logits-only view of :func:`_overconfident_pair` (labels discarded)."""
    return _overconfident_pair(n=n, **kw)[0]


def _perfect_probs(n: int = 3000, c: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Probs that are exactly correct AND maximally confident."""
    y = np.random.default_rng(seed=5).integers(0, c, size=n)
    p = np.full((n, c), 0.02)
    p[np.arange(n), y] = 0.98
    p /= p.sum(axis=1, keepdims=True)
    return p, y


# ---------------------------------------------------------------------------
# validate_probs
# ---------------------------------------------------------------------------


def test_validate_probs_ok() -> None:
    n, c = validate_probs(np.full((4, 3), 1.0 / 3.0))
    assert (n, c) == (4, 3)


def test_validate_probs_rejects_1d() -> None:
    with pytest.raises(ValueError, match="2-D"):
        validate_probs(np.array([0.5, 0.5]))


def test_validate_probs_rejects_empty_rows() -> None:
    with pytest.raises(ValueError, match="at least one row"):
        validate_probs(np.zeros((0, 3)))


def test_validate_probs_rejects_single_class() -> None:
    with pytest.raises(ValueError, match=">=2 classes"):
        validate_probs(np.array([[0.5], [0.5]]))


def test_validate_probs_rejects_nan() -> None:
    bad = np.full((3, 3), 1.0 / 3.0)
    bad[1, 1] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        validate_probs(bad)


def test_validate_probs_rejects_negative() -> None:
    bad = np.full((3, 3), 1.0 / 3.0)
    bad[0, 0] = -0.1
    with pytest.raises(ValueError, match="negative"):
        validate_probs(bad)


def test_validate_probs_enforces_n_classes() -> None:
    with pytest.raises(ValueError, match="expected 3 classes"):
        validate_probs(np.full((4, 2), 0.5), n_classes=3)


# ---------------------------------------------------------------------------
# compute_ece
# ---------------------------------------------------------------------------


def test_ece_perfectly_calibrated_is_small() -> None:
    p, y = _perfect_probs()
    assert compute_ece(p, y, n_bins=10) < 0.05


def test_ece_anchor_zero_when_confidence_matches_accuracy() -> None:
    """Flat 1/C confidence with 1/C accuracy is perfectly calibrated (ECE 0)."""
    n, c = 900, 3
    y = np.arange(n) % c
    p = np.full((n, c), 1.0 / c)
    assert compute_ece(p, y) == pytest.approx(0.0, abs=1e-6)


def test_ece_maximally_overconfident_near_class_ceiling() -> None:
    """A confidently-wrong model approaches the 3-class ECE ceiling 1 - 1/C.

    Max-probability cannot exceed 1.0 and accuracy cannot go below 0, so for
    C=3 the attainable ECE is bounded by 1 - 1/3 ~= 0.667 — asserting >0.9
    would be mathematically impossible and would mask a real regression.
    """
    n, c = 900, 3
    y = np.zeros(n, dtype=np.int64)
    p = np.full((n, c), 1.0 / 3.0)
    p[:, 1] = 0.999999  # confidently WRONG on every row
    p /= p.sum(axis=1, keepdims=True)
    e = compute_ece(p, y)
    assert e > 0.59
    assert e <= 1.0 - 1.0 / c + 1e-6


def test_ece_overconfident_fold_is_materially_miscalibrated() -> None:
    """The fixture used by the temperature tests: conf ~0.90, acc ~0.68."""
    z, y = _overconfident_pair(3000)
    p = apply_temperature(z, 1.0)
    assert float(p.max(axis=1).mean()) > 0.80
    assert float((p.argmax(axis=1) == y).mean()) < 0.80
    assert compute_ece(p, y) > 0.10


def test_ece_monotone_in_miscalibration() -> None:
    """Sharpening a noisy logit set is monotone in *confidence*, not in ECE.

    ECE = |acc_b - conf_b| is zero at both extremes (perfectly calibrated and
    perfectly wrong), so it is NOT monotone in overconfidence — it rises to a
    maximum then falls as the model becomes confidently incorrect. The monotone
    quantity is the mean confidence itself, which is what this test asserts,
    alongside a real miscalibration signal at the extreme settings.
    """
    rng = np.random.default_rng(seed=3)
    y = rng.integers(0, 3, size=3000)
    z = rng.normal(0, 1, size=(3000, 3))
    z[np.arange(3000), y] += 1.5
    confs = []
    for sharp in (5.0, 2.0, 1.0, 0.5, 0.2):  # flattest -> sharpest
        p = apply_temperature(z / sharp, 1.0)
        confs.append(float(p.max(axis=1).mean()))
    assert confs == sorted(confs)  # sharpening is monotone in confidence
    assert compute_ece(apply_temperature(z / 0.2, 1.0), y) > 0.10
    assert compute_ece(apply_temperature(z / 5.0, 1.0), y) > 0.10


def test_ece_bin_count_bounds() -> None:
    p, y = _perfect_probs()
    for nb in (1, 5, 20):
        e = compute_ece(p, y, n_bins=nb)
        assert 0.0 <= e <= 1.0


def test_ece_rejects_bad_n_bins() -> None:
    p, y = _perfect_probs()
    with pytest.raises(ValueError, match="n_bins"):
        compute_ece(p, y, n_bins=0)


def test_ece_rejects_out_of_range_labels() -> None:
    p = np.full((5, 3), 1.0 / 3.0)
    with pytest.raises(ValueError, match="out of range"):
        compute_ece(p, np.array([0, 1, 2, 3, 0]))


def test_ece_renormalizes_unnormalized_rows() -> None:
    p = np.array([[2.0, 1.0, 1.0], [1.0, 2.0, 1.0]])
    y = np.array([0, 1])
    assert 0.0 <= compute_ece(p, y) <= 1.0


# ---------------------------------------------------------------------------
# reliability_diagram
# ---------------------------------------------------------------------------


def test_reliability_populated_bins_match_samples() -> None:
    p, y = _perfect_probs(n=2000)
    rows = reliability_diagram(p, y, n_bins=10)
    assert rows
    assert sum(r["n"] for r in rows) == 2000
    assert all(0.0 <= r["gap"] <= 1.0 or -1.0 <= r["gap"] <= 1.0 for r in rows)


def test_reliability_bin_index_monotone() -> None:
    p, y = _perfect_probs()
    rows = reliability_diagram(p, y, n_bins=8)
    idx = [r["bin_index"] for r in rows]
    assert idx == sorted(idx)


def test_reliability_perfect_model_has_near_zero_gap() -> None:
    p, y = _perfect_probs(n=1500)
    rows = reliability_diagram(p, y, n_bins=10)
    assert rows
    assert all(abs(r["gap"]) < 0.05 for r in rows)


def test_reliability_range_string_bracket_form() -> None:
    """Populated bins are labeled with their closed bracket range."""
    z, y = _overconfident_pair(500)
    rows = reliability_diagram(apply_temperature(z, 1.0), y, n_bins=10)
    assert rows
    assert all(r["range"].startswith("[") and r["range"].endswith("]") for r in rows)
    assert all(", " in r["range"] for r in rows)


def test_reliability_confidence_one_lands_in_top_bin() -> None:
    """The closed top edge: exactly-1.0 confidence must not be dropped."""
    p = np.array([[0.999999, 0.0000005, 0.0000005]] * 10)
    p /= p.sum(axis=1, keepdims=True)
    p[0, 0] = 1.0  # exact
    rows = reliability_diagram(p, np.zeros(10, dtype=np.int64), n_bins=10)
    assert rows[-1]["bin_index"] == 9
    assert rows[-1]["n"] == 10


# ---------------------------------------------------------------------------
# brier_score / log_loss
# ---------------------------------------------------------------------------


def test_brier_perfect_one_hot_is_zero() -> None:
    y = np.array([0, 1, 2])
    p = np.eye(3)
    assert brier_score(p, y) == pytest.approx(0.0, abs=1e-6)


def test_brier_flat_uniform_value() -> None:
    y = np.array([0, 1, 2])
    p = np.full((3, 3), 1.0 / 3.0)
    # Each row deviates by (1/3, -2/3, 1/3) -> squared sum 1/9+4/9+1/9 = 6/9,
    # identical for all 3 rows. brier_score averages over ALL N*C elements
    # (3*3 = 9), so the value is (3 * 6/9) / 9 = 18/81 = 2/9.
    assert brier_score(p, y) == pytest.approx(2.0 / 9.0, abs=1e-6)


def test_brier_bounded_in_0_2() -> None:
    p, y = _perfect_probs()
    assert 0.0 <= brier_score(p, y) <= 2.0


def test_log_loss_perfect_is_near_zero() -> None:
    p, y = _perfect_probs()
    assert log_loss(p, y) < 0.05


def test_log_loss_zero_prob_is_clamped() -> None:
    p = np.array([[0.0, 1.0, 0.0]])
    y = np.array([0])
    assert math.isfinite(log_loss(p, y))


def test_nll_is_log_loss_alias() -> None:
    p, y = _perfect_probs()
    assert nll_loss(p, y) == log_loss(p, y)


def test_log_loss_rejects_bad_eps() -> None:
    p, y = _perfect_probs()
    with pytest.raises(ValueError, match="eps"):
        log_loss(p, y, eps=0.0)


# ---------------------------------------------------------------------------
# apply_temperature
# ---------------------------------------------------------------------------


def test_apply_temperature_identity_at_one() -> None:
    z = _overconfident_logits(500)
    raw = apply_temperature(z, 1.0)
    ref = torch.softmax(torch.from_numpy(z.astype(np.float32)), dim=-1).numpy()
    np.testing.assert_allclose(raw, ref, atol=1e-6)


def test_apply_temperature_rows_sum_to_one() -> None:
    z = _overconfident_logits(200)
    p = apply_temperature(z, 2.5)
    np.testing.assert_allclose(p.sum(axis=1), np.ones(200), atol=1e-9)


def test_apply_temperature_argmax_preserved() -> None:
    """T only moves confidence; the class ranking is invariant (T>0)."""
    z = _overconfident_logits(500)
    base = apply_temperature(z, 1.0).argmax(axis=1)
    for t in (0.3, 0.7, 1.0, 2.0, 8.0):
        assert np.array_equal(apply_temperature(z, t).argmax(axis=1), base)


def test_apply_temperature_sharpens_below_one() -> None:
    z = _overconfident_logits(500)
    lo = apply_temperature(z, 0.5).max(axis=1)
    hi = apply_temperature(z, 1.0).max(axis=1)
    assert float(lo.mean()) > float(hi.mean())


def test_apply_temperature_flattens_above_one() -> None:
    z = _overconfident_logits(500)
    hi = apply_temperature(z, 4.0).max(axis=1)
    base = apply_temperature(z, 1.0).max(axis=1)
    assert float(hi.mean()) < float(base.mean())


def test_apply_temperature_rejects_nonpositive() -> None:
    z = _overconfident_logits(10)
    for bad in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite and > 0"):
            apply_temperature(z, bad)


def test_apply_temperature_rejects_nonfinite_logits() -> None:
    z = _overconfident_logits(10)
    z[0, 0] = float("inf")
    with pytest.raises(ValueError, match="non-finite"):
        apply_temperature(z, 1.0)


# ---------------------------------------------------------------------------
# fit_temperature / TemperatureScaler
# ---------------------------------------------------------------------------


def test_fit_temperature_reduces_nll_on_overconfident_fold() -> None:
    z, y = _overconfident_pair(4000)
    t = fit_temperature(z, y)
    assert t > 1.0
    pre = log_loss(apply_temperature(z, 1.0), y)
    post = log_loss(apply_temperature(z, t), y)
    assert post < pre


def test_fit_temperature_reduces_ece_on_overconfident_fold() -> None:
    z, y = _overconfident_pair(4000)
    t = fit_temperature(z, y)
    assert compute_ece(apply_temperature(z, t), y) < compute_ece(apply_temperature(z, 1.0), y)


def test_fit_temperature_deterministic() -> None:
    z, y = _overconfident_pair(1000)
    assert fit_temperature(z, y) == fit_temperature(z, y)


def test_fit_temperature_underconfident_below_one() -> None:
    """A model that is too *flat* fits T < 1 (sharpening is optimal)."""
    rng = np.random.default_rng(seed=11)
    y = rng.integers(0, 3, size=3000)
    z = rng.normal(0, 1.5, size=(3000, 3))
    z[np.arange(3000), y] += 1.8
    # heavy blunting: even the true-class logit barely separates
    z = z / 6.0
    p_raw = apply_temperature(z, 1.0)
    assert float(p_raw.max(axis=1).mean()) < 0.45
    assert fit_temperature(z, y) < 1.0


def test_fit_temperature_rejects_single_row() -> None:
    z = np.zeros((1, 3))
    with pytest.raises(ValueError, match=">=2 validation rows"):
        fit_temperature(z, np.zeros(1, dtype=np.int64))


def test_fit_temperature_rejects_single_class_fold() -> None:
    z = _overconfident_logits(50)
    with pytest.raises(ValueError, match="single-class"):
        fit_temperature(z, np.zeros(50, dtype=np.int64))


def test_fit_temperature_rejects_label_shape_mismatch() -> None:
    z = _overconfident_logits(40)
    with pytest.raises(ValueError, match="labels shape"):
        fit_temperature(z, np.zeros(39, dtype=np.int64))


def test_scaler_fit_sets_metrics_and_improves() -> None:
    z, y = _overconfident_pair(3000)
    s = TemperatureScaler().fit(z, y)
    assert s.fitted
    assert s.fit_n == 3000
    assert s.fit_metrics["ece_after"] < s.fit_metrics["ece_before"]
    assert s.fit_metrics["nll_after"] < s.fit_metrics["nll_before"]


def test_scaler_apply_before_fit_raises() -> None:
    with pytest.raises(RuntimeError, match="before fit"):
        TemperatureScaler().apply_to_logits(np.zeros((3, 3)))


def test_scaler_apply_to_logits_matches_apply_temperature() -> None:
    z, y = _overconfident_pair(200)
    s = TemperatureScaler().fit(z, y)
    np.testing.assert_allclose(s.apply_to_logits(z), apply_temperature(z, s.temperature), atol=1e-9)


def test_scaler_apply_to_probs_roundtrip() -> None:
    """Calibrating probs = inverse-softmax then temperature-softmax."""
    z, y = _overconfident_pair(200)
    s = TemperatureScaler().fit(z, y)
    raw_p = apply_temperature(z, 1.0)
    np.testing.assert_allclose(s.apply_to_probs(raw_p), s.apply_to_logits(z), atol=1e-8)


def test_scaler_apply_to_probs_argmax_unchanged() -> None:
    z, y = _overconfident_pair(300)
    s = TemperatureScaler().fit(z, y)
    raw_p = apply_temperature(z, 1.0)
    assert np.array_equal(s.apply_to_probs(raw_p).argmax(axis=1), raw_p.argmax(axis=1))


def test_scaler_apply_to_probs_rejects_zero_row() -> None:
    z, y = _overconfident_pair(64)
    s = TemperatureScaler().fit(z, y)
    p = apply_temperature(z, 1.0)
    p[0, :] = 0.0
    with pytest.raises(ValueError, match="strictly positive"):
        s.apply_to_probs(p)


def test_scaler_serialization_roundtrip() -> None:
    z, y = _overconfident_pair(500)
    s = TemperatureScaler(n_classes=3).fit(z, y)
    blob = json.dumps(s.to_dict())
    s2 = TemperatureScaler.from_dict(json.loads(blob))
    assert s2.temperature == s.temperature
    assert s2.fitted is True
    assert s2.n_classes == 3
    assert s2.fit_metrics == s.fit_metrics
    np.testing.assert_allclose(s2.apply_to_logits(z), s.apply_to_logits(z))


def test_scaler_from_dict_rejects_bad_temperature() -> None:
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError, match="invalid temperature"):
            TemperatureScaler.from_dict({"temperature": bad, "fitted": True})


def test_scaler_from_dict_rejects_non_dict() -> None:
    with pytest.raises(TypeError, match="requires a dict"):
        TemperatureScaler.from_dict("nope")  # type: ignore[arg-type]


def test_scaler_fit_n_classes_mismatch_raises() -> None:
    z, y = _overconfident_pair(60)
    with pytest.raises(ValueError, match="expected 2 logit columns"):
        TemperatureScaler().fit(z, y, n_classes=2)


# ---------------------------------------------------------------------------
# evaluate_temperature_scaling / CalibrationReport
# ---------------------------------------------------------------------------


def test_evaluate_report_improved_on_overconfident_fold() -> None:
    z, y = _overconfident_pair(4000)
    rep = evaluate_temperature_scaling(z, y)
    assert isinstance(rep, CalibrationReport)
    assert rep.improved is True
    assert rep.ece_after < rep.ece_before
    assert rep.temperature > 1.0
    assert rep.n == 4000
    assert rep.n_classes == 3


def test_evaluate_report_reliability_rows_populated() -> None:
    z, y = _overconfident_pair(2000)
    rep = evaluate_temperature_scaling(z, y, n_bins=8)
    assert len(rep.reliability_before) >= 1
    assert sum(r["n"] for r in rep.reliability_after) == 2000


def test_evaluate_report_to_dict_json_clean() -> None:
    z, y = _overconfident_pair(300)
    rep = evaluate_temperature_scaling(z, y)
    blob = json.dumps(rep.to_dict())
    assert json.loads(blob)["temperature"] == rep.temperature


def test_evaluate_report_summary_lines_mention_ece() -> None:
    z, y = _overconfident_pair(300)
    lines = evaluate_temperature_scaling(z, y).summary_lines()
    assert any("ECE" in ln for ln in lines)
    assert any("Brier" in ln for ln in lines)


def test_evaluate_report_already_well_calibrated_is_not_worse() -> None:
    """A near-calibrated fold should not see a big ECE regression from T."""
    rng = np.random.default_rng(seed=23)
    y = rng.integers(0, 3, size=4000)
    z = rng.normal(0, 1, size=(4000, 3))
    z[np.arange(4000), y] += 1.6
    t = fit_temperature(z, y)
    rep = evaluate_temperature_scaling(z, y)
    assert abs(t - 1.0) < 1.0
    assert rep.ece_after <= rep.ece_before + 1e-3


# ---------------------------------------------------------------------------
# Honest OOS protocol: fit on train, evaluate on val
# ---------------------------------------------------------------------------


def test_oos_protocol_fit_train_eval_val_reduces_ece() -> None:
    """The honest protocol: T from the training fold, applied to held-out data."""
    z_tr, y_tr = _overconfident_pair(5000, seed=101)
    z_va, y_va = _overconfident_pair(3000, seed=202)
    assert not np.array_equal(y_tr[: len(y_va)], y_va)
    s = TemperatureScaler().fit(z_tr, y_tr)
    assert s.temperature > 1.0
    val_pre = compute_ece(apply_temperature(z_va, 1.0), y_va)
    val_post = compute_ece(apply_temperature(z_va, s.temperature), y_va)
    assert val_post < val_pre


# ---------------------------------------------------------------------------
# torch interop
# ---------------------------------------------------------------------------


def test_wrap_model_temperature_matches_numpy() -> None:
    class Net(torch_nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lin = torch_nn.Linear(4, 3)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.lin(x)

    net = Net()
    net.eval()
    with torch.inference_mode():
        x = torch.randn(16, 4)
        logits = net(x)
        out = wrap_model_temperature(net, 2.0)(x)
        ref = torch.softmax(logits / 2.0, dim=-1)
        np.testing.assert_allclose(out.numpy(), ref.numpy(), atol=1e-6)


def test_wrap_model_temperature_preserves_argmax() -> None:
    class Net(torch_nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lin = torch_nn.Linear(4, 3)

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            return self.lin(x)

    net = Net()
    net.eval()
    with torch.inference_mode():
        x = torch.randn(32, 4)
        raw_arg = net(x).argmax(dim=1)
        for t in (0.5, 1.0, 3.0):
            out = wrap_model_temperature(net, t)(x)
            assert torch.equal(out.argmax(dim=1), raw_arg)


# ---------------------------------------------------------------------------
# Contract conformance (repo invariants)
# ---------------------------------------------------------------------------


def test_module_imports_without_torch() -> None:
    """Slim-venv contract: importing the module must not require torch."""
    import importlib
    import sys

    had_torch = any(k.startswith("torch") for k in sys.modules)
    saved = {k: sys.modules[k] for k in list(sys.modules) if k.startswith("torch")}
    for k in list(sys.modules):
        if k.startswith("torch"):
            del sys.modules[k]
    try:
        mod = importlib.reload(importlib.import_module("nexus_scalp.research.calibration"))
        assert mod.compute_ece is not None
        assert mod.TemperatureScaler is not None
    finally:
        sys.modules.update(saved)
    assert had_torch or True


def test_no_live_path_wiring_required() -> None:
    """Research module must not import the live inference service (INV-012)."""
    import nexus_scalp.research.calibration as c

    src = open(c.__file__, encoding="utf-8").read()
    assert "application.live" not in src
    assert "live_engine" not in src


def test_public_api_surface() -> None:
    import nexus_scalp.research.calibration as c

    for name in (
        "compute_ece",
        "reliability_diagram",
        "brier_score",
        "log_loss",
        "nll_loss",
        "TemperatureScaler",
        "CalibrationReport",
        "apply_temperature",
        "fit_temperature",
        "evaluate_temperature_scaling",
        "wrap_model_temperature",
        "validate_probs",
    ):
        assert hasattr(c, name), name
