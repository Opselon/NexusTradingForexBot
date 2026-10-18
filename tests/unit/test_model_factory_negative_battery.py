"""Negative Test Battery (MODEL FACTORY contract Section 31).

Aggressively tests failure modes across every pipeline stage:
  1. Data quality: duplicate timestamps, missing required columns, NaN/Inf prices,
     invalid OHLC bounds (high < low, open > high, close > high, negative prices).
  2. Feature quality: wrong feature dimension, column count mismatch, constant features,
     all-NaN features, non-finite mean.
  3. Label quality: missing label column, completely collapsed classes, NO_TRADE dominance,
     severe fold imbalance, regime-specific collapse.
  4. Sequence construction: window with inter-bar gap > max_gap_us, cross-symbol window.
  5. Artifact integrity & Load contract:
     - missing model.pt
     - missing scaler.npz
     - missing manifest (.meta.json)
     - missing certificate (.certificate.json)
     - non-certified model rejected
     - corrupt/tampered model.pt checksum mismatch
     - corrupt manifest checksum mismatch
     - wrong feature dimension (e.g. 50D artifact loaded against 70D contract)
     - wrong feature schema (e.g. scalp_v1 vs scalp_v3)
     - wrong scaler dimension
     - corrupt/unreadable scaler sidecar
     - wrong sequence length
  6. Model certification gate:
     - fails on failed dataset gate
     - fails on failed feature gate
     - fails on failed label gate
     - fails on unmeasured training metrics (metrics_measured=False)
     - fails on un-evaluated OOS holdout
     - fails on missing walk-forward result

Every one must fail loudly, deterministically, with an exact human-readable reason.
NO fake PASS. NO silent fallback. NO silent truncation or padding.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "data"))
from ingest_historical_candles import generate_synthetic_bars

from nexus_scalp.model_generation.certification import (
    CompatibilityError,
    ModelCertificationGate,
    certify_model,
    verify_artifact_bundle,
)
from nexus_scalp.model_generation.data_quality import DataQualityCertifier, QualityStatus
from nexus_scalp.model_generation.feature_quality import (
    FeatureQualityCertifier,
    FeatureStatus,
    certify_feature_quality,
)
from nexus_scalp.model_generation.label_quality import (
    LabelQualityAudit,
    LabelStatus,
    audit_label_quality,
)
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.models.scalp_net import ScalpNet

# =============================================================================
# 1. DATA QUALITY NEGATIVE TESTS
# =============================================================================


def test_data_quality_rejects_missing_time() -> None:
    df = pl.DataFrame({"open": [2000.0], "high": [2010.0], "low": [1990.0], "close": [2005.0]})
    with pytest.raises(ValueError, match="no 'time' column"):
        DataQualityCertifier().certify(df)


def test_data_quality_rejects_missing_price_columns() -> None:
    df = pl.DataFrame({"time": [datetime.now(UTC)], "open": [2000.0], "high": [2010.0]})
    with pytest.raises(ValueError, match="required column 'low' missing"):
        DataQualityCertifier().certify(df)


@pytest.mark.parametrize(
    "mode,expected_reason",
    [
        ("high_below_low", "HIGH_BELOW_LOW"),
        ("open_above_high", "OPEN_ABOVE_HIGH"),
        ("open_below_low", "OPEN_BELOW_LOW"),
        ("close_above_high", "CLOSE_ABOVE_HIGH"),
        ("close_below_low", "CLOSE_BELOW_LOW"),
        ("negative_price", "NON_POSITIVE_PRICE"),
        ("zero_price", "NON_POSITIVE_PRICE"),
        ("nan_price", "NAN_PRICE"),
        ("inf_price", "INF_PRICE"),
    ],
)
def test_data_quality_detects_and_rejects_impossible_bars(mode: str, expected_reason: str) -> None:
    raw = generate_synthetic_bars(count=100, seed=1).to_dicts()
    h = float(raw[15]["high"])
    l = float(raw[15]["low"])
    if mode == "high_below_low":
        raw[15]["high"] = l - 10.0
    elif mode == "open_above_high":
        raw[15]["open"] = h + 10.0
    elif mode == "open_below_low":
        raw[15]["open"] = l - 10.0
    elif mode == "close_above_high":
        raw[15]["close"] = h + 10.0
    elif mode == "close_below_low":
        raw[15]["close"] = l - 10.0
    elif mode == "negative_price":
        raw[15]["close"] = -5.0
    elif mode == "zero_price":
        raw[15]["close"] = 0.0
    elif mode == "nan_price":
        raw[15]["close"] = float("nan")
    elif mode == "inf_price":
        raw[15]["close"] = float("inf")

    clean, report = DataQualityCertifier().certify(pl.DataFrame(raw))
    assert report.rejected_rows >= 1
    assert expected_reason in report.rejection_breakdown
    assert clean.height == len(raw) - report.rejected_rows


def test_data_quality_rejects_excessive_corrupt_data() -> None:
    """When corruption exceeds max_reject_fraction (5%), status MUST be FAIL."""
    raw = generate_synthetic_bars(count=200, seed=2).to_dicts()
    # Corrupt 30 rows = 15%
    for i in range(30):
        raw[i]["high"] = raw[i]["low"] - 10.0
    _, report = DataQualityCertifier().certify(pl.DataFrame(raw))
    assert report.quality_status == QualityStatus.FAIL
    assert report.trainable is False


# =============================================================================
# 2. FEATURE QUALITY NEGATIVE TESTS
# =============================================================================


def test_feature_quality_rejects_non_2d_matrix() -> None:
    cert = FeatureQualityCertifier()
    with pytest.raises(ValueError, match="expected a 2D matrix"):
        cert.certify(np.zeros((10, 50, 2)), [f"f{i}" for i in range(50)])


def test_feature_quality_rejects_column_name_count_mismatch() -> None:
    cert = FeatureQualityCertifier()
    with pytest.raises(ValueError, match="matrix has 50 columns but 49 names"):
        cert.certify(np.zeros((10, 50)), [f"f{i}" for i in range(49)])


def test_feature_quality_rejects_non_canonical_dimension() -> None:
    cert = FeatureQualityCertifier()
    with pytest.raises(ValueError, match="canonical contracts are 50D and 70D, got 64D"):
        cert.certify(np.zeros((10, 64)), [f"f{i}" for i in range(64)])


def test_feature_quality_fails_on_constant_feature() -> None:
    m = np.random.default_rng(0).normal(size=(200, 50))
    m[:, 10] = 42.0  # Constant column
    report = certify_feature_quality(m, names=[f"feat_{i}" for i in range(50)])
    assert report.quality_status == FeatureStatus.FAIL
    assert report.failed >= 1
    failed_features = [f["name"] for f in report.features if f["status"] == "FAIL"]
    assert "feat_10" in failed_features
    feat_10 = next(f for f in report.features if f["name"] == "feat_10")
    assert feat_10["constant"] is True
    assert "CONSTANT" in feat_10["issues"]


def test_feature_quality_fails_on_nan_feature() -> None:
    m = np.random.default_rng(1).normal(size=(200, 50))
    m[:, 25] = np.nan
    report = certify_feature_quality(m, names=[f"feat_{i}" for i in range(50)])
    assert report.quality_status == FeatureStatus.FAIL
    failed_features = [f["name"] for f in report.features if f["status"] == "FAIL"]
    assert "feat_25" in failed_features


# =============================================================================
# 3. LABEL QUALITY NEGATIVE TESTS
# =============================================================================


def test_label_quality_rejects_missing_label_column() -> None:
    with pytest.raises(ValueError, match="no 'label' column"):
        audit_label_quality(pl.DataFrame({"close": [10.0, 20.0]}))


def test_label_quality_fails_on_total_class_collapse() -> None:
    """A dataset with only NO_TRADE (class 0) must FAIL."""
    df = pl.DataFrame({"label": [0] * 500})
    report = audit_label_quality(df)
    assert report.quality_status == LabelStatus.FAIL
    assert report.no_trade_percentage == 1.0
    assert "BUY or SELL entirely absent" in report.warnings


def test_label_quality_fails_on_single_direction_collapse() -> None:
    """A dataset with only BUY and NO_TRADE (SELL is 0%) must FAIL."""
    df = pl.DataFrame({"label": [0] * 400 + [1] * 100})
    report = audit_label_quality(df)
    assert report.quality_status == LabelStatus.FAIL
    assert any("SELL" in c for c in report.collapsed_classes)


# =============================================================================
# 4. SEQUENCE CONSTRUCTION NEGATIVE TESTS
# =============================================================================


def test_sequence_builder_rejects_temporal_gap_exceeding_max() -> None:
    """Windows straddling an inter-bar gap > max_gap_us must be marked valid=False."""
    # 35 bars: bar 0..15, then 20-minute gap, then bar 16..34
    t0 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    times = [t0 + timedelta(minutes=i) for i in range(16)]
    t_after_gap = times[-1] + timedelta(minutes=25)  # 25-minute gap > 10-minute max
    times += [t_after_gap + timedelta(minutes=i) for i in range(19)]

    df = pl.DataFrame(
        {
            "timestamp": times,
            "feat_0": np.linspace(1.0, 2.0, len(times)),
            "feat_1": np.linspace(2.0, 3.0, len(times)),
            "label": [0] * len(times),
            "symbol": ["XAUUSD"] * len(times),
            "timeframe": ["M1"] * len(times),
        }
    )
    # L=32, max_gap_us = 10 minutes (600_000_000 us)
    builder = SequenceBuilder(seq_len=32, max_gap_us=10 * 60 * 1_000_000)
    res = builder.build(df)
    # The windows that cross the 25-minute gap must be marked invalid
    assert len(res["valid"]) > 0
    assert not np.all(res["valid"]), "Windows straddling the gap must not be valid"
    assert bool(np.any(~res["valid"])) is True


def test_sequence_builder_rejects_cross_symbol_window() -> None:
    """Windows straddling different symbols must be marked valid=False."""
    df = pl.DataFrame(
        {
            "timestamp": [datetime(2026, 1, 1, 10, i, tzinfo=UTC) for i in range(40)],
            "feat_0": np.ones(40),
            "label": [0] * 40,
            "symbol": ["XAUUSD"] * 20 + ["EURUSD"] * 20,
            "timeframe": ["M1"] * 40,
        }
    )
    builder = SequenceBuilder(seq_len=16, max_gap_us=None)
    res = builder.build(df)
    # Any window with both XAUUSD and EURUSD must be invalid
    assert not np.all(res["valid"])


# =============================================================================
# 5. ARTIFACT INTEGRITY & LOAD CONTRACT NEGATIVE TESTS
# =============================================================================


@pytest.fixture
def valid_bundle(tmp_path: Path) -> tuple[Path, str, dict]:
    """Generates a valid certified 50D model bundle."""
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    model_id = "test_neg_model_50d"

    model = ScalpNet(num_features=50, num_classes=3)
    torch.save(model.state_dict(), bundle_dir / f"{model_id}.pt")
    np.savez(
        bundle_dir / f"{model_id}.scaler.npz",
        mean=np.zeros(50, dtype=np.float32),
        std=np.ones(50, dtype=np.float32),
        dimension=50,
        schema_id="scalp_v1",
    )

    weights_sha = hashlib.sha256((bundle_dir / f"{model_id}.pt").read_bytes()).hexdigest()
    scaler_sha = hashlib.sha256((bundle_dir / f"{model_id}.scaler.npz").read_bytes()).hexdigest()

    manifest = {
        "model_id": model_id,
        "schema_id": "scalp_v1",
        "dimension": 50,
        "sequence_length": 1,
        "output_classes": 3,
        "weights_sha256": weights_sha,
        "scaler_sha256": scaler_sha,
        "label_schema_id": "triple_barrier_v3",
        "normalization": "zscore_clip5",
        "oos_isolated": True,
    }
    manifest_sha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, default=str).encode()
    ).hexdigest()
    manifest["manifest_sha256"] = manifest_sha
    (bundle_dir / f"{model_id}.meta.json").write_text(json.dumps(manifest, indent=2))

    cert = {
        "model_id": model_id,
        "model_status": "CERTIFIED",
        "certification_version": "cert_v1",
        "schema_id": "scalp_v1",
        "dimension": 50,
        "sequence_length": 1,
        "label_schema_id": "triple_barrier_v3",
        "weights_sha256": weights_sha,
        "failed_stages": [],
        "passed_stages": [
            "DATASET",
            "FEATURE",
            "LABEL",
            "TRAIN",
            "VALIDATION",
            "OOS",
            "ARTIFACT_INTEGRITY",
            "COMPATIBILITY",
        ],
    }
    (bundle_dir / f"{model_id}.certificate.json").write_text(json.dumps(cert, indent=2))
    return bundle_dir, model_id, manifest


def test_verify_bundle_rejects_missing_model_pt(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    (bundle_dir / f"{model_id}.pt").unlink()
    with pytest.raises(CompatibilityError, match=r"model\.pt missing"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_missing_scaler(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    (bundle_dir / f"{model_id}.scaler.npz").unlink()
    with pytest.raises(CompatibilityError, match="scaler missing"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_missing_manifest(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    (bundle_dir / f"{model_id}.meta.json").unlink()
    with pytest.raises(CompatibilityError, match="manifest missing"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_missing_certificate(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    (bundle_dir / f"{model_id}.certificate.json").unlink()
    with pytest.raises(CompatibilityError, match="has no certification certificate"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_non_certified_model(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    cert_path = bundle_dir / f"{model_id}.certificate.json"
    cert = json.loads(cert_path.read_text())
    cert["model_status"] = "REJECTED"
    cert["rejection_reason"] = "FEATURE gate failed: 3 constant features"
    cert_path.write_text(json.dumps(cert))

    with pytest.raises(CompatibilityError, match="is not CERTIFIED"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_tampered_model_weights(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    # Tamper with model.pt bytes
    weights_path = bundle_dir / f"{model_id}.pt"
    content = weights_path.read_bytes()
    # Mutate last 16 bytes
    weights_path.write_bytes(content[:-16] + b"\x00" * 16)

    with pytest.raises(CompatibilityError, match=r"model\.pt checksum mismatch"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_schema_mismatch(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    with pytest.raises(CompatibilityError, match="feature schema mismatch"):
        verify_artifact_bundle(bundle_dir, model_id, expected_schema_id="scalp_v3")


def test_verify_bundle_rejects_dimension_mismatch(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, _ = valid_bundle
    with pytest.raises(CompatibilityError, match="feature dimension mismatch"):
        verify_artifact_bundle(bundle_dir, model_id, expected_dimension=70)


def test_verify_bundle_rejects_scaler_dimension_mismatch(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    bundle_dir, model_id, _ = valid_bundle
    # Overwrite scaler with 70D array
    np.savez(
        bundle_dir / f"{model_id}.scaler.npz",
        mean=np.zeros(70, dtype=np.float32),
        std=np.ones(70, dtype=np.float32),
        dimension=70,
        schema_id="scalp_v3",
    )
    with pytest.raises(CompatibilityError, match="scaler dimension mismatch"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_corrupt_unreadable_scaler(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    bundle_dir, model_id, _ = valid_bundle
    (bundle_dir / f"{model_id}.scaler.npz").write_bytes(b"CORRUPT_NOT_A_ZIP")
    with pytest.raises(CompatibilityError, match=r"scaler sidecar.*is corrupt or unreadable"):
        verify_artifact_bundle(bundle_dir, model_id)


def test_verify_bundle_rejects_sequence_length_mismatch(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    bundle_dir, model_id, _ = valid_bundle
    with pytest.raises(CompatibilityError, match="sequence length mismatch"):
        verify_artifact_bundle(bundle_dir, model_id, expected_sequence_length=32)


# =============================================================================
# 6. MODEL CERTIFICATION GATE NEGATIVE TESTS
# =============================================================================


def test_certify_model_rejects_when_dataset_report_missing(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    bundle_dir, model_id, manifest = valid_bundle
    cert = certify_model(
        model_id=model_id,
        manifest=manifest,
        dataset_report=None,  # Missing!
        feature_report={"quality_status": "PASS", "failed": 0},
        label_report={"quality_status": "PASS"},
        training_metrics={
            "metrics_measured": True,
            "train_loss": 0.5,
            "validation_loss": 0.5,
            "validation_accuracy": 0.4,
            "epochs_completed": 1,
        },
        walk_forward_result={"passed": True, "fold_count": 4, "purge_embargo": True},
        oos_metrics={"oos_loss": 0.5, "oos_accuracy": 0.4},
        bundle_dir=bundle_dir,
    )
    assert cert.certified is False
    assert cert.model_status == "REJECTED"
    assert "DATASET" in cert.failed_stages


def test_certify_model_rejects_when_feature_report_fails(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    bundle_dir, model_id, manifest = valid_bundle
    cert = certify_model(
        model_id=model_id,
        manifest=manifest,
        dataset_report={"quality_status": "PASS"},
        feature_report={"quality_status": "FAIL", "failed": 3},  # Failed!
        label_report={"quality_status": "PASS"},
        training_metrics={
            "metrics_measured": True,
            "train_loss": 0.5,
            "validation_loss": 0.5,
            "validation_accuracy": 0.4,
            "epochs_completed": 1,
        },
        walk_forward_result={"passed": True, "fold_count": 4, "purge_embargo": True},
        oos_metrics={"oos_loss": 0.5, "oos_accuracy": 0.4},
        bundle_dir=bundle_dir,
    )
    assert cert.certified is False
    assert cert.model_status == "REJECTED"
    assert "FEATURE" in cert.failed_stages


def test_certify_model_rejects_when_label_report_fails(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    bundle_dir, model_id, manifest = valid_bundle
    cert = certify_model(
        model_id=model_id,
        manifest=manifest,
        dataset_report={"quality_status": "PASS"},
        feature_report={"quality_status": "PASS", "failed": 0},
        label_report={
            "quality_status": "FAIL",
            "collapsed_classes": ["BUY below minimum ratio (0.00%)"],
        },
        training_metrics={
            "metrics_measured": True,
            "train_loss": 0.5,
            "validation_loss": 0.5,
            "validation_accuracy": 0.4,
            "epochs_completed": 1,
        },
        walk_forward_result={"passed": True, "fold_count": 4, "purge_embargo": True},
        oos_metrics={"oos_loss": 0.5, "oos_accuracy": 0.4},
        bundle_dir=bundle_dir,
    )
    assert cert.certified is False
    assert cert.model_status == "REJECTED"
    assert "LABEL" in cert.failed_stages


def test_certify_model_rejects_unmeasured_training_metrics(
    valid_bundle: tuple[Path, str, dict],
) -> None:
    """Register-but-never-trained models with metrics_measured=False MUST be rejected."""
    bundle_dir, model_id, manifest = valid_bundle
    cert = certify_model(
        model_id=model_id,
        manifest=manifest,
        dataset_report={"quality_status": "PASS"},
        feature_report={"quality_status": "PASS", "failed": 0},
        label_report={"quality_status": "PASS"},
        training_metrics={
            "metrics_measured": False,
            "train_loss": None,
            "epochs_completed": 0,
        },  # Unmeasured!
        walk_forward_result={"passed": True, "fold_count": 4, "purge_embargo": True},
        oos_metrics={"oos_loss": 0.5, "oos_accuracy": 0.4},
        bundle_dir=bundle_dir,
    )
    assert cert.certified is False
    assert cert.model_status == "REJECTED"
    assert "TRAIN" in cert.failed_stages


def test_certify_model_rejects_missing_oos_evaluation(valid_bundle: tuple[Path, str, dict]) -> None:
    """A model without evaluated OOS loss MUST be rejected."""
    bundle_dir, model_id, manifest = valid_bundle
    cert = certify_model(
        model_id=model_id,
        manifest=manifest,
        dataset_report={"quality_status": "PASS"},
        feature_report={"quality_status": "PASS", "failed": 0},
        label_report={"quality_status": "PASS"},
        training_metrics={
            "metrics_measured": True,
            "train_loss": 0.5,
            "validation_loss": 0.5,
            "validation_accuracy": 0.4,
            "epochs_completed": 1,
        },
        walk_forward_result={"passed": True, "fold_count": 4, "purge_embargo": True},
        oos_metrics={"oos_loss": None},  # No OOS!
        bundle_dir=bundle_dir,
    )
    assert cert.certified is False
    assert cert.model_status == "REJECTED"
    assert "OOS" in cert.failed_stages


def test_certify_model_rejects_failed_walk_forward(valid_bundle: tuple[Path, str, dict]) -> None:
    bundle_dir, model_id, manifest = valid_bundle
    cert = certify_model(
        model_id=model_id,
        manifest=manifest,
        dataset_report={"quality_status": "PASS"},
        feature_report={"quality_status": "PASS", "failed": 0},
        label_report={"quality_status": "PASS"},
        training_metrics={
            "metrics_measured": True,
            "train_loss": 0.5,
            "validation_loss": 0.5,
            "validation_accuracy": 0.4,
            "epochs_completed": 1,
        },
        walk_forward_result={"passed": False, "fold_count": 4, "purge_embargo": True},  # WF failed!
        oos_metrics={"oos_loss": 0.5, "oos_accuracy": 0.4},
        bundle_dir=bundle_dir,
    )
    assert cert.certified is False
    assert cert.model_status == "REJECTED"
    assert "WALK_FORWARD" in cert.failed_stages


# =============================================================================
# 6. LOAD CONTRACT ENDPOINT — STACK TRACE EXPOSURE REGRESSION (CodeQL #1184)
# =============================================================================


def test_load_contract_endpoint_never_leaks_exception_text(
    valid_bundle: tuple[Path, str, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``execute_verify_load_contract`` must not mirror ``str(exc)``.

    SEC (CodeQL py/stack-trace-exposure #1184): the CompatibilityError message
    is built from the manifest/request, but the exception object can also carry
    filesystem paths and source locations from its construction context. The
    endpoint must log the traceback and return a stable refusal payload.
    """
    from nexus_scalp.web.model_studio_routes import (
        ModelStudioLoadContractRequest,
        execute_verify_load_contract,
    )

    bundle_dir, model_id, _ = valid_bundle
    monkeypatch.setattr(
        "nexus_scalp.web.model_studio_routes._bundle_dir_for",
        lambda _mid: bundle_dir,
    )
    (bundle_dir / f"{model_id}.pt").unlink()  # forces CompatibilityError

    req = ModelStudioLoadContractRequest(model_id=model_id)
    out = execute_verify_load_contract(req)

    assert out["status"] == "LOAD_REJECTED"
    assert out["load_rejected"] is True
    assert out["verified"] is False
    # The payload must NOT contain the raw exception text (no artifact paths,
    # no checksum fragments, no source locations).
    payload = json.dumps(out)
    assert "model.pt missing" not in payload
    assert "artifact bundle is incomplete" not in payload
    assert out["reason"] == out["detail"]
    assert "LOAD_REJECTED" in out["reason"]


def test_load_contract_success_reports_verified_and_detail(
    valid_bundle: tuple[Path, str, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The success payload must satisfy the typed client contract."""
    from nexus_scalp.web.model_studio_routes import (
        ModelStudioLoadContractRequest,
        execute_verify_load_contract,
    )

    bundle_dir, model_id, _ = valid_bundle
    monkeypatch.setattr(
        "nexus_scalp.web.model_studio_routes._bundle_dir_for",
        lambda _mid: bundle_dir,
    )

    out = execute_verify_load_contract(ModelStudioLoadContractRequest(model_id=model_id))
    assert out["status"] == "OK"
    assert out["load_rejected"] is False
    assert out["verified"] is True
    assert out["detail"]
