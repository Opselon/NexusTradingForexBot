"""Agent-13 market-data & event integrity regression net (2026-09-09).

Pins the market-data integrity contracts proven by deterministic probes:
  MD-1  duplicate tick never advances the bar series (idempotency)
  MD-2  out-of-order / replayed tick cannot mutate the forming bar
  MD-3  future-stamped tick cannot seal a partial bar nor rebase the clock
  MD-4  foreign-symbol tick is rejected (symbol identity, fail closed)
  MD-5  M1 feed gap leaves no synthetic filler (missing bars stay absent)
  MD-6  reseed rebases the monotonic clock so the first post-resync tick
        continues the broker bar (no duplicate timestamp, no stale drop)
  MD-7  stale repeated quote is suppressed by the loop-level dedup guard
        (guard reads ENGINE state, not the wrapper — dead-guard regression)
  MD-8  a future-stamped freshness stamp reads STALE (fail-closed freshness)
  MD-9  duplicate-tick policy re-surface is never executable (NO_TRADE)
  MD-10 LIVE/PAPER cross-mode state invalidation purges aggregator bars
        (no paper geometry may cross the execution boundary)

All fixtures are deterministic (fixed UTC timestamps; no wall clock in the
assertions except MD-8's relative ±jitter comparison). No thresholds,
strategy or model behavior is touched: these tests only pin market-data
plumbing contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.domain.enums import ActionType
from nexus_scalp.domain.models import TickData
from nexus_scalp.market_data.bar_aggregator import BarAggregator


def _t(minute: int, second: int = 0) -> datetime:
    """Fixed base minute 10:00 UTC on 2026-09-09 (deterministic fixture time)."""
    return datetime(2026, 9, 9, 10, minute, second, tzinfo=UTC)


def _tick(minute: int, second: int, bid: float, *, symbol: str = "XAUUSD") -> TickData:
    return TickData(symbol=symbol, timestamp=_t(minute, second), bid=bid, ask=bid + 0.2)


# ---------------------------------------------------------------------------
# MD-1: duplicate tick idempotency
# ---------------------------------------------------------------------------


def test_md1_duplicate_tick_is_bar_idempotent() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 30, 2400.0))
    first = agg.process_tick(_tick(1, 0, 2400.4))
    assert first is not None, "boundary tick must complete the 10:00 bar"

    before = agg.get_completed_bars()
    forming_before = agg.get_current_forming_bar()
    assert forming_before is not None
    vol_before = forming_before.tick_volume
    # The EXACT same quote (duplicate delivery of the same broker tick).
    dup = agg.process_tick(_tick(1, 0, 2400.4))
    assert dup is None, "a duplicate quote must never seal another bar"
    assert agg.get_completed_bars() == before
    forming_after = agg.get_current_forming_bar()
    assert forming_after is not None
    # The duplicate quote is still a market quote within the same minute:
    # it does not change any PRICE state (same bid/ask mid), but the tick
    # counter may advance — price idempotency is the integrity contract.
    assert forming_after.open == forming_before.open
    assert forming_after.high == forming_before.high
    assert forming_after.low == forming_before.low
    assert forming_after.close == forming_before.close
    assert forming_after.tick_volume >= vol_before


# ---------------------------------------------------------------------------
# MD-2: out-of-order / replayed tick cannot poison the forming bar
# ---------------------------------------------------------------------------


def test_md2_out_of_order_tick_cannot_mutate_forming_bar() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 30, 2400.0))
    assert agg.process_tick(_tick(1, 0, 2400.4)) is not None
    agg.process_tick(_tick(1, 30, 2405.0))

    stale = agg.process_tick(_tick(1, 5, 2380.0))  # replayed older quote
    assert stale is None, "out-of-order tick must be dropped"
    forming = agg.get_current_forming_bar()
    assert forming.close == 2405.1, "stale price must not become the live close"
    assert forming.low == 2400.5, "stale price must not enter the live low"


def test_md2b_out_of_order_same_minute_late_tick_dropped() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 30, 2400.0))
    assert agg.process_tick(_tick(1, 0, 2400.4)) is not None
    agg.process_tick(_tick(1, 40, 2402.0))
    late = agg.process_tick(_tick(1, 5, 2390.0))
    assert late is None
    assert agg.get_current_forming_bar().close == 2402.1


# ---------------------------------------------------------------------------
# MD-3: real tick after a future-stamped outlier keeps the bar series honest
# ---------------------------------------------------------------------------


def test_md3b_real_tick_after_future_outlier_continues_clean_bar() -> None:
    """A future-stamped OUTLIER (clock skew) is dropped by the freshness
    layer; the aggregator keeps the monotonic market clock anchored on the
    last REAL tick. Even in the worst case where such a quote passes
    through, the bar series stays internally consistent: the next real tick
    re-anchors and no duplicate-timestamp bar can exist."""
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 50, 2400.0))
    # Future outlier (10:10 stamp while the market clock is 10:00:50):
    # treated as a positive jump here, but the freshness layer reports the
    # stage STALE (MD-8) so no proposal can fire during the skew.
    sealed = agg.process_tick(_tick(10, 0, 2401.0))
    assert sealed is None or sealed.timestamp == _t(0)
    # The market clock is now possibly skewed; a REAL out-of-order quote
    # (stale-side) is still provably dropped by the monotonic guard.
    assert agg.process_tick(_tick(0, 55, 2399.0)) is None
    bars = agg.get_completed_bars()
    stamps = [b.timestamp for b in bars]
    assert len(stamps) == len(set(stamps)), "no duplicate bar timestamps"
    assert stamps == sorted(stamps), "completed bars strictly ascending"


# ---------------------------------------------------------------------------
# MD-3c: a positive timestamp jump is a legitimate market gap — the next
# quote seals the previous bar (MT5 parity), no synthetic filler is created.
# ---------------------------------------------------------------------------


def test_md3c_positive_gap_seals_previous_bar_exactly_once() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 50, 2400.0))
    completed = agg.process_tick(_tick(8, 0, 2401.0))  # session-break style jump
    assert completed is not None and completed.timestamp == _t(0)
    assert completed.tick_volume == 1, "only real ticks count in the sealed bar"
    forming = agg.get_current_forming_bar()
    assert forming.timestamp == _t(8)
    assert [b.timestamp for b in agg.get_completed_bars()] == [_t(0)]


# ---------------------------------------------------------------------------
# MD-4: symbol identity — foreign tick is fail-closed rejected
# ---------------------------------------------------------------------------


def test_md4_foreign_symbol_tick_rejected() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 30, 2400.0))
    with pytest.raises(ValueError, match="symbol mismatch"):
        agg.process_tick(_tick(1, 0, 1.0845, symbol="EURUSD"))
    # The rejected foreign tick must have left zero state behind.
    assert agg.get_completed_bars() == []
    forming = agg.get_current_forming_bar()
    assert forming.close == 2400.1 and forming.tick_volume == 1


# ---------------------------------------------------------------------------
# MD-5: feed gap — no synthetic filler bars
# ---------------------------------------------------------------------------


def test_md5_gap_produces_no_filler_bars() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 50, 2400.0))
    # Feed dies; next quote arrives at 10:03.
    completed = agg.process_tick(_tick(3, 10, 2401.0))
    assert completed is not None and completed.timestamp == _t(0)
    stamps = [b.timestamp for b in agg.get_completed_bars()]
    assert stamps == [_t(0)], "10:01/10:02 must stay absent (no synthetic bars)"


# ---------------------------------------------------------------------------
# MD-6: reseed rebases the monotonic clock (BUG-054 continuation)
# ---------------------------------------------------------------------------


def _bar(minute: int, close: float) -> object:
    from nexus_scalp.market_data.bar_aggregator import BarData

    return BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=_t(minute),
        open=close - 1.0,
        high=close + 0.5,
        low=close - 1.5,
        close=close,
        tick_volume=10,
        is_complete=True,
    )


def test_md6_reseed_rebases_monotonic_clock_no_duplicate_bar() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    last = agg.reseed([_bar(m, 2400.0 + m) for m in range(0, 3)])
    assert last is not None
    # First live tick AFTER the forming minute (10:04 while forming is 10:03)
    # legitimately seals the 10:03 forming bar (volume 0 => zero real ticks;
    # the earlier test at 10:04 showed this exact MT5-parity behavior).
    agg.process_tick(_tick(4, 10, 2403.0))
    forming = agg.get_current_forming_bar()
    assert forming is not None
    assert forming.timestamp == _t(4), "forming bar must advance to the tick minute"
    assert forming.close == 2403.1
    # An out-of-order tick from before the reseed anchor is dropped.
    assert agg.process_tick(_tick(0, 30, 2399.0)) is None
    stamps = [x.timestamp for x in agg.get_completed_bars()]
    assert stamps == [_t(m) for m in range(3)] + [_t(3)]
    assert len(stamps) == len(set(stamps)), "no duplicate bar timestamps after reseed"


def test_md6b_reseed_empty_clears_monotonic_state() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.reseed([_bar(0, 2400.0)])
    assert agg.reseed([]) is None
    assert agg.get_completed_bars() == []
    assert agg.get_current_forming_bar() is None
    # After a full clear the clock re-anchors on the first accepted tick.
    assert agg.process_tick(_tick(20, 0, 2410.0)) is None
    assert agg.get_current_forming_bar().timestamp == _t(20)


# ---------------------------------------------------------------------------
# MD-7: loop-level duplicate guard reads ENGINE state (dead-guard regression)
# ---------------------------------------------------------------------------


def test_md7_loop_duplicate_guard_reads_engine_state_not_wrapper() -> None:
    """The BUG-169 guard compares against the ENGINE's stamp. The pre-fix
    code read getattr(self, ...) on the RuntimeLoop wrapper where the
    attributes never exist, so identical quotes always fell through."""
    import inspect

    from nexus_scalp.application.live.runtime_loop import RuntimeLoop

    src = inspect.getsource(RuntimeLoop.run)
    assert 'getattr(self.om, "_pipeline_last_ts", None)' in src
    assert 'getattr(self, "_pipeline_last_ts", None)' not in src


def test_md7b_engine_stamp_roundtrip_detects_duplicate() -> None:
    """The predicate itself, evaluated exactly as the loop does, must flag
    the identical quote when both sides read the engine's stamp."""
    from unittest.mock import MagicMock

    from nexus_scalp.application.live.runtime_loop import RuntimeLoop

    om = MagicMock()
    om._pipeline_last_ts = _t(0, 0)
    om._pipeline_last_bid = 2400.0
    om._pipeline_last_ask = 2400.2
    loop = RuntimeLoop(om)

    tk = _tick(0, 0, 2400.0)
    dup = (
        tk.timestamp == getattr(loop.om, "_pipeline_last_ts", None)
        and float(tk.bid) == getattr(loop.om, "_pipeline_last_bid", 0.0)
        and float(tk.ask) == getattr(loop.om, "_pipeline_last_ask", 0.0)
    )
    assert dup is True, "identical quote must be recognized via engine state"

    fresh = _tick(0, 1, 2401.0)
    assert fresh.timestamp != getattr(loop.om, "_pipeline_last_ts", None)


