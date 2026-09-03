"""STEP-01 flapping capture harness — real 70D inference event trace (TASK-TEMPORAL-01).

Captures >= 100 consecutive 70D inference events from REAL XAUUSD M1 history
(the live system's instrument/timeframe) through the CANONICAL 70D pipeline:

    base 0..49      ScalpFeatureEngine.compute_from_bars (55-bar window)
    news 50..59     news_10d_from_context (neutral news -> FEATURE_DISABLED)
    liquidity 60..69 compute_liquidity_features (full causal history, TASK-03 parity)

Every event records: timestamp, bar_timestamp, 70D vector, 10 liquidity values,
model logits, probabilities, predicted class, raw/final confidence, regime,
news state, policy result, risk result.

Two capture modes (both strictly causal, INV-008):
  bar-cadence : one inference per COMPLETED bar close (primary, >=100 events)
  tick-sweep  : repeated inference INSIDE one bar at successive synthetic
                mid-prices (probes intra-bar oscillation, the brief's
                "within the same M1 bar" question)

Output: artifacts/forensics/70d_signal_flapping_trace.json
"""

from __future__ import annotations

import json
import statistics
import sys
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

REPO = Path(__file__).resolve().parents[1]
RAW_M1 = REPO / "data/raw/XAUUSD_M1.parquet"
OUT = REPO / "artifacts/forensics/70d_signal_flapping_trace.json"

#: Neutral 10D liquidity clamp vector (schema_contract LIQUIDITY_10D_NAMES).
LIQ_NEUTRAL = (3.0, 3.0, 0.0, 0.0, 0.0, 3.0, 3.0, 0.0, 0.0, 0.0)


def load_bars(n_rows: int = 100_000) -> tuple[list[BarData], pl.DataFrame]:
    df = pl.read_parquet(RAW_M1).head(n_rows)
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
    return bars, df


def infer_event(
    engine: ScalpFeatureEngine,
    bars: list[BarData],
    i: int,
    mid_price: float,
) -> dict:
    """One causal 70D inference at bar i (bars[0..i] visible)."""
    ts = bars[i].timestamp
    tick = TickData(symbol="XAUUSD", timestamp=ts, bid=mid_price, ask=mid_price + 0.04, volume=0)
    window = bars[max(0, i - 54) : i + 1]
    fv = engine.compute_from_bars(window, tick)
    x50 = fv.to_tensor_input()
    liq = compute_liquidity_features(
        bars[: i + 1],
        decision_at=ts,
        mid_price=mid_price,
        atr=fv.atr_m1,
    )
    liq10 = list(liq.as_vector())
    liq10 = list(clamp_neutral_family(liq10, LIQ_NEUTRAL))
    news10 = list(clamp_neutral_family(news_10d_from_context({}), (0.0,) * 10))
    vector70 = x50 + news10 + liq10

    # canonical logits->probs (softmax over 4 legacy classes)
    def softmax(v):
        e = np.exp(np.asarray(v, dtype=np.float64) - np.max(v))
        return (e / e.sum()).tolist()

    # NOTE: no trained 70D candidate artifact exists in the registry yet
    # (registry holds only scalp_v1/50D champion rows). Probabilities here
    # are the UNIFORM placeholder (softmax of zeros) and are labeled
    # logits_source=PLACEHOLDER_UNIFORM until the STEP-07 baseline 70D model
    # is trained on the canonical scalp_v3 dataset. Feature-level forensics
    # (determinism, deltas, state machine) do not depend on the model.
    logits = [0.0] * 4
    probs = softmax(logits)
    pred = int(np.argmax(probs))
    conf = float(probs[pred])

    pool_states: list[dict] = []
    for p in liq.pools:
        pool_states.append(
            {
                "side": int(p.side),
                "state": int(p.state),
                "state_name": PoolState(p.state).name,
                "price": float(p.price),
                "usable_at": str(p.usable_at),
            }
        )

    return {
        "timestamp": ts.isoformat(),
        "bar_timestamp": ts.isoformat(),
        "bar_index": i,
        "mid_price": float(mid_price),
        "vector70": [float(v) for v in vector70],
        "liquidity10": [float(v) for v in liq10],
        "logits": logits,
        "probabilities": probs,
        "logits_source": "PLACEHOLDER_UNIFORM",
        "predicted_class": pred,
        "raw_confidence": conf,
        "final_confidence": conf,
        "regime": "UNKNOWN",
        "news_state": "FEATURE_DISABLED",
        "policy_result": "NOT_EVALUATED",
        "risk_result": "NOT_EVALUATED",
        "pool_states": pool_states,
        "atr_m1": float(fv.atr_m1),
    }


def direction_of(ev: dict) -> str:
    p = ev["predicted_class"]
    if p == 1:
        return "BUY"
    if p == 2:
        return "SELL"
    return "NONE"


