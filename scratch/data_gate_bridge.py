"""DATA GATE — raw broker bars -> dataset artifact bridge (READ-ONLY, no training).

Pipeline (mirrors LiveEngine._build_feature_history warmup exactly):

    raw parquet bars (data/raw/XAUUSD_M1.parquet etc.)
        -> BarData list
        -> ScalpFeatureEngine.compute_from_bars(window, synthetic_tick)
           per bar (sliding window of all bars up to i, min 55)
        -> 50D feature vectors feat_0..feat_49 + atr_m1 + spread
        -> SampleFactory (triple-barrier labels, purge/embargo, deterministic ids)
        -> DatasetFactory.build() -> artifacts/model_generation/datasets/ds_<id>/

Does NOT train. Does NOT touch artifacts/models/scalp/XAUUSD/v1.0.0.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

import polars as pl

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation import ArtifactStore, DatasetFactory, default_artifact_root

RAW_DIR = Path("data/raw")
DEFAULT_TF = "M5"  # best balance: 17 months of history
SPREAD_USD = 0.20  # live-engine synthetic spread convention (bid/ask gap)


def load_bars(tf: str) -> pl.DataFrame:
    pq = RAW_DIR / f"XAUUSD_{tf}.parquet"
    if not pq.exists():
        raise FileNotFoundError(f"Raw capture missing: {pq}")
    df = pl.read_parquet(pq)
    df = df.sort("time")
    return df


def _compute_range_worker(args: tuple[int, int, list[BarData]]) -> list[dict]:
    """Module-level worker for ProcessPool (Windows spawn: args pickled)."""
    start, end, bars = args
    local_engine = ScalpFeatureEngine(symbol="XAUUSD")
    out: list[dict] = []
    for i in range(start, end):
        # pass only the 55-bar window the engine slices internally —
        # identical output, far less memory traffic than bars[:i+1]
        window = bars[max(0, i - 54) : i + 1]
        b = bars[i]
        synthetic_tick = TickData(
            symbol="XAUUSD",
            timestamp=b.timestamp,
            bid=b.close,
            ask=b.close + SPREAD_USD,
            volume=b.tick_volume,
        )
        fv = local_engine.compute_from_bars(window, synthetic_tick)
        x50 = fv.to_tensor_input()
        rec = {
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "spread": SPREAD_USD,
            "atr_m1": float(fv.atr_m1),
            "tick_volume": b.tick_volume,
        }
        for idx in range(50):
            rec[f"feat_{idx}"] = float(x50[idx])
        out.append(rec)
    return out


def bars_to_feature_frame(df: pl.DataFrame, tf: str) -> pl.DataFrame:
    """Compute the live-engine 50D features per completed bar (causal window).

    Uses the live-engine hot-path window: compute_from_bars internally takes
    completed_bars[-55:] — a fixed 55-bar lookback, NOT the full history.
    So per-bar cost is O(55) work. Runs in a ProcessPool to use all cores.
    """
    bars: list[BarData] = []
    for row in df.to_dicts():
        ts = datetime.fromtimestamp(int(row["time"]), tz=UTC)
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe=tf,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                tick_volume=int(row["tick_volume"]),
                is_complete=True,
            )
        )
    # warmup: rows < 55 have no stable feature basis (live engine cold-start)
    warmup = 54
    n = len(bars)
    chunk = max(4, n // 8)
    ranges = [(s, min(s + chunk, n)) for s in range(warmup, n, chunk)]

    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=min(8, len(ranges))) as pool:
        parts = pool.map(_compute_range_worker, [(s, e, bars) for s, e in ranges])

    records: list[dict] = []
    for part in parts:
        records.extend(part)

    out = pl.DataFrame(records)
    # regime: lightweight deterministic proxy on bars — RANGING/TRENDING by
    # short-term close dispersion vs ATR (explainable, no lookahead).
    out = out.with_columns(
        pl.when(
            pl.col("close").rolling_std(window_size=20).fill_null(0.0) < pl.col("atr_m1") * 0.55
        )
        .then(pl.lit("RANGING"))
        .otherwise(pl.lit("TRENDING"))
        .alias("regime")
    )
    return out


def main() -> int:
    t0 = time.perf_counter()
    tf = DEFAULT_TF
    print(f"=== DATA GATE bridge: XAUUSD {tf} -> dataset artifact ===")
    raw = load_bars(tf)
    print(f"raw bars: {raw.height}  ({raw['time'].min()} -> {raw['time'].max()})")

    feat = bars_to_feature_frame(raw, tf)
    print(f"featured bars: {feat.height} (warmup dropped first 54)")

    # NaN/Inf guard on features (contract: finite, clipped [-3,3])
    feat_np = feat.select([f"feat_{i}" for i in range(50)]).to_numpy()
    import numpy as np

    bad = int((~np.isfinite(feat_np)).sum())
    print(f"non-finite feature values: {bad}")
    if bad > 0:
        feat = feat.with_columns(
            [
                pl.col(f"feat_{i}").fill_null(0.0).clip(-3.0, 3.0).alias(f"feat_{i}")
                for i in range(50)
            ]
        )

    store = ArtifactStore(default_artifact_root())
    dh = DatasetFactory(store=store).build(
        feat,
        symbol="XAUUSD",
        timeframe=tf,
        news_frame=None,
        strategy_id="scalp_default",
        strategy_version="1.0.0",
    )
    elapsed = time.perf_counter() - t0
    print("\n=== DATASET BUILT ===")
    print(f"dataset_id : {dh['dataset_id']}")
    print(f"rows       : {dh['counts']}")
    print(f"path       : {dh['path']}")
    print(f"hash       : {dh['hash']}")
    print(f"elapsed    : {elapsed:.1f}s")

    man = store.read_dataset_manifest(dh["dataset_id"])
    if man:
        print("\n=== MANIFEST ===")
        print(f"temporal_range : {man['temporal_range']}")
        print(f"label_schema   : {man['label_schema_id']}")
        print(f"feature_schema : {man['feature_schema_id']}")
        print(f"news_schema    : {man.get('news_schema_id')}")
        print(f"purge/embargo  : {man.get('purge_parameters')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
