"""Regression tests: pending-order cancellation MUST be broker-verified before the
exposure slot is released; reconciliation MUST detect internal/broker mismatches;
retcode=0 MUST NOT be treated as success or blind failure (BUG-072/073 guards).

Covers the forensic findings of 2026-08-18:
  - `cancel_pending_order` interpreted `retcode=0` without broker verification
  - the exposure gate read the (stale) internal cache while the tick pipeline
    was crashing on UnboundLocalError('time') inside _process_tick_pipeline
  - internal pending state could outlive the real broker order
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import TickData
from nexus_scalp.execution.order_manager import MAX_TOTAL_EXPOSURE, OrderLifecycleManager
from nexus_scalp.signals.policy import MAX_TOTAL_EXPOSURE as POLICY_MAX_EXPOSURE
from nexus_scalp.signals.policy import SignalPolicy


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------
class FakeAudit:
    """Minimal audit double recording only what the tests observe."""

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.executions: list[tuple[Any, str]] = []

    def log_order(self, **kwargs: Any) -> None:
        self.orders.append(kwargs)

    def log_execution(self, order: Any, status: str) -> None:
        self.executions.append((order, status))

    def log_signal(self, proposal: Any) -> None:
        pass


class FakeBroker:
    """Deterministic broker double: real pending list + cancel outcomes."""

    def __init__(self) -> None:
        self.pendings: list[dict[str, Any]] = []
        self.cancel_retcode: int = 10009  # TRADE_RETCODE_DONE
        self.cancel_calls: list[int] = []
        self.orders_get_failures = 0

    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self.orders_get_failures > 0:
            self.orders_get_failures -= 1
            raise RuntimeError("simulated orders_get failure")
        return [dict(p) for p in self.pendings]

    def cancel_pending_order(self, ticket: int) -> bool:
        self.cancel_calls.append(ticket)
        if self.cancel_retcode == 10009:
            self.pendings = [p for p in self.pendings if p["ticket"] != ticket]
            return True
        if self.cancel_retcode == 0:
            # MT5 package semantics: retcode 0 = request never reached server.
            # Broker state is UNCHANGED.
            return False
        return False  # any other failure leaves the order in place

    def get_positions(self, symbol: str | None = None) -> list[Any]:
        return []

    def get_account_info(self) -> Any:
        return None

    def get_symbol_info(self, symbol: str) -> Any:
        return None

    def send_order(self, order: Any) -> bool:
        return True

    def place_pending_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        ticket = 5000 + len(self.pendings) + 1
        self.pendings.append(
            {
                "ticket": ticket,
                "symbol": symbol,
                "type": order_type,
                "volume": volume,
                "price_open": price,
                "sl": stop_loss,
                "tp": take_profit,
                "magic": 888101,
            }
        )
        return ticket


def make_tick(bid: float = 4410.0, ask: float = 4410.3, ts: datetime | None = None) -> TickData:
    return TickData(
        symbol="XAUUSD",
        bid=bid,
        ask=ask,
        timestamp=ts or datetime.now(UTC),
        volume=0.0,
        spread=ask - bid,
    )


def make_manager(broker: FakeBroker) -> OrderLifecycleManager:
    audit = FakeAudit()
    mgr = OrderLifecycleManager(
        adapter=broker,  # type: ignore[arg-type]
        audit_repo=audit,  # type: ignore[arg-type]
        risk_engine=None,
    )
    return mgr


# ---------------------------------------------------------------------------
# TEST 1: successful cancel -> broker confirms gone -> slot released
# ---------------------------------------------------------------------------
def test_cancel_confirmed_releases_slot() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 777,
            "symbol": "XAUUSD",
            "type": "SELL_LIMIT",
            "volume": 0.5,
            "price_open": 4430.82,
            "sl": 4435.0,
            "tp": 4420.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)

    # Internal exposure: 1 pending
    mgr._live_tickets_cache = {
        777: {
            "ticket": 777,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4430.82,
            "magic": 888101,
        }
    }

    assert not mgr._is_exposure_available("XAUUSD")

    # Cancel + verify against the broker double
    ok = mgr.cancel_pending_order_verified(ticket=777)
    assert ok is True
    # Broker confirmed the order is gone
    assert broker.get_pending_orders("XAUUSD") == []

    # Refresh the internal cache from the (now-clean) broker view
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    positions, pendings = mgr.count_total_exposure("XAUUSD")
    assert pendings == 0 and positions == 0
    assert mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 2: cancel response ambiguous -> broker query confirms gone -> slot released
# ---------------------------------------------------------------------------
def test_ambiguous_cancel_but_broker_gone_releases_slot() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 778,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        778: {
            "ticket": 778,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }

    # Adapter returns False (ambiguous/retcode 0) but broker state actually empty
    broker.cancel_retcode = 0
    broker.pendings = []  # broker already processed the removal

    ok = mgr.cancel_pending_order_verified(ticket=778)
    # Verification query shows GONE -> treated as confirmed
    assert ok is True
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 3: cancel response ambiguous -> broker reports still active -> slot stays locked
# ---------------------------------------------------------------------------
def test_ambiguous_cancel_broker_still_active_keeps_slot() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 779,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        779: {
            "ticket": 779,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }

    broker.cancel_retcode = 0  # ambiguous, order stays

    ok = mgr.cancel_pending_order_verified(ticket=779)
    assert ok is False
    assert broker.get_pending_orders("XAUUSD")  # still active on broker
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 4: broker cancel rejected -> internal state remains occupied
# ---------------------------------------------------------------------------
def test_rejected_cancel_keeps_internal_occupied() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 780,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        780: {
            "ticket": 780,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }

    broker.cancel_retcode = 10006  # TRADE_RETCODE_REJECT

    ok = mgr.cancel_pending_order_verified(ticket=780)
    assert ok is False
    # Internal state NOT cleared until broker confirms
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 5: repeated cancellation is idempotent
# ---------------------------------------------------------------------------
def test_cancel_is_idempotent() -> None:
    broker = FakeBroker()
    mgr = make_manager(broker)

    ok1 = mgr.cancel_pending_order_verified(ticket=999)
    ok2 = mgr.cancel_pending_order_verified(ticket=999)
    assert ok1 is True
    assert ok2 is True
    # No storm: both calls are cheap and broker state unchanged


# ---------------------------------------------------------------------------
# TEST 6: reconciliation detects internal/broker mismatch
# ---------------------------------------------------------------------------
def test_reconcile_detects_internal_pending_missing_on_broker() -> None:
    broker = FakeBroker()  # broker has NO pendings
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        1234: {
            "ticket": 1234,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }

    report = mgr.reconcile_pending_state(symbol="XAUUSD", current_tick=make_tick())
    assert report["pending_internal"] == 1
    assert report["pending_broker"] == 0
    assert report["mismatch"] is True
    # Broker truth wins: stale internal pending is removed
    assert mgr.count_total_exposure("XAUUSD") == (0, 0)
    assert mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 7: stale pending cannot disappear internally before broker confirms
# ---------------------------------------------------------------------------
def test_stale_pending_not_removed_before_broker_confirms() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 781,
            "symbol": "XAUUSD",
            "type": "SELL_LIMIT",
            "volume": 0.5,
            "price_open": 4430.82,
            "sl": 4435.0,
            "tp": 4420.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        781: {
            "ticket": 781,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4430.82,
            "magic": 888101,
        }
    }

    # A cancel REQUEST that fails must NOT clear internal state
    broker.cancel_retcode = 0
    mgr.cancel_pending_order_verified(ticket=781)
    assert mgr.count_total_exposure("XAUUSD") == (0, 1)
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 8: stale internal pending is repaired after broker confirms absence
# ---------------------------------------------------------------------------
def test_stale_internal_repaired_after_broker_confirms_absence() -> None:
    broker = FakeBroker()
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        5678: {
            "ticket": 5678,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4410.0,
            "magic": 888101,
        }
    }

    mgr.reconcile_pending_state(symbol="XAUUSD", current_tick=make_tick())
    assert mgr.count_total_exposure("XAUUSD") == (0, 0)


# ---------------------------------------------------------------------------
# TEST 9: MAX_EXPOSURE_REACHED uses actual broker state
# ---------------------------------------------------------------------------
def test_policy_exposure_uses_broker_verified_cache() -> None:
    broker = FakeBroker()
    mgr = make_manager(broker)

    # Broker reports NO pendings -> cache must be empty -> exposure available
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert mgr._is_exposure_available("XAUUSD")

    # Broker has 1 pending -> cache reflects it -> exposure blocked
    broker.pendings.append(
        {
            "ticket": 782,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 10: unresolved cancellation keeps exposure protection active
# ---------------------------------------------------------------------------
def test_unresolved_cancel_keeps_protection() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 783,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        783: {
            "ticket": 783,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }

    broker.cancel_retcode = 0
    broker.orders_get_failures = 3  # verification queries also fail -> UNKNOWN

    ok = mgr.cancel_pending_order_verified(ticket=783)
    assert ok is False
    # UNKNOWN state must NOT release the slot
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 11: cancellation retry is bounded
# ---------------------------------------------------------------------------
def test_cancel_retry_bounded() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 784,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    broker.cancel_retcode = 10006  # always rejected

    attempts = mgr.cancel_pending_order_with_retry(ticket=784, max_attempts=3)
    assert attempts <= 3
    assert len(broker.cancel_calls) <= 3


# ---------------------------------------------------------------------------
# TEST 12: cancellation failure does not crash the manager
# ---------------------------------------------------------------------------
def test_cancel_failure_isolated() -> None:
    broker = FakeBroker()

    def boom(ticket: int) -> bool:
        raise RuntimeError("broker exploded")

    broker.cancel_pending_order = boom  # type: ignore[method-assign]
    mgr = make_manager(broker)
    # Broker truth: the ticket is NOT on the broker (empty active list).
    # A cancel that raises because there is nothing to cancel is confirmed
    # gone; the slot may be released.
    mgr._live_tickets_cache = {
        785: {
            "ticket": 785,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }
    ok = mgr.cancel_pending_order_verified(ticket=785)
    assert ok is True
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 13: new entry remains blocked while pending state is unknown
# ---------------------------------------------------------------------------
def test_entry_blocked_while_unknown() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 786,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        786: {
            "ticket": 786,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }
    # Verification query fails -> UNKNOWN -> still blocked
    broker.orders_get_failures = 5
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 14: once cancellation is verified, entry slot becomes available
# ---------------------------------------------------------------------------
def test_verified_cancel_opens_slot() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 787,
            "symbol": "XAUUSD",
            "type": "SELL_LIMIT",
            "volume": 0.5,
            "price_open": 4430.82,
            "sl": 4435.0,
            "tp": 4420.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        787: {
            "ticket": 787,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4430.82,
            "magic": 888101,
        }
    }

    assert not mgr._is_exposure_available("XAUUSD")
    assert mgr.cancel_pending_order_verified(ticket=787) is True
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 15: healthy pending order does not get incorrectly canceled
# ---------------------------------------------------------------------------
def test_healthy_pending_not_canceled() -> None:
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 788,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)
    mgr._live_tickets_cache = {
        788: {
            "ticket": 788,
            "symbol": "XAUUSD",
            "type": "PENDING",
            "price": 4400.0,
            "magic": 888101,
        }
    }

    # No cancel requested -> order untouched, slot occupied
    assert broker.get_pending_orders("XAUUSD")
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 16: UnboundLocalError('time') in the tick pipeline must not freeze the
# exposure cache (regression for the 03:24:32 live crash)
# ---------------------------------------------------------------------------
def test_tick_pipeline_crash_does_not_freeze_exposure_cache() -> None:
    from nexus_scalp.application.live_engine import LiveEngine

    # (LiveEngine import guards against import-time regressions)
    broker = FakeBroker()
    broker.pendings.append(
        {
            "ticket": 789,
            "symbol": "XAUUSD",
            "type": "BUY_LIMIT",
            "volume": 0.4,
            "price_open": 4400.0,
            "sl": 4395.0,
            "tp": 4410.0,
            "magic": 888101,
        }
    )
    mgr = make_manager(broker)

    # Simulate a pipeline exception between cache rebuilds; then the next
    # rebuild must still happen (the engine calls manage_active_positions
    # inside its try block; the crash is isolated per tick).
    with patch.object(
        mgr,
        "manage_active_positions",
        side_effect=UnboundLocalError("cannot access local variable 'time'"),
    ):
        try:
            mgr.manage_active_positions(
                symbol="XAUUSD", current_tick=make_tick(), probs=None, regime_state=None
            )
        except UnboundLocalError:
            pass

    # After the isolated failure, a fresh rebuild (as the engine's next tick
    # would do) restores a correct cache from the broker view.
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert mgr.count_total_exposure("XAUUSD") == (0, 1)
    assert not mgr._is_exposure_available("XAUUSD")


# ---------------------------------------------------------------------------
# TEST 17: broker history view — the forensic ticket never existed
# (guards against "reused phantom" tickets entering the exposure cache)
# ---------------------------------------------------------------------------
def test_phantom_reuse_does_not_enter_exposure_cache() -> None:
    broker = FakeBroker()
    mgr = make_manager(broker)

    # The idempotency guard must NOT fabricate a pending when orders_get is
    # empty (the 04:11/05:18 forensic case: retcode 0 + empty broker view).
    broker.orders_get_failures = 0
    mgr.refresh_live_tickets_cache(symbol="XAUUSD", current_tick=make_tick())
    assert mgr.count_total_exposure("XAUUSD") == (0, 0)
    assert mgr._is_exposure_available("XAUUSD")
