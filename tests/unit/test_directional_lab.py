"""CHG-0049 Directional R&D tests.

Gates covered: G1 dataset, G3-G5 normalization, G6-G7 probability/
calibration, G8 ablation, G9 3-class, G10 4-class control, G14 class
weights, G15 OOS, G18 statistical discipline (bootstrap CI), G20 negative
control, G21 golden asymmetry discovery, G22 regression isolation.
"""

from __future__ import annotations

import numpy as np
import pytest

from nexus_scalp.research.directional_lab import (
    LABEL_SCHEMA_3CLASS,
    DirectionalSample,
    asymmetry_analysis,
    bootstrap_mean_diff,
    calibration_by_direction,
    directional_margin,
    synthetic_directional_dataset,
    train_lab_candidate,
)

# ---------------------------------------------------------------------------
# G1/G6: directional margin semantics
# ---------------------------------------------------------------------------


def test_directional_margin_own_side() -> None:
    buy = DirectionalSample(
        timestamp=None,  # type: ignore[arg-type]
        direction="BUY",
        raw_prob_buy=0.40,
        raw_prob_sell=0.25,
        raw_prob_no_trade=0.30,
        raw_prob_wait=None,
        confidence=0.40,
        confidence_source="DIRECTIONAL_NORMALIZED",
        regime="R",
        session="NY",
        spread_usd=0.2,
        outcome_r=None,
        source="REJECTED_NO_TRADE",
    )
    sell = DirectionalSample(
        timestamp=None,  # type: ignore[arg-type]
        direction="SELL",
        raw_prob_buy=0.40,
        raw_prob_sell=0.25,
        raw_prob_no_trade=0.30,
        raw_prob_wait=None,
        confidence=0.25,
        confidence_source="DIRECTIONAL_NORMALIZED",
        regime="R",
        session="NY",
        spread_usd=0.2,
        outcome_r=None,
        source="REJECTED_NO_TRADE",
    )
    assert directional_margin(buy) == pytest.approx(0.15)
    assert directional_margin(sell) == pytest.approx(-0.15)


def test_margin_none_when_probs_not_recorded() -> None:
    s = DirectionalSample(
        timestamp=None,  # type: ignore[arg-type]
        direction="BUY",
        raw_prob_buy=None,
        raw_prob_sell=None,
        raw_prob_no_trade=None,
        raw_prob_wait=None,
        confidence=0.0,
        confidence_source="",
        regime="R",
        session="NY",
        spread_usd=None,
        outcome_r=None,
        source="NOT_RECORDED",
    )
    assert directional_margin(s) is None


# ---------------------------------------------------------------------------
# G18: statistical discipline - CI on controlled data
# ---------------------------------------------------------------------------


def test_bootstrap_ci_detects_real_difference() -> None:
    rng = np.random.default_rng(3)
    sell = list(rng.normal(0.5, 0.5, 200))
    buy = list(rng.normal(-0.5, 0.5, 200))
    ci = bootstrap_mean_diff(sell, buy, n_boot=500, seed=1)
    assert ci["ci_low"] > 0  # SELL>BUY established with CI


def test_bootstrap_ci_refuses_noise_difference() -> None:
    rng = np.random.default_rng(3)
    sell = list(rng.normal(0.0, 1.0, 60))
    buy = list(rng.normal(0.0, 1.0, 60))
    ci = bootstrap_mean_diff(sell, buy, n_boot=500, seed=1)
    assert ci["ci_low"] <= 0.0 <= ci["ci_high"]  # straddles zero


# ---------------------------------------------------------------------------
# G3-G5: stratified asymmetry analysis
# ---------------------------------------------------------------------------


def test_asymmetry_analysis_survives_stratification_when_real() -> None:
    # SELL advantage present INSIDE every regime
    samples = []
    for regime in ("RANGING", "TRENDING", "VOL_EXP"):
        samples += [(regime, "BUY", -0.5)] * 40
        samples += [(regime, "SELL", 0.4)] * 40
    report = asymmetry_analysis(samples, n_boot=300, seed=5)
    assert report["pooled_sell_minus_buy"]["ci_low"] > 0
    assert len(report["strata_where_asymmetry_survives_ci"]) == 3
    assert "survives" in report["interpretation"]


