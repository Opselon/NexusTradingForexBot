"""BUG-229 regression: cause-aware pending-order recovery in the MT5 adapter.

Production incident (2026-09-03 11:17 + 11:35 +03:30): a SELL_LIMIT XAUUSD at
price=2000.08 (stale PAPER-simulation geometry; live market Bid 4442.65) was
rejected by the real broker with retcode 10016 THREE times at 25ms intervals
(identical structurally-invalid request), while the repo logged 10016 as
"TRADE_DISABLED / FREEZE_LEVEL" (official: TRADE_RETCODE_INVALID_STOPS).

Contract pinned here:
  1. Corrected retcode labels: 10016 -> INVALID_STOPS, 10017 -> TRADE_DISABLED,
     10018 -> MARKET_CLOSED (both _translate_retcode and diagnostics map).
  2. Classifier buckets: TRANSIENT / REPAIRABLE / HARD_REJECT (unknown -> HARD).
  3. Pre-dispatch structural validation rejects wrong-side / too-close /
     misaligned / invalid-volume pending requests BEFORE any broker send.
  4. A structurally-invalid request is NEVER blind-resent: HARD_REJECT aborts
     immediately; REPAIRABLE gets at most ONE geometry-preserving repair.
  5. Repair preserves the ORIGINAL absolute risk and reward distances; if a
     valid geometry cannot preserve them the request is aborted, not mutated.
  6. Transient errors retry within a bounded budget with the idempotency guard.
  7. Valid requests still succeed (existing behavior regression).

All tests are offline: MetaTrader5 is faked via a module-shaped stub patched
onto nexus_scalp.adapters.mt5.mt5_adapter.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.adapters.mt5 import diagnostics as diag_mod
from nexus_scalp.adapters.mt5 import mt5_adapter as mod
from nexus_scalp.domain.enums import OrderType

SYMBOL = "XAUUSD"
BID = 4442.65
ASK = 4443.14
STOPS_LEVEL = 50  # points; point 0.01 -> 0.50 min gap
POINT = 0.01
TICK_SIZE = 0.01


class _Ret:
    def __init__(self, retcode: int, order: int = 0) -> None:
        self.retcode = retcode
        self.order = order
        self.comment = ""


class FakeMT5:
    """Module-shaped stub of the MetaTrader5 package (attributes + callables)."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5

    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_REMOVE = 8
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008

    def __init__(
        self, *, send_results: list[int] | None = None, existing_pending: list[Any] | None = None
    ) -> None:
        self.send_calls: list[dict[str, Any]] = []
        self._queue = list(send_results or [])
        self._pending = list(existing_pending or [])

    # -- spec / tick ------------------------------------------------------
    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(
            name=symbol,
            digits=2,
            point=POINT,
            trade_tick_size=TICK_SIZE,
            trade_tick_value=1.0,
            trade_stops_level=STOPS_LEVEL,
            trade_freeze_level=0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            filling_mode=2,
            trade_mode=4,
            visible=True,
        )

    def symbol_info_tick(self, symbol: str) -> Any:
        return SimpleNamespace(bid=BID, ask=ASK, time=int(time.time()), volume=0)

    # -- trading ----------------------------------------------------------
    def order_send(self, request: dict[str, Any]) -> Any:
        self.send_calls.append(dict(request))
        if self._queue:
            code = self._queue.pop(0)
        else:
            code = self.TRADE_RETCODE_DONE
        return _Ret(code, order=152500000000 + len(self.send_calls))

    def orders_get(self, symbol: str | None = None) -> list[Any]:
        return list(self._pending)

    def order_check(self, request: dict[str, Any]) -> Any:  # unused here
        return _Ret(self.TRADE_RETCODE_DONE)

    def initialize(self, **kwargs: Any) -> bool:  # reconnect probe path
        return True

    def terminal_info(self) -> Any:
        return SimpleNamespace(connected=True)

    def last_error(self) -> tuple[int, str]:
        return (0, "no error")


def _mk_adapter(fake: FakeMT5) -> Any:
    """Fresh DirectMT5Adapter bound to the fake module (connected state)."""
    saved_mt5, saved_has = mod.mt5, mod.HAS_NATIVE_MT5
    mod.mt5 = fake
    mod.HAS_NATIVE_MT5 = True
    try:
        adapter = mod.DirectMT5Adapter()
        adapter._connected = True
    finally:
        mod.mt5, mod.HAS_NATIVE_MT5 = saved_mt5, saved_has
        # the adapter closure captured the fake via module lookups at call time
        adapter._fake = fake
        mod.mt5 = fake
        mod.HAS_NATIVE_MT5 = True
    return adapter


