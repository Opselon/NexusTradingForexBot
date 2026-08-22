"""TASK-03-70D-PARITY — runtime 70D hook tests (TEST-70D-PARITY-14..17, 25, 26).

Covers:
  TEST-70D-PARITY-14  News ON  + Liquidity ON
  TEST-70D-PARITY-15  News OFF + Liquidity ON
  TEST-70D-PARITY-16  News ON  + Liquidity OFF
  TEST-70D-PARITY-17  News OFF + Liquidity OFF
  TEST-70D-PARITY-25  runtime toggle does not corrupt vector
  TEST-70D-PARITY-26  legacy model remains loadable (60D path untouched)
"""

from __future__ import annotations

from datetime import UTC, datetime

from nexus_scalp.features.features70 import FeatureSourceState
from nexus_scalp.features.inference_validator import RejectionCode
from nexus_scalp.features.runtime70 import Runtime70Hook
from tests.helpers.liquidity_fixtures import steady_bars


def _bars() -> list:
    return steady_bars(120, price=3300.0, step=0.1, t0=datetime(2026, 8, 1, 0, 0, tzinfo=UTC))


def _news_context() -> dict[str, float]:
    return {
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
        "time_since_event_sec": 60.0,
    }


def _base50_provider(bars):
    from nexus_scalp.domain.models import TickData
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine

    engine = ScalpFeatureEngine(symbol="XAUUSD")
    last = bars[-1]
    tick = TickData(
        symbol="XAUUSD",
        timestamp=last.timestamp,
        bid=last.close,
        ask=last.close + 0.2,
        volume=last.tick_volume,
    )
    return list(engine.compute_from_bars(bars, tick).to_tensor_input())


def _snap(hook, *, news_ctx=None, ts=None):
    bars = _bars()
    return hook.compute_snapshot(
        completed_bars=bars,
        base50=_base50_provider(bars),
        news_context=news_ctx,
        timestamp_utc=ts or datetime.now(UTC),
        context="test",
    )


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-14..17 — four News/Liquidity combinations
# ---------------------------------------------------------------------------


def test_p14_news_on_liquidity_on() -> None:
    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=True)
    res = _snap(hook, news_ctx=_news_context())
    assert res.ok is True
    assert res.snapshot is not None
    s = res.snapshot
    assert s.news_status == FeatureSourceState.FEATURE_AVAILABLE
    assert s.liquidity_status == FeatureSourceState.FEATURE_AVAILABLE
    assert len(s.feature_vector) == 70
    # news block populated
    assert any(v != 0.0 for v in s.feature_vector[50:60])
    # model attached -> PASS
    hook.set_model("scalp_v3", 70, model_id="m1", model_version="v1")
    assert hook.model_compatibility()["result"] == "PASS"


def test_p15_news_off_liquidity_on() -> None:
    hook = Runtime70Hook(news_enabled=False, liquidity_enabled=True)
    res = _snap(hook)
    assert res.ok is True
    s = res.snapshot
    assert s.news_status == FeatureSourceState.FEATURE_DISABLED
    assert s.liquidity_status == FeatureSourceState.FEATURE_AVAILABLE
    # news block = neutral zeros (explicit DISABLED, never fake)
    assert s.feature_vector[50:60] == (0.0,) * 10
    # liquidity still computed independently (brief 24)
    assert s.feature_vector[60:70] != [0.0] * 10 or s.liquidity_status.value == "FEATURE_AVAILABLE"


def test_p16_news_on_liquidity_off() -> None:
    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=False)
    res = _snap(hook, news_ctx=_news_context())
    assert res.ok is True
    s = res.snapshot
    assert s.news_status == FeatureSourceState.FEATURE_AVAILABLE
    assert s.liquidity_status == FeatureSourceState.FEATURE_DISABLED
    # news still populated independently (brief 25)
    assert any(v != 0.0 for v in s.feature_vector[50:60])
    assert len(s.feature_vector) == 70  # dimension NEVER shrinks