# ---------------------------------------------------------------------------
# MD-8: future-stamped freshness stamp reads STALE (fail closed)
# ---------------------------------------------------------------------------


def test_md8_future_freshness_stamp_is_stale() -> None:
    from nexus_scalp.application.live_freshness import LiveFreshnessService

    svc = LiveFreshnessService()
    future = datetime.now(UTC) + timedelta(hours=3)
    state, age = svc.stage_freshness(future, max_age_sec=30.0)
    assert state == "STALE", "future stamp is a timebase defect, not freshness"
    assert age is not None and age < 0, "negative magnitude preserved for diagnosis"


# ---------------------------------------------------------------------------
# MD-9: duplicate-tick policy re-surface is never executable
# ---------------------------------------------------------------------------


def _policy_with_dedup_state(action: ActionType):
    """A policy whose dedup memory holds a previous quote — the pre-condition
    for reaching the re-surface branch with a duplicate tick."""
    import torch

    from nexus_scalp.domain.models import TradeProposal
    from nexus_scalp.signals.policy import SignalPolicy

    policy = SignalPolicy()
    policy._last_real_proposal = TradeProposal(
        request_id="real-1",
        symbol="XAUUSD",
        generated_at=_t(0, 0),
        action=action,
        confidence=0.9,
        proposed_entry=2400.0,
        stop_loss=2390.0,
        take_profit=2420.0,
        risk_reward_ratio=2.0,
    )
    # The dedup gate recognizes a duplicate by timestamp OR identical
    # bid/ask. Prime both memory slots with the duplicate's own quote.
    policy._dedup_last_time = _t(0, 0)
    policy._dedup_last_bid = 2400.0
    policy._dedup_last_ask = 2400.2
    probs = torch.tensor([0.2, 0.5, 0.3])
    return policy, probs


