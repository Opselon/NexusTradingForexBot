"""MLPWR-06-02 root-cause probe: HTF feature history-window asymmetry.

compute_from_bars slices the LAST 55 bars for base features (line 526) but
aggregates HTF (feat_40..43) from the FULL completed_bars list passed in
(line 741-744). Callers pass different history depths:
  - LIVE  (_process_tick_pipeline:3557): aggregator completed_bars, up to 4000
  - TRAIN (schema_v2.compute_70d_frame:601): window = all_bars[i-54:i+1] = 55 bars
Result: the SAME feature engine produces different feat_41/42 values for the
same market state depending on caller history depth. Quantify by bar depth.
"""
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

print("depth | h4 | h1_mom | m30 | m15 | feat41 | feat42")
for depth in (55, 60, 120, 240, 400, 4000):
    sub = bd[-depth:] if depth <= len(bd) else bd
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(sub, tick)
    v = fv.to_tensor_input()
    print(f"{depth:>5} | {fv.htf_h4_trend:>4.1f} | {fv.htf_h1_momentum:6.3f} | "
          f"{fv.htf_m30_structure:>4.1f} | {fv.htf_m15_confirmation:>4.1f} | {v[41]:>6.3f} | {v[42]:>4.1f}")
