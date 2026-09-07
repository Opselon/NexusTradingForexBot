"""Calibrated confidence -> risk sizing tests (P0 phase 3).

Invariants under test:

* Platt fit is deterministic and maps monotone confidences to monotone
  calibrated probabilities.
* Calibration quality is MEASURED (ECE + Brier) on a validation slice.
* INSUFFICIENT_DATA (small samples) raises — never reports OPTIMIZED.
* NOT_CALIBRATED / DEGRADED states fall back to flat sizing (1.0x), never
  to raw-softmax scaling.
* The risk multiplier is bounded in [MIN, 1.0] and NEVER exceeds 1.0:
  confidence de-risks, it cannot lever money-at-risk.
* The risk engine applies the multiplier through the calibrated path; with
  no calibration artifact the multiplier is exactly 1.0 (flat sizing).
* Persisted artifacts carry complete provenance; incomplete provenance =>
  NOT_CALIBRATED on load.
"""

from __future__ import annotations

import math

import pytest

from nexus_scalp.model_lifecycle.confidence_calibration import (
    MIN_CALIBRATION_SAMPLES,
    CalibrationProvenance,
    ConfidenceCalibrator,
    build_calibration_artifact,
    confidence_to_risk_multiplier,
    evaluate_calibration,
    fit_platt_calibration,
    persist_calibration_artifact,
)
from nexus_scalp.risk.risk_engine import RiskEngine


# --------------------------------------------------------------------------
# Platt fit
# --------------------------------------------------------------------------


def _sample_data(n: int = 200, seed: int = 7) -> tuple[list[float], list[int]]:
    """Deterministic synthetic confidences with a KNOWN monotone win bias."""
    import random

    rng = random.Random(seed)
    confs, outs = [], []
    for _ in range(n):
        c = rng.uniform(0.2, 0.9)
        # true win prob = c (perfectly calibrated synthetic ground truth),
        # sampled -> Platt should recover identity-ish mapping
        confs.append(c)
        outs.append(1 if rng.random() < c else 0)
    return confs, outs


def test_platt_fit_is_deterministic_and_monotone() -> None:
    confs, outs = _sample_data()
    p1 = fit_platt_calibration(confs, outs)
    p2 = fit_platt_calibration(confs, outs)
    assert p1 == p2, "same data must produce identical params (reproducibility)"

    cal = ConfidenceCalibrator(
        params=p1,
        provenance=_complete_provenance(sample_count=len(confs)),
    )
    vals = [cal.calibrate(c)[0] for c in [0.2, 0.4, 0.6, 0.8]]
    assert vals == sorted(vals), "calibrated output must be monotone in confidence"
    assert all(0.0 <= v <= 1.0 for v in vals)


def test_platt_fit_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        fit_platt_calibration([0.5, 0.6], [1])
    with pytest.raises(ValueError):
        fit_platt_calibration([], [])
    with pytest.raises(ValueError):
        fit_platt_calibration([0.5], [2])  # non-binary outcome


def _complete_provenance(sample_count: int = MIN_CALIBRATION_SAMPLES) -> CalibrationProvenance:
    return CalibrationProvenance(
        model_version="test-model",
        calibration_dataset_id="ds_cal",
        calibration_period_start="2026-01-01T00:00:00+00:00",
        calibration_period_end="2026-02-01T00:00:00+00:00",
        validation_dataset_id="ds_val",
        validation_period_start="2026-02-01T00:00:00+00:00",
        validation_period_end="2026-03-01T00:00:00+00:00",
        method="platt_logistic",
        created_at="2026-03-01T00:00:00+00:00",
        feature_schema_version="scalp_v3",
        sample_count=sample_count,
        validation_sample_count=MIN_CALIBRATION_SAMPLES,
    )


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_ece_brier_measured() -> None:
    metrics = evaluate_calibration([0.9, 0.9, 0.1, 0.1], [1, 1, 0, 0])
    assert metrics["n"] == 4
    # each bin's |mean_conf - win_rate| = 0 -> ECE 0 up to fp noise
    assert metrics["ece"] <= 0.11  # 0.1-bin boundary quantization allowance
    assert metrics["brier"] < 0.05
    bad = evaluate_calibration([0.9, 0.9], [0, 0])
    assert bad["ece"] > 0.5  # confidently wrong


