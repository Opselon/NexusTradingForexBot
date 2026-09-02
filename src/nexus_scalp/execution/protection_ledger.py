"""Per-ticket profit-protection ledger (Agent-5 P0 seam S1).

Extracted VERBATIM from execution/order_manager.py (behavior-preserving
decomposition; every field/semantics identical). This ledger owns the ONLY
per-ticket protection facts used by SL/TP management:

    peak_win_usd, was_sl_modified, profit_giveback_triggered, close_requested,
    telemetry/BE-failure/BE-attempt timestamps, breakeven_sl_price.

State ownership: the ledger dict is touched ONLY here (get/create) plus the
order manager's refresh path which mutates the returned dataclass in place.
Lifecycle: entries live for the manager's lifetime (bounded by open tickets);
deliberately NOT part of the per-ticket cleanup bundle.

USED BY: execution/order_manager.py (facade methods get_protection_state /
refresh_protection_state delegate here); web/debug_snapshot reads via the
facade method.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PositionProtectionState:
    """
    Deterministic, per-ticket profit-protection state machine.

    Bound to the MT5 ticket (never a global/shared variable) so protection decisions
    are idempotent across repeated management passes. Every field is a fact about
    THIS ticket only.
    """

    #: Monotonic high-water mark of floating PnL in account currency. Never decreases
    #: while the position remains open.
    peak_win_usd: float = 0.0
    #: True only after the broker CONFIRMED the breakeven SL modification, or after the
    #: observed broker-side SL was found to already sit at/beyond the breakeven level.
    was_sl_modified: bool = False
    #: True once profit-giveback protection has armed for this ticket.
    profit_giveback_triggered: bool = False
    #: True once a close request for this ticket was accepted by the adapter.
    close_requested: bool = False
    #: Monotonic-clock stamp of the last CONSOLE telemetry emission (never gates audit).
    last_telemetry_log_time: float = 0.0
    #: Monotonic-clock stamp of the last breakeven FAILURE log, used only to avoid log
    #: spam on every loop iteration. It never gates the retry itself.
    last_be_failure_log_time: float = 0.0
    #: Monotonic-clock stamp of the last breakeven MODIFY attempt. Gates retries so
    #: a broker-rejected modification cannot hammer the terminal every tick.
    last_be_attempt_time: float = 0.0
    #: Breakeven price level actually locked in (0.0 until computed).
    breakeven_sl_price: float = 0.0

    def update_peak(self, current_pnl_usd: float) -> float:
        """Applies the monotonic peak invariant and returns the resulting peak."""
        try:
            pnl = float(current_pnl_usd)
        except (TypeError, ValueError):
            return self.peak_win_usd
        if math.isnan(pnl) or math.isinf(pnl):
            return self.peak_win_usd
        self.peak_win_usd = max(self.peak_win_usd, pnl)
        return self.peak_win_usd

    def retention_ratio(self, current_pnl_usd: float) -> float:
        """
        Fraction of peak profit still retained. Returns 1.0 when no positive peak
        exists yet, so an unarmed position can never mis-fire the giveback logic.
        """
        if self.peak_win_usd <= 0.0:
            return 1.0
        try:
            return float(current_pnl_usd) / self.peak_win_usd
        except (TypeError, ValueError):
            return 1.0


class PositionProtectionLedger:
    """Per-ticket protection-state registry (lazy per-ticket creation)."""

    def __init__(self) -> None:
        self._states: dict[int, PositionProtectionState] = {}

    def get(self, ticket: int) -> PositionProtectionState:
        """
        Returns (creating on first use) the protection state bound to this MT5 ticket.

        State is per-ticket by construction, never a shared/global variable, so two
        concurrently tracked positions can never contaminate each other's peak profit
        or protection flags.
        """
        state = self._states.get(ticket)
        if state is None:
            state = PositionProtectionState()
            self._states[ticket] = state
        return state

    def drop(self, ticket: int) -> None:
        """Releases one ticket's protection state (explicit teardown hook)."""
        self._states.pop(ticket, None)

    def __contains__(self, ticket: int) -> bool:
        return ticket in self._states

    def __len__(self) -> int:
        return len(self._states)
