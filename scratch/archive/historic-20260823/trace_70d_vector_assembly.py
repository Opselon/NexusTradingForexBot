"""Forensic probe: deterministic 70D vector assembly trace (TASK-70D-SYSTEM-FLOW-FORENSICS).

Builds the 70D vector through the REAL runtime builders (scalp_features ->
news_provider -> liquidity_engine -> build_70d_vector / assemble_70d),
validates against the canonical schema contract, and writes
artifacts/forensics/feature_vector_trace.json (per-index mapping).
"""

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/src")

from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID, FEATURE_SCHEMAS
from nexus_scalp.features.schema_contract import (
    DIMENSION,
    canonical_feature_names,
    feature_schema_hash,
    validate_70d_vector,
)
from nexus_scalp.market_data.bar_aggregator import BarData


def bars(n: int = 80, start: float = 1.10000, step: float = 0.0001) -> list[BarData]:
    out: list[BarData] = []
    ts = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    px = start
    for i in range(n):
        o = px
        c = px + step * (1 if i % 3 else -1)
        h = max(o, c) + 0.0002
        l = min(o, c) - 0.0002
        out.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=ts,
                open=o,
                high=h,
                low=l,
                close=c,
                tick_volume=100 + i,
                is_complete=True,
            )
        )
        px = c
        ts = ts.replace(
            minute=(ts.minute + 1) % 60, hour=(ts.hour + (1 if ts.minute == 59 else 0)) % 24
        )
        if i and i % 24 == 23:
            ts = ts.replace(day=ts.day + 1)
    return out


def main() -> None:
    b = bars()
    fe = ScalpFeatureEngine()
    from nexus_scalp.domain.models import TickData

    mid = b[-1].close
    tick = TickData(
        symbol="XAUUSD", timestamp=b[-1].timestamp, bid=mid, ask=mid + 0.0001, volume=1.0
    )
    fv = fe.compute_from_bars(b, tick)
    vec50 = list(fv.to_tensor_input())
    assert len(vec50) == 50, len(vec50)
    print("base50 len:", len(vec50), "finite:", all(math.isfinite(v) for v in vec50))

    # news 10 from a canonical context
    from nexus_scalp.governance.alignment import vectorize_news_context
    from nexus_scalp.shadow.shadow70.news_provider import build_news_10

    ctx = {
        "state": "ELEVATED",
        "active_high_impact_events": 1.0,
        "xauusd_relevance": 0.8,
        "usd_relevance": 0.5,
        "bullish_pressure": 0.2,
        "bearish_pressure": 0.1,
        "conflict_score": 0.0,
        "novelty": 1.0,
        "freshness": 0.9,
        "confidence": 0.6,
    }
    nv = vectorize_news_context(ctx)
    news10, nver = build_news_10(nv)
    print("news v12 len:", len(nv), "-> news10 len:", len(news10))

    # liquidity 10
    from nexus_scalp.features.liquidity_engine import compute_liquidity_features

    liq = compute_liquidity_features(b, use_htf=True)
    liq10 = list(liq.as_vector() if hasattr(liq, "as_vector") else [])
    if len(liq10) == 60:
        liq10 = liq10[50:60]
    elif len(liq10) != 10:
        raise SystemExit(f"liq10 unexpected len {len(liq10)}")
    print("liq10 len:", len(liq10), liq10[:3], "...")

    # assemble both ways
    from nexus_scalp.features.features70 import assemble_70d
    from nexus_scalp.features.liquidity_runtime import build_70d_vector

    v70a = build_70d_vector(vec50, family_10=news10, liquidity_10=liq10)
    snap = assemble_70d(
        base50=vec50, news10=news10, liquidity10=liq10, symbol="XAUUSD", timeframe="M1"
    )
    v70b = snap.vector

    names = canonical_feature_names()
    print("70D dim:", DIMENSION, "len a/b:", len(v70a), len(v70b), "equal:", v70a == v70b)
    print("names len:", len(names))
    h = feature_schema_hash()
    print("schema hash:", h)
    print("ACTIVE_SCHEMA:", ACTIVE_SCHEMA_ID, "active dim:", FEATURE_SCHEMAS.active.dimension)
    ok = validate_70d_vector(v70a, schema_hash=h, context="probe")
    print("validate ok:", len(ok))

    out = []
    for i in range(DIMENSION):
        fam = "BASE" if i < 50 else ("NEWS" if i < 60 else "LIQ")
        out.append(
            {
                "index": i,
                "name": names[i],
                "family": fam,
                "source": "scalp_features"
                if i < 50
                else ("news_provider" if i < 60 else "liquidity_engine"),
                "value": float(v70a[i]),
            }
        )
    dest = Path(
        r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/forensics/feature_vector_trace.json"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "dimension": DIMENSION,
                "schema_id": "scalp_v3",
                "schema_hash": h,
                "news_projection_version": nver,
                "names_len": len(names),
                "base50_len": len(vec50),
                "news10_len": len(news10),
                "liq10_len": len(liq10),
                "vector": [round(float(v), 6) for v in v70a],
                "features": out,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("trace written:", dest)


if __name__ == "__main__":
    main()
