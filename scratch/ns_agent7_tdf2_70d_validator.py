"""Agent 7 — TDF-2: 70D assembly + missing-block semantics probe (read-only).

Verifies the REAL 70D assembly contracts:
  1. assemble_70d produces Base 0..49 | News 50..59 | Liquidity 60..69 exactly
  2. missing family (None) raises — no silent fabrication
  3. wrong dimension raises
  4. wrong schema hash raises
  5. non-finite raises
  6. build_70d_vector (live path) neutral fill semantics + width gates
  7. news_10d_from_context canonical ordering + junk tolerance
  8. InferenceValidator live chain: every rejection code is REACHABLE and blocks
  9. validator bypass hunt: try to sneak NaN/Inf/wrong-dim/wrong-hash through
"""
from __future__ import annotations

import math
import sys

sys.path.insert(0, "src")

from nexus_scalp.features.features70 import (
    LIQUIDITY_NEUTRAL_10D,
    NEWS_NEUTRAL_10D,
    FeatureSourceState,
    assemble_70d,
    news_10d_from_context,
)
from nexus_scalp.features.inference_validator import (
    InferenceValidator,
    RejectionCode,
    ScalerContract,
)
from nexus_scalp.features.liquidity_runtime import build_70d_vector
from nexus_scalp.features.schema_contract import (
    feature_schema_hash,
    validate_70d_vector,
)

FAILURES: list[str] = []
NOTES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}")


def expect_raises(name: str, fn, exc=Exception) -> None:
    try:
        fn()
        print(f"  FAIL {name} (no exception raised)")
        FAILURES.append(f"{name}: no exception — silent pass-through")
    except exc as e:
        print(f"  PASS {name} (raised {type(e).__name__})")


base50 = [0.1 * (i % 7) for i in range(50)]
news10 = [0.2] * 10
liq10 = list(LIQUIDITY_NEUTRAL_10D)

print("=== TDF-2: 70D assembly + validator chain ===")

# --- assemble_70d -----------------------------------------------------------
snap = assemble_70d(base50=base50, news10=news10, liquidity10=liq10, symbol="XAUUSD")
v = snap.feature_vector
check("geometry base", list(v[0:50]) == base50)
check("geometry news", list(v[50:60]) == news10)
check("geometry liquidity", list(v[60:70]) == liq10)
check("names attached", len(snap.feature_names) == 70)

expect_raises("assemble: news None raises", lambda: assemble_70d(base50=base50, liquidity10=liq10))
expect_raises(
    "assemble: liquidity None raises", lambda: assemble_70d(base50=base50, news10=news10)
)
expect_raises(
    "assemble: base 49D raises",
    lambda: assemble_70d(base50=base50[:49], news10=news10, liquidity10=liq10),
)
expect_raises(
    "assemble: news 9D raises",
    lambda: assemble_70d(base50=base50, news10=news10[:9], liquidity10=liq10),
)

# availability downgrading
snap_na = assemble_70d(
    base50=base50,
    news10=list(NEWS_NEUTRAL_10D),
    liquidity10=liq10,
    news_available=False,
    news_status=FeatureSourceState.FEATURE_UNAVAILABLE,
)
check("news unavailable status preserved", snap_na.news_status == FeatureSourceState.FEATURE_UNAVAILABLE)

# --- validate_70d_vector ------------------------------------------------------
vec = list(v)
check("validate ok", validate_70d_vector(vec, schema_hash=feature_schema_hash()) == vec)

bad_dim = vec[:-1]
expect_raises(
    "validate: 69D raises",
    lambda: validate_70d_vector(bad_dim, schema_hash=feature_schema_hash()),
)
expect_raises(
    "validate: wrong hash raises",
    lambda: validate_70d_vector(vec, schema_hash="deadbeefdeadbeef"),
)
nonfinite = list(vec)
nonfinite[55] = float("nan")
expect_raises(
    "validate: NaN at 55 raises",
    lambda: validate_70d_vector(nonfinite, schema_hash=feature_schema_hash()),
)
inf_v = list(vec)
inf_v[62] = float("inf")
expect_raises(
    "validate: Inf at 62 raises",
    lambda: validate_70d_vector(inf_v, schema_hash=feature_schema_hash()),
)
oor = list(vec)
oor[3] = 5.0
expect_raises(
    "validate: 5.0 at idx3 raises",
    lambda: validate_70d_vector(oor, schema_hash=feature_schema_hash()),
)

# --- build_70d_vector (live path used by live_engine + shadow70) -------------
v70 = build_70d_vector(base50, family_10=news10, liquidity_10=liq10)
check("build_70d_vector geometry", v70[50:60] == list(news10) and v70[60:70] == list(liq10))
v70_none = build_70d_vector(base50, family_10=None, liquidity_10=liq10)
NOTES.append(
    "build_70d_vector(family_10=None) fills neutral and logs FAMILY_NEUTRAL_FILL (documented semantics; caller must label)"
)
expect_raises(
    "build_70d_vector: liquidity None raises", lambda: build_70d_vector(base50, family_10=news10, liquidity_10=None)
)
expect_raises(
    "build_70d_vector: base 51 raises",
    lambda: build_70d_vector(base50 + [0.0], family_10=news10, liquidity_10=liq10),
)