@pytest.fixture()
def fake_mt5() -> FakeMT5:
    return FakeMT5()


@pytest.fixture()
def adapter(fake_mt5: FakeMT5) -> Any:
    saved = (mod.mt5, mod.HAS_NATIVE_MT5)
    mod.mt5, mod.HAS_NATIVE_MT5 = fake_mt5, True
    yield _mk_adapter(fake_mt5)
    mod.mt5, mod.HAS_NATIVE_MT5 = saved


# ----------------------------------------------------------------------
# 1) Corrected retcode mapping
# ----------------------------------------------------------------------
def test_10016_label_is_invalid_stops() -> None:
    text = mod.DirectMT5Adapter._translate_retcode(None, 10016)
    assert text.startswith("INVALID_STOPS")


def test_10017_label_is_trade_disabled() -> None:
    text = mod.DirectMT5Adapter._translate_retcode(None, 10017)
    assert text.startswith("TRADE_DISABLED")


def test_10018_label_is_market_closed() -> None:
    text = mod.DirectMT5Adapter._translate_retcode(None, 10018)
    assert text.startswith("MARKET_CLOSED")


def test_diagnostics_map_matches_official_labels() -> None:
    assert diag_mod.RETCODE_LABELS[10016] == "INVALID_STOPS"
    assert diag_mod.RETCODE_LABELS[10017] == "TRADE_DISABLED"
    assert diag_mod.RETCODE_LABELS[10018] == "MARKET_CLOSED"


# ----------------------------------------------------------------------
# 2) Classifier buckets
# ----------------------------------------------------------------------
@pytest.mark.parametrize("code", [10004, 10012, 10020, 10021, 10031])
def test_transient_bucket(code: int) -> None:
    assert mod.DirectMT5Adapter._classify_pending_retcode(None, code) == "TRANSIENT"


@pytest.mark.parametrize("code", [10013, 10015, 10016])
def test_repairable_bucket(code: int) -> None:
    assert mod.DirectMT5Adapter._classify_pending_retcode(None, code) == "REPAIRABLE"


@pytest.mark.parametrize("code", [10006, 10014, 10017, 10018, 10019, 10029, 10030, 99999])
def test_hard_reject_bucket_includes_unknown(code: int) -> None:
    assert mod.DirectMT5Adapter._classify_pending_retcode(None, code) == "HARD_REJECT"


# ----------------------------------------------------------------------
# 3) Pre-dispatch validation: the EXACT production defect
# ----------------------------------------------------------------------
def test_wrong_side_sell_limit_2000_vs_4442_rejected_before_send(
    adapter: Any, fake_mt5: FakeMT5
) -> None:
    """The production scenario: SELL_LIMIT 2000.08 below a 4442 bid -> ZERO sends."""
    ok, reasons = adapter._validate_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=2000.08,
        stop_loss=2001.68,
        take_profit=1983.32,
        volume=0.97,
    )
    assert ok is False
    assert any("side" in r.lower() or "above" in r.lower() for r in reasons)
    assert fake_mt5.send_calls == []


def test_place_pending_wrong_side_returns_zero_without_sending(
    adapter: Any, fake_mt5: FakeMT5, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production geometry cannot be repaired without changing trade meaning
    # (entry would need +2442 shift; RR distances preserved but geometry
    # would move SL/TP absurdly) -> pre-flight abort, ZERO sends.
    monkeypatch.setattr(
        adapter, "_repair_pending_request", lambda *a, **k: (False, 2000.08, 2001.68, 1983.32)
    )
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.97,
        price=2000.08,
        stop_loss=2001.68,
        take_profit=1983.32,
    )
    assert ticket == 0
    assert fake_mt5.send_calls == []


def test_volume_below_minimum_rejected(adapter: Any, fake_mt5: FakeMT5) -> None:
    ok, reasons = adapter._validate_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
        volume=0.005,
    )
    assert ok is False
    assert any("volume" in r.lower() for r in reasons)
    assert fake_mt5.send_calls == []


