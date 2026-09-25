"""TicketsCache — explicit owner of the live-tickets cache view.

S6 Phase-2 seam (Agent-5, CHG-0032/TASK-OM-P0-DECOMP): the live-tickets
cache (the position+pending view published for web/debug readers) and its
rebuild logic moved out of OrderLifecycleManager.

Ownership contract:
    OWNS    : the cache dict + its rebuild algorithm (positions first, then
              pending orders with per-field tolerant lookup; pending-query
              failures are isolated with an error log — never fatal)
    NOT OWNED: broker authority (reads via a lookup callable passed in),
               risk/policy/strategy/execution/lifecycle decisions

Lock semantics preserved: the manager performs the swap under its
_live_tickets_lock exactly as before; the cache never locks internally (the
owner of the atomicity remains the manager's existing critical sections).

Cache entry shapes (unchanged):
    POSITION: ticket/symbol/price/magic/type/direction/volume/sl/tp/profit
    PENDING: ticket/symbol/price/magic/type/direction/volume
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.execution.tickets_cache")


class TicketsCache:
    """Live-tickets cache owner (positions + pending orders view)."""

    def __init__(self) -> None:
        self._cache: dict[int, dict[str, Any]] = {}

    @property
    def cache(self) -> dict[int, dict[str, Any]]:
        """Live cache dict (manager swaps the reference under its lock)."""
        return self._cache

    def rebuild(
        self,
        positions: list[Any],
        pending_lookup: Callable[[], list[Any]] | None,
        pending_field: Callable[..., Any],
        symbol: str,
    ) -> dict[int, dict[str, Any]]:
        """Rebuild the cache from broker positions + pending orders.

        VERBATIM from manage_active_positions' rebuild block. Returns the
        new cache for the manager to swap under its lock."""
        new_cache: dict[int, dict[str, Any]] = {}
        if positions:
            for pos in positions:
                new_cache[pos.ticket] = {
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "price": pos.price_open,
                    "magic": getattr(pos, "magic", 888101),
                    "type": "POSITION",
                    # Direction is published so SignalPolicy can detect an opposing
                    # signal and request an AI reversal instead of stacking orders.
                    "direction": pos.type.value,
                    "volume": pos.volume,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                }

        try:
            if pending_lookup is not None:
                pending_orders = pending_lookup()
                if pending_orders:
                    for pending in pending_orders:
                        ticket = pending_field(pending, "ticket", "order_id")
                        if ticket:
                            pending_type = pending_field(pending, "type", "order_type")
                            pending_dir = (
                                "BUY"
                                if "BUY"
                                in str(getattr(pending_type, "value", pending_type)).upper()
                                else "SELL"
                            )
                            new_cache[ticket] = {
                                "ticket": ticket,
                                "symbol": pending_field(pending, "symbol", default=symbol),
                                "price": pending_field(pending, "price_open", "price", default=0.0),
                                "magic": pending_field(
                                    pending, "magic", "magic_number", default=888101
                                ),
                                "type": "PENDING",
                                "direction": pending_dir,
                                "volume": pending_field(pending, "volume", default=0.0),
                            }
        except Exception as e:
            logger.error("Failed to query pending orders for cache", error=e)

        return new_cache

    def swap(self, new_cache: dict[int, dict[str, Any]]) -> None:
        """Publish the rebuilt cache (manager calls this under its lock)."""
        self._cache = new_cache

    def pop_ticket(self, ticket: int) -> None:
        """Release one ticket (manager cleanup, under the manager's lock)."""
        self._cache.pop(ticket, None)
