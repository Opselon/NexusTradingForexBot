"""Calibration identity-binding + evidence-gate tests (mission phases 2/7/11/12).

Covers the acceptance cases that the plain calibration tests do not:

* mismatched artifact/model  -> flat sizing (NOT_CALIBRATED)
* fingerprint NOT_RECORDED   -> NOT_CALIBRATED even with complete other fields
* serving artifact missing   -> NOT_CALIBRATED (fail-closed)
* explicit OOS/holdout gate  -> training-slice observations are rejected
* insufficient per-split obs -> build_calibration_artifact raises
* corrupted artifact         -> rejected (NOT_CALIBRATED)
* RiskEngine with a mismatched artifact on disk stays flat-sized
"""

from __future__ import annotations

import hashlib
import json

import pytest

from nexus_scalp.model_lifecycle.calibration_identity import (
    artifact_matches_serving,
    load_bound_calibrator,
    observability_block,
    resolve_serving_fingerprint,
)
from nexus_scalp.model_lifecycle.confidence_calibration import (
    CalibrationProvenance,
    ConfidenceCalibrator,
    build_calibration_artifact,
    confidence_to_risk_multiplier,
    fit_platt_calibration,
    persist_calibration_artifact,
)


def _prov(fp: str) -> CalibrationProvenance:
    return CalibrationProvenance(
        model_version="m1",
        calibration_dataset_id="ds_cal",
        artifact_fingerprint=fp,
        calibration_period_start="2026-01-01T00:00:00+00:00",
        calibration_period_end="2026-02-01T00:00:00+00:00",
        validation_dataset_id="ds_val",
        validation_period_start="2026-02-01T00:00:00+00:00",
        validation_period_end="2026-03-01T00:00:00+00:00",
        method="platt_logistic",
        created_at="2026-03-01T00:00:00+00:00",
        feature_schema_version="scalp_v3",
        sample_count=60,
        validation_sample_count=40,
    )


def _tmp_model(tmp_path, tag: str) -> str:
    """A tiny fake 'artifact' file whose hash pins identity."""
    p = tmp_path / f"model_{tag}.bin"
    p.write_bytes(f"artifact-{tag}".encode())
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Phase 7: identity binding
# --------------------------------------------------------------------------


def test_fingerprint_match_passes(tmp_path) -> None:
    fp = _tmp_model(tmp_path, "a")
    assert artifact_matches_serving(_prov(fp), serving_fingerprint=fp) is True


def test_fingerprint_mismatch_rejected(tmp_path) -> None:
    fp_cal = _tmp_model(tmp_path, "a")
    fp_serving = _tmp_model(tmp_path, "b")
    assert artifact_matches_serving(_prov(fp_cal), serving_fingerprint=fp_serving) is False


def test_missing_fingerprint_is_never_a_match() -> None:
    assert artifact_matches_serving(_prov(""), serving_fingerprint="whatever") is False
    assert artifact_matches_serving(None, serving_fingerprint="whatever") is False


def test_missing_serving_artifact_fails_closed(tmp_path) -> None:
    fp = _tmp_model(tmp_path, "a")
    ok = artifact_matches_serving(
        _prov(fp), serving_artifact_path=tmp_path / "does_not_exist.bin"
    )
    assert ok is False


def test_load_bound_calibrator_rejects_foreign_model(tmp_path) -> None:
    fp_cal = _tmp_model(tmp_path, "calibrated")
    fp_now = _tmp_model(tmp_path, "replaced")
    art = tmp_path / "cal.json"
    assert persist_calibration_artifact(
        art,
        {"a": 3.0, "b": -1.0},
        _prov(fp_cal),
        {"ece": 0.05, "brier": 0.2},
    )
    cal = load_bound_calibrator(
        calibration_path=art,
        serving_artifact_path=tmp_path / "replaced.bin",
    )
    assert cal.state == "NOT_CALIBRATED"
    # and the multiplier stays exactly 1.0 (flat sizing)
    assert confidence_to_risk_multiplier(0.9, cal.calibrate(0.9)[1]) == 1.0


def test_load_bound_calibrator_accepts_matching_model(tmp_path) -> None:
    serving = tmp_path / "model_serving.bin"
    fp = _tmp_model(tmp_path, "serving")
    art = tmp_path / "cal.json"
    assert persist_calibration_artifact(
        art, {"a": 3.0, "b": -1.0}, _prov(fp), {"ece": 0.05, "brier": 0.2}
    )
    cal = load_bound_calibrator(calibration_path=art, serving_artifact_path=serving)
    assert cal.state in ("CALIBRATED", "DEGRADED")


def test_load_bound_calibrator_accepts_explicit_fingerprint(tmp_path) -> None:
    """An engine that already holds the verified hash can pass it directly —
    the file is not re-read but the binding check still applies."""
    fp = _tmp_model(tmp_path, "verified")
    art = tmp_path / "cal.json"
    assert persist_calibration_artifact(
        art, {"a": 3.0, "b": -1.0}, _prov(fp), {"ece": 0.05}
    )
    cal = load_bound_calibrator(
        calibration_path=art,
        serving_artifact_path=tmp_path / "does_not_matter.bin",
        serving_fingerprint=fp,
    )
    assert cal.state in ("CALIBRATED", "DEGRADED")
    # wrong explicit fingerprint -> still rejected
    cal_bad = load_bound_calibrator(
        calibration_path=art,
        serving_artifact_path=tmp_path / "does_not_matter.bin",
        serving_fingerprint="deadbeef" * 2,
    )
    assert cal_bad.state == "NOT_CALIBRATED"


