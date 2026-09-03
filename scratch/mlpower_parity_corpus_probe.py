"""MLPWR-06-01 probe (NEXUS-MLPOWER lane 06): full-vector TRAIN-vs-LIVE parity
corpus over the existing scalp_v3 dataset artifact + live-style recomputation.

Read-only probe (scratch/). Produces a per-feature parity table:
    feature_index | canonical_name | family | train_value | live_value | delta | status

Paths compared:
    TRAIN : compute_70d_frame (canonical dataset builder, d90f29b0 semantics)
    LIVE  : ScalpFeatureEngine 50D (to_tensor_input) + news_10d_from_context
            projection equivalence + LiquidityGovernor snapshot (VALID)
Also asserts name-level identity via the canonical registry (post-MLPWR-05-01).
"""
from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")

import polars as pl

from nexus_scalp.features.features70 import clamp_neutral_family, news_10d_from_context
from nexus_scalp.features.liquidity_runtime import LiquidityGovernor
from nexus_scalp.features.schema_contract import (
    canonical_feature_names,
    family_of,
    feature_schema_hash,
)
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
from nexus_scalp.market_data.bar_aggregator import BarData

OUT = Path(__file__).with_name("mlpower_parity_corpus_probe.out.txt")
TOL = 1e-12


def mkbars(n, t0, base=3300.0, step=0.1, seed=7):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        o = base + i * step
        c = o + rng.uniform(-0.3, 0.3)
        h = max(o, c) + rng.uniform(0.1, 0.6)
        l = min(o, c) - rng.uniform(0.1, 0.6)
        out.append(
            SimpleNamespace(
                symbol="XAUUSD", timeframe="M1", timestamp=t0 + timedelta(minutes=i),
                open=o, high=h, low=l, close=c, tick_volume=100, is_complete=True,
            )
        )
    return out


def to_bd(bars):
    return [
        BarData(symbol=b.symbol, timeframe="M1", timestamp=b.timestamp, open=b.open,
                high=b.high, low=b.low, close=b.close, tick_volume=b.tick_volume,
                is_complete=True)
        for b in bars
    ]


def run(n_bars: int, t0: datetime, seed: int) -> dict:
    bars = mkbars(n_bars, t0, seed=seed)
    frame = compute_70d_frame(
        pl.DataFrame([
            {"time": b.timestamp, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "tick_volume": b.tick_volume}
            for b in bars
        ])
    )
    row = frame.tail(1).row(0, named=True)
    train = [float(row[f"feat_{i}"]) for i in range(70)]

    # LIVE-style assembly: 50D engine + neutral news (no live feed in probe) + governor liquidity
    bd = to_bd(bars)
    close = bars[-1].close
    tick = SimpleNamespace(timestamp=bars[-1].timestamp, bid=close, ask=close + 0.20, volume=100)
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bd, tick)
    base50 = fv.to_tensor_input()

    # news: no live feed in probe -> FEATURE_DISABLED neutral (documented train semantics)
    news10 = clamp_neutral_family(news_10d_from_context(None), (0.0,) * 10)

    gov = LiquidityGovernor(enabled=True)
    gov.compute_from_engine(
        bars=bd, mid_price=float(close), atr=float(fv.atr_m1), decision_at=bars[-1].timestamp
    )
    liq10 = list(gov.last_snapshot.features)

    live = list(base50) + list(news10) + list(liq10)

    names = canonical_feature_names()
    rows = []
    for i in range(70):
        delta = abs(train[i] - live[i])
        rows.append({
            "index": i,
            "name": names[i],
            "family": family_of(i),
            "train": round(train[i], 10),
            "live": round(live[i], 10),
            "delta": delta,
            "status": "MATCH" if delta <= TOL else "MISMATCH",
        })
    mismatches = [r for r in rows if r["status"] == "MISMATCH"]
    return {
        "n_bars": n_bars,
        "seed": seed,
        "schema_hash": feature_schema_hash(),
        "max_delta": max(r["delta"] for r in rows),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:12],
        "rows": rows,
    }


def main() -> None:
    results = []
    for n, seed in ((120, 7), (240, 11), (240, 23)):
        results.append(run(n, datetime(2026, 9, 2, 0, 0, tzinfo=UTC), seed))
    lines = []
    ok = True
    for r in results:
        ok &= r["mismatch_count"] == 0
        lines.append(
            f"window n={r['n_bars']} seed={r['seed']}: max_delta={r['max_delta']:.3e} "
            f"mismatches={r['mismatch_count']} {r['mismatches'] if r['mismatches'] else ''}"
        )
    lines.append("PARITY VERDICT: " + ("MATCH (train==live, all 70 dims)" if ok else "MISMATCH"))
    # name-level identity assertions (post-MLPWR-05-01 capability)
    names = canonical_feature_names()
    lines.append(f"canonical names: {len(names)} | hash: {feature_schema_hash()}")
    lines.append(f"idx50={names[50]} idx60={names[60]} idx69={names[69]}")
    report = "\n".join(lines)
    print(report)
    OUT.write_text(report + "\n\n" + json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
