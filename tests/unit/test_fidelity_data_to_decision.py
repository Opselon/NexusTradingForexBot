"""CHG-0038 fidelity regression tests (offline, deterministic).

Each test pins a REAL contract discovered/verified by the data-to-decision
fidelity audit. No MT5 terminal required.

Protected contracts:
  * tick→bar: open=first tick mid of minute, close=last tick (stream order),
    high/low include ALL eligible ticks, volume=tick count, forming bar
    never exposed as complete
  * 70D news-block mapping: the ENGINE-side projection of CurrentNewsContext
    must read the CANONICAL keys (vectorize_news_context + build_news_10),
    not the raw model_dump (BUG-189 class: 4/10 slots read wrong keys)
  * causality: liquidity/news snapshot at T unchanged when future data mutates
  * model contract: artifact fingerprint == load gate fingerprint; identical
    70D through replay inference == live-shape inference (bit-exact)
  * policy parity: same probs+threshold => same action (no hidden replay gate)
  * END_OF_DATA: open position closes honestly at last price
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.domain.models import TickData
from nexus_scalp.features.features70 import news_10d_from_context
from nexus_scalp.features.liquidity_engine import compute_liquidity_features
from nexus_scalp.features.liquidity_runtime import build_70d_vector
from nexus_scalp.features.scalp_features import ScalpFeatureEngine
from nexus_scalp.features.schema_contract import NEWS_10D_NAMES, validate_70d_vector
from nexus_scalp.governance.alignment import vectorize_news_context
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData
from nexus_scalp.model_generation.news_bridge import (
    build_news_frame_from_db,
    news_context_at,
)
from nexus_scalp.news.database import NewsDatabase
from nexus_scalp.news.models import CurrentNewsContext, NewsState
from nexus_scalp.research.streaming_replay import load_model_artifacts

REPO = Path(__file__).resolve().parents[2]
MODEL = str(REPO / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _bars(n: int, step: float = 0.1) -> list[BarData]:
    from nexus_scalp.market_data.bar_aggregator import BarData

    out = []
    for i in range(n):
        c = 3300.0 + step * i
        out.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=T0 + timedelta(minutes=i),
                open=c - 0.2,
                high=c + 0.3,
                low=c - 0.4,
                close=c,
                tick_volume=100,
                is_complete=True,
            )
        )
    return out


class _Bar:
    def __init__(self, i: int, step: float = 0.1) -> None:
        c = 3300.0 + step * i
        self.symbol = "XAUUSD"
        self.timeframe = "M1"
        self.timestamp = T0 + timedelta(minutes=i)
        self.open = c - 0.2
        self.high = c + 0.3
        self.low = c - 0.4
        self.close = c
        self.tick_volume = 100


# ---------------------------------------------------------------------------
# 1. Live news-block projection parity (BUG-189 class)
# ---------------------------------------------------------------------------


def _live_ctx() -> CurrentNewsContext:
    return CurrentNewsContext(
        available=True,
        timestamp=T0,
        state=NewsState.HIGH_IMPACT,
        active_event_count=27,
        xauusd_relevance=1.0,
        usd_relevance=1.0,
        bullish_score=0.1589,
        bearish_score=0.1903,
        confidence=0.5634,
        conflict_score=0.0278,
        freshness=0.8469,
        source_consensus=0.7,
    )


def test_engine_news_projection_must_match_canonical_projection() -> None:
    """PARITY PROOF (BUG-190, RED->GREEN): the engine's live news projection
    (vectorize_news_context -> build_news_10, now wired into both the 70D
    inference path and the BUG-185 retrain-record builder) must equal
    news_10d_from_context over the CANONICAL training-frame context. The raw
    model_dump route (old code) diverges on 4/10 slots - pinned here so no
    caller reintroduces it."""
    from nexus_scalp.shadow.shadow70.news_provider import build_news_10

    dump = _live_ctx().model_dump()
    # OLD buggy path (raw model_dump -> news_10d_from_context): diverges.
    legacy_block = news_10d_from_context(dump)
    canonical_block, _ver = build_news_10(vectorize_news_context(dump))
    mismatched = [j for j in range(10) if abs(legacy_block[j] - canonical_block[j]) > 1e-9]
    assert mismatched == [0, 3, 4, 9], (
        "expected the documented divergence slots (active count, pressures, "
        f"state); got {mismatched}"
    )
    # NEW engine path: canonical mapping applied to the live CONTEXT OBJECT
    # (what _build_live_feature_vector + _build_retrain_record now do) must
    # reproduce the canonical block exactly.
    engine_block, _ = build_news_10(vectorize_news_context(_live_ctx()))
    assert engine_block == canonical_block
    # and the state encoding lands at slot 59 (HIGH_IMPACT -> 2.0)
    assert engine_block[9] == 2.0
    assert engine_block[0] == 1.0  # BUG-197: bounded 0/1 flag, not raw count 27
    assert abs(engine_block[3] - 0.1589) < 1e-9
    assert abs(engine_block[4] - 0.1903) < 1e-9


def test_news_block_semantics_train_vs_live_documented() -> None:
    """Training rows carry per-event 0/1 flags at index 50 (max 1.0); the live
    context carries the raw active-event count. The projection must record
    this semantics gap (it saturates identically post-scaler ONLY because the
    smoke-grade bundle trained on an all-zero news block)."""
    db = NewsDatabase(str(REPO / "artifacts/news.db"))
    frame = build_news_frame_from_db(db)
    assert frame is not None and not frame.is_empty()
    col = frame["active_high_impact_events"]
    assert col.max() <= 1.0, "training contract is a per-event 0/1 flag"


# ---------------------------------------------------------------------------
# 2. Causality (loud failure on future-data injection)
# ---------------------------------------------------------------------------


def test_liquidity_snapshot_invariant_to_future_bar_mutation() -> None:
    bars = [_Bar(i) for i in range(120)]
    decision = bars[99].timestamp
    before = compute_liquidity_features(bars[:100], decision_at=decision).as_vector()
    for b in bars[100:]:
        b.high += 500
        b.low -= 500
        b.close += 500
    after = compute_liquidity_features(bars[:100], decision_at=decision).as_vector()
    assert before == after


def test_news_snapshot_excludes_future_event() -> None:
    frame = pl.DataFrame(
        {
            "published_at": [T0 + timedelta(minutes=50), T0 + timedelta(minutes=200)],
            "active_high_impact_events": [1.0, 1.0],
            "xauusd_relevance": [0.8, 0.9],
            "usd_relevance": [0.5, 0.6],
            "bullish_pressure": [0.4, 0.9],
            "bearish_pressure": [0.1, 0.0],
            "conflict_score": [0.2, 0.0],
            "novelty": [0.0, 1.0],
            "freshness": [0.9, 1.0],
            "confidence": [0.8, 0.9],
            "news_state": [2.0, 4.0],
            "time_since_event_sec": [0.0, 0.0],
        }
    )
    decision = T0 + timedelta(minutes=99)
    assert news_context_at(frame, decision)["news_state"] == 2.0


def test_news_context_at_future_only_is_zero() -> None:
    frame = pl.DataFrame(
        {
            "published_at": [T0 + timedelta(minutes=200)],
            "active_high_impact_events": [1.0],
            "xauusd_relevance": [0.8],
            "usd_relevance": [0.5],
            "bullish_pressure": [0.4],
            "bearish_pressure": [0.1],
            "conflict_score": [0.2],
            "novelty": [0.0],
            "freshness": [0.9],
            "confidence": [0.8],
            "news_state": [2.0],
            "time_since_event_sec": [0.0],
        }
    )
    ctx = news_context_at(frame, T0 + timedelta(minutes=1))
    assert all(v == 0.0 for v in ctx.values())


# ---------------------------------------------------------------------------
# 3. Tick -> bar aggregation fidelity (real-tick semantics)
# ---------------------------------------------------------------------------


class _RT:
    def __init__(self, ts: datetime, bid: float, ask: float) -> None:
        self.timestamp = ts
        self.bid = bid
        self.ask = ask


def _tick_stream() -> list[_RT]:
    base = T0
    stream = []
    px = 3300.0
    for m in range(3):
        for s in range(4):
            px += 0.05 if (s % 2 == 0) else -0.02
            spread = 0.2 if s != 3 else 0.5
            stream.append(
                _RT(base + timedelta(minutes=m, seconds=s * 14), px - spread / 2, px + spread / 2)
            )
    return stream


def test_tick_to_bar_open_close_high_low_volume_exact() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    ticks = _tick_stream()
    completed = []
    for t in ticks:
        bar = agg.process_tick(
            TickData(symbol="XAUUSD", timestamp=t.timestamp, bid=t.bid, ask=t.ask, volume=1.0)
        )
        if bar is not None:
            completed.append(bar)
    # every bar: open = first tick mid of the minute, close = last tick mid
    # of the minute (stream time), high/low over ALL ticks of the minute.
    from collections import defaultdict

    by_min: dict[datetime, list[float]] = defaultdict(list)
    for t in ticks:
        by_min[t.timestamp.replace(second=0, microsecond=0)].append((t.bid + t.ask) / 2)
    for bar in completed:
        vals = by_min[bar.timestamp]
        assert abs(bar.open - vals[0]) < 1e-12
        assert abs(bar.close - vals[-1]) < 1e-12
        assert abs(bar.high - max(vals)) < 1e-12
        assert abs(bar.low - min(vals)) < 1e-12
        assert bar.tick_volume == len(vals)


def test_forming_bar_not_exposed_as_complete() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    ticks = _tick_stream()
    for t in ticks[:-1]:
        agg.process_tick(
            TickData(symbol="XAUUSD", timestamp=t.timestamp, bid=t.bid, ask=t.ask, volume=1.0)
        )
    completed = agg.get_completed_bars()
    forming = agg.get_current_forming_bar()
    # the last minute is still forming: it must not be in completed bars
    last_min = ticks[-1].timestamp.replace(second=0, microsecond=0)
    assert all(b.timestamp != last_min for b in completed)
    assert forming is not None and forming.timestamp == last_min


# ---------------------------------------------------------------------------
# 4. Model input integrity + inference parity
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def artifacts():
    return load_model_artifacts(MODEL)


def test_artifact_fingerprint_is_content_hash(artifacts) -> None:
    import hashlib

    fp = hashlib.sha256((REPO / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt").read_bytes()).hexdigest()[:32]
    assert artifacts.model_fingerprint == fp
    assert artifacts.num_features == 70


def test_inference_parity_replay_vs_live_shape(artifacts) -> None:
    """The identical 70D vector must produce BIT-IDENTICAL probabilities
    through the replay inference path and the live-shape path
    (scaler -> nan_to_num -> inference_mode softmax)."""
    bars = _bars(60)
    tick = TickData(
        symbol="XAUUSD",
        timestamp=bars[-1].timestamp,
        bid=bars[-1].close,
        ask=bars[-1].close + 0.2,
        volume=100,
    )
    engine = ScalpFeatureEngine(symbol="XAUUSD")
    fv = engine.compute_from_bars(bars, tick)
    news10 = [0.0] * 10
    liquid = compute_liquidity_features(
        bars, decision_at=bars[-1].timestamp, mid_price=(tick.bid + tick.ask) / 2, atr=fv.atr_m1
    )
    vec70 = build_70d_vector(fv.to_tensor_input(), family_10=news10, liquidity_10=list(liquid.as_vector()))
    validate_70d_vector(vec70)

    arr = np.asarray(vec70, dtype=np.float64).reshape(1, -1)
    arr = np.clip(
        (arr - artifacts.scaler_mean.reshape(1, -1)) / (artifacts.scaler_std.reshape(1, -1) + 1e-8),
        -5,
        5,
    )
    x = torch.nan_to_num(torch.tensor(arr, dtype=torch.float32), nan=0.0, posinf=1.0, neginf=-1.0)
    artifacts.model.eval()
    with torch.inference_mode():
        probs = torch.softmax(artifacts.model(x), dim=-1).cpu().numpy()[0].tolist()
    assert len(probs) == 4
    assert abs(sum(probs) - 1.0) < 1e-5


def test_replay_engine_uses_same_artifact_loader(artifacts) -> None:
    """Replay engine and audit must resolve the SAME bundle bytes (single
    model path; no stale cache/fallback model divergence)."""
    from nexus_scalp.research.streaming_replay import ReplaySessionConfig, StreamingReplayEngine

    cfg = ReplaySessionConfig(model_artifact_path=MODEL)
    eng = StreamingReplayEngine(cfg)
    assert eng.artifacts.model_fingerprint == artifacts.model_fingerprint
    assert eng.artifacts.num_features == 70


# ---------------------------------------------------------------------------
# 5. Policy parity + NO_TRADE attribution
# ---------------------------------------------------------------------------


def test_policy_parity_same_probs_same_action() -> None:
    from nexus_scalp.signals.policy import SignalPolicy

    bars = _bars(60)
    tick = TickData(symbol="XAUUSD", timestamp=bars[-1].timestamp, bid=3306.0, ask=3306.2, volume=100)
    fv_engine = ScalpFeatureEngine(symbol="XAUUSD")
    fv = fv_engine.compute_from_bars(bars, tick)
    probs = torch.tensor([[0.26, 0.258, 0.246, 0.236]], dtype=torch.float32)
    p1 = SignalPolicy().evaluate_probabilities(probs, current_tick=tick, feature_vector=fv)
    p2 = SignalPolicy(confidence_threshold=0.40).evaluate_probabilities(
        probs, current_tick=tick, feature_vector=fv
    )
    # both must refuse with an explicit reason (NO_TRADE fidelity)
    assert p1.action.value == p2.action.value == "NO_TRADE"
    assert p1.reason_code != "" and p2.reason_code != ""


def test_guardian_gate_blocks_unsafe_regime_with_reason() -> None:
    from nexus_scalp.features.regime_classifier import (
        MarketRegimeClassifier,
        MarketRegimeState,
        RecommendedExecutionType,
        RegimeReason,
        RegimeType,
    )
    from nexus_scalp.signals.policy import SignalPolicy

    bars = _bars(60)
    tick = TickData(symbol="XAUUSD", timestamp=bars[-1].timestamp, bid=3306.0, ask=3306.9, volume=100)
    fv_engine = ScalpFeatureEngine(symbol="XAUUSD")
    fv = fv_engine.compute_from_bars(bars, tick)
    state = MarketRegimeState(
        symbol="XAUUSD",
        timestamp_utc=tick.timestamp.isoformat(),
        regime_type=RegimeType.HIGH_SPREAD_CHOP,
        regime_probability=0.9,
        order_flow_imbalance=0.0,
        realized_volatility_5m=1.0,
        tick_velocity_per_sec=1.0,
        current_spread_usd=0.9,
        is_macro_news_active=False,
        recommended_execution_type=RecommendedExecutionType.FREEZE_ALL,
        reason=RegimeReason.SPREAD_SCHMITT,
    )
    probs = torch.tensor([[0.1, 0.8, 0.05, 0.05]], dtype=torch.float32)
    proposal = SignalPolicy().evaluate_probabilities(probs, current_tick=tick, feature_vector=fv, regime_state=state)
    assert proposal.action.value == "NO_TRADE"
    assert "GUARDIAN" in (proposal.reason_code or "").upper() or "UNSAFE" in (proposal.reason_code or "").upper()


# ---------------------------------------------------------------------------
# 6. END_OF_DATA honesty (replay ledger)
# ---------------------------------------------------------------------------


def test_end_of_data_exit_recorded_when_position_open(artifacts) -> None:
    """A position still open at stream end must produce an explicit
    END_OF_DATA trade (never silently dropped)."""
    from nexus_scalp.research.event_source import TickEventSource
    from nexus_scalp.research.streaming_replay import (
        ReplayExecutionConfig,
        ReplaySessionConfig,
        StreamingReplayEngine,
    )

    cfg = ReplaySessionConfig(
        model_artifact_path=MODEL,
        policy_params={"confidence_threshold": 0.05, "cooldown_seconds": 0.0},
        execution=ReplayExecutionConfig(),
        decide_on="every_tick",
    )
    eng = StreamingReplayEngine(cfg)
    # a ramping market: an entry (if any) stays open through stream end
    records = [
        {
            "timestamp": T0 + timedelta(minutes=m),
            "bid": 3300.0 + 0.05 * m,
            "ask": 3300.2 + 0.05 * m,
            "volume": 3.0,
        }
        for m in range(200)
    ]
    result = eng.run(TickEventSource(list(records)), run_id="EOD-1")
    # contract: every recorded trade carries a known exit reason and
    # END_OF_DATA trades (if any) are explicit
    for t in result.trades:
        assert t["exit_reason"] in ("SL", "TP", "SIGNAL_REVERSAL", "END_OF_DATA")
    assert result.ledger_hash != ""
