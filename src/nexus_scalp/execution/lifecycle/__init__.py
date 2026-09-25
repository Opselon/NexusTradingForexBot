"""Order lifecycle decomposition package (P0 seam S5/S7).

Thin re-export surface for extracted lifecycle owners. The
``OrderLifecycleManager`` facade in ``execution/order_manager.py`` composes
these components; further lifecycle responsibilities (dispatch, protection,
state machine, reconciliation, ...) land in this package as they are
extracted.
"""

from nexus_scalp.execution.lifecycle.pending_orders import (
    PENDING_ORDER_LOCK_SECONDS,
    PendingOrderLifecycle,
)
from nexus_scalp.execution.lifecycle.ticket_state import TicketState, TicketStateStore

__all__ = [
    "PENDING_ORDER_LOCK_SECONDS",
    "PendingOrderLifecycle",
    "TicketState",
    "TicketStateStore",
]