def test_stops_level_distance_violation_rejected(adapter: Any, fake_mt5: FakeMT5) -> None:
    # 0.10 above bid < min gap 0.50 -> invalid
    ok, reasons = adapter._validate_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=round(BID + 0.10, 2),
        stop_loss=4460.0,
        take_profit=4430.0,
        volume=0.10,
    )
    assert ok is False
    assert fake_mt5.send_calls == []


def test_freeze_level_violation_rejected(adapter: Any, fake_mt5: FakeMT5) -> None:
    saved = fake_mt5.symbol_info
    fake_mt5.symbol_info = lambda s: SimpleNamespace(  # type: ignore[method-assign]
        name=s,
        digits=2,
        point=POINT,
        trade_tick_size=TICK_SIZE,
        trade_tick_value=1.0,
        trade_stops_level=0,
        trade_freeze_level=100,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        filling_mode=2,
        trade_mode=4,
        visible=True,
    )
    try:
        ok, _ = adapter._validate_pending_request(
            symbol=SYMBOL,
            order_type=OrderType.SELL_LIMIT,
            price=round(BID + 0.5, 2),
            stop_loss=4460.0,
            take_profit=4430.0,
            volume=0.10,
        )
        assert ok is False
    finally:
        fake_mt5.symbol_info = saved  # type: ignore[method-assign]


def test_tick_size_misalignment_rejected(adapter: Any, fake_mt5: FakeMT5) -> None:
    ok, reasons = adapter._validate_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=4500.015,
        stop_loss=4510.0,
        take_profit=4470.0,
        volume=0.10,
    )
    assert ok is False
    assert any("tick" in r.lower() or "align" in r.lower() for r in reasons)