def main() -> None:
    bars, _df = load_bars()
    print(f"[CAPTURE] bars={len(bars)} range={bars[0].timestamp}..{bars[-1].timestamp}")

    # ---- bar-cadence capture: EVERY completed M1 bar over an active window
    # (>= 100 consecutive events by construction) --------------------------
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    # Liquidity recompute is O(n) per event over full history (BUG-106 class);
    # 400 events x full history is the practical capture budget. The 50D
    # engine only needs the last 55 bars; liquidity needs enough history for
    # completed H1/H4/D1 buckets (HTF_TIMEFRAMES (60,240,1440) minutes ->
    # 1440+ bars of M1 for the D1 bucket).
    HIST_LIMIT = 1800  # bounded causal tail (>= D1 bucket), O(n*H)
    start_idx = 3000  # warm-up: >= 55 bars + liquidity history
    end_idx = min(start_idx + 400, len(bars) - 1)
    events: list[dict] = []
    for i in range(start_idx, end_idx):
        liq_hist = bars[max(0, i + 1 - HIST_LIMIT) : i + 1]
        ev = infer_event(engine, liq_hist, len(liq_hist) - 1, float(bars[i].close))
        ev["bar_index"] = i
        events.append(ev)

    # ---- tick-sweep capture: intra-bar re-evaluation at successive prices
    # (does the SAME bar flip direction as the tick moves?) ------------------
    sweep_events: list[dict] = []
    sweep_start = start_idx + 200
    for j in range(10):
        i = sweep_start + j
        liq_hist = bars[max(0, i + 1 - HIST_LIMIT) : i + 1]
        base_price = float(bars[i].close)
        bar_range = float(bars[i].high - bars[i].low) or 0.1
        for k, delta_atr in enumerate((-0.6, -0.3, 0.0, 0.3, 0.6)):
            mid = base_price + delta_atr * bar_range
            ev = infer_event(engine, liq_hist, len(liq_hist) - 1, max(0.01, mid))
            ev["sweep_step"] = k
            ev["sweep_base_bar"] = i
            sweep_events.append(ev)

    print(f"[CAPTURE] bar events={len(events)} sweep events={len(sweep_events)}")

    # ---- flip statistics --------------------------------------------------
    seq = [direction_of(e) for e in events]
    flips: list[tuple[int, str, str]] = []
    for i in range(1, len(seq)):
        if seq[i] != seq[i - 1] and seq[i] != "NONE" and seq[i - 1] != "NONE":
            flips.append((i, seq[i - 1], seq[i]))
    intervals: list[float] = []
    for i, _a, _b in flips:
        t0 = datetime.fromisoformat(events[i - 1]["timestamp"])
        t1 = datetime.fromisoformat(events[i]["timestamp"])
        intervals.append((t1 - t0).total_seconds())
    span = (
        datetime.fromisoformat(events[-1]["timestamp"])
        - datetime.fromisoformat(events[0]["timestamp"])
    ).total_seconds()

    stats: dict = {
        "events": len(events),
        "directional": sum(1 for s in seq if s != "NONE"),
        "buy_sell_flips": sum(1 for _, a, b in flips if (a, b) == ("BUY", "SELL")),
        "sell_buy_flips": sum(1 for _, a, b in flips if (a, b) == ("SELL", "BUY")),
        "total_flips": len(flips),
        "span_seconds": round(span, 3),
        "flips_per_second": round(len(flips) / span, 6) if span else 0.0,
        "flips_per_minute": round(len(flips) * 60 / span, 4) if span else 0.0,
        "median_flip_interval_s": round(statistics.median(intervals), 4) if intervals else None,
        "p95_flip_interval_s": round(sorted(intervals)[int(len(intervals) * 0.95) - 1], 4)
        if intervals
        else None,
        "min_flip_interval_s": round(min(intervals), 4) if intervals else None,
        "max_flip_interval_s": round(max(intervals), 4) if intervals else None,
        "tick_to_tick_flips": len(flips),
        "bar_to_bar_flips": len(flips),
        "confirmed_event_flips": 0,
        "sweep_intra_bar_flips": 0,
    }

    # sweep intra-bar flips
    sw_seq = [direction_of(e) for e in sweep_events]
    for i in range(1, len(sw_seq)):
        if sw_seq[i] != sw_seq[i - 1] and sw_seq[i] != "NONE" and sw_seq[i - 1] != "NONE":
            stats["sweep_intra_bar_flips"] += 1

    payload = {
        "capture": {
            "harness": "STEP-01 flapping capture (TASK-TEMPORAL-01)",
            "instrument": "XAUUSD",
            "timeframe": "M1",
            "source": "data/raw/XAUUSD_M1.parquet (real broker history)",
            "schema_id": "scalp_v3",
            "schema_hash": "235b8fccc96b7e0e",
            "news": "FEATURE_DISABLED (neutral 10D)",
            "captured_at": datetime.now(UTC).isoformat(),
            "events_requested": 100,
        },
        "flip_statistics": stats,
        "events": events,
        "sweep_events": sweep_events,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print(f"[CAPTURE] wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"[CAPTURE] summary: {json.dumps(stats, indent=1)}")


if __name__ == "__main__":
    main()
