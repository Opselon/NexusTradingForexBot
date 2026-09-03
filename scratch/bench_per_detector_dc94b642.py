"""Per-detector micro cost + named-column coverage on real replay rows."""
import json
import time
from datetime import datetime
from pathlib import Path

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.setup_detector import SetupDetector

bars = json.loads((Path(".") / "scratch/data/forensic_50d_live_T1_bars.json").read_text())["bars"][:900]
bar_objs = [
    BarData(symbol="XAUUSD", timeframe="M1", timestamp=datetime.fromisoformat(b["time"]),
            open=b["open"], high=b["high"], low=b["low"], close=b["close"],
            tick_volume=b["tick_volume"], is_complete=True)
    for b in bars
]
engine = ScalpFeatureEngine(symbol="XAUUSD")
rows = []
for i in range(60, 900):
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

det = SetupDetector()
names = [n for n in dir(SetupDetector) if n.startswith("_detect_")]
print(f"detectors={len(names)}")
for n in names:
    m = getattr(SetupDetector, n)
    t0 = time.perf_counter()
    hits = 0
    for row in rows:
        try:
            if m(det, row, None) is not None:
                hits += 1
        except Exception:
            pass
    dt = (time.perf_counter() - t0) * 1000
    print(f"{n:36s} total={dt:7.2f}ms per-row={dt/len(rows):6.4f}ms hits={hits}")

named = ["feat_ob_liquidity_swept", "liquidity_sweep_signal", "stop_hunt_depth",
         "order_block_type", "feat_ob_equilibrium_ratio", "feat_ob_valid_bos",
         "feat_ob_fib_50_60_alignment", "fvg_sig", "close_location_value",
         "htf_h4_trend", "breakout_sig", "norm_displacement", "choch_sig",
         "consecutive_momentum_count", "htf_h1_momentum", "dist_to_ema_21",
         "dist_to_swing_high_20", "lag_1_volume_z", "price_compression_flag_ratio",
         "norm_rsi", "lower_wick_ratio", "pinbar_sig"]
present = [k for k in named if k in rows[0]]
print(f"\n_sig reads resolving by NAME on feat_N-style record: {len(present)}/{len(named)}")
print("named keys present:", present)
print("absent:", [k for k in named if k not in rows[0]])
