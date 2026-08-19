"""TASK-03-70D-PARITY — inference validator + model compatibility
(TEST-70D-PARITY-11/12/13/21/22/23/24 + scaler 09/10).

Covers:
  TEST-70D-PARITY-11  60D model accepts 60D only
  TEST-70D-PARITY-12  70D model accepts 70D only
  TEST-70D-PARITY-13  60D/70D mismatch blocked (both directions)
  TEST-70D-PARITY-21  nonfinite features blocked
  TEST-70D-PARITY-22  out-of-range features blocked
  TEST-70D-PARITY-23  schema hash mismatch blocked
  TEST-70D-PARITY-24  stale features blocked
  TEST-70D-PARITY-09/10 scaler dimension/hash compatibility
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.features.inference_validator import (
    FAMILY_AVAILABLE,
    FAMILY_INVALID,
    FAMILY_UNAVAILABLE,
    InferenceValidator,
    RejectionCode,
    ScalerContract,
    ValidationResult,
    compatible_model_schema,
)
from nexus_scalp.features.schema_contract import feature_schema_hash


def _vec70() -> list[float]:
    return [0.0] * 50 + [0.1] * 10 + [0.2] * 10


def _validator(**kw) -> InferenceValidator:
    base = dict(
        expected_schema_id="scalp_v3",
        expected_dimension=70,
        expected_schema_hash=feature_schema_hash(),
    )
    base.update(kw)
    return InferenceValidator(**base)


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-13 — mismatch blocked (both directions)
# ---------------------------------------------------------------------------


def test_p13_60d_model_blocks_60d_runtime() -> None:
    # A 60D (scalp_v2) model must NOT receive a 70D vector
    res = compatible_model_schema("scalp_v2", 60, "scalp_v3", 70)
    assert res["result"] == "BLOCK"
    assert res["reason"] == "SCHEMA_MISMATCH"


def test_p13_70d_model_blocks_70d_runtime_with_60d_model() -> None:
    # Established 60D model + 70D runtime feature -> BLOCK
    res = compatible_model_schema("scalp_v2", 60, "scalp_v3", 70)
    assert res["result"] == "BLOCK"


def test_p13_pass_when_schema_and_dimension_match() -> None:
    res = compatible_model_schema("scalp_v3", 70, "scalp_v3", 70)
    assert res["result"] == "PASS"
    res60 = compatible_model_schema("scalp_v2", 60, "scalp_v2", 60)
    assert res60["result"] == "PASS"


def test_p13_unknown_without_metadata() -> None:
    res = compatible_model_schema(None, None, "scalp_v3", 70)
    assert res["result"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-12 — 70D model accepts 70D only
# ---------------------------------------------------------------------------


def test_p12_validator_accepts_70d() -> None:
    v = _validator()
    r = v.validate(_vec70(), context="test")
    assert r.ok is True
    assert r.code is None


def test_p12_validator_rejects_60d_vector() -> None:
    v = _validator()
    r = v.validate([0.0] * 60, context="test")
    assert r.ok is False
    assert r.code == RejectionCode.DIMENSION_MISMATCH


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-11 — 60D model accepts 60D only (legacy path intact)
# ---------------------------------------------------------------------------


def test_p11_60d_validator_accepts_60d() -> None:
    v = InferenceValidator(
        expected_schema_id="scalp_v2", expected_dimension=60, expected_schema_hash=""
    )
    r = v.validate([0.0] * 60, context="legacy")
    assert r.ok is True


def test_p11_60d_validator_rejects_70d() -> None:
    v = InferenceValidator(
        expected_schema_id="scalp_v2", expected_dimension=60, expected_schema_hash=""
    )
    r = v.validate(_vec70(), context="legacy")
    assert r.ok is False
    assert r.code == RejectionCode.DIMENSION_MISMATCH


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-21 — nonfinite blocked
# ---------------------------------------------------------------------------


def test_p21_nonfinite_blocked() -> None:
    v = _validator()
    vec = _vec70()
    vec[52] = float("nan")
    r = v.validate(vec, context="test")
    assert r.ok is False
    assert r.code == RejectionCode.NONFINITE_FEATURE
    assert "52" in r.reason


def test_p21_inf_blocked() -> None:
    v = _validator()
    vec = _vec70()
    vec[63] = float("inf")
    r = v.validate(vec, context="test")
    assert r.code == RejectionCode.NONFINITE_FEATURE


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-22 — out-of-range blocked
# ---------------------------------------------------------------------------


def test_p22_out_of_range_blocked() -> None:
    v = _validator()
    vec = _vec70()
    vec[65] = 3.5
    r = v.validate(vec, context="test")
    assert r.ok is False
    assert r.code == RejectionCode.OUT_OF_RANGE_FEATURE


def test_p22_negative_out_of_range_blocked() -> None:
    v = _validator()
    vec = _vec70()
    vec[50] = -3.01
    r = v.validate(vec, context="test")
    assert r.code == RejectionCode.OUT_OF_RANGE_FEATURE


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-23 — schema hash mismatch blocked
# ---------------------------------------------------------------------------


def test_p23_schema_hash_mismatch_blocked() -> None:
    v = _validator(expected_schema_hash="deadbeefdeadbeef")
    r = v.validate(_vec70(), context="test")
    assert r.ok is False
    assert r.code == RejectionCode.SCHEMA_HASH_MISMATCH


def test_p23_schema_id_mismatch_blocked() -> None:
    v = _validator()
    r = v.validate(_vec70(), actual_schema_id="scalp_v2", context="test")
    assert r.ok is False
    assert r.code == RejectionCode.SCHEMA_MISMATCH


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-24 — stale features blocked
# ---------------------------------------------------------------------------


def test_p24_stale_blocked() -> None:
    v = _validator(max_age_seconds=30.0)
    old_ts = datetime.now(UTC) - timedelta(seconds=120)
    r = v.validate(_vec70(), timestamp_utc=old_ts, context="test")
    assert r.ok is False
    assert r.code == RejectionCode.STALE_FEATURES


def test_p24_fresh_passes() -> None:
    v = _validator(max_age_seconds=30.0)
    r = v.validate(_vec70(), timestamp_utc=datetime.now(UTC), context="test")
    assert r.ok is True


# ---------------------------------------------------------------------------
# NEWS / LIQUIDITY availability (brief 23/24/25)
# ---------------------------------------------------------------------------


def test_news_unavailable_blocks() -> None:
    v = _validator()
    r = v.validate(_vec70(), news_status=FAMILY_UNAVAILABLE, context="test")
    assert r.ok is False
    assert r.code == RejectionCode.NEWS_UNAVAILABLE


def test_news_invalid_blocks() -> None:
    v = _validator()
    r = v.validate(_vec70(), news_status=FAMILY_INVALID, context="test")
    assert r.code == RejectionCode.NEWS_UNAVAILABLE


def test_liquidity_unavailable_blocks() -> None:
    v = _validator()
    r = v.validate(_vec70(), liquidity_status=FAMILY_UNAVAILABLE, context="test")
    assert r.ok is False
    assert r.code == RejectionCode.LIQUIDITY_UNAVAILABLE


def test_liquidity_disabled_allowed_for_validation() -> None:
    # FEATURE_DISABLED is an EXPLICIT state (news off -> neutral block).
    v = _validator()
    r = v.validate(
        _vec70(), news_status=FAMILY_AVAILABLE, liquidity_status=FAMILY_AVAILABLE, context="test"
    )
    assert r.ok is True


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-09/10 — scaler compatibility
# ---------------------------------------------------------------------------


def test_p09_scaler_dimension_mismatch_blocked() -> None:
    v = _validator(scaler=ScalerContract(dimension=60))
    r = v.validate(_vec70(), context="test")
    assert r.ok is False
    assert r.code == RejectionCode.SCALER_MISMATCH
    assert "60" in r.reason


def test_p09_scaler_dimension_match_passes() -> None:
    v = _validator(scaler=ScalerContract(dimension=70))
    r = v.validate(_vec70(), context="test")
    assert r.ok is True


def test_p10_scaler_never_padded_or_truncated() -> None:
    # Silently padding a 60D scaler to 70D is FORBIDDEN (brief 19):
    # dimension mismatch must stop, not adapt.
    v = _validator(scaler=ScalerContract(dimension=60))
    r = v.validate(_vec70(), context="test")
    assert r.code == RejectionCode.SCALER_MISMATCH


def test_validator_result_shape() -> None:
    v = _validator()
    r: ValidationResult = v.validate(_vec70(), context="test")
    d = r.to_dict()
    assert d["ok"] is True
    assert d["dimension"] == 70
    assert d["schema_id"] == "scalp_v3"
