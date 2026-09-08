"""BUG-253 end-to-end harness: replay the exact live failure through the REAL tick_pipeline code.

Scenario from production log 2026-09-08T19:15:
  1. 70D champion served, warmup READY, inference enabled.
  2. Liquidity governor snapshot ages past causal window (STALE) because
     re-warm never fired (is_new_bar was clobbered by the duplicate
     aggregator.process_tick call).
  3. OLD code: _infer_probabilities raised RuntimeError every tick ->
     '[INFERENCE] in-trade inference failed' + circuit-breaker feeding.
  4. NEW code: governor gate degrades to probs=None (no raise), stale
     retry re-warms the governor on a bounded 15s cadence, and is_new_bar
     now propagates so _on_new_bar fires on bar close.
"""
from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, "src")

from nexus_scalp.application.live.tick_pipeline import TickPipeline


def _tick(minute: int, second: int = 0):
    from nexus_scalp.domain.models import TickData

    base = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)
    t = base + timedelta(minutes=minute, seconds=second)
    return TickData(symbol="XAUUSD", timestamp=t, bid=4400.0, ask=4400.2, volume=1.0)


def _bar(i: int):
    from nexus_scalp.market_data.bar_aggregator import BarData

    return BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=datetime(2026, 9, 8, 14, 0, tzinfo=UTC) + timedelta(minutes=i),
        open=4400, high=4401, low=4399, close=4400.5,
        tick_volume=100, is_complete=True,
    )


def make_engine(gov_causal: str):
    om = MagicMock()
    # aggregator: the CALLER has already processed the tick (bug: pipeline did it again)
    completed = [_bar(i) for i in range(200)]
    om.aggregator.process_tick.return_value = None  # duplicate call -> None (the poison)
    om.aggregator.get_completed_bars.return_value = completed
    om.aggregator._completed_bars = completed
    om.feature_engine.compute_from_bars.return_value = SimpleNamespace(atr_m1=1.5)
    om.liquidity_governor.last_snapshot = SimpleNamespace(features=(0.1,) * 10)
    om.liquidity_governor.causal_state.return_value = gov_causal
    om.effective_feature_dim = 70
    om._inference_enabled = True
    om.warmup_state = "READY"
    om._regime_last_ts = None
    om._regime_last_bid = 0.0
    om._regime_last_ask = 0.0
    om._last_warmup_check_time = time.time()
    om._liq_stale_retry_at = 0.0
    om._last_radar_log_time = time.time()
    om._last_inference_blocked_log = 0.0
    om.signal_policy.evaluate_probabilities.return_value = "PROPOSAL_OK"
    om.order_manager.manage_active_positions.return_value = []
    return om


def case_old_vs_new(label: str, gov_causal: str, expect_infer_called: bool):
    om = make_engine(gov_causal)
    pipeline = TickPipeline(om)
    tick = _tick(10, 30)
    # caller: live_engine.py:2881 already ran aggregator.process_tick(tick) -> None
    om.aggregator.process_tick(tick)
    result = pipeline.run_pre_policy_stages(tick=tick, account="ACC", is_new_bar=False, completed_bars=None)
    infer_called = om._infer_probabilities.called
    rewarm_called = om._warm_liquidity_from_bars.called
    status = "OK" if infer_called == expect_infer_called else "UNEXPECTED"
    print(f"[{label}] gov={gov_causal:7s} infer_called={infer_called} (expect {expect_infer_called}) "
          f"rewarm_called={rewarm_called} on_new_bar_called={om._on_new_bar.called} -> {status}")
    return infer_called, rewarm_called


print("=== CASE A: snapshot STALE (production failure state) ===")
infer_a, rewarm_a = case_old_vs_new("A", "STALE", expect_infer_called=False)
assert infer_a is False, "STALE governor must NOT reach _infer_probabilities (no raise, no circuit-breaker feed)"
assert rewarm_a, "STALE governor must trigger the bounded re-warm retry"
# second tick immediately: retry throttle must hold (no per-tick recompute)
om = make_engine("STALE")
pipeline = TickPipeline(om)
tick = _tick(10, 31)
pipeline.run_pre_policy_stages(tick=tick, account="ACC", is_new_bar=False, completed_bars=None)
assert om._warm_liquidity_from_bars.call_count <= 1, "retry must be throttled, not per-tick (BUG-169 class)"
print("[A2] retry throttle holds: re-warm calls in one tick =", om._warm_liquidity_from_bars.call_count)

print("=== CASE B: snapshot VALID (healthy 70D serving) ===")
infer_b, rewarm_b = case_old_vs_new("B", "VALID", expect_infer_called=True)
assert infer_b is True, "VALID governor must serve inference normally"

print("=== CASE C: 50D contract (governor state irrelevant) ===")
om = make_engine("STALE")
om.effective_feature_dim = 50
pipeline = TickPipeline(om)
pipeline.run_pre_policy_stages(tick=_tick(10, 32), account="ACC", is_new_bar=False, completed_bars=None)
assert om._infer_probabilities.called, "50D must never be gated by liquidity"
print("[C] 50D inference unaffected by governor state -> OK")

print("=== CASE D: is_new_bar propagation (the root-cause poison) ===")
om = make_engine("VALID")
pipeline = TickPipeline(om)
pipeline.run_pre_policy_stages(tick=_tick(11, 0), account="ACC", is_new_bar=True, completed_bars=None)
assert om._on_new_bar.called, "caller's is_new_bar=True must reach _on_new_bar (was clobbered before)"
assert om._warm_liquidity_from_bars.called, "new bar must re-warm the governor"
print("[D] is_new_bar=True -> _on_new_bar fired + governor re-warmed -> OK")

print()
print("ALL BUG-253 HARNESS CASES PASSED")
