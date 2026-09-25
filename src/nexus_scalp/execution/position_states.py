"""Position lifecycle states (Agent-5 P0 seam S2).

The 11 explicit in-trade lifecycles, moved VERBATIM from
execution/order_manager.py. Single source of truth; order_manager.py
re-exports the name for backward compatibility.
"""

from __future__ import annotations

from enum import Enum


class PositionState(Enum):
    """The 11 explicit in-trade lifecycles."""

    PROFIT_UNPROTECTED = "PROFIT_UNPROTECTED"
    PROFIT_PROTECTED = "PROFIT_PROTECTED"
    PROFIT_TRAILING = "PROFIT_TRAILING"
    PROFIT_GIVEBACK_WARNING = "PROFIT_GIVEBACK_WARNING"
    PROFIT_GIVEBACK_CRITICAL = "PROFIT_GIVEBACK_CRITICAL"

    LOSS_EARLY = "LOSS_EARLY"
    LOSS_RECOVERY_CANDIDATE = "LOSS_RECOVERY_CANDIDATE"
    LOSS_RECOVERY_CONFIRMED = "LOSS_RECOVERY_CONFIRMED"
    LOSS_RECOVERY_FAILING = "LOSS_RECOVERY_FAILING"
    LOSS_EXIT_PRESSURE = "LOSS_EXIT_PRESSURE"
    LOSS_HARD_EXIT = "LOSS_HARD_EXIT"
