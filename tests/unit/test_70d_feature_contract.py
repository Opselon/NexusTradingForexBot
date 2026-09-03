"""FEATURE_CONTRACT v1 (Fix #3 complement + TASK T70D) — feature-schema pins.

test_70d_feature_contract enforces that SCALP_V3 = 70D with the documented
family layout (Base 50 | News 10 | Liquidity 10), that no dimension
change sneaks through without a contract+schema change, and that inference
rejects a mismatched bundle width (the 70D/50D hot-swap invariant).

These are the REPRODUCIBLE, static guarantees around *what* the model head
consumes — without them the model-class contract has no meaning.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.features.schema_contract import (
    DIMENSION,
    FAMILY_BASE,
    FAMILY_LIQUIDITY,
    FAMILY_NEWS,
    LIQUIDITY_10D_NAMES,
    NEWS_10D_NAMES,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SchemaContractError,
    canonical_feature_names,
    canonical_registry_json,
    family_of,
    feature_schema_hash,
    validate_70d_vector,
)

# ── Dimension contract ────────────────────────────────────────────────────


def test_70d_feature_contract_dimension_is_70() -> None:
    assert DIMENSION == 70
    assert SCHEMA_ID == "scalp_v3"
    assert SCHEMA_VERSION == "1.0.0"
    reg = FEATURE_SCHEMAS.resolve("scalp_v3")
    assert reg.dimension == 70
    assert len(canonical_feature_names()) == 70


# ── Family layout ─────────────────────────────────────────────────────────


def test_70d_feature_contract_families() -> None:
    names = canonical_feature_names()
    assert names[0:50].count("") == 0
    assert tuple(names[50:60]) == NEWS_10D_NAMES
    assert tuple(names[60:70]) == LIQUIDITY_10D_NAMES
    assert family_of(0) == FAMILY_BASE
    assert family_of(49) == FAMILY_BASE
    assert family_of(50) == FAMILY_NEWS
    assert family_of(59) == FAMILY_NEWS
    assert family_of(60) == FAMILY_LIQUIDITY
    assert family_of(69) == FAMILY_LIQUIDITY


def test_70d_feature_contract_names_stable_and_unique() -> None:
    names = canonical_feature_names()
    assert len(set(names)) == 70
    assert names[50] == "active_high_impact_events"
    assert names[59] == "news_state"
    assert names[60] == "bsl_distance_atr"
    assert names[69] == "post_sweep_displacement"


# ── Hash determinism & registry JSON ─────────────────────────────────────


def test_70d_feature_contract_hash_deterministic() -> None:
    h1 = feature_schema_hash()
    h2 = feature_schema_hash()
    assert h1 == h2
    assert len(h1) == 16


def test_70d_feature_contract_registry_json_covers_layout() -> None:
    j = canonical_registry_json()
    assert '"schema_id":"scalp_v3"' in j
    assert '"dimension":70' in j
    assert '"base"' in j and '"news"' in j and '"liquidity"' in j
    assert j.count('"index":') == 70


# ── Vector validation (finite, [-3,+3], family in errors) ────────────────


def test_70d_feature_contract_vector_validation() -> None:
    good = [0.0] * 50 + [0.1] * 10 + [0.2] * 10
    assert validate_70d_vector(good, context="unit") == good
    with pytest.raises(SchemaContractError, match="expected dimension 70"):
        validate_70d_vector([0.0] * 60)
    bad = [0.0] * 70
    bad[52] = float("nan")
    with pytest.raises(SchemaContractError, match="non-finite value at index 52"):
        validate_70d_vector(bad)
    oob = [0.0] * 70
    oob[65] = 3.5
    with pytest.raises(SchemaContractError, match="out of \\[-3,\\+3\\]"):
        validate_70d_vector(oob)
    with pytest.raises(SchemaContractError, match="schema hash mismatch"):
        validate_70d_vector([0.0] * 70, schema_hash="deadbeefdeadbeef")


# ── Inference validator (70D bundle must reject mismatched width/scaler) ──

try:
    from nexus_scalp.features.inference_validator import InferenceContractValidator
except ImportError:
    InferenceContractValidator = None  # type: ignore[assignment]


@pytest.mark.skipif(InferenceContractValidator is None, reason="inference validator not present")
def test_70d_feature_contract_inference_validator_rejects_mismatch() -> None:
    """A 50-feature tensor against a 70D bundle must block (width mismatch)."""
    v = InferenceContractValidator(expected_dim=70)
    with pytest.raises(Exception):  # noqa: B017 - validator raises SchemaContractError family
        v.validate(np.zeros(50, dtype=np.float32))


# ── Lifecycle integrity (head width on disk is 4, label contract is 3) ────


def test_70d_feature_contract_head_vs_label_distinction() -> None:
    from nexus_scalp.model_lifecycle.model_class_contract import (
        LEGACY_HEAD_CLASSES,
        TRAINED_CLASS_COUNT,
    )
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    assert WalkForwardTrainer.NUM_CLASSES == 3
    assert WalkForwardTrainer.MODEL_HEAD_CLASSES == 4
    assert TRAINED_CLASS_COUNT == 3
    assert LEGACY_HEAD_CLASSES == 4


# ── No feature contract hash drift on smoke/prod builds ──────────────────


def test_70d_feature_contract_hash_unchanged_by_smoke_flag(tmp_path: Path) -> None:
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    h = feature_schema_hash()
    for smoke in (True, False):
        p = tmp_path / f"h_{smoke}" / "model.pt"
        tr = WalkForwardTrainer(feature_schema_id="scalp_v3", artifact_save_path=p, smoke=smoke)
        tr._save_metadata([f"feat_{i}" for i in range(70)])
        meta = json.loads((tmp_path / f"h_{smoke}" / "model.meta.json").read_text(encoding="utf-8"))
        # model_class_contract feature_schema_hash in meta matches the factory hash
        assert meta["feature_schema_hash"] == h
        assert meta["smoke"] is smoke
        assert meta["production_eligible"] is (not smoke)