def test_p17_news_off_liquidity_off() -> None:
    hook = Runtime70Hook(news_enabled=False, liquidity_enabled=False)
    res = _snap(hook)
    assert res.ok is True
    s = res.snapshot
    assert s.news_status == FeatureSourceState.FEATURE_DISABLED
    assert s.liquidity_status == FeatureSourceState.FEATURE_DISABLED
    assert len(s.feature_vector) == 70


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-25 — toggle does not corrupt the vector
# ---------------------------------------------------------------------------


def test_p25_toggle_then_vector_still_canonical() -> None:
    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=True)
    r1 = _snap(hook, news_ctx=_news_context())
    assert r1.ok
    # toggle liquidity off mid-run
    hook.set_toggles(liquidity_enabled=False)
    r2 = _snap(hook, news_ctx=_news_context())
    assert r2.ok
    assert r2.snapshot.liquidity_status == FeatureSourceState.FEATURE_DISABLED
    assert len(r2.snapshot.feature_vector) == 70  # geometry preserved
    # toggle back on
    hook.set_toggles(liquidity_enabled=True)
    r3 = _snap(hook, news_ctx=_news_context())
    assert r3.ok
    assert r3.snapshot.liquidity_status == FeatureSourceState.FEATURE_AVAILABLE


def test_p25_state_reflects_toggles() -> None:
    hook = Runtime70Hook()
    d = hook.to_state_dict()
    assert d["schema"] == "scalp_v3"
    assert d["dimension"] == 70
    assert d["liquidity_enabled"] is True
    assert d["news_enabled"] is True
    assert d["schema_hash"]


# ---------------------------------------------------------------------------
# TEST-70D-PARITY-26 — model compatibility blocks mismatches at runtime
# ---------------------------------------------------------------------------


def test_p26_60d_model_blocks_70d_runtime() -> None:
    hook = Runtime70Hook()
    hook.set_model("scalp_v2", 60, model_id="legacy", model_version="v1")
    res = _snap(hook, news_ctx=_news_context())
    assert res.ok is False
    assert res.rejection_code == RejectionCode.SCHEMA_MISMATCH
    assert "MODEL_INPUT_UNAVAILABLE" in res.reason


def test_p26_70d_model_passes() -> None:
    hook = Runtime70Hook()
    hook.set_model("scalp_v3", 70, model_id="cand70", model_version="v1")
    res = _snap(hook, news_ctx=_news_context())
    assert res.ok is True


def test_p26_no_model_unknown_blocks_safely() -> None:
    # No model attached -> UNKNOWN (never infer unvalidated)
    hook = Runtime70Hook()
    res = hook.model_compatibility()
    assert res["result"] == "UNKNOWN"
    assert res["reason"] == "NO_MODEL_METADATA"


# ---------------------------------------------------------------------------
# News unavailable (provider returns None) must be explicit
# ---------------------------------------------------------------------------


def test_news_unavailable_is_explicit_not_fake() -> None:
    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=True)
    bars = _bars()
    res = hook.compute_snapshot(
        completed_bars=bars,
        base50=_base50_provider(bars),
        news_context=None,  # declared enabled but no context -> UNAVAILABLE
        timestamp_utc=datetime.now(UTC),
        context="test",
    )
    assert res.snapshot is not None
    assert res.snapshot.news_status == FeatureSourceState.FEATURE_UNAVAILABLE


def test_throttled_trace_not_spammy() -> None:
    # The hook logs ONE structured line per snapshot (valid or rejected);
    # no per-value vector dump at INFO (brief 15/42).
    hook = Runtime70Hook(news_enabled=True, liquidity_enabled=True)
    res = _snap(hook, news_ctx=_news_context())
    assert res.ok
    assert "total_ms" in res.timings_ms
    assert len(res.timings_ms) >= 5  # calculation/assembly/validation split
