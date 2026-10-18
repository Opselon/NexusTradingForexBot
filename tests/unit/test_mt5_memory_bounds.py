"""
PURPOSE
-------
MT5-PARITY-FORENSICS lane E/H memory-bounds + honest-diagnostics regression
net. Pins the three fixes this lane shipped:

  H-04  PaperMT5Adapter._execution_ledger grows without bound
        (paper_adapter.py declaration + append; no maxlen anywhere).
        Fix: bounded ``deque(maxlen=_LEDGER_MAXLEN)`` ring + a dropped-entries
        counter, preserving every ledger field and the append order the audit
        consumer (risk/paper_parity.export_paper_ledger) reads.

  H-05  BarAggregator._completed_bars grows without bound
        (bar_aggregator.py append + reseed; read by the whole tick path).
        Fix: retained list bounded to ``COMPLETED_BARS_MAXLEN`` while keeping
        (a) forming-bar semantics untouched, (b) the reseed() path working,
        (c) every consumer (warmup/history/HTF/liquidity/UI windows) still
        receiving the bars it needs. A dropped count is exposed when the cap
        is ever hit.

  F-22  Diagnostics API degrades to a false-green off-native: the v1 route
        returned ``mt5=null`` while the live-state route returned
        ``available:True`` with empty diagnostics.
        Fix: an adapter that cannot provide diagnostics surfaces an explicit
        degraded reason (never available:True with empty diagnostics, never a
        bare null).

OWNER
-----
IMPL-E, MT5-PARITY-FORENSICS wave (agent/feature/mt5-parity-e).

CONSUMES
--------
nexus_scalp.adapters.paper.paper_adapter.PaperMT5Adapter
nexus_scalp.market_data.bar_aggregator.BarAggregator / BarData
nexus_scalp.web.api_v1.system (system_diagnostics route)

PROVIDES
--------
Deterministic regression proof for H-04 / H-05 / F-22. All fixtures are
synthetic (no terminal, no network, no live orders). Assertions are on
container sizes, eviction counts, field preservation, iteration order and
API payload shape — no timing thresholds, no strategy behavior.

INVARIANTS
----------
* The ledger ring never exceeds its cap and never silently under-counts.
* The bar ring never exceeds its cap and never silently drops without a count.
* Forming-bar state is untouched by the bound (a retention cap on COMPLETED
  bars must not perturb the bar currently being formed).
* reseed() keeps working: dedupe/order/ascending-seed semantics are identical,
  and a reseed within the cap drops nothing.
* The diagnostics payload is never null and never available:True-but-empty.

EXTEND
------
When adding a new legitimate consumer of _completed_bars / _execution_ledger,
add a case to TEST_CONSUMER_WINDOWS asserting the cap still covers its window.
"""

from __future__ import annotations

import itertools
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from nexus_scalp.adapters.paper.paper_adapter import (
    _LEDGER_MAXLEN,
    PaperMT5Adapter,
)
from nexus_scalp.domain.models import TickData
from nexus_scalp.market_data.bar_aggregator import BarAggregator, BarData

_BASE = datetime(2026, 1, 1, tzinfo=UTC)

#: Fields every ledger entry must carry (documented at the declaration site in
#: paper_adapter.py — the audit export reads every one of them).
_LEDGER_FIELDS = frozenset(
    {
        "ts",
        "symbol",
        "order_type",
        "volume",
        "requested_price",
        "bid_at_request",
        "ask_at_request",
        "spread",
        "fill_price",
        "slippage",
        "latency_ticks",
        "rejection_reason",
        "is_fill",
        "ticket",
    }
)

#: Legitimate consumer windows of the completed-bar series, pinned so the cap
#: (BarAggregator.COMPLETED_BARS_MAXLEN) can never silently fall below the
#: largest of them. Update this list when a consumer with a bigger window is
#: added; the cap is sized to the maximum of these by contract.
TEST_CONSUMER_WINDOWS: tuple[int, ...] = (
    55,  # ScalpFeatureEngine hot window (scalp_features.py compute_from_bars)
    60,  # 60D challenger extras (shadow_recorder / schema_augment)
    200,  # MSLIE swing reaction lookback (mslie/swing.py MAX_REACTION_LOOKBACK)
    900,  # UI chart + resync feature window (live_engine / web server bars)
    4000,  # HTF_HISTORY_BARS — the shared train==live HTF contract
    20000,  # _resync_from_broker broker reseed (live_engine.py chart_count)
)