def test_evaluate_handles_nonfinite() -> None:
    metrics = evaluate_calibration([float("nan"), 0.5], [1, 0])
    assert metrics["n"] == 1


# --------------------------------------------------------------------------
# Build artifact: calibration/validation split + provenance
# --------------------------------------------------------------------------


def test_build_artifact_requires_sufficient_samples() -> None:
    confs, outs = _sample_data(50)
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        build_calibration_artifact(
            model_version="m",
            feature_schema_version="scalp_v3",
            cal_confidences=confs[:10],
            cal_outcomes=outs[:10],
            val_confidences=confs,
            val_outcomes=outs,
            calibration_dataset_id="a",
            validation_dataset_id="b",
            cal_start="t1", cal_end="t2", val_start="t3", val_end="t4",
        )


def test_build_artifact_calibrates_and_measures() -> None:
    confs, outs = _sample_data(120, seed=11)
    val_confs, val_outs = _sample_data(80, seed=13)
    art = build_calibration_artifact(
        model_version="m1",
        feature_schema_version="scalp_v3",
        cal_confidences=confs,
        cal_outcomes=outs,
        val_confidences=val_confs,
        val_outcomes=val_outs,
        calibration_dataset_id="ds_cal",
        validation_dataset_id="ds_val",
        cal_start="2026-01-01T00:00:00+00:00",
        cal_end="2026-02-01T00:00:00+00:00",
        val_start="2026-02-01T00:00:00+00:00",
        val_end="2026-03-01T00:00:00+00:00",
    )
    assert set(art["params"]) == {"a", "b"}
    assert math.isfinite(art["params"]["a"]) and math.isfinite(art["params"]["b"])
    assert art["provenance"]["sample_count"] == 120
    assert art["validation_metrics"]["calibrated"]["n"] == 80
    assert art["validation_metrics"]["ece"] <= 1.0


# --------------------------------------------------------------------------
# Runtime states and bounded risk multiplier
# --------------------------------------------------------------------------


def test_missing_artifact_is_not_calibrated(tmp_path) -> None:
    cal = ConfidenceCalibrator.from_artifact(tmp_path / "missing.json")
    assert cal.state == "NOT_CALIBRATED"
    val, state = cal.calibrate(0.77)
    assert state == "NOT_CALIBRATED"
    assert val == 0.77  # passthrough, never manufactured


def test_incomplete_provenance_is_not_calibrated() -> None:
    prov = CalibrationProvenance(
        model_version="",  # missing model identity
        calibration_dataset_id="ds",
        calibration_period_start="s", calibration_period_end="e",
        validation_dataset_id="ds2",
        validation_period_start="s", validation_period_end="e",
        method="platt_logistic", created_at="now",
        feature_schema_version="scalp_v3",
        sample_count=10,  # below floor
        validation_sample_count=10,
    )
    cal = ConfidenceCalibrator(params={"a": 1.0, "b": 0.0}, provenance=prov)
    assert cal.state == "NOT_CALIBRATED"


def test_invalid_calibration_falls_back_flat() -> None:
    cal = ConfidenceCalibrator()  # nothing loaded
    assert confidence_to_risk_multiplier(0.95, "NOT_CALIBRATED") == 1.0
    assert confidence_to_risk_multiplier(0.30, "DEGRADED") == 1.0


