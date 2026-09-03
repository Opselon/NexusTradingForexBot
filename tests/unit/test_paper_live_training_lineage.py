"""MLFIX-T7 — Paper/live training-lineage hard guard regression tests.

Verifies that:
  * every DatasetFactory manifest is lineage-stamped,
  * tainted label origins (PAPER/LIVE/UNKNOWN + degenerate) are NOT
    production-eligible without an explicit governance_override,
  * the hard guard (LineageGovernanceError) blocks training, and
  * a clean CLEAN_HISTORICAL lineage passes without override.
"""

from __future__ import annotations

import pytest

from nexus_scalp.model_generation.lineage import (
    LabelOrigin,
    LineageGovernanceError,
    assert_production_eligible,
    classify_source,
    manifest_is_production_eligible,
    requires_governance_override,
    stamp_manifest,
)


def test_clean_historical_is_production_eligible() -> None:
    assert not requires_governance_override(LabelOrigin.CLEAN_HISTORICAL)
    assert_production_eligible(LabelOrigin.CLEAN_HISTORICAL)


def test_synthetic_is_production_eligible() -> None:
    assert not requires_governance_override(LabelOrigin.SYNTHETIC)
    assert_production_eligible(LabelOrigin.SYNTHETIC)


@pytest.mark.parametrize(
    "origin", [LabelOrigin.PAPER_GENERATED, LabelOrigin.LIVE_GENERATED, LabelOrigin.UNKNOWN]
)
def test_tainted_requires_override(origin: LabelOrigin) -> None:
    assert requires_governance_override(origin)
    with pytest.raises(LineageGovernanceError):
        assert_production_eligible(origin)
    # explicit operator token bypasses once
    assert_production_eligible(origin, governance_override=True)


def test_degenerate_flag_is_tainted() -> None:
    assert classify_source(is_degenerate_model_derived=True) == LabelOrigin.PAPER_GENERATED
    assert requires_governance_override(classify_source(is_degenerate_model_derived=True))


def test_string_origin_is_normalized() -> None:
    assert classify_source(label_origin="paper_generated") == LabelOrigin.PAPER_GENERATED
    assert classify_source(label_origin="CLEAN_HISTORICAL") == LabelOrigin.CLEAN_HISTORICAL
    assert classify_source(label_origin="  live_generated  ") == LabelOrigin.LIVE_GENERATED


def test_stamp_manifest_claims_taint() -> None:
    base = {"dataset_id": "ds_test", "rows": 99}
    stamped = stamp_manifest(base, LabelOrigin.PAPER_GENERATED)
    assert stamped["label_origin"] == LabelOrigin.PAPER_GENERATED.value
    assert stamped["governance_override_required"] is True
    assert "label_origin_stamped_at" in stamped
    assert base.get("label_origin") is None  # input untouched


def test_legacy_manifest_without_stamp_is_ineligible() -> None:
    eligible, reason = manifest_is_production_eligible({"dataset_id": "ds_old"})
    assert not eligible
    assert "legacy manifest" in reason.lower() or "missing" in reason.lower()


def test_clean_manifest_is_eligible() -> None:
    eligible, _ = manifest_is_production_eligible({"label_origin": "CLEAN_HISTORICAL"})
    assert eligible


def test_paper_manifest_is_ineligible_without_override() -> None:
    eligible, reason = manifest_is_production_eligible({"label_origin": "PAPER_GENERATED"})
    assert not eligible
    assert "governance_override" in reason.lower()


def test_dataset_factory_stamps_manifest(tmp_path) -> None:
    from datetime import UTC, datetime

    # minimal 70-row frame with feat_0..49 so SampleFactory can emit samples
    # (TripleBarrier needs close/high/low/atr_m1 too)
    import numpy as np
    import polars as pl

    from nexus_scalp.model_generation.artifact_store import ArtifactStore
    from nexus_scalp.model_generation.dataset_factory import DatasetFactory
    from nexus_scalp.model_generation.sample_factory import SampleFactory

    np.random.seed(1)
    n = 90
    close = np.cumsum(np.random.choice([-1, 1], n) * 0.4) + 4650.0
    high = close + 0.9
    low = close - 0.9
    frame = pl.DataFrame(
        {
            "timestamp": [datetime(2026, 5, 1, 12, 0, tzinfo=UTC).isoformat()] * n,
            "close": close,
            "high": high,
            "low": low,
            "atr_m1": np.full(n, 1.2),
            "spread": np.full(n, 0.35),
            **{f"feat_{i}": np.random.randn(n) * 0.2 for i in range(50)},
        }
    )
    store = ArtifactStore(root=tmp_path)
    factory = DatasetFactory(
        store=store, sample_factory=SampleFactory(feature_schema_id="scalp_v1")
    )
    _ = factory.build(
        frame, dataset_id="ds_lineage_smoke", label_origin=LabelOrigin.CLEAN_HISTORICAL
    )
    man = store.read_dataset_manifest("ds_lineage_smoke")
    assert man is not None
    assert man.get("label_origin") == LabelOrigin.CLEAN_HISTORICAL.value

    # tainted dataset must also be stamped (and then be ineligible)
    _ = factory.build(
        frame, dataset_id="ds_lineage_paper", label_origin=LabelOrigin.PAPER_GENERATED
    )
    man2 = store.read_dataset_manifest("ds_lineage_paper")
    assert man2.get("label_origin") == LabelOrigin.PAPER_GENERATED.value
    assert man2.get("governance_override_required") is True
    eligible, _ = manifest_is_production_eligible(man2)
    assert not eligible