def _tick(minute: int, bid: float = 2400.0, ask: float = 2400.2) -> TickData:
    return TickData(
        symbol="XAUUSD",
        timestamp=_BASE + timedelta(minutes=minute),
        bid=bid,
        ask=ask,
        volume=1,
    )


def _bar(i: int) -> BarData:
    return BarData(
        symbol="XAUUSD",
        timeframe="M1",
        timestamp=_BASE + timedelta(minutes=i),
        open=2400.0 + i * 0.01,
        high=2400.5 + i * 0.01,
        low=2399.5 + i * 0.01,
        close=2400.2 + i * 0.01,
        tick_volume=10,
        is_complete=True,
    )


def _append_ledger_rows(adapter: PaperMT5Adapter, count: int, start_ticket: int = 100001) -> int:
    """Append ``count`` ledger rows via the only production append path."""
    for i in range(count):
        adapter._ledger_append(
            symbol="XAUUSD",
            order_type=None,
            volume=0.1,
            requested_price=2400.0,
            bid=2400.0,
            ask=2400.2,
            spread=0.2,
            fill_price=2400.2 + i * 0.001,
            slippage=0.2 + i * 0.001,
            rejection_reason=None,
            ticket=start_ticket + i,
        )
    return start_ticket + count


# ===========================================================================
# H-04 — PaperMT5Adapter._execution_ledger is a bounded ring
# ===========================================================================