def test_risk_multiplier_bounded_never_levers() -> None:
    cal = ConfidenceCalibrator(
        params={"a": 4.0, "b": -2.0},
        provenance=_complete_provenance(),
        validation_metrics={"ece": 0.05},
    )
    assert cal.state == "CALIBRATED"
    for raw in [0.0, 0.25, 0.5, 0.75, 1.0]:
        calibrated, state = cal.calibrate(raw)
        mult = confidence_to_risk_multiplier(calibrated, state)
        assert 0.25 <= mult <= 1.0, f"multiplier {mult} outside bounded range"
        assert mult <= 1.0  # NEVER above flat sizing


def test_degraded_ece_reports_degraded_state() -> None:
    cal = ConfidenceCalibrator(
        params={"a": 4.0, "b": -2.0},
        provenance=_complete_provenance(),
        validation_metrics={"ece": 0.45},  # above MAX_USABLE_ECE
    )
    assert cal.state == "DEGRADED"
    assert confidence_to_risk_multiplier(0.9, cal.state) == 1.0


# --------------------------------------------------------------------------
# Persisted artifact round-trip
# --------------------------------------------------------------------------


def test_persist_and_load_round_trip(tmp_path) -> None:
    path = tmp_path / "cal" / "confidence_calibration.json"
    params = {"a": 3.0, "b": -1.5}
    prov = _complete_provenance(sample_count=60)
    assert persist_calibration_artifact(path, params, prov, {"ece": 0.08, "brier": 0.2})
    cal = ConfidenceCalibrator.from_artifact(path)
    assert cal.state in ("CALIBRATED", "DEGRADED")
    val, state = cal.calibrate(0.5)
    assert 0.0 <= val <= 1.0 and state == cal.state


# --------------------------------------------------------------------------
# Risk engine integration (no artifact wired -> exactly flat sizing)
# --------------------------------------------------------------------------


def _risk_engine() -> RiskEngine:
    from nexus_scalp.configuration.config import RiskConfig

    return RiskEngine(config=RiskConfig())


def test_risk_engine_without_calibration_uses_flat_sizing(monkeypatch) -> None:
    """With NO calibration artifact the confidence multiplier must be exactly
    1.0 — the old raw-softmax lever (up to 1.2x) is gone."""
    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import (
        AccountInfo,
        Position,
        SymbolInfo,
        TickData,
        TradeProposal,
    )
    from datetime import UTC, datetime

    engine = _risk_engine()
    monkeypatch.setattr(engine, "_confidence_calibrator", ConfidenceCalibrator(), raising=False)

    account = AccountInfo(
        login=1, trade_mode=0, balance=10000.0, equity=10000.0, margin=0.0,
        margin_free=10000.0,
        leverage=100,
    )
    symbol_info = SymbolInfo(
        symbol="XAUUSD", point=0.01, digits=2, trade_contract_size=100.0,
        tick_value=0.1, tick_size=0.01, volume_min=0.01, volume_max=10.0, volume_step=0.01,
        stops_level=0, freeze_level=0,
    )
    tick = TickData(
        symbol="XAUUSD", timestamp=datetime.now(UTC), bid=2000.0, ask=2000.20, volume=1.0,
    )
    proposal = TradeProposal(
        request_id="r-test",
        symbol="XAUUSD",
        generated_at=datetime.now(UTC),
        action=ActionType.BUY_MARKET,
        confidence=0.95,  # would have been levered 1.117x under the old raw path
        proposed_entry=2000.20,
        stop_loss=1998.60,
        take_profit=2004.60,
        risk_reward_ratio=2.75,
    )
    order = engine.evaluate_proposal(
        proposal=proposal,
        account=account,
        symbol_info=symbol_info,
        active_positions=[],
        current_tick=tick,
        atr=1.5,
    )
    assert order is not None
    # flat sizing: risk_amount == equity * risk_pct exactly
    expected_risk_usd = 10000.0 * (engine.config.risk_per_trade_pct / 100.0)
    sl_distance = abs(2000.20 - 1998.60)
    expected_raw_lots = expected_risk_usd / (sl_distance * 100.0)
    assert abs(order.volume - expected_raw_lots) < 0.02
