"""STEP-01b — REAL 70D model flapping capture (TASK-TEMPORAL-01).

Builds a bounded causal scalp_v3 (70D) frame on REAL XAUUSD M1 history with
REAL timestamps (the existing dataset artifact ds_d3f35b12d63148da carries
synthetic 1970 microsecond timestamps — unusable for temporal research),
labels it with the canonical purged triple-barrier labeler, trains a 70D
baseline through CandidateTrainer (same recipe as the TASK-4 benchmark),
then runs batch inference to produce REAL logits/probabilities across the
sequence. Output replaces the PLACEHOLDER_UNIFORM values in
artifacts/forensics/70d_signal_flapping_trace.json.

Bounded-history note (BUG-106 class): the dataset builder's unbounded
full-history liquidity recompute is O(n^2). This harness uses a bounded
causal tail HIST_LIMIT=1800 (>= D1 bucket) for BOTH frame build and live
inference, so training == live for this research schema. Documented
deviation, parity tested in STEP-03.

Usage: python scratch/temporal_step01b_train_and_capture.py [--rows 4000]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, "src")

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.features70 import (
    clamp_neutral_family,
    news_10d_from_context,
)
from nexus_scalp.features.liquidity_engine import (
    PoolState,
    compute_liquidity_features,
)
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.market_data.bar_aggregator import BarData
from nexus_scalp.model_generation.artifact_store import ArtifactStore
from nexus_scalp.model_generation.experiment_factory import ExperimentFactory
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.model_generation.training import CandidateTrainer

REPO = Path(__file__).resolve().parents[1]
RAW_M1 = REPO / "data/raw/XAUUSD_M1.parquet"
OUT = REPO / "artifacts/forensics/70d_signal_flapping_trace.json"
HIST_LIMIT = 300  # bounded causal tail (H1/H4 buckets; D1 only after 1440 M1 bars)
LIQ_NEUTRAL = (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0)

TRAIN_CFG = {"epochs": 8, "batch_size": 256, "learning_rate": 0.001, "seed": 42}


def bars_from_m1(n: int) -> tuple[list[BarData], list[datetime]]:
    df = pl.read_parquet(RAW_M1).head(n)
    times: list[datetime] = []
    for row in df.iter_rows(named=True):
        t = row.get("time_utc") or row.get("time")
        ts = t if isinstance(t, datetime) else None
        if ts is None:
            ts = datetime.fromtimestamp(int(row["time"]), tz=UTC)
        ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
        times.append(ts)
    bars = [
        BarData(
            symbol="XAUUSD",
            timeframe="M1",
            timestamp=times[i],
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            tick_volume=int(r.get("tick_volume", 0) or 0),
            is_complete=True,
        )
        for i, r in enumerate(df.iter_rows(named=True))
    ]
    return bars, times


def compute_vector70(
    engine: ScalpFeatureEngine,
    all_bars: list[BarData],
    i: int,
    mid_price: float,
) -> tuple[list[float], dict]:
    """Causal 70D vector at bar i over the bounded tail. Returns (vector70, meta)."""
    ts = all_bars[i].timestamp
    tick = TickData(
        symbol="XAUUSD", timestamp=ts, bid=mid_price, ask=mid_price + 0.04, volume=0
    )
    hist = all_bars[max(0, i + 1 - HIST_LIMIT) : i + 1]
    window = all_bars[max(0, i - 54) : i + 1]
    fv = engine.compute_from_bars(window, tick)
    x50 = fv.to_tensor_input()
    liq = compute_liquidity_features(
        hist,
        decision_at=ts,
        mid_price=mid_price,
        atr=fv.atr_m1,
    )
    liq10 = list(clamp_neutral_family(liq.as_vector(), LIQ_NEUTRAL))
    news10 = list(clamp_neutral_family(news_10d_from_context({}), (0.0,) * 10))
    vector70 = x50 + news10 + liq10
    meta = {
        "atr_m1": float(fv.atr_m1),
        "pools": [
            {
                "side": int(p.side),
                "state": int(p.state),
                "state_name": PoolState(p.state).name,
                "price": float(p.price),
                "usable_at": str(p.usable_at),
            }
            for p in liq.pools
        ],
    }
    return vector70, meta


def build_training_frame(bars: list[BarData], engine: ScalpFeatureEngine, n: int) -> pl.DataFrame:
    """feat_0..69 + close/atr_m1/spread rows for the labeler + trainer."""
    rows: list[dict] = []
    for i in range(n):
        v70, meta = compute_vector70(engine, bars, i, float(bars[i].close))
        rec = {
            "timestamp": bars[i].timestamp,
            "close": float(bars[i].close),
            "high": float(bars[i].high),
            "low": float(bars[i].low),
            "open": float(bars[i].open),
            "spread": 0.04,
            "atr_m1": meta["atr_m1"],
        }
        for idx in range(70):
            rec[f"feat_{idx}"] = v70[idx]
        rows.append(rec)
        if (i + 1) % 1000 == 0:
            print(f"[FRAME] {i+1}/{n} rows", flush=True)
    return pl.DataFrame(rows)


def softmax(v: np.ndarray) -> np.ndarray:
    e = np.exp(v - v.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=4000)
    ap.add_argument("--epochs", type=int, default=8)
    args = ap.parse_args()
    n = args.rows

    t0 = time.perf_counter()
    bars, _ = bars_from_m1(n)
    print(f"[STEP01b] bars={len(bars)} range={bars[0].timestamp}..{bars[-1].timestamp}")

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    frame = build_training_frame(bars, engine, n)
    print(f"[STEP01b] frame rows={frame.height} cols={len(frame.columns)} ({time.perf_counter()-t0:.0f}s)")

    # label with the canonical purged triple-barrier labeler
    from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler

    labeler = TripleBarrierLabeler()
    labeled = labeler.label_dataframe(frame)
    # labels are canonical 3-class strings (NO_TRADE/BUY_MARKET/SELL_MARKET)
    from nexus_scalp.model_generation.models import default_label_schema

    ls = default_label_schema()
    lbl_str = labeled["label"].to_list()
    lbl = [ls.encode(s) for s in lbl_str]
    labeled = labeled.with_columns(pl.Series("label", lbl))
    lbl_counts = {int(k): int(v) for k, v in zip(*np.unique(lbl, return_counts=True), strict=False)}
    print("[STEP01b] labels:", lbl_counts)

    # train 70D baseline via CandidateTrainer
    store = ArtifactStore()
    exp = ExperimentFactory(store=store).create(
        "ds_temporal_capture",
        template="baseline_scalpnet_v1",
        experiment_id="temporal_capture_70d",
        overrides={"training": {"epochs": args.epochs, "batch_size": 256, "learning_rate": 0.001, "seed": 42}},
    )
    feat_cols = [f"feat_{i}" for i in range(70)]
    mid = "temporal_capture_70d_v1"
    t1 = time.perf_counter()
    res = CandidateTrainer(store=store).train_candidate(
        exp, labeled, feature_cols=feat_cols, model_id=mid, epochs=args.epochs
    )
    print(f"[STEP01b] train res={json.dumps(res, default=str)[:300]} ({time.perf_counter()-t1:.0f}s)")
    if res.get("status") != "COMPLETED":
        print("[STEP01b] TRAIN FAILED — aborting capture")
        return
    res["artifact"]

    # load artifact weights + scaler
    import torch

    model = ModelFactory().build(
        architecture=exp.architecture,
        num_classes=3,
        parameters={"input_dim": 70, **(exp.architecture_parameters or {})},
    )
    weights_path = store.model_weights_path(mid)
    model.load_state_dict(torch.load(weights_path, map_location="cpu"))
    model.eval()
    mean, std = store.read_scaler(mid)

    X = frame.select(feat_cols).to_numpy().astype(np.float32)
    Xs = (X - mean) / std

    with torch.inference_mode():
        logits_all = model(torch.from_numpy(Xs)).numpy()
    probs_all = softmax(logits_all)
    preds = probs_all.argmax(axis=1)
    confs = probs_all.max(axis=1)

    # rebuild events with REAL model output
    events: list[dict] = []
    for i in range(n):
        ev = {
            "timestamp": bars[i].timestamp.isoformat(),
            "bar_timestamp": bars[i].timestamp.isoformat(),
            "bar_index": i,
            "mid_price": float(bars[i].close),
            "vector70": [float(x) for x in X[i]],
            "liquidity10": [float(x) for x in X[i][60:70]],
            "logits": [float(v) for v in logits_all[i]],
            "probabilities": [float(v) for v in probs_all[i]],
            "logits_source": "TRAINED_70D_BASELINE",
            "predicted_class": int(preds[i]),
            "raw_confidence": float(confs[i]),
            "final_confidence": float(confs[i]),
            "regime": "UNKNOWN",
            "news_state": "FEATURE_DISABLED",
            "policy_result": "NOT_EVALUATED",
            "risk_result": "NOT_EVALUATED",
            "label": int(lbl[i]),
        }
        events.append(ev)

    # flip statistics
    def direction_of(ev: dict) -> str:
        if ev["predicted_class"] == 1:
            return "BUY"
        if ev["predicted_class"] == 2:
            return "SELL"
        return "NONE"

    seq = [direction_of(e) for e in events]
    flips = [(i, seq[i - 1], seq[i]) for i in range(1, len(seq)) if seq[i] != seq[i - 1] and seq[i] != "NONE" and seq[i - 1] != "NONE"]
    intervals = []
    for i, _, _ in flips:
        t0e = datetime.fromisoformat(events[i - 1]["timestamp"])
        t1e = datetime.fromisoformat(events[i]["timestamp"])
        intervals.append((t1e - t0e).total_seconds())
    span = (
        datetime.fromisoformat(events[-1]["timestamp"])
        - datetime.fromisoformat(events[0]["timestamp"])
    ).total_seconds()
    stats = {
        "events": len(events),
        "directional": sum(1 for s in seq if s != "NONE"),
        "buy_sell_flips": sum(1 for _, a, b in flips if (a, b) == ("BUY", "SELL")),
        "sell_buy_flips": sum(1 for _, a, b in flips if (a, b) == ("SELL", "BUY")),
        "total_flips": len(flips),
        "span_seconds": round(span, 3),
        "flips_per_second": round(len(flips) / span, 6) if span else 0.0,
        "flips_per_minute": round(len(flips) * 60 / span, 4) if span else 0.0,
        "median_flip_interval_s": round(statistics.median(intervals), 4) if intervals else None,
        "p95_flip_interval_s": round(sorted(intervals)[int(len(intervals) * 0.95) - 1], 4) if intervals else None,
        "min_flip_interval_s": round(min(intervals), 4) if intervals else None,
        "max_flip_interval_s": round(max(intervals), 4) if intervals else None,
        "tick_to_tick_flips": len(flips),
        "bar_to_bar_flips": len(flips),
        "confirmed_event_flips": 0,
    }

    payload = {
        "capture": {
            "harness": "STEP-01b real-model capture (TASK-TEMPORAL-01)",
            "instrument": "XAUUSD",
            "timeframe": "M1",
            "source": "data/raw/XAUUSD_M1.parquet (real broker history)",
            "schema_id": "scalp_v3",
            "schema_hash": "235b8fccc96b7e0e",
            "model_id": mid,
            "model_architecture": exp.architecture,
            "news": "FEATURE_DISABLED (neutral 10D)",
            "bounded_history_limit": HIST_LIMIT,
            "captured_at": datetime.now(UTC).isoformat(),
        },
        "flip_statistics": stats,
        "events": events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print(f"[STEP01b] wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"[STEP01b] flips: {json.dumps(stats, indent=1)}")


if __name__ == "__main__":
    main()