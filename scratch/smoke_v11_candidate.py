"""TASK-6: v1.1 candidate smoke + causality inheritance check (TRAIN-only evidence)."""
import sys

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

from datetime import UTC, datetime, timedelta

import numpy as np

from nexus_scalp.features.liquidity_engine import (
    LiquidityPool,
    PoolSide,
    PoolSource,
)
from nexus_scalp.features.liquidity_engine_opt import (
    LIQUIDITY_ALGORITHM_VERSION,
    LiquidityParams,
    equal_high_low_strengths_v1_1,
)
from nexus_scalp.features.liquidity_engine_opt import (
    compute_liquidity_features_v1_1 as v11,
)
from tests.helpers.liquidity_fixtures import (
    steady_bars,
    swing_high_bars,
)

print("version:", LIQUIDITY_ALGORITHM_VERSION)

# 1) eqh price-awareness fix proof
t0 = datetime(2026, 8, 1, tzinfo=UTC)
sh = [
    LiquidityPool(100.0, PoolSide.BSL, PoolSource.SWING_HIGH, 1, 1.0, t0, t0 + timedelta(minutes=5)),
    LiquidityPool(105.0, PoolSide.BSL, PoolSource.SWING_HIGH, 1, 1.0, t0, t0 + timedelta(minutes=55)),
]
e_far = equal_high_low_strengths_v1_1(sh, [], atr=1.0, mid_price=50.0)
e_near = equal_high_low_strengths_v1_1(sh, [], atr=1.0, mid_price=103.0)
print("eqh v1.1 mid=50 (far) :", e_far, " -> expect LOW")
print("eqh v1.1 mid=103 (near):", e_near, " -> expect HIGH")

# 2) sweep relevance gate proof
from nexus_scalp.features.liquidity_engine import detect_reactive_sweep as v1_sweep
from nexus_scalp.features.liquidity_engine_opt import detect_reactive_sweep_v1_1

bars = steady_bars(30, price=3300.0, t0=t0)
far_pool = LiquidityPool(3500.0, PoolSide.BSL, PoolSource.SWING_HIGH, 1, 1.0, t0, t0)
st_v1, _ = v1_sweep([far_pool], bars, atr=1.0, decision_at=bars[-1].timestamp)
st_v11, _ = detect_reactive_sweep_v1_1([far_pool], bars, atr=1.0, decision_at=bars[-1].timestamp, relevance_atr=2.0)
print("far pool v1 state =", st_v1, "(bug: APPROACHING)", "| v1.1 state =", st_v11, "(fixed: NO_RELEVANT)")

# 3) causality inheritance: full vs prefix equivalence at frozen timestamps
bars2 = swing_high_bars(60, 3310.0, 3300.0, t0=t0)
for t_i in (40, 60, 66):
    t = bars2[t_i].timestamp
    f_full = v11(bars2, decision_at=t, mid_price=3300.0)
    f_cut = v11(bars2[: t_i + 1], decision_at=t, mid_price=3300.0)
    same = f_full.as_vector() == f_cut.as_vector()
    print(f"causality @bar{t_i}: full==cut {same}")

# 4) full vector sanity on real bars
from scratch.liq_opt_lab import compute_vectors, load_bars

real = load_bars(n=3000)
A, _ = compute_vectors(real, lambda w, **kw: v11(w, **kw, params=LiquidityParams()))
print("v1.1 real rows:", A.shape, "finite:", np.isfinite(A).all(), "range ok:", A.min() >= -3.0 and A.max() <= 3.0)
print("v1.1 dist stats:")
names = ["bsl", "ssl", "eqh", "eql", "htf", "internal", "external", "confluence", "sweep", "disp"]
for k in range(10):
    col = A[:, k]
    print(f"  {names[k]:<12} mean={col.mean():.3f} med={np.median(col):.3f} sat%={(np.abs(col)>=2.9999).mean()*100:.1f} uniq={len(np.unique(np.round(col,5)))}")