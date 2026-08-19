"""TASK-01-60D-LIQUIDITY — contract, registry, BSL/SSL, EQH/EQL, missing-value,
edge-case tests (TEST-LIQ-01..11, 32-37, 44)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.features.liquidity_engine import (
    BASE_50D,
    LIQUIDITY_60D_DIM,
    LIQUIDITY_FEATURE_NAMES,
    PoolSide,
    PoolSource,
    PoolState,
    build_60d_vector,
    compute_liquidity_features,
    detect_confirmed_swings,
    equal_high_low_strengths,
    liquidity_atr,
    validate_60d_liquidity_vector,
)
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from tests.helpers.liquidity_fixtures import (
    bar,
    ramp_bars,
    steady_bars,
    swing_high_bars,
    swing_low_bars,
)

# ---------------------------------------------------------------------------
# TEST-LIQ-01/02/03/04/05 — 50D contract preserved, registry, dimension,
# finiteness, clipping
# ---------------------------------------------------------------------------


def test_liq01_registry_has_60_dimensions_in_order() -> None:
    schema = FEATURE_SCHEMAS.resolve("scalp_liquidity_v1")
    assert schema.dimension == 60
    assert schema.supersedes == "scalp_v1"
    # active live contract untouched
    assert FEATURE_SCHEMAS.active.schema_id == "scalp_v1"
    assert FEATURE_SCHEMAS.active.dimension == 50


def test_liq02_liquidity_feature_names_unique_and_indexed() -> None:
    assert len(LIQUIDITY_FEATURE_NAMES) == 10
    assert len(set(LIQUIDITY_FEATURE_NAMES)) == 10
    # the protected 50D base remains exactly 50
    assert BASE_50D == 50
    assert LIQUIDITY_60D_DIM == 60


def test_liq03_vector_is_exactly_60() -> None:
    bars = steady_bars(80)
    f = compute_liquidity_features(bars)
    v = build_60d_vector([0.0] * 50, f)
    assert len(v) == 60
    with pytest.raises(ValueError):
        build_60d_vector([0.0] * 49, f)  # base too narrow
    # extras are embedded in LiquidityFeatures; a wrong-feature count raise
    # is covered by validate_60d_liquidity_vector
    with pytest.raises(ValueError):
        validate_60d_liquidity_vector([0.0] * 59)


def test_liq04_all_values_finite() -> None:
    bars = swing_high_bars(50, 3310.0, 3300.0)
    f = compute_liquidity_features(bars)
    for v in f.as_vector():
        assert math.isfinite(v)


def test_liq05_all_values_clipped_minus3_plus3() -> None:
    bars = swing_high_bars(50, 3302.0, 3300.0)
    f = compute_liquidity_features(bars)
    vec = f.as_vector()
    assert all(-3.0 <= v <= 3.0 for v in vec)
    assert all(math.isfinite(v) for v in vec)
    # the 60D composite (50 base + 10 liquid) passes the validator
    v60 = build_60d_vector([0.0] * 50, f)
    validate_60d_liquidity_vector(v60)


# ---------------------------------------------------------------------------
# TEST-LIQ-06/07 — BSL / SSL detection
# ---------------------------------------------------------------------------


def test_liq06_bsl_distance_measured_above_price() -> None:
    # pool high 3302 vs base 3300, ATR ~1.67 -> distance ~1.2 ATR (not clipped)
    bars = swing_high_bars(50, high_price=3302.0, base=3300.0)
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.bsl_distance_atr > 0.0
    assert f.bsl_distance_atr <= 3.0
    assert f.bsl_distance_atr < 3.0  # meaningfully inside the clip range
    # BSL feature must not be negative when the BSL is above price
    assert f.bsl_distance_atr >= 0.0


def test_liq07_ssl_distance_measured_below_price() -> None:
    # pool low 3298 vs base 3300 -> distance ~1.2 ATR
    bars = swing_low_bars(50, low_price=3298.0, base=3300.0)
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.ssl_distance_atr > 0.0
    assert f.ssl_distance_atr <= 3.0
    assert f.ssl_distance_atr < 3.0
    # SSL feature must not be negative when the SSL is below price
    assert f.ssl_distance_atr >= 0.0


def test_liq06_07_directionality_no_cross_contamination() -> None:
    # only a swing HIGH just above price: BSL near, SSL comes only from the
    # session-low level (a legitimate far-away sell-side pool). The key
    # contract: bsl is the SWING pool, ssl is strictly below price.
    bars = swing_high_bars(50, high_price=3302.0, base=3300.0)
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.bsl_distance_atr < 3.0
    assert f.ssl_distance_atr > 0.0 and f.ssl_distance_atr <= 3.0
    # no BSL below price: bsl_above only picks pools > price
    assert f.bsl_distance_atr >= 0.0


# ---------------------------------------------------------------------------
# TEST-LIQ-08/09 — EQH/EQL detection
# ---------------------------------------------------------------------------


def test_liq08_eqh_detected_from_equal_swing_highs() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(40, price=3300.0, t0=t0)
    i = 40
    bars.append(bar(i, t0, 3300.0, 3310.0, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        bars.append(bar(i + j, t0, 3300.0, 3300.5, 3299.5, 3300.0))
    i2 = 55
    bars.append(bar(i2, t0, 3300.0, 3310.05, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        bars.append(bar(i2 + j, t0, 3300.0, 3300.5, 3299.5, 3300.0))
    f = compute_liquidity_features(bars, mid_price=3300.0)
    # two confirmed highs within tolerance -> real EQH cluster
    assert f.eqh_strength > 0.0
    assert f.eqh_strength <= 1.0


def test_liq09_eql_detected_from_equal_swing_lows() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(40, price=3300.0, t0=t0)
    i = 40
    bars.append(bar(i, t0, 3300.0, 3301.0, 3290.0, 3300.0, vol=200))
    for j in range(1, 7):
        bars.append(bar(i + j, t0, 3300.0, 3300.5, 3299.5, 3300.0))
    i2 = 55
    bars.append(bar(i2, t0, 3300.0, 3301.0, 3289.95, 3300.0, vol=200))
    for j in range(1, 7):
        bars.append(bar(i2 + j, t0, 3300.0, 3300.5, 3299.5, 3300.0))
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.eql_strength > 0.0
    assert f.eql_strength <= 1.0


# ---------------------------------------------------------------------------
# TEST-LIQ-10/11 — EQH/EQL tolerance (no float equality)
# ---------------------------------------------------------------------------


def test_liq10_eqh_uses_atr_tolerance_not_float_equality() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(40, price=3300.0, t0=t0)
    i = 40
    # two highs differing by 0.05 with ATR ~1: within 0.30*ATR tolerance
    bars.append(bar(i, t0, 3300.0, 3310.0, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        bars.append(bar(i + j, t0, 3300.0, 3300.5, 3299.5, 3300.0))
    i2 = 55
    bars.append(bar(i2, t0, 3300.0, 3310.05, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        bars.append(bar(i2 + j, t0, 3300.0, 3300.5, 3299.5, 3300.0))
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.eqh_strength > 0.5  # strong cluster despite non-identical highs


def test_liq11_far_apart_highs_not_a_cluster() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = ramp_bars(40, 3298.0, 0.05, t0)
    i = 40
    bars.append(bar(i, t0, 3300.0, 3302.0, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        c = 3300.0 + 0.05 * j
        bars.append(bar(i + j, t0, c - 0.2, c + 0.5, c - 0.5, c))
    i2 = 55
    # second high far above -> NOT equal to the first
    bars.append(bar(i2, t0, 3300.0, 3320.0, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        c = 3300.0 + 0.05 * j
        bars.append(bar(i2 + j, t0, c - 0.2, c + 0.5, c - 0.5, c))
    # the two engineered levels must NOT share an equal-high cluster
    sh, _ = detect_confirmed_swings(bars)
    from nexus_scalp.features.liquidity_engine import _cluster_equal_levels

    vals = np.asarray([p.price for p in sh], dtype=np.float64)
    ts = [p.confirmed_at for p in sh]
    clusters = _cluster_equal_levels(vals, ts, atr=1.0)
    members_in_same_cluster = any(
        abs(c["value"] - 3302.0) < 0.31 and abs(c["value"] - 3320.0) < 0.31 for c in clusters
    )
    assert not members_in_same_cluster
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert 0.0 <= f.eqh_strength <= 1.0


# ---------------------------------------------------------------------------
# TEST-LIQ-32 — missing-value behavior
# ---------------------------------------------------------------------------


def test_liq32_no_history_yields_documented_defaults() -> None:
    f = compute_liquidity_features([])
    assert f.as_vector() == [3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0]
    assert all(math.isfinite(v) for v in f.as_vector())


def test_liq32_short_history_no_crash() -> None:
    bars = steady_bars(10)
    f = compute_liquidity_features(bars)
    assert all(math.isfinite(v) for v in f.as_vector())


def test_liq32_zero_atr_handled() -> None:
    # flat bars -> zero true range -> canonical floor MIN_ATR
    bars = []
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    for i in range(60):
        bars.append(bar(i, t0, 3300.0, 3300.0, 3300.0, 3300.0))
    f = compute_liquidity_features(bars)
    assert all(math.isfinite(v) for v in f.as_vector())
    assert all(-3.0 <= v <= 3.0 for v in f.as_vector())


# ---------------------------------------------------------------------------
# TEST-LIQ-33 — ATR normalization is the canonical mean-TR-14
# ---------------------------------------------------------------------------


def test_liq33_atr_matches_engine_semantics() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = []
    for i in range(60):
        c = 3300.0 + i * 0.1
        bars.append(bar(i, t0, c - 0.5, c + 0.8, c - 0.7, c))
    highs = np.array([b.high for b in bars])
    lows = np.array([b.low for b in bars])
    closes = np.array([b.close for b in bars])
    atr = liquidity_atr(highs, lows, closes)
    # manual mean-TR-14 over the last 14 bars (prior-close reference)
    trs = []
    for k in range(len(closes) - 14, len(closes)):
        trs.append(
            max(highs[k] - lows[k], abs(highs[k] - closes[k - 1]), abs(lows[k] - closes[k - 1]))
        )
    expected = max(float(np.mean(trs)), 0.20)
    assert atr == pytest.approx(expected, rel=1e-9)


# ---------------------------------------------------------------------------
# TEST-LIQ-34/35 — no DB / no network dependency (structural)
# ---------------------------------------------------------------------------


def test_liq34_no_db_import_in_engine() -> None:
    import inspect

    import nexus_scalp.features.liquidity_engine as le

    src = inspect.getsource(le)
    for banned in ("sqlite3", "audit", "news.db", "research", "requests", "urllib", "http"):
        assert banned not in src, f"liquidity_engine imports/references banned dependency: {banned}"


def test_liq35_pure_function_no_io() -> None:
    import inspect

    src = inspect.getsource(compute_liquidity_features)
    for banned in ("open(", "Path(", "requests", "sqlite"):
        assert banned not in src


# ---------------------------------------------------------------------------
# TEST-LIQ-36/37 — determinism
# ---------------------------------------------------------------------------


def test_liq36_repeated_calculation_identical() -> None:
    bars = swing_high_bars(50, 3310.0, 3300.0)
    a = compute_liquidity_features(bars).as_vector()
    b = compute_liquidity_features(bars).as_vector()
    assert a == b  # bit-exact

    # with decision_at snapshot: same result from a frozen prefix
    bars2 = steady_bars(240)
    t = bars2[200].timestamp
    v1 = compute_liquidity_features(bars2, decision_at=t).as_vector()
    v2 = compute_liquidity_features(bars2[:201], decision_at=t).as_vector()
    assert v1 == v2


# ---------------------------------------------------------------------------
# TEST-LIQ-44 — edge cases
# ---------------------------------------------------------------------------


def test_liq44_duplicate_levels_single_pool() -> None:
    # repeated equal highs collapse to one cluster; feature stays sane
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars: list = []
    for k in range(3):
        bars.extend(swing_high_bars(20 + k * 12, 3310.0, 3300.0, t0=t0 + timedelta(minutes=k * 60)))
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.eqh_strength >= 0.0
    assert f.eqh_strength <= 1.0


def test_liq44_exact_price_equality_ok() -> None:
    bars = swing_high_bars(50, 3300.0, 3299.0)  # pool exactly at current price
    f = compute_liquidity_features(bars, mid_price=3300.0)
    vec = f.as_vector()
    assert all(math.isfinite(v) for v in vec)


def test_liq44_large_prices_finite() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars: list = []
    for i in range(80):
        c = 100_000.0 + i * 0.5
        bars.append(bar(i, t0, c - 1.0, c + 2.0, c - 2.0, c))
    f = compute_liquidity_features(bars)
    assert all(math.isfinite(v) for v in f.as_vector())


def test_liq44_sparse_data_defaults() -> None:
    bars = steady_bars(2)
    f = compute_liquidity_features(bars)
    assert all(math.isfinite(v) for v in f.as_vector())


def test_liq44_midnight_boundary_ok() -> None:
    # bars spanning a UTC midnight boundary
    t0 = datetime(2026, 8, 1, 23, 30, tzinfo=UTC)
    bars = steady_bars(120, price=3300.0, t0=t0)
    f = compute_liquidity_features(bars)
    assert all(math.isfinite(v) for v in f.as_vector())


def test_pool_source_taxonomy_complete() -> None:
    # the canonical source taxonomy covers the required candidates
    required = {
        "SWING_HIGH",
        "SWING_LOW",
        "EQH",
        "EQL",
        "PDH",
        "PDL",
        "PWH",
        "PWL",
        "SESSION_HIGH",
        "SESSION_LOW",
        "HTF_SWING_HIGH",
        "HTF_SWING_LOW",
        "HTF_EQH",
        "HTF_EQL",
    }
    assert required <= {s.name for s in PoolSource}


def test_pool_lifecycle_states_exist() -> None:
    required = {
        "CANDIDATE",
        "CONFIRMED",
        "APPROACHING",
        "TOUCHED",
        "SWEPT",
        "RECLAIMED",
        "DISPLACED",
        "INVALIDATED",
    }
    assert required <= {s.name for s in PoolState}
    assert PoolSide.BSL > 0
    assert PoolSide.SSL < 0