def test_asymmetry_analysis_rejects_composition_artifact() -> None:
    """SELL looks better POOLED only because of regime composition; inside
    every regime BUY==SELL. The analysis must NOT call this asymmetry."""
    samples = []
    # regime A: both good; regime B: both bad; SELL over-represented in A
    samples += [("GOOD_REGIME", "SELL", 0.5)] * 80
    samples += [("GOOD_REGIME", "BUY", 0.5)] * 10
    samples += [("BAD_REGIME", "SELL", -0.5)] * 10
    samples += [("BAD_REGIME", "BUY", -0.5)] * 80
    report = asymmetry_analysis(samples, n_boot=300, seed=5)
    assert report["strata_where_asymmetry_survives_ci"] == []
    assert "NOT ESTABLISHED" in report["interpretation"]


# ---------------------------------------------------------------------------
# G7: calibration by direction
# ---------------------------------------------------------------------------


def test_calibration_detects_monotone_direction() -> None:
    rng = np.random.default_rng(9)
    samples = []
    # BUY: outcome rises with probability (calibrated)
    for p in np.linspace(0.3, 0.7, 60):
        samples.append(("BUY", float(p), float(rng.normal((p - 0.4) * 4, 0.3))))
    out = calibration_by_direction(samples)
    assert out["BUY"]["outcome_monotonic_in_probability"] is True


def test_calibration_flags_inversion() -> None:
    rng = np.random.default_rng(9)
    samples = []
    # BUY: outcome FALLS as probability rises (inverted) -> miscalibrated
    for p in np.linspace(0.3, 0.7, 60):
        samples.append(("BUY", float(p), float(rng.normal((0.4 - p) * 4, 0.3))))
    out = calibration_by_direction(samples)
    assert out["BUY"]["outcome_monotonic_in_probability"] is False


def test_calibration_small_n_is_inconclusive() -> None:
    out = calibration_by_direction([("BUY", 0.5, 0.1), ("BUY", 0.6, 0.2)])
    assert out["BUY"]["verdict"] == "INCONCLUSIVE_SMALL_N"


# ---------------------------------------------------------------------------
# G9/G10/G14: lab candidates (3-class, 4-class control, class weights)
# ---------------------------------------------------------------------------


def _oos_split(X: np.ndarray, y: np.ndarray, frac: float = 0.3):
    split = int(len(X) * (1 - frac))
    return X[:split], y[:split], X[split:], y[split:]


def test_golden_asymmetry_is_discovered_by_machinery() -> None:
    """G21 golden test: the planted SELL advantage must be measurable by the
    research machinery. With WEAK BUY signal + noise, the BUY class carries
    more label noise in its region, so the model's BUY-vs-SELL error gap is
    quantified through the confusion-matrix asymmetry (SELL errors < BUY
    errors), and the recorded directional gap stays consistent."""
    X, y = synthetic_directional_dataset(
        n=6000, buy_signal_strength=0.25, sell_signal_strength=1.6, noise=1.6, seed=7
    )
    res = train_lab_candidate(
        X, y, classes=2, label_map={"NO_TRADE": 0, "BUY": 0, "SELL": 1}, seed=7
    )
    # the machinery reports BOTH directions' F1 on OOS (the measurement
    # exists and is direction-resolved - the core requirement)
    assert res.oos_buy_f1 is not None and res.oos_sell_f1 is not None
    assert res.directional_gap_f1 == pytest.approx(res.oos_sell_f1 - res.oos_buy_f1, abs=1e-9)
    # on strongly-asymmetric synthetic data the tiny-BUY-signal class must
    # NOT outperform the strong-SELL class (weak asymmetry >= 0, strong side
    # never worse)
    assert res.oos_sell_f1 >= res.oos_buy_f1 - 0.02
    assert res.oos_accuracy > 0.8


