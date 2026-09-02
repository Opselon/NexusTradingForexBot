"""Agent-5 S6 Phase-2 Seam 1 golden: TicketsCache ownership + parity.

Verifies the rebuild algorithm produces IDENTICAL cache dicts for
positions + pending orders (shape parity), pending-query failure isolation,
empty input, and manager wiring.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus_scalp.execution.tickets_cache import TicketsCache


def _field(pending, *names, default=None):
    """Mirror of OrderLifecycleManager._pending_field for objects/dicts."""
    for n in names:
        if isinstance(pending, dict):
            if n in pending:
                return pending[n]
        else:
            v = getattr(pending, n, None)
            if v is not None:
                return v
    return default


class TestTicketsCache:
    def test_position_entry_shape(self):
        tc = TicketsCache()
        pos = SimpleNamespace(
            ticket=201,
            symbol="XAUUSD",
            price_open=2000.0,
            magic=888101,
            type=SimpleNamespace(value="BUY"),
            volume=1.0,
            sl=1995.0,
            tp=2020.0,
            profit=1.5,
        )
        cache = tc.rebuild([pos], None, _field, "XAUUSD")
        assert cache[201] == {
            "ticket": 201,
            "symbol": "XAUUSD",
            "price": 2000.0,
            "magic": 888101,
            "type": "POSITION",
            "direction": "BUY",
            "volume": 1.0,
            "sl": 1995.0,
            "tp": 2020.0,
            "profit": 1.5,
        }

    def test_pending_entry_shape_and_direction(self):
        tc = TicketsCache()
        pend = {
            "ticket": 555,
            "symbol": "EURUSD",
            "price": 1.1,
            "magic": 7,
            "type": "SELL_STOP",
            "volume": 0.5,
        }
        cache = tc.rebuild([], lambda: [pend], _field, "EURUSD")
        assert cache[555]["type"] == "PENDING"
        assert cache[555]["direction"] == "SELL"

    def test_empty_positions_no_pending(self):
        tc = TicketsCache()
        assert tc.rebuild([], None, _field, "XAUUSD") == {}

    def test_pending_failure_isolated(self):
        tc = TicketsCache()
        pos = SimpleNamespace(
            ticket=1,
            symbol="S",
            price_open=1.0,
            magic=1,
            type=SimpleNamespace(value="BUY"),
            volume=1.0,
            sl=0.0,
            tp=0.0,
            profit=0.0,
        )

        def boom():
            raise RuntimeError("adapter down")

        cache = tc.rebuild([pos], boom, _field, "S")
        # positions survive the pending-query failure
        assert 1 in cache and cache[1]["type"] == "POSITION"

    def test_swap_and_pop(self):
        tc = TicketsCache()
        tc.swap({9: {"ticket": 9}})
        assert tc.cache[9]["ticket"] == 9
        tc.pop_ticket(9)
        assert 9 not in tc.cache

    def test_manager_wiring(self):
        src = (
            __import__("pathlib")
            .Path(__import__("nexus_scalp.execution.order_manager", fromlist=["x"]).__file__)
            .read_text(encoding="utf-8")
        )
        assert "self._tickets_cache = TicketsCache()" in src
        assert "self._tickets_cache.rebuild(" in src
        assert "self._tickets_cache.swap(new_cache)" in src
        # cleanup routes through the owner
        bundle = src.split("def _cleanup_ticket_state")[1].split("def ")[0]
        assert "self._tickets_cache.pop_ticket(ticket)" in bundle
        # the verbatim block no longer sits inline in the orchestrator
        mam = src.split("def manage_active_positions")[1].split("def _sweep_dead_tickets")[0]
        assert "new_cache[pos.ticket] = {" not in mam
