"""Build the 4000-bar parity golden row ONCE (slow O(n^2) frame build)."""
import sys, json, time; sys.path.insert(0, '.')
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import polars as pl
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine

t0 = datetime(2026, 8, 1, tzinfo=UTC)
import random
rng = random.Random(7)
bars = []
for i in range(4000):
    o = 3300.0 + i * 0.1
    c = o + rng.uniform(-0.3, 0.3)
    bars.append(SimpleNamespace(symbol='XAUUSD', timeframe='M1',
        timestamp=t0 + timedelta(minutes=i), open=o,
        high=max(o, c) + rng.uniform(0.1, 0.6),
        low=min(o, c) - rng.uniform(0.1, 0.6), close=c,
        tick_volume=100, is_complete=True))
rows = [{'time': b.timestamp, 'open': b.open, 'high': b.high, 'low': b.low,
         'close': b.close, 'tick_volume': b.tick_volume} for b in bars]
tm = time.perf_counter()
frame = compute_70d_frame(pl.DataFrame(rows))
print('frame built %.0fs rows=%d' % (time.perf_counter() - tm, frame.height), file=sys.stderr)
last = frame.tail(1).row(0, named=True)
ds_liq = [float(last[f'feat_{i}']) for i in range(60, 70)]

bd = [BarData(symbol='XAUUSD', timeframe='M1', timestamp=b.timestamp, open=b.open,
              high=b.high, low=b.low, close=b.close, tick_volume=b.tick_volume,
              is_complete=True) for b in bars]
close = bars[-1].close
tick = SimpleNamespace(timestamp=bars[-1].timestamp, bid=close, ask=close + 0.20, volume=100)
fv = ScalpFeatureEngine(symbol='XAUUSD').compute_from_bars(bd, tick)
gov = LiquidityGovernor(enabled=True)
gov.compute_from_engine(bars=bd, mid_price=float(close), atr=float(fv.atr_m1),
                        decision_at=bars[-1].timestamp)
live_liq = list(gov.last_snapshot.features)
data = {
    "n_bars": 4000, "seed": 7,
    "dataset_liquidity_60_69": [round(v, 10) for v in ds_liq],
    "live_liquidity_60_69": [round(v, 10) for v in live_liq],
    "exact_match": all(abs(a - b) <= 1e-12 for a, b in zip(ds_liq, live_liq)),
}
out = r"tests/golden/70d_liquidity_parity/deep4000_golden.json"
import pathlib
pathlib.Path(out).write_text(json.dumps(data, indent=2), encoding="utf-8")
print("WROTE", out, "exact:", data["exact_match"])
