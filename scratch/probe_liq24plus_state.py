"""Probe: test_liq24/25/22/26/21 internal state — and the step function in EQH.

TASK-06-70D-LIQUIDITY-OPTIMIZATION forensics. Read-only.
"""
import sys
from datetime import UTC, datetime

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")
sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\tests")

from nexus_scalp.features import liquidity_engine as le
from tests.helpers.liquidity_fixtures import bar, steady_bars

t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def vec(label, bars, decision_at=None, mid=None):
    f = le.compute_liquidity_features(bars, decision_at=decision_at, mid_price=mid)
    print("==", label, "vec=", [f"{v:.3f}" for v in f.as_vector()])
    return f


# ---- test_liq24: two equal-high clusters? ----
t0x = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
bx = steady_bars(40, price=3300.0, t0=t0x)  # note: flat bars -> every bar is a fractal pivot!
bx.append(bar(40, t0x, 3300.0, 3310.0, 3299.0, 3300.0, vol=200))
for j in range(1, 7):
    bx.append(bar(40 + j, t0x, 3300.0, 3300.5, 3299.5, 3300.0))
i2 = 55
bx.append(bar(i2, t0x, 3300.0, 3310.05, 3299.0, 3300.0, vol=200))
for j in range(1, 7):
    bx.append(bar(i2 + j, t0x, 3300.0, 3300.5, 3299.5, 3300.0))
print("test_liq24 bars len", len(bx))
ft = le.compute_liquidity_features(bx, decision_at=bx[54].timestamp, mid_price=3300.0)
ff = le.compute_liquidity_features(bx, mid_price=3300.0)
print("EQH f_t (first cluster)=", ft.eqh_strength, " f_full =", ff.eqh_strength)
print("EQH raw counts?")