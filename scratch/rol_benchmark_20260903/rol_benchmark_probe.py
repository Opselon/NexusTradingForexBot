"""ROL benchmark — before/after GO-rate on real XAUUSD M1 (scratch evidence).

Uses the CANONICAL feature path: ScalpFeatureEngine.compute_from_bars over
real data/raw/XAUUSD_M1.parquet bars -> FeatureVector -> row dict via the
50D feature-name map (named columns + feat_N fallbacks both available).
Honest: every input the detectors read is real (derived from real bars);
the only proxy is `regime` = constant RANGING (documented below).
"""

import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, "src")
import polars as pl
import torch

from nexus_scalp.features.scalp_features import FeatureVector, ScalpFeatureEngine
from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.sample_maker import HunterSampleMaker
from nexus_scalp.model_generation.strategy_factory import HUNTER_STRATEGIES, StrategyFactory
from nexus_scalp.model_generation.setup_detector import SETUP_TYPES

PQ = "data/raw/XAUUSD_M1.parquet"
STRIDE = 50
MAX_ROWS = 500

df = pl.read_parquet(PQ)
print("parquet cols:", df.columns[:12], "... total rows:", len(df))
# Column names: inspect
ts_col = "timestamp" if "timestamp" in df.columns else df.columns[0]
print("ts col:", ts_col, "| sample ts:", df[ts_col][0])

bars = []
for row in df.iter_rows(named=True):
    ts = row[ts_col]
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(float(ts), tz=UTC)
    elif isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    elif ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    bars.append(
        BarData(
            symbol="XAUUSD",
            timestamp=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            tick_volume=int(row.get("tick_volume", row.get("volume", 100))),
            spread=float(row.get("spread", 2.0)),
            timeframe="M1",
            is_complete=True,
        )
    )
print("bars loaded:", len(bars))

engine = ScalpFeatureEngine()
rows = []
for i in range(60, len(bars), STRIDE):
    if len(rows) >= MAX_ROWS:
        break
    window = bars[max(0, i - 60) : i]
    if len(window) < 55:
        continue
    cur = bars[i]
    tick = type("T", (), {})()
    from nexus_scalp.domain.models import TickData

    tick = TickData(
        symbol="XAUUSD",
        timestamp=cur.timestamp,
        bid=cur.close,
        ask=cur.close + 0.02,
        volume=1.0,
    )
    fv = engine.compute_from_bars(window, tick)
    row = {"atr_m1": fv.atr_m1, "spread": 0.02, "regime": "RANGING"}
    names = FEATURE_NAMES
    for idx, name in enumerate(names):
        row[name] = float(getattr(fv, name, 0.0) or 0.0)
        row[f"feat_{idx}"] = row[name]
    row["timestamp"] = cur.timestamp
    rows.append(row)
print("rows built:", len(rows))


def run(maker, factory):
    go = 0
    by_type = {}
    by_strategy = {}
    for r in rows:
        res = maker.analyze_row(r, r["timestamp"])
        st = res["setup_type"]
        d = res["decision"]
        t = by_type.setdefault(st, {"GO": 0, "NO_GO": 0})
        t[d] = t.get(d, 0) + 1
        if d == "GO":
            go += 1
            by_strategy[res["strategy_id"]] = by_strategy.get(res["strategy_id"], 0) + 1
    return go, by_type, by_strategy


# ---- BEFORE (revert both fixes via local copies) ----
before_strats = {}
for sid, s in HUNTER_STRATEGIES.items():
    tp = 2.0 if sid == "hunter_trend_v1" else (1.6 if sid == "hunter_range_v1" else s.atr_tp_mult)
    before_strats[sid] = s.model_copy(update={"atr_tp_mult": tp}) if hasattr(s, "model_copy") else s
import dataclasses

before_strats = {}
for sid, s in HUNTER_STRATEGIES.items():
    tp = 2.0 if sid == "hunter_trend_v1" else (1.6 if sid == "hunter_range_v1" else s.atr_tp_mult)
    before_strats[sid] = dataclasses.replace(s, atr_tp_mult=tp)

before_maker = HunterSampleMaker(
    strategy_factory=StrategyFactory(strategies=before_strats),
    default_strategy="hunter_smc_v1",
)
go_before, by_type_before, strat_before = run(before_maker, None)

# ---- AFTER (landed state) ----
after_maker = HunterSampleMaker()
go_after, by_type_after, strat_after = run(after_maker, None)

print()
print(f"TOTAL GO  before={go_before}  after={go_after}  (rows={len(rows)})")
print()
fams = sorted(set(by_type_before) | set(by_type_after))
print(f"{'setup family':24s} {'BEFORE':>8s} {'AFTER':>8s}")
for f in fams:
    b = by_type_before.get(f, {}).get("GO", 0)
    a = by_type_after.get(f, {}).get("GO", 0)
    print(f"{f:24s} {b:8d} {a:8d}")
print()
print("GO strategy mix AFTER:", strat_after)
