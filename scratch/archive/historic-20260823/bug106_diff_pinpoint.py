"""Equivalence probe v2 — compare feature-by-feature, then pinpoint diff sources."""
import sys
from collections import Counter
from datetime import UTC, datetime

import polars as pl

sys.path.insert(0, "src")
from nexus_scalp.domain.models import TickData
from nexus_scalp.features.liquidity_engine import (
    compute_liquidity_features,
    liquidity_confluence,
)
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.model_generation.schema_v2_incremental import (
    IncrementalLiquidityState,
    compute_70d_frame_fast,
)

df = pl.read_parquet("data/raw/XAUUSD_M5.parquet").head(400)
canon = compute_70d_frame(df, news_frame=None)
fast = compute_70d_frame_fast(df, news_frame=None)
feat_cols = [c for c in canon.columns if c.startswith("feat_")]
diff_rows: dict[int, list[str]] = {}
for c in feat_cols:
    a = canon[c].to_list()
    b = fast[c].to_list()
    for i, (x, y) in enumerate(zip(a, b, strict=True)):
        if x != y:
            diff_rows.setdefault(i, []).append(c)
print("diff rows:", sorted(diff_rows)[:10])
print("diff counts per feature:", Counter(c for cs in diff_rows.values() for c in cs))

# for the first diff row, compare the FULL canonical LiquidityFeatures vs fast assembly
if diff_rows:
    row_i = sorted(diff_rows)[0]
    raw = df.sort("time")
    times = []
    for row in raw.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            continue
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)
    all_bars = []
    for j in range(raw.height):
        bj = raw.row(j, named=True)
        all_bars.append(BarData(symbol="XAUUSD", timeframe="M1", timestamp=times[j],
                                open=float(bj["open"]), high=float(bj["high"]),
                                low=float(bj["low"]), close=float(bj["close"]),
                                tick_volume=int(bj.get("tick_volume", 0) or 0), is_complete=True))
    ts = times[row_i]
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=float(raw.row(row_i, named=True)["close"]),
                    ask=float(raw.row(row_i, named=True)["close"]) + 0.20, volume=0)
    window = all_bars[max(0, row_i - 54): row_i + 1]
    fv = engine.compute_from_bars(window, tick)
    atr = fv.atr_m1
    print(f"\nrow {row_i} ts={ts} atr={atr} diff_feats={diff_rows[row_i]}")
    canon_lf = compute_liquidity_features(all_bars[: row_i + 1], decision_at=ts,
                                          mid_price=float(raw.row(row_i, named=True)["close"]), atr=atr)
    lst = IncrementalLiquidityState(all_bars)
    vis = lst.pools_visible_at(ts, atr)
    sess = lst.session_pools_at(ts)
    daily = lst.daily_pools_at(ts)
    pools_all = vis + sess + daily
    usable = [p for p in pools_all if p.usable_at <= ts and p.state != 3]  # CANDIDATE
    print("canon pools:", len(canon_lf.pools), "fast usable:", len(usable))
    print("canon sources:", Counter(p.source.value for p in canon_lf.pools))
    print("fast  sources:", Counter(p.source.value for p in usable))
    print("canon vector:", [round(v, 4) for v in canon_lf.as_vector()])
    safe_atr = max(atr, 0.2)
    print("fast conf:", liquidity_confluence(usable, decision_at=ts, atr=safe_atr))
    # compare pool states (state value + touch_count) between canon and fast
    c_states = [(round(p.price,2), p.state.value if hasattr(p.state,'value') else p.state, p.touch_count, p.source.value, str(p.usable_at)) for p in canon_lf.pools]
    f_states = [(round(p.price,2), p.state.value if hasattr(p.state,'value') else p.state, p.touch_count, p.source.value, str(p.usable_at)) for p in usable]
    print("canon states:", c_states)
    print("fast  states:", f_states)
    print("identical:", c_states == f_states)
    # direct: confluence on the CANON pool list (what canon actually fed)
    print("conf on canon pools:", liquidity_confluence(canon_lf.pools, decision_at=ts, atr=safe_atr))
    print("conf on fast pools:", liquidity_confluence(usable, decision_at=ts, atr=safe_atr))
    print("conf on canon sorted-by-price:", liquidity_confluence(sorted(canon_lf.pools, key=lambda p: p.price), decision_at=ts, atr=safe_atr))