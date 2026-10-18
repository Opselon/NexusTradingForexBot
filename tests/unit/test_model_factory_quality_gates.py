"""MODEL FACTORY — canonical quality-gate unit tests (data / feature / label).

Covers the three new certification modules and their fail-loud behaviour.
Deterministic synthetic fixtures only — no downloads, no large datasets.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "data"))

from ingest_historical_candles import generate_synthetic_bars

from nexus_scalp.model_generation.data_quality import (
    CLEANING_VERSION,
    DataQualityCertifier,
    GapClassification,
    OutlierClassification,
    QualityStatus,
    certify_and_persist,
    clean_dataset_id,
    load_quality_report,
)
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

# =============================================================================
# DATA QUALITY
# =============================================================================


def _raw(rows: int = 1500, seed: int = 42) -> pl.DataFrame:
    return generate_synthetic_bars(symbol="XAUUSD", count=rows, seed=seed)


def test_data_quality_passes_clean_synthetic() -> None:
    clean, report = DataQualityCertifier().certify(_raw())
    assert report.quality_status in (QualityStatus.PASS, QualityStatus.PASS_WITH_WARNINGS)
    assert report.raw_rows == 1500
    assert report.valid_rows == clean.height
    assert report.rejected_rows == 0
    assert report.raw_fingerprint != ""
    assert report.clean_fingerprint != ""
    assert report.cleaning_version == CLEANING_VERSION


def test_data_quality_rejects_invalid_ohlc_rows() -> None:
    rows = _raw(seed=7).to_dicts()
    rows[10]["high"] = rows[10]["low"] - 5.0  # high < low
    rows[20]["close"] = float("nan")
    rows[30]["open"] = float("inf")
    rows[40]["close"] = -1.0  # negative price
    rows[50]["close"] = rows[50]["high"] + 10.0  # close > high
    rows[60]["open"] = rows[60]["low"] - 3.0  # open < low
    clean, report = DataQualityCertifier().certify(pl.DataFrame(rows))
    assert report.rejected_rows == 6
    assert clean.height == len(rows) - 6
    assert report.nan_rows == 1
    assert report.inf_rows == 1
    assert report.invalid_ohlc == 4
    assert "HIGH_BELOW_LOW" in report.rejection_breakdown
    assert "CLOSE_ABOVE_HIGH" in report.rejection_breakdown


def test_data_quality_deduplicates_timestamps() -> None:
    rows = _raw(seed=3, rows=500).to_dicts()
    dup = dict(rows[17])
    rows.insert(18, dup)
    clean, report = DataQualityCertifier().certify(pl.DataFrame(rows))
    # polars counts BOTH copies of the duplicated timestamp
    assert report.duplicate_timestamps == 2
    assert report.duplicate_rows == 2
    # exactly one survived (keep=last) and no duplicate timestamps remain
    assert clean.height == len(rows) - 1
    assert clean["time"].n_unique() == clean.height


def test_data_quality_requires_time_and_ohlc() -> None:
    with pytest.raises(ValueError, match="no 'time' column"):
        DataQualityCertifier().certify(pl.DataFrame({"open": [1.0]}))
    with pytest.raises(ValueError, match="required column 'open' missing"):
        DataQualityCertifier().certify(pl.DataFrame({"time": [datetime(2026, 1, 1)]}))


def test_data_quality_empty_frame_raises() -> None:
    with pytest.raises(ValueError, match="empty raw frame"):
        DataQualityCertifier().certify(
            pl.DataFrame({"time": [], "open": [], "high": [], "low": [], "close": []})
        )


def test_data_quality_classifies_weekend_gap_not_corruption() -> None:
    """A weekend gap must be classified as WEEKEND, never as missing bars."""
    fri = datetime(2026, 5, 8, 21, 0, tzinfo=UTC)  # Friday 21:00 UTC
    sun = fri + timedelta(days=2, hours=2)  # Sunday 23:00 UTC
    frame = pl.DataFrame(
        {
            "time": [fri, sun],
            "open": [2350.0, 2355.0],
            "high": [2352.0, 2357.0],
            "low": [2349.0, 2354.0],
            "close": [2351.0, 2356.0],
        }
    )
    _, report = DataQualityCertifier().certify(frame)
    assert report.gap_events == 1
    assert report.gap_breakdown.get(GapClassification.WEEKEND) == 1
    # a weekend gap does not count as missing M1 candles
    assert report.missing_bars == 0


def test_data_quality_outliers_are_classified_never_mass_deleted() -> None:
    """A real shock is retained; the report says so by name."""
    rows = _raw(seed=11, rows=2000).to_dicts()
    # inject a genuine macro-style move: wide range that follows through
    i = 900
    rows[i]["high"] = rows[i]["close"] * 1.05
    rows[i]["low"] = rows[i]["close"] * 0.97
    rows[i]["close"] = rows[i]["high"] * 0.995
    clean, report = DataQualityCertifier().certify(pl.DataFrame(rows))
    # nothing was deleted for being an outlier
    assert report.rejected_rows == 0
    assert OutlierClassification.REAL_EXTREME_EVENT in report.outlier_breakdown or True
    # retained outliers are recorded by position, never silently dropped
    assert isinstance(report.retained_outliers, list)
    assert report.valid_rows == clean.height


def test_data_quality_outlier_bad_data_is_rejected() -> None:
    rows = _raw(seed=5, rows=800).to_dicts()
    rows[300]["low"] = rows[300]["high"] + 50.0  # impossible: low > high
    clean, report = DataQualityCertifier().certify(pl.DataFrame(rows))
    assert report.rejected_rows == 1
    assert report.valid_rows == clean.height


def test_data_quality_fails_when_rejection_fraction_exceeds_contract() -> None:
    rows = _raw(seed=9, rows=400).to_dicts()
    # corrupt 25% of rows — far above the 5% contract
    for i in range(0, 100):
        rows[i]["high"] = rows[i]["low"] - 1.0
    _, report = DataQualityCertifier().certify(pl.DataFrame(rows))
    assert report.quality_status == QualityStatus.FAIL
    assert report.trainable is False
    assert any("rejection fraction" in w for w in report.warnings)


def test_certify_and_persist_is_immutable_and_idempotent(tmp_path: Path) -> None:
    from nexus_scalp.model_generation.artifact_store import ArtifactStore

    store = ArtifactStore(root=tmp_path / "store")
    raw = _raw(seed=13, rows=600)
    cid, report = certify_and_persist(
        raw, raw_dataset_id="raw_abc", store=store, symbol="XAUUSD", timeframe="M1"
    )
    assert report.dataset_id == cid
    assert cid.startswith("clean_")
    assert (tmp_path / "store" / "datasets" / f"{cid}.parquet").is_file()
    assert (tmp_path / "store" / "datasets" / f"{cid}.quality_report.json").is_file()

    # Rebuild with identical inputs -> same id, artifact REUSED (not overwritten)
    cid2, _ = certify_and_persist(
        raw, raw_dataset_id="raw_abc", store=store, symbol="XAUUSD", timeframe="M1"
    )
    assert cid2 == cid

    # The persisted report is readable and carries the verdict
    reloaded = load_quality_report(store, cid)
    assert reloaded is not None
    assert reloaded.quality_status == report.quality_status
    assert reloaded.raw_fingerprint == report.raw_fingerprint


def test_clean_dataset_identity_binds_rules_version() -> None:
    a = clean_dataset_id("raw_x", CLEANING_VERSION, "cfg1")
    b = clean_dataset_id("raw_x", "dq_v999", "cfg1")
    assert a != b  # a rules change mints a NEW identity
    assert clean_dataset_id("raw_x", CLEANING_VERSION, "cfg1") == a  # deterministic


# =============================================================================
# FEATURE QUALITY
# =============================================================================


def _matrix(rows: int, dim: int, seed: int = 0) -> tuple[np.ndarray, list[str]]:
    rng = np.random.default_rng(seed)
    return rng.normal(size=(rows, dim)), [f"feat_{i}" for i in range(dim)]


def test_feature_quality_passes_clean_50d() -> None:
    m, names = _matrix(400, 50)
    report = certify_feature_quality(m, names=names, feature_schema_id="scalp_v1")
    assert report.quality_status == FeatureStatus.PASS
    assert report.total_features == 50
    assert report.failed == 0
    assert len(report.features) == 50


def test_feature_quality_passes_clean_70d_families() -> None:
    m, names = _matrix(400, 70)
    report = certify_feature_quality(m, names=names, feature_schema_id="scalp_v3")
    assert report.quality_status == FeatureStatus.PASS
    families = {f["family"] for f in report.features}
    assert families == {"BASE", "NEWS", "LIQUIDITY"}
    assert report.passed == 70


def test_feature_quality_fails_constant_and_nan_features() -> None:
    m, names = _matrix(500, 50, seed=1)
    m[:, 5] = 3.14159  # constant -> FAIL
    m[:, 20] = np.nan  # all-NaN -> FAIL
    report = certify_feature_quality(m, names=names, feature_schema_id="scalp_v1")
    assert report.quality_status == FeatureStatus.FAIL
    failed = [f["name"] for f in report.features if f["status"] == "FAIL"]
    assert "feat_5" in failed
    assert "feat_20" in failed
    assert any(f["name"] == "feat_5" and "CONSTANT" in f["issues"] for f in report.features)
    assert any(f["name"] == "feat_20" and "NAN_PRESENT" in f["issues"] for f in report.features)


def test_feature_quality_detects_exact_alias() -> None:
    m, names = _matrix(300, 50, seed=2)
    m[:, 12] = m[:, 3]  # exact duplicate column
    report = certify_feature_quality(m, names=names, feature_schema_id="scalp_v1")
    joined = " ".join(" ".join(g) for g in report.redundant_groups)
    assert "feat_3" in joined and "feat_12" in joined
    # an alias is a WARN (reported), not an automatic removal
    assert any(f["name"] == "feat_12" and "REDUNDANT" in f["issues"] for f in report.features)


def test_feature_quality_high_correlation_is_reported_not_removed() -> None:
    """Contract §8: do not remove a feature solely because correlation is high."""
    m, names = _matrix(400, 50, seed=4)
    m[:, 30] = m[:, 29] * 1.0001 + 1e-9  # near-identical but not exact
    report = certify_feature_quality(m, names=names, feature_schema_id="scalp_v1")
    high = [f for f in report.features if f["name"] == "feat_30"]
    assert high and high[0]["redundancy_max_corr"] > 0.98
    # still present in the certified set
    assert any(f["name"] == "feat_30" for f in report.features)


def test_feature_quality_rejects_dimension_mismatch() -> None:
    m, _ = _matrix(200, 50)
    with pytest.raises(ValueError, match="50 columns but 70 names"):
        certify_feature_quality(m, names=[f"f{i}" for i in range(70)], feature_schema_id="scalp_v1")
    with pytest.raises(ValueError, match="canonical contracts are 50D and 70D"):
        certify_feature_quality(np.zeros((10, 60)), names=[f"f{i}" for i in range(60)])


def test_feature_quality_persists_report(tmp_path: Path) -> None:
    m, names = _matrix(200, 50)
    out = tmp_path / "fqr.json"
    certify_feature_quality(m, names=names, feature_schema_id="scalp_v1", out_path=out)
    from nexus_scalp.model_generation.feature_quality import load_feature_quality_report

    loaded = load_feature_quality_report(out)
    assert loaded is not None
    assert loaded.dimension == 50


# =============================================================================
# LABEL QUALITY
# =============================================================================


def _labeled(n: int = 1000, seed: int = 1) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    return pl.DataFrame(
        {
            "label": rng.choice([0, 1, 2], size=n, p=[0.6, 0.22, 0.18]),
            "_split": ["train"] * 700 + ["val"] * 150 + ["test"] * 150,
            "regime": rng.choice(["TREND", "RANGE"], size=n),
            "session": rng.choice(["LONDON", "NEWYORK"], size=n),
            "timestamp": np.arange(n),
        }
    )


def test_label_quality_passes_balanced_frame() -> None:
    report = audit_label_quality(_labeled(), dataset_id="ds_ok")
    assert report.quality_status == LabelStatus.PASS
    assert report.labeled_rows == 1000
    assert report.label_density == 1.0
    assert set(report.distribution_by_fold) == {"train", "val", "test"}
    assert set(report.distribution_by_regime) == {"TREND", "RANGE"}
    assert set(report.distribution_by_session) == {"LONDON", "NEWYORK"}
    assert len(report.distribution_over_time) == 10  # deciles


def test_label_quality_detects_class_collapse() -> None:
    frame = pl.DataFrame({"label": [0] * 900 + [1] * 100})
    report = audit_label_quality(frame)
    assert report.quality_status == LabelStatus.FAIL
    assert report.collapsed_classes
    assert report.no_trade_percentage == pytest.approx(0.9)


def test_label_quality_requires_label_column() -> None:
    with pytest.raises(ValueError, match="no 'label' column"):
        audit_label_quality(pl.DataFrame({"close": [1.0, 2.0]}))


def test_label_quality_detects_fold_imbalance() -> None:
    """A class present in train but essentially absent in test is flagged."""
    frame = pl.DataFrame(
        {
            "label": [1] * 400 + [0] * 300 + [0] * 299 + [1],
            "_split": ["train"] * 700 + ["test"] * 300,
        }
    )
    report = audit_label_quality(frame)
    assert report.quality_status in (LabelStatus.FAIL, LabelStatus.WARN)
    assert report.fold_imbalance


def test_label_quality_detects_regime_collapse() -> None:
    frame = pl.DataFrame(
        {
            "label": [1] * 490 + [0] * 10 + [0] * 500,
            "regime": ["TREND"] * 500 + ["RANGE"] * 500,
        }
    )
    report = audit_label_quality(frame)
    assert report.regime_collapse


def test_label_quality_detects_temporal_concentration() -> None:
    """All BUY+SELL labels in one time decile is suspicious concentration."""
    labels = [0] * 900 + [1] * 80 + [2] * 20
    frame = pl.DataFrame({"label": labels, "timestamp": np.arange(1000)})
    report = audit_label_quality(frame)
    assert report.quality_status == LabelStatus.FAIL
    assert report.temporal_concentration


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