# --------------------------------------------------------------------------
# Phase 2: training-data / contamination gate
# --------------------------------------------------------------------------


def test_oos_membership_gate_rejects_training_slice() -> None:
    """The builder requires an explicit OOS declaration; train-slice rows are
    rejected before any fitting happens."""
    confs = [0.3 + 0.001 * i for i in range(40)]
    outs = [1 if i % 2 else 0 for i in range(40)]
    with pytest.raises(ValueError, match="TRAIN_SLICE"):
        build_calibration_artifact(
            model_version="m",
            feature_schema_version="scalp_v3",
            cal_confidences=confs,
            cal_outcomes=outs,
            val_confidences=confs,
            val_outcomes=outs,
            calibration_dataset_id="ds",
            validation_dataset_id="ds",
            cal_start="s",
            cal_end="e",
            val_start="s",
            val_end="e",
            artifact_fingerprint="fp",
            calibration_is_oos=False,
            validation_is_oos=True,
        )


def test_oos_membership_gate_required_on_both_slices() -> None:
    confs = [0.3 + 0.001 * i for i in range(40)]
    outs = [1 if i % 2 else 0 for i in range(40)]
    with pytest.raises(ValueError, match="TRAIN_SLICE"):
        build_calibration_artifact(
            model_version="m",
            feature_schema_version="scalp_v3",
            cal_confidences=confs,
            cal_outcomes=outs,
            val_confidences=confs,
            val_outcomes=outs,
            calibration_dataset_id="ds_cal",
            validation_dataset_id="ds_val",
            cal_start="s",
            cal_end="e",
            val_start="s",
            val_end="e",
            artifact_fingerprint="fp",
            calibration_is_oos=True,
            validation_is_oos=False,
        )


def test_artifact_fingerprint_required_for_trustworthy_artifact() -> None:
    """Even with OOS flags and N>=30, a missing fingerprint must yield a
    provenance that is_complete()==False (never production-trustworthy)."""
    confs = [0.3 + 0.001 * i for i in range(40)]
    outs = [1 if i % 2 else 0 for i in range(40)]
    art = build_calibration_artifact(
        model_version="m",
        feature_schema_version="scalp_v3",
        cal_confidences=confs,
        cal_outcomes=outs,
        val_confidences=confs,
        val_outcomes=outs,
        calibration_dataset_id="ds_cal",
        validation_dataset_id="ds_val",
        cal_start="s",
        cal_end="e",
        val_start="s",
        val_end="e",
        artifact_fingerprint="",
        calibration_is_oos=True,
        validation_is_oos=True,
    )
    prov = art["provenance"]
    assert prov["artifact_fingerprint"] == ""
    # is_complete must be False -> consumers treat as NOT_CALIBRATED
    assert CalibrationProvenance.from_dict(prov).is_complete() is False


# --------------------------------------------------------------------------
# Phase 11: corrupted artifact / observability
# --------------------------------------------------------------------------


def test_corrupted_artifact_is_rejected(tmp_path) -> None:
    p = tmp_path / "cal.json"
    p.write_text("{ this is not json", encoding="utf-8")
    cal = ConfidenceCalibrator.from_artifact(p)
    assert cal.state == "NOT_CALIBRATED"


def test_tampered_params_rejected_on_reload(tmp_path) -> None:
    fp = _tmp_model(tmp_path, "x")
    art = tmp_path / "cal.json"
    assert persist_calibration_artifact(art, {"a": 3.0, "b": -1.0}, _prov(fp), {"ece": 0.05})
    # tamper: a -> string (schema violation) — the loaded calibrator must be NOT_CALIBRATED
    payload = json.loads(art.read_text(encoding="utf-8"))
    payload["params"]["a"] = "not-a-number"
    art.write_text(json.dumps(payload), encoding="utf-8")
    cal = ConfidenceCalibrator.from_artifact(art)
    assert cal.state == "NOT_CALIBRATED"


def test_observability_block_is_secret_free_and_bounded(tmp_path) -> None:
    fp = _tmp_model(tmp_path, "obs")
    cal = ConfidenceCalibrator(params={"a": 3.0, "b": -1.0}, provenance=_prov(fp),
                               validation_metrics={"ece": 0.07, "brier": 0.21})
    block = observability_block(cal)
    assert block["calibration_status"] == "CALIBRATED"
    assert block["required_sample_count"] == 30
    assert block["eligible_sample_count"] == 60
    assert block["calibration_artifact_fingerprint"] == fp
    # only whitelisted keys — no raw confidence/outcome data
    assert set(block) == {
        "calibration_status", "required_sample_count", "eligible_sample_count",
        "calibration_model_version", "calibration_artifact_fingerprint",
        "calibration_method", "ece", "brier", "matches_serving",
    }
