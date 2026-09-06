"""Order lifecycle decomposition package (P0 seam S5).

Thin re-export surface for the canonical per-ticket state owner. The
``OrderLifecycleManager`` facade in ``execution/order_manager.py`` composes
``TicketStateStore`` from here; further lifecycle responsibilities
(dispatch, protection, state machine, reconciliation, ...) land in this
package as they are extracted.
"""

from nexus_scalp.execution.lifecycle.ticket_state import TicketState, TicketStateStore

__all__ = ["TicketState", "TicketStateStore"]