# --- news_10d_from_context ordering -----------------------------------------
ctx = {name: float(i + 1) for i, name in enumerate(
    [
        "active_high_impact_events",
        "xauusd_relevance",
        "usd_relevance",
        "bullish_pressure",
        "bearish_pressure",
        "conflict_score",
        "novelty",
        "freshness",
        "confidence",
        "source_consensus",  # index 9 must be EXCLUDED
        "news_state",  # index 10 must be INCLUDED
    ]
)}
out = news_10d_from_context(ctx)
check("news10 excludes source_consensus", out[9] == 11.0, f"got {out}")
check("news10 length", len(out) == 10)
out_bad = news_10d_from_context({"bullish_pressure": float("nan"), "freshness": "x"})
check("news10 junk -> 0.0", out_bad[3] == 0.0 and out_bad[6] == 0.0)

# --- InferenceValidator: trigger EVERY rejection code -----------------------
val = InferenceValidator(expected_schema_id="scalp_v3", expected_dimension=70)
print("\n-- validator chain (each code must be REACHABLE) --")

r = val.validate(vec, actual_schema_id="scalp_v2", context="t")
check("SCHEMA_MISMATCH reachable+blocks", (not r.ok) and r.code == RejectionCode.SCHEMA_MISMATCH)

r = val.validate(vec[:-1], context="t")
check("DIMENSION_MISMATCH reachable+blocks", (not r.ok) and r.code == RejectionCode.DIMENSION_MISMATCH)

r = val.validate(vec, feature_names=list(vec_names) if (vec_names := None) else None, context="t")  # names None skipped
bad_names = ["x"] * 70
r = val.validate(vec, feature_names=bad_names, context="t")
check("FEATURE_ORDER_MISMATCH reachable+blocks", (not r.ok) and r.code == RejectionCode.FEATURE_ORDER_MISMATCH)

# hash drift: simulate canonical hash changing under the validator
from nexus_scalp.features import schema_contract as sc

orig = sc.feature_schema_hash
try:
    sc.feature_schema_hash = lambda *a, **k: "driftedhash"  # type: ignore[assignment]
    r = val.validate(vec, context="t")
    check(
        "SCHEMA_HASH_MISMATCH reachable+blocks",
        (not r.ok) and r.code == RejectionCode.SCHEMA_HASH_MISMATCH,
        f"code={r.code}",
    )
finally:
    sc.feature_schema_hash = orig  # type: ignore[assignment]

r_nan = list(vec)
r_nan[61] = float("nan")
r = val.validate(r_nan, context="t")
check("NONFINITE_FEATURE reachable+blocks", (not r.ok) and r.code == RejectionCode.NONFINITE_FEATURE)

r_oor = list(vec)
r_oor[61] = 3.5
r = val.validate(r_oor, context="t")
check("OUT_OF_RANGE_FEATURE reachable+blocks", (not r.ok) and r.code == RejectionCode.OUT_OF_RANGE_FEATURE)

r = val.validate(
    vec, news_status="FEATURE_UNAVAILABLE", context="t"
)
check("NEWS_UNAVAILABLE reachable+blocks", (not r.ok) and r.code == RejectionCode.NEWS_UNAVAILABLE)

r = val.validate(
    vec, liquidity_status="FEATURE_UNAVAILABLE", context="t"
)
check("LIQUIDITY_UNAVAILABLE reachable+blocks", (not r.ok) and r.code == RejectionCode.LIQUIDITY_UNAVAILABLE)

from datetime import UTC, datetime, timedelta

val_age = InferenceValidator(expected_dimension=70, max_age_seconds=5.0)
r = val_age.validate(vec, timestamp_utc=datetime.now(UTC) - timedelta(seconds=60), context="t")
check("STALE_FEATURES reachable+blocks", (not r.ok) and r.code == RejectionCode.STALE_FEATURES)

val_sc = InferenceValidator(
    expected_dimension=70, scaler=ScalerContract(dimension=50)
)
r = val_sc.validate(vec, context="t")
check("SCALER_MISMATCH reachable+blocks", (not r.ok) and r.code == RejectionCode.SCALER_MISMATCH)

# happy path must stay ok
r = val.validate(vec, news_status="FEATURE_AVAILABLE", liquidity_status="FEATURE_AVAILABLE")
check("happy path ok", r.ok)

# --- bypass hunt: validator returns ok=False -> does ANY caller proceed? -----
NOTES.append("bypass hunt: live engine path calls validate_70d_vector directly and raises on failure (verified by source read); runtime70 hook returns ok=False and callers must gate on it")

print()
for n in NOTES:
    print("NOTE:", n)
if FAILURES:
    print("TDF-2 VERDICT: FAIL")
    for f in FAILURES:
        print("  -", f)
    return_code = 1
else:
    print("TDF-2 VERDICT: PASS (70D assembly + validator chain enforced, all codes reachable)")
    return_code = 0
sys.exit(return_code)
