"""MLPWR-06-02 diagnostic: why does the probe's LIVE path see htf_h1_momentum=3.0
while the TRAINING dataset row (same window) sees 0.0? Both call the SAME
ScalpFeatureEngine — the difference must be the INPUT HISTORY, not the math.
Hypothesis: compute_from_bars in the training builder passes the same 55-bar
window, but my probe's live-style call passed all 240 bars (full history) =>
H1 aggregation needs >=2 completed H1 buckets = 120+ minutes. The TRAINING
builder slices window=55 bars -> H1 bucket count 1 -> 0.0.
Check both sides exactly as each path calls it."""
from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, "src")
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine


def mkbars(n, t0, base=3300.0, step=0.1, seed=7):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        o = base + i * step
        c = o + rng.uniform(-0.3, 0.3)
        h = max(o, c) + rng.uniform(0.1, 0.6)
        l = min(o, c) - rng.uniform(0.1, 0.6)
        out.append(SimpleNamespace(symbol="XAUUSD", timeframe="M1", timestamp=t0 + timedelta(minutes=i),
                                   open=o, high=h, low=l, close=c, tick_volume=100, is_complete=True))
    return out


def to_bd(bars):
    return [BarData(symbol=b.symbol, timeframe="M1", timestamp=b.timestamp, open=b.open, high=b.high,
                    low=b.low, close=b.close, tick_volume=b.tick_volume, is_complete=True) for b in bars]


t0 = datetime(2026, 9, 2, 0, 0, tzinfo=UTC)
bars = mkbars(240, t0)
bd = to_bd(bars)
close = bars[-1].close
tick = SimpleNamespace(timestamp=bars[-1].timestamp, bid=close, ask=close + 0.20, volume=100)

fv_all = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bd, tick)
v_all = fv_all.to_tensor_input()
fv_55 = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bd[-55:], tick)
v_55 = fv_55.to_tensor_input()
print("LIVE-style (full 240-bar history): feat_41 =", v_all[41], "feat_42 =", v_all[42])
print("55-bar window (train convention):  feat_41 =", v_55[41], "feat_42 =", v_55[42])