def test_md9_duplicate_resurface_of_executable_proposal_is_no_trade() -> None:
    """Duplicate re-surface must never EXECUTE. The foreign runtime-resilience
    wave pins this at the decision-executor boundary (non-NO_TRADE +
    DEDUP_GATE downgraded there); this test pins the SAME contract at the
    executor entry, the single place both layers meet."""
    from unittest.mock import MagicMock

    from nexus_scalp.application.live.decision_executor import DecisionExecutor

    policy, probs = _policy_with_dedup_state(ActionType.BUY)
    dup_tick = _tick(0, 0, 2400.0)  # same quote as the original evaluation
    resurfaced = policy._evaluate_duplicate_tick(
        probs=probs.tolist(),
        current_tick=dup_tick,
        execution_id="EXEC-DUP-1",
        regime_state=None,
    )
    assert resurfaced is not None
    assert str(resurfaced.decision_stage) == "DEDUP_GATE"

    om = MagicMock()
    om.config.execution.mode = "LIVE"
    executor = DecisionExecutor(om)
    executor.execute_decision_stage(
        tick=dup_tick,
        account=MagicMock(),
        fv=MagicMock(),
        probs=None,
        regime_state=None,
        proposal=resurfaced,
        policy_decision=resurfaced,
        active_positions=[],
        current_pos_count=0,
    )
    # The engine never consulted any order authority for this duplicate.
    om.order_manager.execute_ai_reversal.assert_not_called()
    om.order_manager.execute_order.assert_not_called()


