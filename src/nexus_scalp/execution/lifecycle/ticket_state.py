"""Canonical per-ticket position state (P0 seam S5: TicketStateStore).

Replaces the ~30 fragmented per-ticket ``dict[int, ...]`` attributes that lived
on ``OrderLifecycleManager`` (``_entry_prices``, ``_last_modify_sl``,
``_sl_modified_flags``, ...). Related fields can no longer drift out of
synchronization because one ``TicketState`` object owns the whole per-ticket
record and ``TicketStateStore`` is the single source of truth.

Ownership contract:
    READS   : nothing external — pure state container.
    WRITES  : only its own fields. No broker I/O, no audit, no dispatch.
    LIFECYCLE: ``get()`` creates on demand -> tracked; ``remove()`` drops the
              whole record atomically (part of the manager's
              ``_cleanup_ticket_state`` bundle) -> cleaned.

Units: prices in PRICE units; PnL/risk fields in USD; timestamps are
timezone-aware ``datetime``; monotonic clocks are ``time.monotonic()``
seconds. ``entry_*`` fields are frozen at open (BUG-045 convention for
entry SL); ``last_modify_sl``/``sl_modified`` advance only on confirmed
broker modifications (BUG-085).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TicketState:
    """Canonical per-ticket position record. One instance per live ticket."""

    ticket: int
    # --- excursion / telemetry -------------------------------------------
    last_tick: float = 0.0
    peak_profit: float = 0.0
    peak_drawdown: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    adverse_ticks: int = 0
    favorable_ticks: int = 0
    stagnation_ticks: int = 0
    time_in_profit_sec: float = 0.0
    time_in_drawdown_sec: float = 0.0
    last_seen_ts: datetime | None = None
    last_price: float = 0.0
    # --- entry baseline (frozen at open) ----------------------------------
    entry_price: float = 0.0
    entry_sl: float = 0.0
    entry_tp: float = 0.0
    entry_direction: str = ""
    entry_confidence: float = 0.0
    entry_regime: str = ""
    entry_reason: str = ""
    entry_timestamp: datetime | None = None
    entry_order_id: str = ""
    last_known_volume: float = 0.0
    initial_risk: float = 0.0
    # --- PHASE 08 execution-quality evidence -------------------------------
    entry_expected_price: float = 0.0
    entry_atr: float = 0.0
    entry_spread: float = 0.0
    entry_fill_latency_ms: float = 0.0
    time_to_mfe_sec: float = 0.0
    time_to_mae_sec: float = 0.0
    # --- engine-command channel (overrides broker-heuristic exit) -----------
    forced_exit_mechanism: str | None = None
    is_closed: bool = False
    exit_pending_final: dict[str, Any] = field(default_factory=dict)
    # --- modification / lifecycle flags ------------------------------------
    last_modify_sl: float = 0.0
    sl_modified: bool = False
    partial_closed: bool = False
    rescue_registered: bool = False
    last_mod_price: float = 0.0
    last_mod_time: datetime | None = None
    # --- close-handoff (transient, consumed by the closing autopsy) ---------
    net_pnl: float | None = None
    exit_mechanism: str | None = None
    # --- setup fingerprint (kept generic; store owns the blob) --------------
    setup_snapshot: dict[str, Any] = field(default_factory=dict)

    def record_sl_modification(self, new_sl: float) -> None:
        """Fuse the BUG-085 pair: last-confirmed SL and its flag advance together."""
        self.last_modify_sl = float(new_sl)
        self.sl_modified = True


class TicketStateStore:
    """Canonical owner of every live :class:`TicketState`.

    ``get()`` lazily creates the record so rescue/bootstrap paths never need
    ``setdefault`` dances; ``remove()`` drops the whole record atomically so
    no field can outlive the ticket (the desync class this store eliminates).
    """

    def __init__(self) -> None:
        self._states: dict[int, TicketState] = {}

    def get(self, ticket: int) -> TicketState:
        """Return the live record, creating it on first touch."""
        state = self._states.get(int(ticket))
        if state is None:
            state = TicketState(ticket=int(ticket))
            self._states[int(ticket)] = state
        return state

    def peek(self, ticket: int) -> TicketState | None:
        """Return the record without creating it (observation-only probes)."""
        return self._states.get(int(ticket))

    def remove(self, ticket: int) -> None:
        """Drop the whole record atomically (cleanup bundle)."""
        self._states.pop(int(ticket), None)

    def contains(self, ticket: int) -> bool:
        """Membership probe (tracked-ticket universe, e.g. sweep passes)."""
        return int(ticket) in self._states

    def keys(self) -> list[int]:
        """All tracked tickets (iteration source for sweep-style passes)."""
        return list(self._states.keys())

    def __len__(self) -> int:
        return len(self._states)

    def __contains__(self, ticket: object) -> bool:
        try:
            return int(ticket) in self._states  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