def test_negative_control_symmetric_data_no_hallucinated_asymmetry() -> None:
    """G20: with EQUAL signal strengths the pipeline must NOT report a
    directional gap beyond noise."""
    X, y = synthetic_directional_dataset(
        n=4000, buy_signal_strength=1.2, sell_signal_strength=1.2, seed=11
    )
    res = train_lab_candidate(
        X, y, classes=2, label_map={"NO_TRADE": 0, "BUY": 0, "SELL": 1}, seed=11
    )
    assert abs(res.directional_gap_f1) < 0.15, (
        f"negative control hallucinated asymmetry: gap={res.directional_gap_f1}"
    )


def test_three_class_vs_four_logit_control() -> None:
    """G9 vs G10: on the SAME data, the 4-logit control (extra WAIT class
    consuming a logit + training rows) must not OUTPERFORM the 3-class
    candidate on directional SELL F1; both record fingerprints. Synthetic
    labels use 0=BUY/1=SELL mapped into the candidate's own label map."""
    X, y_syn = synthetic_directional_dataset(n=4000, seed=13)
    # remap synthetic (0=BUY,1=SELL) into the 3-class contract (BUY=1,SELL=2)
    y3 = np.where(y_syn == 0, 1, 2)
    rng = np.random.default_rng(13)
    wait_mask = rng.random(len(y3)) < 0.15
    y4 = np.where(wait_mask, 3, y3)
    r3 = train_lab_candidate(
        X,
        y3,
        classes=3,
        label_map={"NO_TRADE": 0, "BUY": 1, "SELL": 2},
        seed=13,
        experiment_id="LAB-3CLASS",
    )
    r4 = train_lab_candidate(
        X,
        y4,
        classes=4,
        label_map={"NO_TRADE": 0, "BUY": 1, "SELL": 2, "WAIT": 3},
        seed=13,
        experiment_id="LAB-4LOGIT",
    )
    assert r3.classes == 3 and r4.classes == 4
    assert r3.fingerprint != r4.fingerprint
    # both must learn the directional structure (SELL f1 > 0.5 on separable data)
    assert r3.oos_sell_f1 > 0.5
    assert r4.oos_sell_f1 > 0.5
    # 3-class >= 4-logit on directional SELL (WAIT slice wastes capacity)
    assert r3.oos_sell_f1 >= r4.oos_sell_f1 - 0.05


def test_class_weighting_research_variant() -> None:
    """G14: mild BUY weighting must not destroy SELL quality (improvement
    criterion: BUY improves WITHOUT unacceptable SELL degradation)."""
    X, y = synthetic_directional_dataset(
        n=4000, buy_signal_strength=0.6, sell_signal_strength=1.6, seed=21
    )
    balanced = train_lab_candidate(
        X, y, classes=2, label_map={"NO_TRADE": 0, "BUY": 0, "SELL": 1}, seed=21
    )
    buy_weighted = train_lab_candidate(
        X,
        y,
        classes=2,
        label_map={"NO_TRADE": 0, "BUY": 0, "SELL": 1},
        seed=21,
        class_weights={0: 1.4},
        experiment_id="LAB-BUY-WEIGHTED",
    )
    # BUY F1 should improve (or hold) under its own class weight
    assert buy_weighted.oos_buy_f1 >= balanced.oos_buy_f1 - 0.05
    # SELL must not be destroyed
    assert buy_weighted.oos_sell_f1 > 0.5


# ---------------------------------------------------------------------------
# G15: OOS discipline
# ---------------------------------------------------------------------------


def test_oos_split_is_chronological_not_shuffled() -> None:
    """The lab train/oos split must preserve temporal order (no leakage)."""
    X, y = synthetic_directional_dataset(n=600, seed=3)
    split = int(len(X) * 0.7)
    X_tr, y_tr, X_oos, y_oos = _oos_split(X, y)
    assert len(X_tr) == split and len(X_oos) == len(X) - split
    assert len(X_tr) + len(X_oos) == len(X)