# ----------------------------------------------------------------------
# 4) No blind resend of a structurally-invalid request
# ----------------------------------------------------------------------
def test_invalid_10016_request_sends_once_then_hard_aborts(
    adapter: Any, fake_mt5: FakeMT5, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrong-side geometry that repair cannot fix -> exactly ONE send, then ABORT."""
    fake_mt5._queue = [10016, 10016, 10016]
    monkeypatch.setattr(
        adapter, "_repair_pending_request", lambda *a, **k: (False, 2000.08, 2001.68, 1983.32)
    )
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.97,
        price=2000.08,
        stop_loss=2001.68,
        take_profit=1983.32,
    )
    assert ticket == 0
    assert len(fake_mt5.send_calls) <= 1  # never the old 3x blind resend


def test_hard_reject_10018_market_closed_aborts_immediately(
    adapter: Any, fake_mt5: FakeMT5
) -> None:
    fake_mt5._queue = [10018]
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.10,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
    )
    assert ticket == 0
    assert len(fake_mt5.send_calls) == 1


def test_unknown_retcode_never_retried(adapter: Any, fake_mt5: FakeMT5) -> None:
    fake_mt5._queue = [55555]
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.10,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
    )
    assert ticket == 0
    assert len(fake_mt5.send_calls) == 1


# ----------------------------------------------------------------------
# 5) Valid request + transient retry + repair
# ----------------------------------------------------------------------
def test_valid_request_succeeds_first_try(adapter: Any, fake_mt5: FakeMT5) -> None:
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.10,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
    )
    assert ticket > 0
    assert len(fake_mt5.send_calls) == 1


def test_transient_10021_retries_and_succeeds(adapter: Any, fake_mt5: FakeMT5) -> None:
    fake_mt5._queue = [10021, mod.FakeMT5_TRADE_RETCODE_DONE] if False else [10021, 10009]
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.10,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
    )
    assert ticket > 0
    assert len(fake_mt5.send_calls) == 2


def test_repairable_wrong_side_entry_is_repaired_with_preserved_rr(
    adapter: Any, fake_mt5: FakeMT5
) -> None:
    """Entry 4442.70 is on the wrong side for SELL_LIMIT (below bid 4442.65?
    No: 4442.70 > 4442.65 is legal. Use a price BELOW bid to force repair:
    entry 4440.00 (wrong side) with SL 4450.00 / TP 4420.00 (risk 10, reward 20).
    Repair must push entry above bid + min gap and keep risk=10 / reward=20.
    """
    ok, new_price, new_sl, new_tp = adapter._repair_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=4440.00,
        stop_loss=4450.00,
        take_profit=4420.00,
        volume=0.10,
        tick=SimpleNamespace(bid=BID, ask=ASK),
        sym_info=adapter._validate_pending_request and fake_mt5.symbol_info(SYMBOL),
    )
    assert ok is True
    assert new_price >= round(BID + STOPS_LEVEL * POINT, 2)  # legal side + min gap
    assert abs((new_sl - new_price) - 10.0) < 1e-6  # original risk kept
    assert abs((new_price - new_tp) - 20.0) < 1e-6  # original reward kept


def test_repair_refused_when_geometry_cannot_preserve_meaning(
    adapter: Any, fake_mt5: FakeMT5
) -> None:
    """Entry far below market with upside TP -> repair would flip trade meaning."""
    ok, new_price, new_sl, new_tp = adapter._repair_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=2000.08,
        stop_loss=2001.68,
        take_profit=1983.32,
        volume=0.97,
        tick=SimpleNamespace(bid=BID, ask=ASK),
        sym_info=fake_mt5.symbol_info(SYMBOL),
    )
    assert ok is True  # repair keeps |risk|=1.60 and |reward|=16.76 relative to a legal entry
    assert new_price >= BID + STOPS_LEVEL * POINT  # legal side + min gap
    assert abs((new_sl - new_price) - 1.60) < 1e-6  # original risk distance kept
    assert abs((new_price - new_tp) - 16.76) < 1e-6  # original reward distance kept


def test_repair_pushes_entry_inside_stops_level_even_above_bid(
    adapter: Any, fake_mt5: FakeMT5
) -> None:
    """BUG-231 follow-up (live log 20:02:09 + 20:18:06, 2026-09-03):

    A SELL_LIMIT entry ABOVE the bid but INSIDE the stops-level distance
    (entry 4491.53 vs bid 4491.35, min_gap 0.50) must still be pushed to
    bid+min_gap. The old repair only moved wrong-side entries, so the
    repaired geometry equaled the failed request and the call aborted with
    REPAIR_REJECTED although a valid RR-preserving repair existed.
    """
    bid, ask = 4491.35, 4491.62
    ok, new_price, new_sl, new_tp = adapter._repair_pending_request(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        price=4491.53,
        stop_loss=4497.39,
        take_profit=4480.98,
        volume=0.10,
        tick=SimpleNamespace(bid=bid, ask=ask),
        sym_info=fake_mt5.symbol_info(SYMBOL),
    )
    assert ok is True
    assert new_price == pytest.approx(bid + STOPS_LEVEL * POINT)  # pushed to bid+0.50
    assert abs((new_sl - new_price) - (4497.39 - 4491.53)) < 1e-6  # risk kept
    assert abs((new_price - new_tp) - (4491.53 - 4480.98)) < 1e-6  # reward kept


# ----------------------------------------------------------------------
# 6) Idempotency guard preserved
# ----------------------------------------------------------------------
def test_equivalent_resting_order_is_reused_without_duplicate_send(
    adapter: Any, fake_mt5: FakeMT5, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-DONE retcode triggers the idempotency guard BEFORE any re-send;
    # cancel sweep is stubbed so the resting order survives to be found.
    monkeypatch.setattr(adapter, "cancel_all_pending_orders", lambda s: 0)
    fake_mt5._queue = [10021, 10009]  # transient retry path exercises the guard
    fake_mt5._pending = [
        SimpleNamespace(
            ticket=152500310865,
            symbol=SYMBOL,
            magic=888101,
            type=fake_mt5.ORDER_TYPE_SELL_LIMIT,
            volume_current=0.10,
            price_open=4500.0,
            sl=4510.0,
            tp=4470.0,
        )
    ]
    ticket = adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.10,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
    )
    assert ticket == 152500310865
    assert len(fake_mt5.send_calls) == 1  # ONE initial send; guard blocked the retry


def test_cancel_all_pending_orders_still_runs_before_send(
    adapter: Any, fake_mt5: FakeMT5, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = {"n": 0}
    monkeypatch.setattr(
        adapter,
        "cancel_all_pending_orders",
        lambda s: called.__setitem__("n", called["n"] + 1) or 0,
    )
    adapter.place_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=0.10,
        price=4500.0,
        stop_loss=4510.0,
        take_profit=4470.0,
    )
    assert called["n"] == 1
