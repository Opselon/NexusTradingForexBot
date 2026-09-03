"""Silent-exception micro-bench for SetupDetector.detect() — measures per-row cost and
exercises the swallow path with a poisoned row (simulated KeyError per detector) WITHOUT
touching src. Run via an inline wrapper that temporarily monkeypatches one _detect_*
method to raise KeyError('feat_ob_valid_bos') — the missing-feature class the mission
asks about — then measures detect() over all 900 real replay rows and compares
output-row deltas vs the clean run (does a raise corrupt the row's setup list?)."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from nexus_scalp.model_generation.setup_detector import SetupDetector
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.domain.models import TickData
from nexus_scalp.market_data.bar_aggregator import BarData

WT = Path(__file__).resolve().parent.parent
BARS_PATH = WT / "scratch" / "data" / "forensic_50d_live_T1_bars.json"
WARMUP = 60


def build_rows():
    bars = json.loads(BARS_PATH.read_text())["bars"][:2000]
    bar_objs = [
        BarData(symbol="XAUUSD", timeframe="M1", timestamp=datetime.fromisoformat(b["time"]),
                open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                tick_volume=b["tick_volume"], is_complete=True)
        for b in bars
    ]
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    rows = []
    for i in range(WARMUP, len(bars)):
        tick = TickData(symbol="XAUUSD", timestamp=bar_objs[i].timestamp,
                        bid=bars[i]["close"], ask=bars[i]["close"] + 0.20)
        fv = engine.compute_from_bars(bar_objs[:i], tick)
        t = fv.to_tensor_input()
        row = {f"feat_{j}": float(v) for j, v in enumerate(t)}
        row.update(close=bars[i]["close"], high=bars[i]["high"], low=bars[i]["low"],
                   open=bars[i]["open"], spread=0.20, atr_m1=fv.atr_m1,
                   session_london=1.0 if 7 <= bar_objs[i].timestamp.hour < 16 else 0.0,
                   session_ny=1.0 if 13 <= bar_objs[i].timestamp.hour < 21 else 0.0)
        rows.append(row)
    return rows


def run(rows, label):
    det = SetupDetector()
    out = []
    t0 = time.perf_counter()
    for k, row in enumerate(rows):
        ts = datetime(2026, 8, 18, 1, 14)  # constant ts fine for cost; IDs not compared
        dets = det.detect(row, timestamp=ts)
        out.append(tuple(sorted((d.setup_type, round(d.quality, 6)) for d in dets)))
    dt = (time.perf_counter() - t0) * 1000.0
    print(f"{label}: total={dt:.1f}ms rows={len(rows)} per-row={dt/len(rows):.3f}ms")
    return out


if __name__ == "__main__":
    rows = build_rows()
    print(f"rows={len(rows)} (real feature records, 50D via to_tensor_input)")

    baseline = run(rows, "clean")

    # (a) poison ONE detector to simulate a missing-feature KeyError per row
    orig = SetupDetector._detect_bos
    def boom(self, row, ts):
        raise KeyError("feat_ob_valid_bos")
    SetupDetector._detect_bos = boom
    poisoned_one = run(rows, "BOS-detector KeyError each row")
    SetupDetector._detect_bos = orig

    diff = sum(1 for a, b in zip(baseline, poisoned_one) if a != b)
    print(f"rows whose setup list changed when BOS detector always raises: {diff}/{len(rows)}")

    # (b) poison ALL 14 detectors -> total silent starvation of the radar
    for name in [n for n in dir(SetupDetector) if n.startswith("_detect_")]:
        setattr(SetupDetector, name, boom.__get__(SetupDetector))
    all_poison = run(rows, "ALL detectors KeyError each row")
    SetupDetector._detect_bos = orig  # restore just one for sanity print
    empty = sum(1 for r in all_poison if len(r) == 0)
    print(f"all-detectors-raising: empty setup lists {empty}/{len(rows)} (baseline empty: "
          f"{sum(1 for r in baseline if len(r)==0)})")

    # (c) which detectors can raise on a WELL-FORMED row? trace _sig/_f access patterns:
    # every read is row.get / `name in row` / _f(None-safe) -> no KeyError possible.
    # The ONLY unguarded ops are math on coerced floats. Verify empirically:
    import math
    weird_rows = []
    for r in rows[:50]:
        w = dict(r)
        w["feat_0"] = float("nan")       # NaN feature
        w["atr_m1"] = 0.0                 # zero ATR
        w["session_london"] = "yes"       # wrong type (string)
        w["feat_15"] = None               # None where flag expected
        weird_rows.append(w)
    res = run(weird_rows, "hostile rows (NaN/0-ATR/str/None)")
    print("hostile-row sample outputs (first 3):", res[:3])
