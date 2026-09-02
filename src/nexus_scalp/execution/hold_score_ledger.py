"""HoldScoreLedger — explicit owner of hold-score state.

S6-escalation seam (Agent-5, CHG-0032/TASK-OM-P0-DECOMP): the four hold-score
state dicts moved out of OrderLifecycleManager. The ledger STORES state only;
hold-score evaluation/policy (throttling window, giveback override, score
recalculation) remains with the manager/evaluators. Compatibility @property
accessors on the manager return these LIVE dicts under the historical names
(single source of truth; the atomic cleanup bundle and direct test seeds keep
working unchanged).

Fields (all TICKET_LOCAL):
    hold_score           int   current effective hold score (post-override)
    base_hold_score      int   pre-override base score
    last_reasons         list  last evaluation invalidate reasons
    last_hold_eval_time  float last base-eval epoch seconds (throttle)

Units: scores 0..100 int; time in epoch seconds (time.time domain).
"""

from __future__ import annotations


class HoldScoreLedger:
    """Explicit hold-score state owner (S6-escalation boundary)."""

    def __init__(self) -> None:
        self._hold_score_tracker: dict[int, int] = {}
        self._base_hold_score_tracker: dict[int, int] = {}
        self._last_reasons_tracker: dict[int, list[str]] = {}
        self._last_hold_eval_time: dict[int, float] = {}

    def drop_ticket(self, ticket: int) -> None:
        """Release all four per-ticket entries (atomic per-ticket teardown,
        called from the manager's _cleanup_ticket_state bundle)."""
        self._hold_score_tracker.pop(ticket, None)
        self._base_hold_score_tracker.pop(ticket, None)
        self._last_reasons_tracker.pop(ticket, None)
        self._last_hold_eval_time.pop(ticket, None)
