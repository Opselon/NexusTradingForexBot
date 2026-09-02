"""TelemetryThrottle — explicit owner of telemetry emission gating.

S6 STEP-A seam (Agent-5, CHG-0032/TASK-OM-P0-DECOMP): the per-ticket
last-emission timestamp dict moved out of OrderLifecycleManager. The throttle
answers exactly two questions — may telemetry be emitted for this ticket now?
and when was it last emitted? — and records emissions. It owns NO broker,
risk, policy, lifecycle, AI, or protection authority.

Contract (BUG-129):
    - the institutional telemetry block and the structured exit-evaluation
      log SHARE this one throttle so they stay aligned (max once per 3s per
      ticket)
    - default interval: 3.0 seconds (epoch seconds, time.time domain)
    - first emission for an unseen ticket is always allowed (get(ticket, 0.0)
      semantics)
"""

from __future__ import annotations


class TelemetryThrottle:
    """Per-ticket telemetry emission gate (session-global, BUG-129 shared)."""

    DEFAULT_INTERVAL = 3.0

    def __init__(self) -> None:
        self._last_telemetry_time: dict[int, float] = {}

    def may_emit(
        self, ticket: int, current_time: float, interval: float = DEFAULT_INTERVAL
    ) -> bool:
        """Whether telemetry may be emitted now for this ticket."""
        return (current_time - self._last_telemetry_time.get(ticket, 0.0)) >= interval

    def last_emit(self, ticket: int) -> float:
        """Last recorded emission time (0.0 if never)."""
        return self._last_telemetry_time.get(ticket, 0.0)

    def record(self, ticket: int, current_time: float) -> None:
        """Record an emission now."""
        self._last_telemetry_time[ticket] = current_time

    def drop_ticket(self, ticket: int) -> None:
        """Release the per-ticket entry (manager cleanup bundle)."""
        self._last_telemetry_time.pop(ticket, None)
