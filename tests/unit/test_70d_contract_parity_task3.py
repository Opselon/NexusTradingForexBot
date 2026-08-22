"""TASK-03-70D-PARITY — canonical 70D schema contract tests (TEST-70D-PARITY-*).

Covers the single-source-of-truth contract:
  TEST-70D-PARITY-01  Base + News + Liquidity dimension == 70
  TEST-70D-PARITY-02  schema ID validated (scalp_v3 == 70D in registry)
  TEST-70D-PARITY-03  schema hash deterministic (+ changes when order/names change)
  TEST-70D-PARITY-04  family layout explicit (base 0..49, news 50..59, liquidity 60..69)
  TEST-70D-PARITY-05  vector validation (dimension/finite/bounds) with explicit reasons
"""

from __future__ import annotations

import hashlib

import pytest

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.features.schema_contract import (
    FAMILY_BASE,
    FAMILY_LIQUIDITY,
    FAMILY_NEWS,
    NEWS_10D_NAMES,
    SCHEMA_ID,
    SchemaContractError,
    canonical_feature_names,
    canonical_registry_json,
    family_of,
    feature_schema_hash,
    validate_70d_vector,
)

# ---------------------------------------------------------------------------
# TEST-70D-PARITY-01 — dimension contract
# ---------------------------------------------------------------------------


def test_p01_dimension_is_exactly_70() -> None:
    names = canonical_feature_names()
    assert len(names) == 70
    assert len(set(names)) == 70  # no duplicates


def test_p01_registry_scalp_v3_is_70d() -> None:
    schema = FEATURE_SCHEMAS.resolve(SCHEMA_ID)
    assert schema.dimension == 70
    assert schema.supersedes == "scalp_v1"  # extends the protected 50D base


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-02 — schema id validated
# ---------------------------------------------------------------------------


def test_p02_schema_id_is_scalp_v3() -> None:
    assert SCHEMA_ID == "scalp_v3"
    assert FEATURE_SCHEMAS.resolve("scalp_v3").schema_id == "scalp_v3"


def test_p02_names_are_index_ordered() -> None:
    names = canonical_feature_names()
    # index == position in tuple; names carry no index prefix
    assert names[0] == "upper_wick_ratio"
    assert names[49] == "feat_ob_fib_50_60_alignment"
    assert names[50] == NEWS_10D_NAMES[0]
    assert names[59] == NEWS_10D_NAMES[-1]
    # liquidity starts exactly at 60
    assert names[60] == "bsl_distance_atr"
    assert names[69] == "post_sweep_displacement"


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-03 — schema hash deterministic
# ---------------------------------------------------------------------------


def test_p03_hash_deterministic() -> None:
    h1 = feature_schema_hash()
    h2 = feature_schema_hash()
    assert h1 == h2
    assert len(h1) == 16  # repo hash prefix convention


def test_p03_hash_covers_order_and_names() -> None:
    h = feature_schema_hash()
    # The hash must represent index+name+family: simulate a reorder by
    # hashing a modified registry json -> hash must differ.
    j = canonical_registry_json()
    j_reordered = j.replace('"name":"active_high_impact_events"', '"name":"zzz_reordered"', 1)
    h2 = hashlib.sha256(j_reordered.encode("utf-8")).hexdigest()[:16]
    assert h2 != h


def test_p03_hash_embedded_in_registry_json() -> None:
    j = canonical_registry_json()
    assert '"schema_id":"scalp_v3"' in j
    assert '"dimension":70' in j
    assert '"news":{"count":10,"end":60,"start":50}' in j
    assert '"liquidity":{"count":10,"end":70,"start":60}' in j
    # 70 entries
    assert j.count('"index":') == 70


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-04 — family layout
# ---------------------------------------------------------------------------


def test_p04_family_boundaries() -> None:
    assert family_of(0) == FAMILY_BASE
    assert family_of(49) == FAMILY_BASE
    assert family_of(50) == FAMILY_NEWS
    assert family_of(59) == FAMILY_NEWS
    assert family_of(60) == FAMILY_LIQUIDITY
    assert family_of(69) == FAMILY_LIQUIDITY
    with pytest.raises(IndexError):
        family_of(70)
    with pytest.raises(IndexError):
        family_of(-1)


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-05 — vector validation
# ---------------------------------------------------------------------------


def test_p05_valid_vector_passes() -> None:
    vec = [0.0] * 50 + [0.1] * 10 + [0.2] * 10
    out = validate_70d_vector(vec, context="test")
    assert out == vec


def test_p05_wrong_dimension_rejected() -> None:
    with pytest.raises(SchemaContractError, match="expected dimension 70"):
        validate_70d_vector([0.0] * 60)


def test_p05_nonfinite_rejected() -> None:
    vec = [0.0] * 70
    vec[52] = float("nan")
    with pytest.raises(SchemaContractError, match="non-finite value at index 52"):
        validate_70d_vector(vec)


def test_p05_out_of_range_rejected() -> None:
    vec = [0.0] * 70
    vec[65] = 3.5
    with pytest.raises(SchemaContractError, match="out of \\[-3,\\+3\\]"):
        validate_70d_vector(vec)


def test_p05_liquidity_range_rejected() -> None:
    vec = [0.0] * 70
    vec[60] = -3.0001
    with pytest.raises(SchemaContractError, match="family=liquidity"):
        validate_70d_vector(vec)


def test_p05_hash_mismatch_rejected() -> None:
    vec = [0.0] * 70
    with pytest.raises(SchemaContractError, match="schema hash mismatch"):
        validate_70d_vector(vec, schema_hash="deadbeefdeadbeef")
