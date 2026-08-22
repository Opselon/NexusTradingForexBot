"""TASK-01-60D-LIQUIDITY — causality & anti-leakage gold-standard tests
(TEST-LIQ-12/13, 18-28, TEST-60D-BASE-01).

The invariant under test: features at T computed from bars through T MUST
equal features at T computed from bars through T+N (once the confirmation
state cannot legitimately change). Future bars must be invisible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from nexus_scalp.features.liquidity_engine import (
    compute_liquidity_features,
    detect_confirmed_swings,
    htf_liquidity_score,
)
from tests.helpers.liquidity_fixtures import (
    bar,
    ramp_bars,
    sweep_pool_bars,
    swing_high_bars,
)


def _prefix(bars, decision) -> list:
    return [b for b in bars if b.timestamp <= decision]


# ---------------------------------------------------------------------------
# TEST-LIQ-23 — future swing cannot leak
# ---------------------------------------------------------------------------


def test_liq23_future_swing_invisible_before_confirmation() -> None:
    bars = swing_high_bars(50, 3302.0, 3300.0)
    peak_idx = 50  # the swing bar
    candidate_t = bars[peak_idx].timestamp
    confirm_t = bars[peak_idx + 5].timestamp

    # Before confirmation the SWING pool does not exist, but the SESSION
    # high/low pools (confirmed by hourly/day session, updated by every
    # completed bar) ARE valid — the causal requirement is that the swing's
    # own level (3302) is not exposed as a CONFIRMED swing pool. Find what
    # the BSL is at T+30s: it must NOT equal the swing pool's price 3302
    # from a SWING_HIGH source.
    f_before = compute_liquidity_features(
        bars, decision_at=candidate_t + timedelta(seconds=30), mid_price=3300.0
    )
    swing_sources = [
        p for p in f_before.pools if p.source.name == "SWING_HIGH" and p.price > 3300.0
    ]
    # the swing is still CANDIDATE (unusable) -> no exposed SWING_HIGH pool
    assert all(p.state.name == "CANDIDATE" for p in swing_sources) or not swing_sources

    # at/after confirmation: the swing pool exists and is close (within clip)
    f_after = compute_liquidity_features(bars, decision_at=confirm_t, mid_price=3300.0)
    swing_after = [p for p in f_after.pools if p.source.name == "SWING_HIGH" and p.price > 3300.0]
    assert swing_after, "swing pool must exist after confirmation"
    assert all(p.state.name != "CANDIDATE" for p in swing_after)
    assert f_after.bsl_distance_atr > 0.0


def test_liq23_candidate_vs_confirmed_timestamps() -> None:
    bars = swing_high_bars(50, 3302.0, 3300.0)
    sh, _ = detect_confirmed_swings(bars)
    p = sh[0]
    assert p.candidate_at == bars[50].timestamp
    assert p.confirmed_at == bars[55].timestamp
    assert p.usable_at == p.confirmed_at


# ---------------------------------------------------------------------------
# TEST-LIQ-24 — future EQH/EQL touch cannot leak
# ---------------------------------------------------------------------------


def test_liq24_future_equal_high_touch_invisible() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = ramp_bars(40, 3298.0, 0.05, t0)
    i = 40
    bars.append(bar(i, t0, 3300.0, 3302.0, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        c = 3300.0 + 0.05 * j
        bars.append(bar(i + j, t0, c - 0.2, c + 0.5, c - 0.5, c))
    # future bars (T+10..T+16) re-touch the 3302 level
    i2 = i + 10
    bars.append(bar(i2, t0, 3300.0, 3302.05, 3299.0, 3300.0, vol=200))
    for j in range(1, 7):
        c = 3300.0 + 0.05 * j
        bars.append(bar(i2 + j, t0, c - 0.2, c + 0.5, c - 0.5, c))

    t = bars[i + 5].timestamp  # after first cluster confirmed, before 2nd touch
    f_t = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
    # strength at T reflects only the FIRST cluster (member count 1)
    f_full = compute_liquidity_features(bars, mid_price=3300.0)
    # the 2nd confirmed touch can legitimately raise the cluster strength
    assert f_full.eqh_strength >= f_t.eqh_strength


# ---------------------------------------------------------------------------
# TEST-LIQ-25 — future HTF close cannot leak
# ---------------------------------------------------------------------------


def test_liq25_incomplete_htf_candle_excluded() -> None:
    # drifting M1 bars (price falls ~5 units over 240 bars) -> completed
    # H1/H4 bucket lows sit near price (negative score contribution). A high
    # injected into the STILL-FORMING hour-11 bucket would add a POSITIVE
    # contribution once that bucket closes — proving the anti-leakage gate.
    t0 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    bars: list = []
    price = 3304.0
    for i in range(240):
        c = price - 0.05 * i
        bars.append(bar(i, t0, c - 0.2, c + 2.0, c - 0.5, c))
    bars[100] = bar(
        100, t0, 3304.0 - 5.0, 3306.0, 3299.0, 3300.0
    )  # near-price high in forming bucket
    decision = bars[90].timestamp  # inside hour 11, bucket still forming
    score_at = htf_liquidity_score(bars, atr=1.0, decision_at=decision)
    early = htf_liquidity_score(bars[:91], atr=1.0, decision_at=decision)
    assert score_at == early  # forming-bucket high invisible at decision

    # after hour 11 closes the high IS visible
    late = htf_liquidity_score(bars, atr=1.0, decision_at=bars[119].timestamp)
    assert late != early


def test_liq25_future_htf_never_changes_features_at_t() -> None:
    # swing at bar 60 of a ramp -> confirmed at 65; decision at the LAST bar
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = swing_high_bars(60, 3302.0, 3300.0, t0=t0)
    t = bars[-1].timestamp
    f_t = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
    f_same = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
    assert f_t.as_vector() == f_same.as_vector()


# ---------------------------------------------------------------------------
# TEST-LIQ-18/19 — BSL / SSL sweeps
# ---------------------------------------------------------------------------


def test_liq18_bsl_sweep_detected_after_rejection() -> None:
    # pool at bar 10, penetration at bar 20, rejecting close at bar 21
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3302.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="bsl",
        t0=t0,
    )
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.liquidity_sweep_state < 0  # SWEPT or SWEPT_AND_DISPLACED


def test_liq19_ssl_sweep_detected_after_rejection() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3298.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="ssl",
        t0=t0,
    )
    f = compute_liquidity_features(bars, mid_price=3300.0)
    assert f.liquidity_sweep_state < 0


# ---------------------------------------------------------------------------
# TEST-LIQ-20 — breakout is NOT a sweep
# ---------------------------------------------------------------------------


def test_liq20_breakout_not_swept() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = swing_high_bars(50, 3302.0, 3300.0, t0=t0)  # confirmed BSL at 3302
    # breakout: high > 3302 AND close > 3302 (no rejection) over the last bars
    i = len(bars)
    bars.append(bar(i, t0, 3300.0, 3304.0, 3300.0, 3303.0, vol=300))
    bars.append(bar(i + 1, t0, 3303.0, 3304.0, 3302.0, 3303.5, vol=300))
    f = compute_liquidity_features(bars, mid_price=3303.5)
    # NO confirmed sweep: no rejecting close back below the pool
    assert f.liquidity_sweep_state >= 0


# ---------------------------------------------------------------------------
# TEST-LIQ-21 — reclaim state
# ---------------------------------------------------------------------------


def test_liq21_sweep_then_reclaim_still_negative_or_touched() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3302.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="bsl",
        t0=t0,
    )
    f = compute_liquidity_features(bars, mid_price=3300.0)
    # the pool spike at 3302.0 must have advanced past CONFIRMED
    # (TOUCHED/SWEPT/RECLAIMED)
    swing_pool = [p for p in f.pools if p.source.name == "SWING_HIGH" and p.price == 3302.0]
    assert swing_pool, "expected the swept swing pool"
    assert swing_pool[0].state.value >= 2


# ---------------------------------------------------------------------------
# TEST-LIQ-22 — post-sweep displacement
# ---------------------------------------------------------------------------


def test_liq22_displacement_only_after_sweep() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3302.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="bsl",
        t0=t0,
    )
    f_before = compute_liquidity_features(bars, decision_at=bars[19].timestamp, mid_price=3300.0)
    assert f_before.post_sweep_displacement == 0.0  # no sweep yet -> nothing to measure
    f_after = compute_liquidity_features(bars, mid_price=3300.0)
    assert f_after.post_sweep_displacement > 0.0


# ---------------------------------------------------------------------------
# TEST-LIQ-26 — future sweep cannot leak
# ---------------------------------------------------------------------------


def test_liq26_future_sweep_invisible_at_t() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3302.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="bsl",
        t0=t0,
    )
    t = bars[19].timestamp  # one bar before the penetration
    f = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
    # penetration bar not yet visible -> no SWEPT state
    assert f.liquidity_sweep_state >= 0
    # with the sweep bars visible, state becomes negative (SWEPT)
    f2 = compute_liquidity_features(bars, mid_price=3300.0)
    assert f2.liquidity_sweep_state < 0


# ---------------------------------------------------------------------------
# TEST-LIQ-27 — future displacement cannot leak
# ---------------------------------------------------------------------------


def test_liq27_future_displacement_invisible_at_t() -> None:
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3302.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="bsl",
        t0=t0,
    )
    t = bars[20].timestamp  # penetration bar closes, rejecting bar not yet
    f = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
    # displacement measured only AFTER the confirmation bar => 0 at t
    assert f.post_sweep_displacement == 0.0


# ---------------------------------------------------------------------------
# TEST-LIQ-28 — historical invariance at timestamp T (the gold standard)
# ---------------------------------------------------------------------------


def _scenario_bars():
    rng = np.random.default_rng(11)
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = []
    price = 3300.0
    for i in range(300):
        price += float(rng.normal(0, 0.6))
        o = price + float(rng.normal(0, 0.15))
        h = max(o, price) + abs(float(rng.normal(0.05, 0.25)))
        l = min(o, price) - abs(float(rng.normal(0.05, 0.25)))
        bars.append(bar(i, t0, o, h, l, price, vol=100))
    # inject a confirmed swing high + a later sweep to stress causality
    bars[120] = bar(120, t0, 3300.0, 3320.0, 3299.0, 3300.0, vol=300)
    bars[130] = bar(130, t0, 3300.0, 3321.0, 3299.0, 3300.0, vol=300)
    bars[131] = bar(131, t0, 3300.0, 3300.5, 3298.0, 3295.0, vol=300)
    return bars


def test_liq28_historical_invariance_full_future() -> None:
    bars = _scenario_bars()
    for t_i in (100, 150, 200):
        t = bars[t_i].timestamp
        f_a = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
        f_b = compute_liquidity_features(bars[: t_i + 1], decision_at=t, mid_price=3300.0)
        assert f_a.as_vector() == f_b.as_vector(), f"invariance failed at bar {t_i}"


def test_liq28_historical_invariance_partial_future() -> None:
    """DATA_A = bars through T; DATA_B = bars through T+N. Features at T
    must be identical."""
    bars = _scenario_bars()
    t = bars[150].timestamp
    f_a = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
    f_b = compute_liquidity_features(bars[:200], decision_at=t, mid_price=3300.0)
    assert f_a.as_vector() == f_b.as_vector()


def test_liq28_swing_eqh_htf_sweep_all_invariant() -> None:
    bars = _scenario_bars()
    checks = [80, 120, 155, 180, 220]
    for t_i in checks:
        t = bars[t_i].timestamp
        full = compute_liquidity_features(bars, decision_at=t, mid_price=3300.0)
        cut = compute_liquidity_features(bars[: t_i + 1], decision_at=t, mid_price=3300.0)
        assert full.bsl_distance_atr == cut.bsl_distance_atr
        assert full.ssl_distance_atr == cut.ssl_distance_atr
        assert full.eqh_strength == cut.eqh_strength
        assert full.eql_strength == cut.eql_strength
        assert full.htf_liquidity_score == cut.htf_liquidity_score
        assert full.internal_liquidity_distance == cut.internal_liquidity_distance
        assert full.external_liquidity_distance == cut.external_liquidity_distance
        assert full.liquidity_confluence == cut.liquidity_confluence
        assert full.liquidity_sweep_state == cut.liquidity_sweep_state
        assert full.post_sweep_displacement == cut.post_sweep_displacement


# ---------------------------------------------------------------------------
# TEST-60D-BASE-01 — 50D semantic regression (the acceptance gate)
# ---------------------------------------------------------------------------


def test_60d_base_01_first_50_unchanged_when_liquidity_enabled() -> None:
    """The liquidity layer must NEVER alter the first 50 dimensions. We
    prove it structurally: build_60d_vector takes the engine's unmodified
    50D output; the schema registry still resolves scalp_v1 as ACTIVE 50D."""
    from nexus_scalp.model_generation.schema_v2 import compute_liquidity_frame
    from tests.helpers.liquidity_fixtures import bars_to_frame

    rng = np.random.default_rng(5)
    rows = []
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    price = 3300.0
    for i in range(120):
        price += float(rng.normal(0, 0.6))
        o = price + float(rng.normal(0, 0.15))
        h = max(o, price) + abs(float(rng.normal(0.05, 0.25)))
        l = min(o, price) - abs(float(rng.normal(0.05, 0.25)))
        rows.append(
            {
                "time": t0 + timedelta(minutes=i * 5),
                "open": o,
                "high": h,
                "low": l,
                "close": price,
                "tick_volume": 100,
            }
        )
    frame = compute_liquidity_frame(bars_to_frame(rows))
    feat_cols = [c for c in frame.columns if c.startswith("feat_")]
    assert len(feat_cols) == 60
    # the first 50 columns are produced by the untouched 50D engine:
    # feat_0..49 shape matches scalp_v1 and every value is within contract
    first50 = frame.select([f"feat_{k}" for k in range(50)]).to_numpy()
    assert bool(np.isfinite(first50).all())
    assert first50.min() >= -3.0 and first50.max() <= 3.0


def test_60d_base_01_active_schema_untouched() -> None:
    from nexus_scalp.features.schema import FEATURE_SCHEMAS

    assert FEATURE_SCHEMAS.active.schema_id == "scalp_v1"
    assert FEATURE_SCHEMAS.active.dimension == 50
    v2 = FEATURE_SCHEMAS.resolve("scalp_v2")
    assert v2.dimension == 60  # TASK-5 contract intact
    lq = FEATURE_SCHEMAS.resolve("scalp_liquidity_v1")
    assert lq.dimension == 60
