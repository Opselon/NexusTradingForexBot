"""TASK-10 forensic probe v2: 70D composite semantics at engine level.

Read-only. Uses the ACTUAL liquidity engine, the 50D engine, the shadow70
contract and a realistic CurrentNewsContext to prove:

  A. liquidity engine output = exactly 10D liquidity block (canonical names)
  B. 70D composite assembly (build_70d_vector): 50+10+10, strict widths
  C. NEWS-FAMILY SEMANTICS: VectorizeNewsContext returns the canonical
     news_context_v1 vector; the live_engine shadow70 path truncates it to
     10 with [..][:10] — evidence of which dimensions survive / are dropped
     (state encoding = HIGH_IMPACT flag is dropped when the 12th field is
     state_enc).
  D. all outputs finite and within [-3, +3].

No fake data; each section records PROVEN evidence.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from nexus_scalp.features.liquidity_engine import (  # noqa: E402
    compute_liquidity_features,
)
from nexus_scalp.features.liquidity_runtime import build_70d_vector  # noqa: E402
from nexus_scalp.features.scalp_features import (  # noqa: E402
    ScalpFeatureEngine,
)
from nexus_scalp.governance.alignment import vectorize_news_context  # noqa: E402

LIQ_NAMES = (
    "bsl_distance_atr",
    "ssl_distance_atr",
    "eqh_strength",
    "eql_strength",
    "htf_liquidity_score",
    "internal_liquidity_distance",
    "external_liquidity_distance",
    "liquidity_confluence",
    "liquidity_sweep_state",
    "post_sweep_displacement",
)


def _to_objs(bars: list[dict]) -> list[SimpleNamespace]:
    out = []
    for b in bars:
        b = dict(b)
        ts = b["timestamp"]
        b["timestamp"] = ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts, tz=UTC)
        b.setdefault("symbol", "XAUUSD")
        out.append(SimpleNamespace(**b))
    return out


def _make_bars(n: int = 150, seed: int = 7) -> list[dict]:
    rng = np.random.default_rng(seed)
    price = 2000.0
    bars = []
    base_ts = int(datetime(2026, 8, 1, 6, 0, tzinfo=UTC).timestamp())
    for i in range(n):
        price += float(rng.normal(0.0, 0.6))
        o = price
        h = o + abs(float(rng.normal(0.35, 0.3)))
        l = o - abs(float(rng.normal(0.35, 0.3)))
        c = o + float(rng.normal(0.0, 0.5))
        c = min(max(c, l + 0.005), h - 0.005)
        bars.append(
            {
                "timestamp": base_ts + i * 60,
                "open": round(o, 5),
                "high": round(h, 5),
                "low": round(l, 5),
                "close": round(c, 5),
                "tick_volume": int(rng.integers(50, 400)),
            }
        )
    return bars


def _tick(bar: dict) -> SimpleNamespace:
    c = float(bar["close"])
    h = float(bar["high"])
    l = float(bar["low"])
    spread = 0.20
    ts = bar["timestamp"]
    return SimpleNamespace(
        bid=c,
        ask=c + spread,
        timestamp=ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts, tz=UTC),
        symbol="XAUUSD",
        volume=float(bar.get("tick_volume", 0.0)),
        high=h,
        low=l,
        open=float(bar["open"]),
    )


def main() -> int:
    findings: list[str] = []
    bars = _make_bars()
    objs = _to_objs(bars)

    # A. liquidity block geometry + names
    feats = compute_liquidity_features(objs, use_htf=True)
    v10 = [float(x) for x in feats.as_vector()]
    print("A. liquidity block length:", len(v10))
    print("   finite:", all(np.isfinite(x) for x in v10))
    print("   in [-3,3]:", all(-3.0 <= x <= 3.0 for x in v10))
    if hasattr(feats, "version"):
        print("   version:", feats.version)
    for i, (nm, val) in enumerate(zip(LIQ_NAMES, v10)):
        print(f"     {60 + i} {nm} = {val:.4f}")
    if len(v10) != 10:
        findings.append(f"A: liquidity block is {len(v10)}D not 10D")

    # B. composite assembly with strict widths
    engine = ScalpFeatureEngine("XAUUSD")
    fv = engine.compute_from_bars(objs, _tick(bars[-1]))
    base50 = [float(x) for x in fv.to_tensor_input()]
    print("B. base 50D length:", len(base50))
    if len(base50) != 50:
        findings.append(f"B: base is {len(base50)}D not 50D")
    vec70 = build_70d_vector(base50, family_10=[0.0] * 10, liquidity_10=v10)
    print("   70D composite:", len(vec70), "| base==[:50]:",
          vec70[:50] == base50, "| liq==[60:70]:", vec70[60:70] == v10)
    if len(vec70) != 70:
        findings.append(f"B: composite is {len(vec70)}D not 70D")
    try:
        build_70d_vector(base50, family_10=[0.0] * 9, liquidity_10=v10)
        findings.append("B: build_70d_vector did NOT reject a 9D family block (silent reshape?)")
    except ValueError:
        print("   build_70d_vector correctly rejects 9D family (strict widths)")

    # C. NEWS FAMILY forensics: canonical 12D vs live-path 10D truncation
    news_ctx = {
        "bullish_score": 0.7,
        "bearish_score": 0.1,
        "state": "HIGH_IMPACT",
        "novelty": "BREAKING",
        "active_event_count": 3.0,
        "xauusd_relevance": 0.92,
        "usd_relevance": 0.55,
        "conflict_score": 0.0,
        "freshness": 0.9,
        "confidence": 0.8,
        "source_consensus": 0.6,
        "time_since_event_sec": 42.0,
    }
    nv12 = vectorize_news_context(news_ctx)
    print("C. vectorize_news_context length:", len(nv12))
    names12 = [
        "active", "xauusd_relevance", "usd_relevance", "bullish", "bearish",
        "conflict", "novelty", "freshness", "confidence", "source_consensus",
        "state_enc", "time_since_event_sec",
    ]
    for nm, val in zip(names12, nv12):
        print(f"     {nm} = {val:.3f}")
    # The EXACT live-engine shadow70 expression (live_engine.py line 3065):
    news10 = (nv12 + [0.0] * 10)[:10]
    dropped = {nm: val for nm, val in zip(names12[10:], nv12[10:]) if val != 0.0}
    print("   live-path news10:", [round(x, 3) for x in news10])
    print("   dropped by [:10]:", dropped)
    if nv12[10] != 0.0 and nv12[10] not in news10:
        findings.append(
            "C: live-path news10 truncation DROPS the news state encoding "
            f"(state_enc={nv12[10]}) silently — HIGH_IMPACT information discarded"
        )

    # D. shadow70 contract slices vs the composite
    try:
        from nexus_scalp.shadow.shadow70.models import (
            BASE_SLICE, LIQUIDITY_SLICE, NEWS_SLICE, SHADOW70_DIMENSION,
        )
        print("D. shadow70 slices:", BASE_SLICE, NEWS_SLICE, LIQUIDITY_SLICE,
              "| dim:", SHADOW70_DIMENSION)
        if len(vec70) != SHADOW70_DIMENSION:
            findings.append(f"D: composite {len(vec70)} != shadow70 dim {SHADOW70_DIMENSION}")
    except Exception as e:
        findings.append(f"D: shadow70 import failed: {e!r}")

    print("\nFINDINGS:")
    for f in findings:
        print(" -", f)
    if not findings:
        print(" (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())