class TestH04ExecutionLedgerBound:
    """H-04: the execution ledger must not grow without bound."""

    def test_declared_as_bounded_ring(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        assert _LEDGER_MAXLEN > 0
        # The ring must be constructed at the documented cap, not left to grow.
        assert ad._execution_ledger.maxlen == _LEDGER_MAXLEN

    def test_cap_is_not_reached_in_a_normal_session(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        _append_ledger_rows(ad, 100)
        led = ad.get_execution_ledger()
        assert len(led) == 100
        # Nothing is dropped while the session stays inside the cap.
        assert ad.ledger_dropped_entries() == 0

    def test_ring_clamps_size_and_counts_evictions(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        appended = _LEDGER_MAXLEN + 500
        _append_ledger_rows(ad, appended)
        led = ad.get_execution_ledger()
        # H-04: memory is now constant, not linear in the session length.
        assert len(led) == _LEDGER_MAXLEN
        # The dropped count is exposed, exact and monotonic.
        assert ad.ledger_dropped_entries() == appended - _LEDGER_MAXLEN == 500

    def test_ring_retains_the_newest_entries_in_order(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        start = 100001
        end = _append_ledger_rows(ad, _LEDGER_MAXLEN + 1000, start_ticket=start)
        led = ad.get_execution_ledger()
        # Every retained entry is the most recent _LEDGER_MAXLEN of the session.
        expected = list(range(end - _LEDGER_MAXLEN, end))
        assert [e["ticket"] for e in led] == expected
        # Chronological (append) order is preserved for the audit consumer.
        stamps = [e["ts"] for e in led]
        assert stamps == sorted(stamps)

    def test_every_field_and_is_fill_semantics_preserved(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        _append_ledger_rows(ad, 10)
        # A rejection must land in the ledger too (fills AND rejections).
        ad._ledger_append(
            symbol="XAUUSD",
            order_type=None,
            volume=0.0,
            requested_price=0.0,
            bid=2400.0,
            ask=2400.2,
            spread=0.2,
            fill_price=None,
            slippage=None,
            rejection_reason="invalid_size_or_price",
            ticket=0,
        )
        led = ad.get_execution_ledger()
        assert len(led) == 11
        for e in led[:-1]:
            assert _LEDGER_FIELDS <= set(e)
            assert e["is_fill"] is True and e["rejection_reason"] is None
        last = led[-1]
        assert _LEDGER_FIELDS <= set(last)
        assert last["is_fill"] is False
        assert last["rejection_reason"] == "invalid_size_or_price"
        assert last["fill_price"] is None and last["slippage"] is None

    def test_returned_copy_does_not_alias_the_ring(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        _append_ledger_rows(ad, 5)
        led = ad.get_execution_ledger()
        led.clear()
        # get_execution_ledger() is a read-only copy: mutation must not leak.
        assert len(ad.get_execution_ledger()) == 5

    def test_clear_resets_ring_and_drop_counter(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        _append_ledger_rows(ad, _LEDGER_MAXLEN + 100)
        ad.clear_execution_ledger()
        assert ad.get_execution_ledger() == []
        assert ad.ledger_dropped_entries() == 0
        # The ring is reusable after a clear (per-scenario isolation).
        _append_ledger_rows(ad, 3)
        assert len(ad.get_execution_ledger()) == 3

    def test_ring_bounds_memory_under_a_stress_loop(self) -> None:
        """The H-04 regression guard: an unbounded session stays O(cap)."""
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        _append_ledger_rows(ad, _LEDGER_MAXLEN * 3)
        led = ad.get_execution_ledger()
        assert len(led) == _LEDGER_MAXLEN
        assert ad.ledger_dropped_entries() == _LEDGER_MAXLEN * 2
        # The audit consumer's window (500 most-recent rows) is fully intact.
        recent = led[-500:]
        assert len(recent) == 500
        assert [e["ticket"] for e in recent] == sorted(e["ticket"] for e in recent)


# ===========================================================================
# H-05 — BarAggregator._completed_bars is a bounded window
# ===========================================================================


class TestH05CompletedBarsBound:
    """H-05: the completed-bar series must not grow without bound."""

    def test_cap_admits_every_legitimate_consumer_window(self) -> None:
        # The cap must be at least the largest legitimate consumer window,
        # otherwise the bound would silently truncate a live consumer.
        assert BarAggregator.COMPLETED_BARS_MAXLEN >= max(TEST_CONSUMER_WINDOWS)

    def test_cap_starts_at_zero_and_drops_nothing_below_it(self) -> None:
        agg = BarAggregator("XAUUSD", 1)
        assert agg.dropped_completed_bars == 0
        for i in range(500):
            agg.process_tick(_tick(i))
        assert len(agg.get_completed_bars()) == 499
        assert agg.dropped_completed_bars == 0

    def test_live_append_path_clamps_and_counts_evictions(self) -> None:
        agg = BarAggregator("XAUUSD", 1)
        n = BarAggregator.COMPLETED_BARS_MAXLEN + 3000
        for i in range(n):
            agg.process_tick(_tick(i))
        bars = agg.get_completed_bars()
        # H-05: constant memory, not linear in the session length.
        assert len(bars) == BarAggregator.COMPLETED_BARS_MAXLEN
        completed = n - 1
        assert agg.dropped_completed_bars == completed - len(bars) == 2999

    def test_live_path_retains_the_newest_bars_monotonic(self) -> None:
        agg = BarAggregator("XAUUSD", 1)
        n = BarAggregator.COMPLETED_BARS_MAXLEN + 10
        for i in range(n):
            agg.process_tick(_tick(i))
        bars = agg.get_completed_bars()
        ts = [b.timestamp for b in bars]
        # The first retained bar sits exactly `dropped` bars after the first
        # completed bar (minute 0), and the tail is the last completed minute.
        assert ts[0] == _BASE + timedelta(minutes=agg.dropped_completed_bars)
        assert ts[-1] == _BASE + timedelta(minutes=n - 2)
        # Strictly ascending and unique — no duplicate/sealed-bar regression.
        assert all(a < b for a, b in itertools.pairwise(ts))
        assert len(set(ts)) == len(ts)

    def test_forming_bar_semantics_untouched_by_the_bound(self) -> None:
        """The retention cap on COMPLETED bars must not perturb the forming bar."""
        agg = BarAggregator("XAUUSD", 1)
        # Cross the cap so the trim path is live, then continue inside one bar.
        n = BarAggregator.COMPLETED_BARS_MAXLEN + 50
        for i in range(n):
            agg.process_tick(_tick(i, 2400.0 + i, 2400.5 + i))
        forming = agg.get_current_forming_bar()
        assert forming is not None
        # The forming bar is stamped with the minute AFTER the last completed
        # bar: tick i seals minute i-1 and opens minute i.
        last_completed = agg.get_completed_bars()[-1]
        assert forming.timestamp == last_completed.timestamp + timedelta(minutes=1)
        assert forming.is_complete is False
        # The forming bar's OHLC come from the MID of the last accepted quote
        # ((bid + ask) / 2), not from the ask itself.
        assert forming.close == pytest.approx((2400.0 + 2400.5) / 2 + (n - 1))
        assert forming.open == pytest.approx((2400.0 + 2400.5) / 2 + (n - 1))

        # A tick in the SAME minute continues the forming bar (no state reset).
        # The forming bar is minute n-1 (the last loop tick sealed minute n-2
        # and opened n-1 with that tick's own stamp), so the continuing tick
        # must be strictly LATER than the opener but still inside minute n-1.
        same_minute = forming.timestamp.replace(second=45, microsecond=0)
        assert same_minute > forming.timestamp
        agg.process_tick(
            TickData(
                symbol="XAUUSD",
                timestamp=same_minute,
                bid=2450.0,
                ask=2450.5,
                volume=1,
            )
        )
        forming2 = agg.get_current_forming_bar()
        assert forming2 is not None
        assert forming2.timestamp == forming.timestamp
        assert forming2.tick_volume == forming.tick_volume + 1
        # close tracks the LAST accepted mid; high is only raised, so a lower
        # quote leaves it at the opener (the trim must not disturb either).
        assert forming2.close == pytest.approx(2450.25)
        assert forming2.high == pytest.approx((2400.0 + 2400.5) / 2 + (n - 1))
        assert forming2.low == pytest.approx(2450.25)

        # A tick in the NEXT minute seals the forming bar and opens a new one;
        # the cap must not interfere with that boundary crossing. The forming
        # bar IS minute n-1, so the sealing tick is minute n.
        sealed = agg.process_tick(_tick(n, 2460.0, 2460.5))
        assert sealed is not None
        assert sealed.timestamp == forming.timestamp
        assert sealed.is_complete is True
        assert sealed.close == pytest.approx(2450.25)
        # The newly-sealed bar lands in the retained series (head evicted).
        assert agg.get_completed_bars()[-1].timestamp == sealed.timestamp
        assert agg.dropped_completed_bars == n - BarAggregator.COMPLETED_BARS_MAXLEN

    def test_integrity_guards_still_enforced_past_the_cap(self) -> None:
        """The bound must not weaken the symbol-identity / monotonicity guards."""
        agg = BarAggregator("XAUUSD", 1)
        for i in range(BarAggregator.COMPLETED_BARS_MAXLEN + 10):
            agg.process_tick(_tick(i))
        before = len(agg.get_completed_bars())
        # Foreign symbol is still fail-closed rejected.
        with pytest.raises(ValueError, match="symbol mismatch"):
            agg.process_tick(
                TickData(
                    symbol="EURUSD",
                    timestamp=_BASE + timedelta(minutes=999999),
                    bid=1.08,
                    ask=1.0801,
                    volume=1,
                )
            )
        assert len(agg.get_completed_bars()) == before
        # An out-of-order tick is still dropped without mutating any bar.
        last = agg.get_completed_bars()[-1]
        assert agg.process_tick(_tick(0)) is None
        assert agg.get_completed_bars()[-1] == last

    def test_reseed_within_cap_drops_nothing(self) -> None:
        agg = BarAggregator("XAUUSD", 1)
        last = agg.reseed([_bar(i) for i in range(4000)])
        bars = agg.get_completed_bars()
        assert len(bars) == 4000
        assert agg.dropped_completed_bars == 0
        assert last is not None and last.timestamp == _BASE + timedelta(minutes=3999)
        # Forming bar seeded at next minute, OHLC from the last close.
        forming = agg.get_current_forming_bar()
        assert forming is not None
        assert forming.timestamp == _BASE + timedelta(minutes=4000)
        assert forming.open == last.close

    def test_reseed_over_cap_keeps_newest_and_counts_the_rest(self) -> None:
        agg = BarAggregator("XAUUSD", 1)
        n = BarAggregator.COMPLETED_BARS_MAXLEN + 5000
        last = agg.reseed([_bar(i) for i in range(n)])
        bars = agg.get_completed_bars()
        assert len(bars) == BarAggregator.COMPLETED_BARS_MAXLEN
        assert agg.dropped_completed_bars == n - len(bars) == 5000
        # The NEWEST bars are the causal tail every consumer reads.
        assert bars[0].timestamp == _BASE + timedelta(minutes=5000)
        assert bars[-1].timestamp == _BASE + timedelta(minutes=n - 1)
        # reseed still returns the true last bar (post-seed geometry is exact).
        assert last is not None
        assert last.timestamp == _BASE + timedelta(minutes=n - 1)
        assert agg.get_current_forming_bar() is not None

    def test_reseed_empty_still_clears_history(self) -> None:
        """The BUG-054 atomic-clear contract survives the bound."""
        agg = BarAggregator("XAUUSD", 1)
        for i in range(100):
            agg.process_tick(_tick(i))
        assert len(agg.get_completed_bars()) == 99
        assert agg.reseed([]) is None
        assert agg.get_completed_bars() == []
        assert agg.get_current_forming_bar() is None

    def test_reseed_dedupe_and_order_still_exact_at_the_cap(self) -> None:
        """Dedupe + ascending sort must stay correct when the trim path runs."""
        agg = BarAggregator("XAUUSD", 1)
        n = BarAggregator.COMPLETED_BARS_MAXLEN + 20
        # Caller-supplied disorder + a duplicate + a non-complete bar.
        shuffled = [_bar(i) for i in range(n)]
        shuffled.insert(5, _bar(3))  # duplicate timestamp
        shuffled.append(_bar(n))  # duplicate of the last (after sort)
        shuffled.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=_BASE + timedelta(minutes=n + 1),
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.0,
                tick_volume=1,
                is_complete=False,  # must be filtered out
            )
        )
        shuffled.reverse()
        agg.reseed(shuffled)
        bars = agg.get_completed_bars()
        ts = [b.timestamp for b in bars]
        assert len(ts) == len(set(ts))
        assert all(a < b for a, b in itertools.pairwise(ts))
        assert all(b.is_complete for b in bars)
        assert len(bars) == BarAggregator.COMPLETED_BARS_MAXLEN


# ===========================================================================
# F-22 — the diagnostics API never reports a false green off-native
# ===========================================================================


def _v1_app(adapter: object | None) -> TestClient:
    app = FastAPI()
    from nexus_scalp.web.api_v1.system import router

    app.include_router(router)
    app.state.engine = SimpleNamespace(adapter=adapter)
    return TestClient(app)


def _mt5_block(body: dict) -> object:
    """Extract the mt5 diagnostics block from a v1 envelope."""
    data = body.get("data", body)
    return data["mt5"]


class TestF22DiagnosticsNoFalseGreen:
    """F-22: an off-native adapter surfaces an explicit degraded state."""

    def test_paper_adapter_reports_real_diagnostics_not_null(self) -> None:
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        ad.connect()
        client = _v1_app(ad)
        resp = client.get("/api/v1/system/diagnostics")
        assert resp.status_code == 200
        mt5 = _mt5_block(resp.json())
        # Never null, never empty-with-available:True. The paper transport is
        # honest about being the in-memory simulation (F-19/F-22 convention).
        assert isinstance(mt5, dict)
        assert mt5.get("transport") == "PAPER"
        assert mt5.get("available") is True
        assert isinstance(mt5.get("connection"), dict)
        assert mt5["connection"].get("state") == "CONNECTED"
        assert "execution_ledger" in mt5

    def test_paper_diagnostics_exposes_the_ledger_bound(self) -> None:
        """The H-04 accounting is reachable from the diagnostics surface."""
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        ad.connect()
        block = ad.diagnostics_summary()["execution_ledger"]
        assert block["maxlen"] == _LEDGER_MAXLEN
        assert block["retained"] == 0
        assert block["dropped"] == 0
        _append_ledger_rows(ad, _LEDGER_MAXLEN + 10)
        block = ad.diagnostics_summary()["execution_ledger"]
        assert block["retained"] == _LEDGER_MAXLEN
        assert block["dropped"] == 10

    def test_raising_adapter_surfaces_degraded_reason(self) -> None:
        class _Boom:
            def diagnostics_summary(self) -> dict:
                raise RuntimeError("sensitive detail: /secret/path")

        client = _v1_app(_Boom())
        resp = client.get("/api/v1/system/diagnostics")
        assert resp.status_code == 200
        mt5 = _mt5_block(resp.json())
        # The old code returned null here, which the live-state route then
        # rendered as available:True with empty diagnostics.
        assert mt5 is not None
        assert mt5["available"] is False
        assert mt5["reason"] == "DIAGNOSTICS_UNAVAILABLE"
        # Exception text must not leak into the payload.
        assert "sensitive detail" not in str(mt5)
        assert "/secret/path" not in str(mt5)
        assert "RuntimeError" in mt5["detail"]

    def test_no_adapter_attached_is_explicit_not_null(self) -> None:
        client = _v1_app(None)
        resp = client.get("/api/v1/system/diagnostics")
        assert resp.status_code == 200
        assert _mt5_block(resp.json()) == {
            "available": False,
            "reason": "NO_ADAPTER_ATTACHED",
        }

    def test_paper_diagnostics_never_reports_empty_but_available(self) -> None:
        """F-22 regression guard: the live-state route shape, by contract.

        ``/api/diagnostics/live-state`` (diagnostics_state_routes.py) renders
        ``available: True`` with an EMPTY ``diagnostics`` dict when the attached
        adapter exposes no ``diagnostics_summary`` — a payload that reads as
        "native MT5 healthy" for a session with no broker. Now that the paper
        adapter implements the producer, the ``hasattr`` branch is reached and
        the diagnostics block must be populated rather than ``{}``.
        """
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        ad.connect()
        # The producer contract the route depends on:
        assert hasattr(ad, "diagnostics_summary")
        block = ad.diagnostics_summary()
        assert block.get("transport") == "PAPER"
        # A consumer that only checks `hasattr` would have rendered {} here.
        assert block  # non-empty
        # And the transport is reported, so the payload can never be mistaken
        # for a native-MT5-healthy read.
        assert block.get("transport") != "NATIVE"

    def test_diagnostics_summary_never_raises(self) -> None:
        """The producer is fail-safe: it reports state, never propagates."""
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        # Disconnect mid-session must still yield a structured payload.
        ad.disconnect()
        summary = ad.diagnostics_summary()
        assert summary["available"] is False
        assert summary["connection"].get("state") == "DISCONNECTED"

    def test_no_mt5_terminal_does_not_raise_on_import(self) -> None:
        """Importing the module is side-effect-free without a terminal."""
        import importlib

        mod = importlib.import_module("nexus_scalp.web.api_v1.system")
        assert mod.router is not None


# ===========================================================================
# Cross-cutting: the bounds hold under the combined live+replay shapes
# ===========================================================================


def test_bounds_hold_with_logging_at_default_level(caplog: pytest.LogCaptureFixture) -> None:
    """The trim paths must not raise or corrupt state under normal logging."""
    with caplog.at_level(logging.INFO):
        agg = BarAggregator("XAUUSD", 1)
        for i in range(BarAggregator.COMPLETED_BARS_MAXLEN + 5):
            agg.process_tick(_tick(i))
        assert len(agg.get_completed_bars()) == BarAggregator.COMPLETED_BARS_MAXLEN
        ad = PaperMT5Adapter(initial_balance=10000.0, symbol="XAUUSD")
        _append_ledger_rows(ad, _LEDGER_MAXLEN + 5)
        assert len(ad.get_execution_ledger()) == _LEDGER_MAXLEN
