"""Order-write uncertainty semantics (mission package P0): UNKNOWN != FAILED.

Offline, deterministic. The MetaTrader5 module is faked via a module-shaped
stub (same discipline as test_pending_recovery_cause_aware.py), the
OrderIntentStore uses tmp_path JSONL, and the RemoteMT5GatewayAdapter is
exercised against a local HTTP stub server so genuine socket timeouts /
connection losses can be injected without any real network.

Contract pins:
  1. WriteResult tri-state: SUCCESS / REJECTED / UNKNOWN (+ ok/unknown flags).
  2. DirectMT5Adapter.execute_market_order / place_pending_order on an
     exception-raising order_send -> WriteResult-convertible UNKNOWN evidence
     is logged with the idempotency fingerprint (never silently FAILED);
     ticket==0 returned to the caller (no blind success).
  3. DirectMT5Adapter.write_market_order / write_pending_order expose the
     typed tri-state result.
  4. Ambiguous send + reconciliation finds the executed order ->
     RESOLVED as SUCCESS via the existing fingerprint equivalence guard
     (no duplicate resend).
  5. Ambiguous send + reconciliation finds nothing and a definitive
     rejection retcode is observable -> REJECTED.
  6. Ambiguity that cannot be resolved stays UNKNOWN (UNKNOWN STAYS
     UNKNOWN) and NO retry send is issued (send-call count proves it).
  7. OrderIntentStore survives restart: pending intent recorded before the
     send is recoverable from a fresh store instance and resolvable.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from nexus_scalp.adapters.mt5 import mt5_adapter as mod
from nexus_scalp.domain.enums import OrderType
from nexus_scalp.execution.order_write import (
    OrderIntent,
    OrderIntentStore,
    WriteOutcome,
    idempotency_fingerprint,
)

SYMBOL = "XAUUSD"
PRICE = 2000.55
VOLUME = 0.10


class _Ret:
    def __init__(self, retcode: int, order: int = 0) -> None:
        self.retcode = retcode
        self.order = order
        self.comment = ""


class FakeMT5:
    """Module-shaped MT5 stub with injectable order_send failures."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_REMOVE = 8
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(
        self,
        *,
        send_results: list[Any] | None = None,
        send_exc: Exception | None = None,
        live_positions: list[Any] | None = None,
        pending_orders: list[Any] | None = None,
    ) -> None:
        self.send_calls: list[dict[str, Any]] = []
        self._queue = list(send_results or [])
        self._send_exc = send_exc
        self._positions = list(live_positions or [])
        self._pending = list(pending_orders or [])

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(
            name=symbol,
            digits=2,
            point=0.01,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_stops_level=50,
            trade_freeze_level=0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            filling_mode=2,
            trade_mode=4,
            visible=True,
        )

    def symbol_info_tick(self, symbol: str) -> Any:
        return SimpleNamespace(bid=1999.90, ask=2000.10, time=1, volume=0)

    def order_send(self, request: dict[str, Any]) -> Any:
        self.send_calls.append(dict(request))
        if self._send_exc is not None:
            raise self._send_exc
        if self._queue:
            code = self._queue.pop(0)
            if isinstance(code, Exception):
                raise code
            return _Ret(code, order=152500000000 + len(self.send_calls))
        return _Ret(self.TRADE_RETCODE_DONE, order=152500000000 + len(self.send_calls))

    def positions_get(self, symbol: str | None = None, **_: Any) -> list[Any]:
        return list(self._positions)

    def orders_get(self, symbol: str | None = None) -> list[Any]:
        return list(self._pending)

    def order_check(self, request: dict[str, Any]) -> Any:
        return _Ret(self.TRADE_RETCODE_DONE)

    def last_error(self) -> tuple[int, str]:
        return (0, "no error")


