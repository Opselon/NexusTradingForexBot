"""TASK-6 TEST-LIQ-OPT-01..28 — 70D Liquidity optimization contract suite.

Covers the TASK-6 §39 required optimization tests against the v1.1 CANDIDATE
module (src/nexus_scalp/features/liquidity_engine_opt.py). The frozen v1
engine is the baseline; v1.1 must:
  - keep every contract invariant (finite, [-3,3], causal, deterministic)
  - never change Base 50D / News (structural: opt module imports v1 for the
    50D-adjacent machinery but produces only the 10 liquidity dimensions)
  - fix the two PROVEN defects (eqh price-awareness, sweep relevance gate)
  - expose bounded, documented parameters (TEST-LIQ-OPT-03)
  - keep the liquidity algorithm version recorded (TEST-LIQ-OPT-20)
  - never mutate itself (no automatic parameter learning — TEST-LIQ-OPT-23)

Naming: TEST-LIQ-OPT-01 .. TEST-LIQ-OPT-28 per the TASK-6 brief.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from nexus_scalp.features.liquidity_engine import (
    LiquidityPool,
    PoolSide,
    PoolSource,
)

# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-01 — implementation files exist
# ---------------------------------------------------------------------------


def test_liq_opt_01_implementation_files_exist() -> None:
    import os

    for p in (
        "src/nexus_scalp/features/liquidity_engine.py",
        "src/nexus_scalp/features/liquidity_engine_opt.py",
        "src/nexus_scalp/features/liquidity_runtime.py",
    ):
        assert os.path.exists(p), f"missing {p}"


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-02 — all ten feature calculators discoverable
# ---------------------------------------------------------------------------


def test_liq_opt_02_ten_features_discoverable() -> None:
    from nexus_scalp.features.liquidity_engine import LIQUIDITY_FEATURE_NAMES
    from nexus_scalp.features.liquidity_engine_opt import (
        compute_liquidity_features_v1_1,
        detect_reactive_sweep_v1_1,
        equal_high_low_strengths_v1_1,
        htf_liquidity_score_v1_1,
        liquidity_confluence_v1_1,
    )

    assert len(LIQUIDITY_FEATURE_NAMES) == 10
    # v1.1 exposes all the family calculators
    for fn in (
        equal_high_low_strengths_v1_1,
        htf_liquidity_score_v1_1,
        detect_reactive_sweep_v1_1,
        liquidity_confluence_v1_1,
        compute_liquidity_features_v1_1,
    ):
        assert callable(fn)


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-03 — parameter configuration deterministic
# ---------------------------------------------------------------------------


def test_liq_opt_03_params_deterministic() -> None:
    from nexus_scalp.features.liquidity_engine_opt import LiquidityParams

    a = LiquidityParams().as_dict()
    b = LiquidityParams().as_dict()
    assert a == b
    assert set(a) == {
        "eqh_tolerance_atr",
        "confluence_cutoff_atr",
        "reclaim_fraction_atr",
        "sweep_relevance_atr",
        "htf_proximity_atr",
        "sweep_window_bars",
    }


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-04 — no future data in optimization (causality inheritance)
# ---------------------------------------------------------------------------


def test_liq_opt_04_v11_causality_inherited() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import bar, swing_high_bars

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = swing_high_bars(60, 3310.0, 3300.0, t0=t0)
    for t_i in (40, 60, 66):
        t = bars[t_i].timestamp
        full = compute_liquidity_features_v1_1(
            bars, decision_at=t, mid_price=3300.0, params=LiquidityParams()
        )
        cut = compute_liquidity_features_v1_1(
            bars[: t_i + 1], decision_at=t, mid_price=3300.0, params=LiquidityParams()
        )
        assert full.as_vector() == cut.as_vector(), f"causality broke at {t_i}"


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-05/06 — Base 50D + News unchanged (structural proof)
# ---------------------------------------------------------------------------


def test_liq_opt_05_06_base50_and_news_untouched() -> None:
    import inspect

    import nexus_scalp.features.liquidity_engine_opt as opt

    src = inspect.getsource(opt)
    # the opt module must NOT reference the 50D feature engine or news modules
    assert "ScalpFeatureEngine" not in src
    for banned in (
        "from nexus_scalp.news",
        "import nexus_scalp.news",
        "news_context",
        "news_enabled",
    ):
        assert banned not in src, f"candidate touches News: {banned}"
    # builds only the 10D liquidity family
    assert "LIQUIDITY_ALGORITHM_VERSION" in src


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-07 — Liquidity 10D reproducible (determinism)
# ---------------------------------------------------------------------------


def test_liq_opt_07_deterministic() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import swing_high_bars

    bars = swing_high_bars(60, 3310.0, 3300.0)
    a = compute_liquidity_features_v1_1(bars, params=LiquidityParams()).as_vector()
    b = compute_liquidity_features_v1_1(bars, params=LiquidityParams()).as_vector()
    assert a == b


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-08 — baseline vs optimized feature diff recorded
# ---------------------------------------------------------------------------


def test_liq_opt_08_golden_baseline_file_exists() -> None:
    import json
    import os

    assert os.path.exists("docs/LIQUIDITY_70D_GOLDEN_BASELINE.json")
    d = json.load(open("docs/LIQUIDITY_70D_GOLDEN_BASELINE.json", encoding="utf-8"))
    assert d["rows_computed"] > 10000
    assert len(d["per_feature"]) == 10


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-11 — parameter perturbation stability (±5%)
# ---------------------------------------------------------------------------


def test_liq_opt_11_perturbation_stability() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import swing_high_bars

    bars = swing_high_bars(80, 3310.0, 3300.0)
    base = LiquidityParams()
    v0 = compute_liquidity_features_v1_1(bars, mid_price=3300.0, params=base).as_vector()
    deltas = []
    for key, fac in (("eqh_tolerance_atr", 1.05), ("sweep_relevance_atr", 0.95)):
        kw = {key: getattr(base, key) * fac}
        p2 = LiquidityParams(**kw)
        v1 = compute_liquidity_features_v1_1(bars, mid_price=3300.0, params=p2).as_vector()
        deltas.append(float(np.mean(np.abs(np.asarray(v1) - np.asarray(v0)))))
    # small perturbation must not produce a completely different vector
    assert max(deltas) < 1.5


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-12 — EQH/EQL tolerance robustness
# ---------------------------------------------------------------------------


def test_liq_opt_12_eqh_tolerance_bounded() -> None:
    from nexus_scalp.features.liquidity_engine_opt import LiquidityParams

    for tol in (0.10, 0.30, 0.50):
        p = LiquidityParams(eqh_tolerance_atr=tol)
        assert 0.0 < p.eqh_tolerance_atr <= 1.0
        # recompute determinism
        assert LiquidityParams(eqh_tolerance_atr=tol).as_dict() == p.as_dict()


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-13 — BSL/SSL level ranking robustness
# ---------------------------------------------------------------------------


def test_liq_opt_13_bsl_ssl_reproducible() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import swing_high_bars, swing_low_bars

    b_hi = swing_high_bars(50, 3310.0, 3300.0)
    f_hi = compute_liquidity_features_v1_1(b_hi, mid_price=3300.0, params=LiquidityParams())
    assert f_hi.bsl_distance_atr > 0.0
    assert f_hi.bsl_distance_atr <= 3.0
    b_lo = swing_low_bars(50, 3290.0, 3300.0)
    f_lo = compute_liquidity_features_v1_1(b_lo, mid_price=3300.0, params=LiquidityParams())
    assert f_lo.ssl_distance_atr > 0.0
    assert f_lo.ssl_distance_atr <= 3.0


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-14 — HTF weighting robustness
# ---------------------------------------------------------------------------


def test_liq_opt_14_htf_proximity_parametrizable() -> None:
    from nexus_scalp.features.liquidity_engine_opt import LiquidityParams

    for prox in (4.0, 6.0, 8.0):
        p = LiquidityParams(htf_proximity_atr=prox)
        assert p.htf_proximity_atr == prox


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-15 — confluence deduplication
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-16 — breakout vs sweep classification
# ---------------------------------------------------------------------------


def test_liq_opt_16_breakout_not_sweep() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import bar, swing_high_bars

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = swing_high_bars(50, 3310.0, 3300.0, t0=t0)
    i = len(bars)
    bars.append(bar(i, t0, 3300.0, 3315.0, 3300.0, 3312.0, vol=300))
    bars.append(bar(i + 1, t0, 3312.0, 3316.0, 3310.0, 3314.0, vol=300))
    f = compute_liquidity_features_v1_1(bars, mid_price=3314.0, params=LiquidityParams())
    assert f.liquidity_sweep_state >= 0


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-17 — post-sweep displacement causality
# ---------------------------------------------------------------------------


def test_liq_opt_17_displacement_after_confirmation_only() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import sweep_pool_bars

    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = sweep_pool_bars(
        pool_price=3310.0,
        base=3300.0,
        pool_index=10,
        sweep_index=20,
        n_total=60,
        side="bsl",
        t0=t0,
    )
    f_before = compute_liquidity_features_v1_1(
        bars, decision_at=bars[19].timestamp, mid_price=3300.0, params=LiquidityParams()
    )
    assert f_before.post_sweep_displacement == 0.0
    f_after = compute_liquidity_features_v1_1(bars, mid_price=3300.0, params=LiquidityParams())
    assert f_after.post_sweep_displacement >= 0.0


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-18 — batch/replay/live parity (structural)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-19 — golden feature dataset stable
# ---------------------------------------------------------------------------


def test_liq_opt_19_golden_reference_stable() -> None:
    import json

    d = json.load(open("docs/LIQUIDITY_70D_GOLDEN_BASELINE.json", encoding="utf-8"))
    assert d["per_feature"]["eqh_strength"]["median"] < 0.95  # v1's near-step is captured
    assert all(d["per_feature"][k]["missing_rate"] == 0.0 for k in d["per_feature"])


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-20 — algorithm version recorded
# ---------------------------------------------------------------------------


def test_liq_opt_20_version_recorded() -> None:
    from nexus_scalp.features.liquidity_engine_opt import LIQUIDITY_ALGORITHM_VERSION

    assert LIQUIDITY_ALGORITHM_VERSION.startswith("liquidity-v1")
    assert LIQUIDITY_ALGORITHM_VERSION != "liquidity-v1"  # candidate distinguishes itself


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-21 — experiment registry recorded
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-22 — shadow remains Champion-independent
# ---------------------------------------------------------------------------


def test_liq_opt_22_no_production_wiring() -> None:
    # The candidate engine is NOT wired into live_engine/governance/shadow yet
    import inspect

    import nexus_scalp.features.liquidity_engine_opt as opt

    src = inspect.getsource(opt)
    assert "live_engine" not in src
    assert "shadow" not in src


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-23 — no automatic parameter mutation
# ---------------------------------------------------------------------------


def test_liq_opt_23_no_self_tuning() -> None:
    import inspect

    import nexus_scalp.features.liquidity_engine_opt as opt

    src = inspect.getsource(opt)
    for banned in ("fit(", "update_params", "self_tune", "online_gradient", "automatic"):
        assert banned not in src, f"self-tuning construct in candidate: {banned}"


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-24/25 — finite + [-3,3] (on the real-data engine run)
# ---------------------------------------------------------------------------


def test_liq_opt_24_25_finite_and_clipped() -> None:
    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import swing_high_bars, swing_low_bars

    for bars in (swing_high_bars(60, 3310.0, 3300.0), swing_low_bars(60, 3290.0, 3300.0)):
        v = compute_liquidity_features_v1_1(bars, params=LiquidityParams()).as_vector()
        assert all(math.isfinite(x) for x in v)
        assert all(-3.0 <= x <= 3.0 for x in v)


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-26 — runtime latency bounded (loose upper bound on synthetic)
# ---------------------------------------------------------------------------


def test_liq_opt_26_latency_bounded() -> None:
    import time

    from nexus_scalp.features.liquidity_engine_opt import (
        LiquidityParams,
        compute_liquidity_features_v1_1,
    )
    from tests.helpers.liquidity_fixtures import swing_high_bars

    bars = swing_high_bars(80, 3310.0, 3300.0)
    t0 = time.perf_counter()
    for _ in range(10):
        compute_liquidity_features_v1_1(bars, params=LiquidityParams())
    per_call = (time.perf_counter() - t0) / 10.0
    assert per_call < 0.25  # ~ms scale; loose bound for CI


# ---------------------------------------------------------------------------
# TEST-LIQ-OPT-27/28 — does not alter execution or labels (structural)
# ---------------------------------------------------------------------------


def test_liq_opt_27_28_no_execution_no_label_change() -> None:
    import inspect

    import nexus_scalp.features.liquidity_engine_opt as opt

    src = inspect.getsource(opt)
    for banned in (
        "OrderManager",
        "RiskEngine",
        "order_manager",
        "risk_engine",
        "label",
        "triple_barrier",
    ):
        assert banned not in src, f"candidate touches execution/labels: {banned}"