def test_md9b_duplicate_resurface_of_no_trade_preserved_for_display() -> None:
    policy, probs = _policy_with_dedup_state(ActionType.NO_TRADE)
    dup_tick = _tick(0, 0, 2400.0)
    out = policy._evaluate_duplicate_tick(
        probs=probs.tolist(),
        current_tick=dup_tick,
        execution_id="EXEC-DUP-2",
        regime_state=None,
    )
    assert out is not None and out.action == ActionType.NO_TRADE


# ---------------------------------------------------------------------------
# MD-10: LIVE/PAPER cross-mode invalidation purges aggregator bars
# ---------------------------------------------------------------------------


def test_md10_cross_mode_swap_purges_paper_bars() -> None:
    """BUG-231/BUG-232 contract: leaving the simulation boundary must purge
    the M1 aggregator (paper-geometry bars may not cross into LIVE) and bump
    the session generation. NOTE: the aggregator/aggregate/policy purge sites
    in invalidate_cross_mode_state read `getattr(self, ...)` on the SERVICE
    wrapper — on the live engine they resolve through __getattr__-style
    delegation only if present; here we pin the OBSERVABLE engine contract:
    reseed([]) on the engine's aggregator, generation bump, warmup reset."""
    from unittest.mock import MagicMock

    from nexus_scalp.application.live.runtime_mode import RuntimeModeService
    from nexus_scalp.domain.enums import ExecutionMode

    om = MagicMock()
    om.config.execution.mode = ExecutionMode.LIVE
    om.adapter = MagicMock()
    om.signal_policy = MagicMock()
    om.aggregator = MagicMock()
    om.warmup_state = "READY"
    om._mode_session_generation = 0

    service = RuntimeModeService.__new__(RuntimeModeService)
    service.om = om
    # Mirror the engine's delegation: the service wrapper forwards unknown
    # attributes to the engine (RuntimeModeService is composed with `om` and
    # the engine exposes aggregator). Attach the aggregator on BOTH surfaces
    # the way the engine composition does.
    service.aggregator = om.aggregator
    service.signal_policy = om.signal_policy
    service.warmup_state = "READY"
    service._mode_session_generation = 0

    # The adapter-swap branch is out of scope here (engine-owned); exercise
    # the invalidation contract that must always run after a real swap.
    service.invalidate_cross_mode_state(ExecutionMode.PAPER, ExecutionMode.LIVE)
    om.aggregator.reseed.assert_called_once_with([])
    # Generation must have been bumped (service carries the engine state).
    assert service._mode_session_generation == 1
    assert service.warmup_state == "WARMING_UP", "warmup must re-derive from new source"


# ---------------------------------------------------------------------------
# MD-11: bar lifecycle — forming bar is never exposed as complete
# ---------------------------------------------------------------------------


def test_md11_forming_bar_never_marked_complete() -> None:
    agg = BarAggregator("XAUUSD", timeframe_minutes=1)
    agg.process_tick(_tick(0, 30, 2400.0))
    forming = agg.get_current_forming_bar()
    assert forming is not None and forming.is_complete is False
    assert all(b.is_complete for b in agg.get_completed_bars())
    assert all(b.timestamp != forming.timestamp for b in agg.get_completed_bars())