def _mk_adapter(fake: FakeMT5) -> Any:
    saved_mt5, saved_has = mod.mt5, mod.HAS_NATIVE_MT5
    mod.mt5 = fake
    mod.HAS_NATIVE_MT5 = True
    try:
        adapter = mod.DirectMT5Adapter()
        adapter._connected = True
    finally:
        mod.mt5, mod.HAS_NATIVE_MT5 = saved_mt5, saved_has
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
# 1) WriteResult tri-state primitives
# ----------------------------------------------------------------------
def test_write_result_tri_state_flags() -> None:
    from nexus_scalp.execution.order_write import rejected, success, unknown

    s = success(42)
    assert s.ok and not s.unknown and s.outcome is WriteOutcome.SUCCESS
    r = rejected(retcode=10018)
    assert not r.ok and not r.unknown and r.retcode == 10018 and r.ticket == 0
    u = unknown(detail="timeout")
    assert not u.ok and u.unknown and u.ticket == 0


def test_fingerprint_stable_and_price_rounded() -> None:
    a = idempotency_fingerprint(symbol=SYMBOL, order_type="BUY", volume=0.1, price=100.005)
    b = idempotency_fingerprint(symbol=SYMBOL, order_type="BUY", volume=0.1, price=100.005)
    c = idempotency_fingerprint(symbol=SYMBOL, order_type="BUY", volume=0.2, price=100.005)
    assert a == b and a != c


# ----------------------------------------------------------------------
# 2) Ambiguous (exception) send on the market path -> UNKNOWN evidence
# ----------------------------------------------------------------------
def test_market_write_exception_is_unknown_not_failed(adapter: Any, fake_mt5: FakeMT5) -> None:
    fake_mt5._send_exc = ConnectionError("socket closed mid-request")
    res = adapter.write_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=VOLUME,
        price=PRICE,
        stop_loss=1998.0,
        take_profit=2004.0,
    )
    assert res.unknown and res.ticket == 0
    assert len(fake_mt5.send_calls) == 1  # no blind retry


def test_market_write_definitive_success(adapter: Any, fake_mt5: FakeMT5) -> None:
    res = adapter.write_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=VOLUME,
        price=PRICE,
        stop_loss=1998.0,
        take_profit=2004.0,
    )
    assert res.ok and res.ticket == 152500000001


def test_market_write_definitive_rejection(adapter: Any, fake_mt5: FakeMT5) -> None:
    fake_mt5._queue = [10018]  # MARKET_CLOSED: definitive hard reject
    res = adapter.write_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=VOLUME,
        price=PRICE,
        stop_loss=1998.0,
        take_profit=2004.0,
    )
    assert (not res.ok) and (not res.unknown) and res.retcode == 10018


# ----------------------------------------------------------------------
# 3) Pending path: ambiguous then reconciled via fingerprint equivalence
# ----------------------------------------------------------------------
def _pending_row(ticket: int, order_type: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        symbol=SYMBOL,
        magic=888101,
        type=order_type,
        volume_current=VOLUME,
        price_open=PRICE,
    )


def test_pending_ambiguous_reconciles_to_existing_order(adapter: Any, fake_mt5: FakeMT5) -> None:
    # Send 1: ambiguous exception. The broker DID place the order but the
    # response was lost -> it shows up in orders_get.
    fake_mt5._send_exc = TimeoutError("response lost")
    res1 = adapter.write_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY_LIMIT,
        volume=VOLUME,
        price=PRICE,
        stop_loss=1998.0,
        take_profit=2004.0,
    )
    assert res1.unknown
    # Reconciliation: the order is actually resting on the broker.
    fake_mt5._send_exc = None
    fake_mt5._pending = [_pending_row(152500000777)]
    res2 = adapter.reconcile_pending_write(
        symbol=SYMBOL, order_type=OrderType.BUY_LIMIT, volume=VOLUME, price=PRICE
    )
    assert res2.ok and res2.ticket == 152500000777
    assert len(fake_mt5.send_calls) == 1  # reconciliation did NOT resend


def test_pending_unresolved_stays_unknown_no_retry(adapter: Any, fake_mt5: FakeMT5) -> None:
    fake_mt5._send_exc = TimeoutError("response lost")
    res1 = adapter.write_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY_LIMIT,
        volume=VOLUME,
        price=PRICE,
        stop_loss=1998.0,
        take_profit=2004.0,
    )
    assert res1.unknown
    # Reconciliation finds NOTHING and there is no authoritative rejection:
    fake_mt5._send_exc = None
    fake_mt5._pending = []
    res2 = adapter.reconcile_pending_write(
        symbol=SYMBOL, order_type=OrderType.BUY_LIMIT, volume=VOLUME, price=PRICE
    )
    assert res2.unknown  # UNKNOWN STAYS UNKNOWN
    assert len(fake_mt5.send_calls) == 1  # and nothing was re-sent


