"""RECON CRITICAL GATE — pending-order re-quote lock + cache reconciliation.

Two execution-layer production paths had ZERO test coverage (verified by the
execution forensic lane, 2026-09-14):

  * should_modify_pending_order's RELEASE leg — the old test only proved
    within-lock blocking with an identical `now`; it never advanced the clock
    past the 30s lock, so an inverted time/drift gate shipped uncaught.
  * reconcile_pending_state (pending_orders.py:592) — the local-vs-broker
    mismatch repair that keeps the exposure gate from sticking open/closed on
    a stale cache. grep over tests/ found zero references.

PendingOrderLifecycle is constructed directly with its real collaborators; the
only stubbed boundary is the broker adapter (a legitimate external boundary —
the thing under test is the lifecycle logic, not MT5).

Deterministic: an injected clock (explicit `now` argument), temp state, no
sleeps, no wall-clock races.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nexus_scalp.execution.lifecycle.pending_orders import (
    PENDING_ORDER_LOCK_SECONDS,
    PendingOrderLifecycle,
)


class _FakeAdapter:
    """Broker-boundary stub: get_pending_orders returns a fixed roster."""

    def __init__(self, pendings):
        self._pendings = pendings

    def get_pending_orders(self, symbol=None):
        return list(self._pendings)


def _lifecycle(pendings=(), tickets=None):
    refresh_calls: list = []
    tl = PendingOrderLifecycle(
        adapter=_FakeAdapter(pendings),
        tickets_view=lambda: tickets or {},
        refresh_cache=lambda *a, **k: refresh_calls.append(a or k),
    )
    tl._refresh_calls = refresh_calls  # type: ignore[attr-defined]
    return tl


class TestRequoteLockRelease:
    def test_requote_blocked_inside_lock_even_with_large_drift(self):
        """First modify is always allowed (seeds the baseline); a second within
        30s must be refused REGARDLESS of drift — this is the churn guard."""
        lc = _lifecycle()
        t0 = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
        assert lc.should_modify_pending_order(7, price=4400.0, atr=1.0, now=t0) is True
        # inside the lock, huge drift: still blocked
        assert (
            lc.should_modify_pending_order(7, price=4450.0, atr=1.0, now=t0 + timedelta(seconds=20))
            is False
        )

    def test_requote_allowed_only_after_lock_AND_sufficient_drift(self):
        """The release path: past 30s with drift >= 1.0x ATR -> allowed."""
        lc = _lifecycle()
        t0 = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
        lc.should_modify_pending_order(9, price=4400.0, atr=1.0, now=t0)
        later = t0 + timedelta(seconds=PENDING_ORDER_LOCK_SECONDS + 0.1)
        assert lc.should_modify_pending_order(9, price=4402.0, atr=1.0, now=later) is True

    def test_requote_still_held_past_lock_when_drift_below_one_atr(self):
        """The second gate, isolated: past the lock but drift < 1.0x ATR ->
        held. (The old test never separated the two legs, so a fix that only
        moved the time gate — leaving drift unchecked — looked green.)"""
        lc = _lifecycle()
        t0 = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
        lc.should_modify_pending_order(11, price=4400.0, atr=1.0, now=t0)
        later = t0 + timedelta(seconds=PENDING_ORDER_LOCK_SECONDS + 0.1)
        assert lc.should_modify_pending_order(11, price=4400.5, atr=1.0, now=later) is False


class TestPendingCacheReconciliation:
    def test_mismatch_repairs_internal_view(self):
        """Internal cache thinks 2 pendings, broker says 0 -> mismatch, the
        cache refresh (repair) fires so the exposure gate stops counting
        ghost orders."""
        tickets = {1: {"type": "PENDING"}, 2: {"type": "PENDING"}}
        lc = _lifecycle(pendings=[], tickets=tickets)
        report = lc.reconcile_pending_state(symbol="XAUUSD")
        assert report["mismatch"] is True
        assert report["pending_internal"] == 2
        assert report["pending_broker"] == 0
        assert report["repaired"] is True
        assert lc._refresh_calls, "repair must refresh the cache, not just report"

    def test_no_mismatch_when_views_agree(self):
        tickets = {1: {"type": "PENDING"}}
        pending_obj = object()
        lc = _lifecycle(pendings=[pending_obj], tickets=tickets)
        report = lc.reconcile_pending_state(symbol="XAUUSD")
        assert report["mismatch"] is False
        assert report["repaired"] is False

    def test_broker_failure_never_repairs_on_guesswork(self):
        """A broker that returns None (query failed) must set broker_error and
        NOT repair — silently trusting the stale cache would drift exposure
        accounting; the safe action is no action (fail closed to reporting)."""

        class _Broken(_FakeAdapter):
            def get_pending_orders(self, symbol=None):
                return None

        tickets = {1: {"type": "PENDING"}}
        lc = _lifecycle(pendings=[], tickets=tickets)
        lc.adapter = _Broken([])
        report = lc.reconcile_pending_state(symbol="XAUUSD")
        assert report["broker_error"] is True
        assert report["repaired"] is False
