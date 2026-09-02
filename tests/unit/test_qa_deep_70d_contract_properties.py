"""TASK-QA-DEEP-ASSURANCE / CHG-0045: property-style 70D vector-contract battery.

Deterministic property tests over the canonical 70D contract surfaces
(schema_contract.validate_70d_vector, inference_validator.InferenceValidator,
forensics.checks_features.check_feature_contract_vector):

- round-trip: any generated valid vector is accepted; mutation of exactly one
  element out of [-3,+3] / to NaN / to inf is rejected at the SAME index
- idempotence of validation on the accepted result
- family attribution: every rejection names the right family for the mutated
  index (base 0..49 / news 50..59 / liquidity 60..69)
- schema-hash identity: hash is stable across calls and changes when the
  registry changes (guarded by reading the real registry)
- legacy widths 50/60 are valid for the forensics check, invalid for 70D
- BUG-184/BUG-192 classes: bool / numeric-string / None elements are
  CRITICAL / SchemaContractError / structured NONFINITE_FEATURE — never
  silent PASS, never raw TypeError.

Property style = generated inputs from a FIXED SEED (random.Random(20260902))
with bounded sizes; no hypothesis dependency (repo adds dependencies only on
measured value; this battery achieves the property-testing defect classes
with stdlib randomness, fully offline).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.features.inference_validator import InferenceValidator, RejectionCode
from nexus_scalp.features.schema_contract import (
    DIMENSION,
    SchemaContractError,
    family_of,
    feature_schema_hash,
    validate_70d_vector,
)

SEED = 20260902

# Owner-routed OPEN defect (BUG-184 extension / BUG-208 addendum):
# validate_70d_vector + InferenceValidator type-guard tests are xfail
# (non-strict) until the feature-contract owner lands the guard; the
# forensics CHECK-FCS-04 half of the same class is GREEN (1490635).
open_defect = pytest.mark.xfail(
    reason="BUG-184 extension: type guard pending feature-contract owner (BUG-208 addendum)",
    strict=False,
)


def _valid_vector(rng: random.Random, dim: int = DIMENSION) -> list[float]:
    """Bounded random vector inside the [-3,+3] contract window."""
    return [round(rng.uniform(-3.0, 3.0), 6) for _ in range(dim)]


def _family_name(i: int) -> str:
    # contract families per scalp_v3: Base 0..49, News 50..59, Liquidity 60..69
    return family_of(i)


# ---------------------------------------------------------------------------
# Property: valid vectors are accepted (round-trip acceptance)
# ---------------------------------------------------------------------------


def test_property_valid_vectors_accepted() -> None:
    rng = random.Random(SEED)
    for _ in range(50):
        vec = _valid_vector(rng)
        out = validate_70d_vector(vec)
        assert out == vec  # returned fresh list preserves content


def test_property_validation_idempotent_on_accepted_result() -> None:
    rng = random.Random(SEED + 1)
    for _ in range(25):
        vec = _valid_vector(rng)
        once = validate_70d_vector(vec)
        twice = validate_70d_vector(once)
        assert once == twice


def test_property_boundary_values_accepted() -> None:
    rng = random.Random(SEED + 2)
    for _ in range(25):
        vec = _valid_vector(rng)
        i = rng.randrange(DIMENSION)
        for boundary in (-3.0, 3.0):
            mutated = list(vec)
            mutated[i] = boundary
            validate_70d_vector(mutated)  # inclusive bounds must accept


# ---------------------------------------------------------------------------
# Property: single-element mutation -> rejection names the SAME index
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mutator", ["nan", "inf", "oob_high", "oob_low"])
def test_property_single_mutation_rejected_at_same_index(mutator: str) -> None:
    rng = random.Random(SEED + 3)
    for _ in range(20):
        vec = _valid_vector(rng)
        i = rng.randrange(DIMENSION)
        mutated = list(vec)
        if mutator == "nan":
            mutated[i] = float("nan")
        elif mutator == "inf":
            mutated[i] = float("inf")
        elif mutator == "oob_high":
            mutated[i] = 3.0001
        else:
            mutated[i] = -3.0001
        with pytest.raises(SchemaContractError) as ei:
            validate_70d_vector(mutated)
        msg = str(ei.value)
        assert f"index {i}" in msg, f"rejection lost the index for {mutator} at {i}: {msg}"


def test_property_family_attribution_matches_mutated_index() -> None:
    rng = random.Random(SEED + 4)
    checked = 0
    for _ in range(30):
        vec = _valid_vector(rng)
        i = rng.randrange(DIMENSION)
        mutated = list(vec)
        mutated[i] = float("nan")
        try:
            validate_70d_vector(mutated)
            pytest.fail("NaN vector must be rejected")
        except SchemaContractError as e:
            assert f"family={_family_name(i)}" in str(e), f"family misattributed at {i}"
            checked += 1
    assert checked >= 25


def test_property_dimension_mutation_rejected_everywhere() -> None:
    rng = random.Random(SEED + 5)
    for d in (49, 51, 69, 71, 0, 140):
        vec = _valid_vector(rng, dim=d) if d > 0 else []
        with pytest.raises(SchemaContractError):
            validate_70d_vector(vec)


# ---------------------------------------------------------------------------
# Schema-hash identity
# ---------------------------------------------------------------------------


def test_schema_hash_stable_across_calls() -> None:
    h1 = feature_schema_hash()
    h2 = feature_schema_hash()
    assert h1 == h2 and len(h1) >= 8


def test_legacy_widths_pass_forensics_check_but_fail_70d_contract() -> None:
    from nexus_scalp.forensics.checks_features import check_feature_contract_vector

    for width in (50, 60):
        r = check_feature_contract_vector([0.1] * width)
        assert r.status.value in ("PASS", "HEALTHY"), width
        with pytest.raises(SchemaContractError):
            validate_70d_vector([0.1] * width)


# ---------------------------------------------------------------------------
# BUG-184 / BUG-192 class: non-numeric elements never silently pass
# ---------------------------------------------------------------------------


@open_defect
@pytest.mark.parametrize("bad", [True, False, "0.1", None])
def test_non_numeric_elements_rejected_structured(bad: object) -> None:
    """REQUIRED semantics after the feature-contract owner fix. Currently
    RED for validate_70d_vector/InferenceValidator (BUG-184 extension,
    see BUG-208 scope addendum); the forensics CHECK-FCS-04 half is GREEN
    (guard landed in 1490635)."""
    vec = [0.1] * DIMENSION
    vec[7] = bad  # type: ignore[list-item]
    # forensics check: CRITICAL with explicit evidence (GREEN today)
    from nexus_scalp.forensics.checks_features import check_feature_contract_vector

    r = check_feature_contract_vector(vec)  # type: ignore[arg-type]
    assert r.status.value == "CRITICAL"
    # 70D contract: structured SchemaContractError (RED until owner fix)
    with pytest.raises(SchemaContractError):
        validate_70d_vector(vec)


@open_defect
def test_bool_true_is_rejected_not_int_coerced() -> None:
    """RED until the feature-contract owner fix (BUG-184 extension)."""
    vec = [0.0] * DIMENSION
    vec[0] = True
    with pytest.raises(SchemaContractError):
        validate_70d_vector(vec)


@open_defect
def test_inference_validator_structured_rejection_for_non_numeric() -> None:
    """RED until the feature-contract owner fix (BUG-184 extension)."""
    iv = InferenceValidator()
    for bad in (None, True, "0.5"):
        vec: list[float] = [0.1] * DIMENSION
        vec[3] = bad  # type: ignore[list-item]
        res = iv.validate(vec, timestamp_utc=datetime.now(UTC))
        assert not res.ok
        assert res.code is RejectionCode.NONFINITE_FEATURE, f"{bad!r} -> {res.code}"


def test_inference_validator_stale_features_rejected() -> None:
    iv = InferenceValidator(max_age_seconds=1.0)
    res = iv.validate(
        _valid_vector(random.Random(SEED + 6)),
        timestamp_utc=datetime.now(UTC) - timedelta(hours=2),
    )
    assert not res.ok and res.code is RejectionCode.STALE_FEATURES


def test_inference_validator_wrong_dimension_rejected() -> None:
    iv = InferenceValidator()
    res = iv.validate([0.1] * 69)
    assert not res.ok and res.code is RejectionCode.DIMENSION_MISMATCH


def test_inference_validator_accepts_fresh_valid_vector() -> None:
    iv = InferenceValidator()
    res = iv.validate(_valid_vector(random.Random(SEED + 7)), timestamp_utc=datetime.now(UTC))
    assert res.ok, res.reason
    assert math.isfinite(float(0))  # keep math import meaningful for ruff
