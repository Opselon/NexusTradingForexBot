"""TASK-03 perf probe: measure 50D vs 70D construction / validation / replay.

Run: .venv/Scripts/python.exe scratch/task03_perf_probe.py
Output: p50/p95/max for each stage (brief 49).
"""

import sys
import time
from datetime import UTC, datetime, timedelta

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot")

import polars as pl

from nexus_scalp.features.features70 import assemble_70d
from nexus_scalp.features.inference_validator import InferenceValidator
from nexus_scalp.features.runtime70 import Runtime70Hook
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.model_generation.replay import replay_70d_vector
from tests.helpers.golden70d import _to_rows
from tests.helpers.liquidity_fixtures import steady_bars


def pcts(samples):
    s = sorted(samples)
    n = len(s)
    return {
        "p50_ms": round(s[int(n * 0.50)], 3),
        "p95_ms": round(s[min(int(n * 0.95), n - 1)], 3),
        "max_ms": round(max(s), 3),
    }


def measure(fn, n=25):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1e3)
    return pcts(out)


if __name__ == "__main__":
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    bars = steady_bars(200, price=3300.0, step=0.1, t0=t0)
    df = pl.DataFrame(_to_rows(bars, t0))
    engine = ScalpFeatureEngine(symbol="XAUUSD")

    from nexus_scalp.domain.models import TickData

    last = bars[-1]
    tick = TickData(
        symbol="XAUUSD",
        timestamp=last.timestamp,
        bid=last.close,
        ask=last.close + 0.2,
        volume=last.tick_volume,
    )

    def fifty():
        engine.compute_from_bars(bars, tick).to_tensor_input()

    def seventy():
        fv = engine.compute_from_bars(bars, tick)
        assemble_70d(
            base50=fv.to_tensor_input(),
            news10=[0.1] * 10,
            liquidity10=[0.2] * 10,
            symbol="XAUUSD",
            timeframe="M1",
            timestamp_utc=last.timestamp,
            news_available=True,
            liquidity_available=True,
        ).validate(context="perf")

    conv = InferenceValidator(
        expected_schema_id="scalp_v3", expected_dimension=70, expected_schema_hash=""
    )
    vec = [0.0] * 50 + [0.1] * 10 + [0.2] * 10

    def validate():
        conv.validate(
            vec,
            news_status="FEATURE_AVAILABLE",
            liquidity_status="FEATURE_AVAILABLE",
            context="perf",
        )

    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=True)
    news_ctx = {
        "active_high_impact_events": 1.0,
        "xauusd_relevance": 0.8,
        "usd_relevance": 0.5,
        "bullish_pressure": 0.4,
        "bearish_pressure": 0.1,
        "conflict_score": 0.2,
        "novelty": 0.0,
        "freshness": 1.0,
        "confidence": 0.9,
        "source_consensus": 0.7,
        "news_state": 2.0,
    }

    def hook_snap():
        fv = engine.compute_from_bars(bars, tick)
        hook.compute_snapshot(
            completed_bars=bars,
            base50=list(fv.to_tensor_input()),
            news_context=news_ctx,
            timestamp_utc=datetime.now(UTC),
            context="perf",
        )

    def replay():
        replay_70d_vector(df, timestamp=t0 + timedelta(minutes=199), news_frame=None)

    print("== 70D TASK-3 PERF (brief 49) ==")
    print("50D base construction   :", measure(fifty))
    print("70D assembly+validate   :", measure(seventy))
    print("validator per-tick      :", measure(validate))
    print("runtime70 live snapshot :", measure(hook_snap))
    print("replay_70d full rebuild :", measure(replay, n=10))
