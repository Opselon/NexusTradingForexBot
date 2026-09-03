"""MLPWR-06-02 caller grid: which history depth does EACH production call
site actually pass to compute_from_bars?
  LIVE  live_engine._process_tick_pipeline:3557 -> aggregator.get_completed_bars()
        (capped at 4000 at line 3554; repo standard 900-bar window -> depth ~900)
  LIVE  live_engine warmup probes :2691 / :3177 / :3265 -> same aggregator depth
  TRAIN schema_v2.compute_70d_frame -> window = all_bars[i-54:i+1] -> ALWAYS 55
This grid pins the asymmetry to the CALLER, not the engine math."""
from __future__ import annotations
import sys
sys.path.insert(0, "src")

# static evidence: print the exact call sites + their argument expression
import inspect
from nexus_scalp.model_generation import schema_v2
src = inspect.getsource(schema_v2.compute_70d_frame)
for i, line in enumerate(src.splitlines()):
    if "window = all_bars" in line or "compute_from_bars" in line:
        print(f"schema_v2.compute_70d_frame:{i}: {line.strip()}")
from nexus_scalp.features import scalp_features
src2 = inspect.getsource(scalp_features.ScalpFeatureEngine.compute_from_bars)
for i, line in enumerate(src2.splitlines()):
    if "tail_bars" in line and "[-55:]" in line:
        print(f"scalp_features.compute_from_bars:{i}: {line.strip()}")
    if "aggregate_bars(completed_bars" in line:
        print(f"scalp_features.compute_from_bars:{i}: {line.strip()}")