def test_pending_definitive_reject_after_ambiguity(adapter: Any, fake_mt5: FakeMT5) -> None:
    fake_mt5._send_exc = TimeoutError("response lost")
    res1 = adapter.write_pending_order(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=VOLUME,
        price=PRICE,
        stop_loss=2002.0,
        take_profit=1996.0,
    )
    assert res1.unknown
    # History sweep later proves an authoritative rejection (order never
    # existed) -> resolution is REJECTED, not UNKNOWN.
    fake_mt5._send_exc = None
    fake_mt5._pending = []
    res2 = adapter.reconcile_pending_write(
        symbol=SYMBOL,
        order_type=OrderType.SELL_LIMIT,
        volume=VOLUME,
        price=PRICE,
        authoritatively_rejected=True,
    )
    assert (not res2.ok) and (not res2.unknown)


# ----------------------------------------------------------------------
# 4) Remote gateway: real socket-level timeout -> UNKNOWN, no retry
# ----------------------------------------------------------------------
class _HangHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
        except Exception:
            pass
        # Never respond: the client must hit its socket timeout.


@pytest.fixture()
def hanging_gateway_url() -> Any:
    server = HTTPServer(("127.0.0.1", 0), _HangHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=2)


def test_remote_gateway_write_timeout_is_unknown(hanging_gateway_url: str) -> None:
    from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

    gw = RemoteMT5GatewayAdapter(
        gateway_url=hanging_gateway_url,
        timeout_seconds=0.3,  # force a real socket timeout
    )
    res = gw.write_market_order(
        symbol=SYMBOL,
        order_type=OrderType.BUY,
        volume=VOLUME,
        price=PRICE,
        stop_loss=1998.0,
        take_profit=2004.0,
    )
    assert res.unknown and res.ticket == 0  # UNKNOWN != FAILED


# ----------------------------------------------------------------------
# 5) OrderIntentStore: intent survives restart
# ----------------------------------------------------------------------
def test_intent_survives_restart_and_resolves(tmp_path: Any) -> None:
    path = tmp_path / "intents" / "order_intents.jsonl"
    store = OrderIntentStore(path)
    intent = OrderIntent(
        intent_id="int-1",
        request_id="req-abc",
        created_at_utc="2026-09-07T06:00:00+00:00",
        symbol=SYMBOL,
        side="BUY",
        order_kind="MARKET",
        requested_volume=VOLUME,
        price=PRICE,
        fingerprint=idempotency_fingerprint(
            symbol=SYMBOL, order_type="BUY", volume=VOLUME, price=PRICE
        ),
    )
    store.record(intent)

    # Process restart: a NEW store instance over the same path must see the
    # PENDING intent (this is what startup reconciliation consumes).
    store2 = OrderIntentStore(path)
    pending = store2.load_pending()
    assert set(pending) == {"int-1"}
    assert pending["int-1"].request_id == "req-abc"
    assert pending["int-1"].status == "PENDING"

    store2.resolve("int-1", status="RECONCILED_SUCCESS", ticket=777)
    store3 = OrderIntentStore(path)
    assert store3.load_pending() == {}  # resolved -> no longer pending

    # Restart-durable idempotency-key derivation: same inputs -> same key.
    assert pending["int-1"].fingerprint == idempotency_fingerprint(
        symbol=SYMBOL, order_type="BUY", volume=VOLUME, price=PRICE
    )


def test_intent_store_survives_corrupt_line(tmp_path: Any) -> None:
    path = tmp_path / "intents.jsonl"
    store = OrderIntentStore(path)
    store.record(
        OrderIntent(
            intent_id="int-2",
            request_id="req-1",
            created_at_utc="2026-09-07T06:00:00+00:00",
            symbol=SYMBOL,
            side="SELL",
            order_kind="PENDING",
            requested_volume=VOLUME,
            price=PRICE,
            fingerprint="fp",
        )
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{corrupt json\n")
    store2 = OrderIntentStore(path)
    assert set(store2.load_pending()) == {"int-2"}
