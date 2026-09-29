"""Position ownership — the central authority gate for position mutation.

TASK-ML-CTRL: when the ML Position Controller is ACTIVE, every position the
engine manages is ML-owned. Legacy position-management code paths become
READ-ONLY for those positions: they may observe, but any attempt to close,
fast-close, modify SL/TP, trail, or otherwise mutate an ML-owned position
raises :class:`OwnershipViolation` instead of executing.

Enforcement lives HERE (this module), not in individual callers, so an old
code path that forgets to check ownership is still blocked at the broker
dispatch layer (OrderManager routes every mutation through
:meth:`PositionOwnershipGate.authorize`).

Failure posture (spec §17, user-confirmed spec-literal):
    * ML ownership PERSISTS through model failure. Legacy stays blocked.
    * The hard emergency risk guard (account-level kill switches, broker
      margin stop-outs) is the ONLY bypass, and it is never strategy logic.
    * Controller failure is surfaced via ``ml_controller_failed`` state, never
      silently absorbed into a legacy takeover.

Ownership states (per engine-managed position, persisted in the runtime
state store so restart recovery restores the same owner):

    LEGACY     default; legacy policies are authoritative.
    ML         ML controller is authoritative; legacy is read-only.
    MANUAL     operator explicitly took over (legacy stays read-only too).
    EMERGENCY  hard risk guard bypasses everything (never persisted as owner).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "Controller",
    "OwnershipViolation",
    "OwnershipDecision",
    "PositionOwnershipGate",
]


class Controller(StrEnum):
    """Who owns a position's management decisions."""

    LEGACY = "LEGACY"
    ML = "ML"
    MANUAL = "MANUAL"


#: Actions legacy code may request. When the owner is ML, every one of these
#: is refused. The emergency guard bypasses the gate entirely (it never goes
#: through ``authorize``); it is not a legacy action.
_LEGACY_MUTATION_ACTIONS = frozenset(
    {
        "CLOSE",
        "PARTIAL_CLOSE",
        "MODIFY_SL",
        "MODIFY_TP",
        "MODIFY_SL_TP",
        "TRAIL_STOP",
        "FAST_CLOSE",
        "BREAK_EVEN",
    }
)


class OwnershipViolation(RuntimeError):
    """Legacy code tried to mutate an ML-owned position. Blocked."""


@dataclass(frozen=True)
class OwnershipDecision:
    """Result of one authorization request (audit-logged, never silent)."""

    allowed: bool
    controller: Controller
    action: str
    ticket: int
    reason: str
    decided_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "controller": str(self.controller),
            "action": self.action,
            "ticket": self.ticket,
            "reason": self.reason,
            "decided_at": self.decided_at,
        }


class PositionOwnershipGate:
    """Central authority check for every position-management mutation.

    One instance per engine (owned by OrderManager). Thread-safe: the
    management loop, risk loop, trailing loop, and web-triggered manual
    actions all call :meth:`authorize` concurrently.

    The gate does NOT track which positions exist — it derives ownership from
    the controller mode. When the ML controller is ACTIVE, every engine-
    managed position is ML-owned (user decision: all engine-managed positions,
    set at activation and at entry from then on). Positions the engine does
    not manage are outside the gate's scope entirely.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ml_active = False
        self._ml_healthy = True  # False => controller failure surfaced
        self._manual_tickets: set[int] = set()
        #: Last N decisions for the /status audit surface (bounded).
        self._audit: list[OwnershipDecision] = []
        self._audit_max = 200

    # ------------------------------------------------------------- state

    def set_ml_active(self, active: bool) -> None:
        """Called by the ML lifecycle on activation/disable.

        Activation sets every engine-managed position's owner to ML
        (persisted by the caller); disable restores LEGACY.
        """
        with self._lock:
            self._ml_active = bool(active)
            if not active:
                # Reset health + manual takeovers when ML is disabled; the
                # owner for all positions returns to LEGACY.
                self._ml_healthy = True
                self._manual_tickets.clear()

    def set_ml_health(self, healthy: bool, reason: str = "") -> None:
        """Surface ML controller failure WITHOUT giving legacy control (§17).

        Ownership stays ML; legacy stays blocked; the failure reason is
        recorded for /status and the operator UI. Only the hard emergency
        guard (outside this gate) may still act.
        """
        with self._lock:
            self._ml_healthy = bool(healthy)
            if not healthy and reason:
                self._record(
                    OwnershipDecision(
                        allowed=False,
                        controller=Controller.ML,
                        action="ML_CONTROLLER_HEALTH",
                        ticket=0,
                        reason=f"ML controller unhealthy: {reason}",
                    )
                )

    def set_manual(self, ticket: int, manual: bool) -> None:
        """Operator explicitly took over (or released) one ticket."""
        with self._lock:
            if manual:
                self._manual_tickets.add(int(ticket))
            else:
                self._manual_tickets.discard(int(ticket))

    def controller_for(self, ticket: int) -> Controller:
        """Effective owner for a ticket right now."""
        with self._lock:
            if int(ticket) in self._manual_tickets:
                return Controller.MANUAL
            return Controller.ML if self._ml_active else Controller.LEGACY

    @property
    def ml_active(self) -> bool:
        with self._lock:
            return self._ml_active

    @property
    def ml_healthy(self) -> bool:
        with self._lock:
            return self._ml_healthy

    # ------------------------------------------------------------- gate

    def authorize(self, *, ticket: int, action: str, actor: str) -> OwnershipDecision:
        """The single authorization point for every position mutation.

        ``actor`` is 'legacy' for all pre-ML code paths, 'ml' for the ML
        controller, 'manual' for operator actions, 'emergency' for the hard
        risk guard. Returns an :class:`OwnershipDecision`; raising is the
        caller's job (callers raise :class:`OwnershipViolation` when
        ``allowed`` is False) so the gate stays side-effect-free.
        """
        actor_l = str(actor).strip().lower()
        controller = self.controller_for(ticket)

        if actor_l == "emergency":
            # Hard emergency risk guard: always allowed, never persisted as
            # owner, audit-logged so every bypass is visible.
            return self._record(
                OwnershipDecision(
                    allowed=True,
                    controller=controller,
                    action=action,
                    ticket=int(ticket),
                    reason="EMERGENCY_GUARD bypass (documented exception, §17)",
                )
            )

        if controller is Controller.LEGACY:
            return self._record(
                OwnershipDecision(
                    allowed=True,
                    controller=controller,
                    action=action,
                    ticket=int(ticket),
                    reason="legacy owner",
                )
            )

        if controller is Controller.MANUAL and actor_l == "manual":
            return self._record(
                OwnershipDecision(
                    allowed=True,
                    controller=controller,
                    action=action,
                    ticket=int(ticket),
                    reason="operator manual action",
                )
            )

        if actor_l == "ml" and controller is Controller.ML:
            return self._record(
                OwnershipDecision(
                    allowed=True,
                    controller=controller,
                    action=action,
                    ticket=int(ticket),
                    reason="ML owner acting",
                )
            )

        # Everything else: legacy (or accidental old path) trying to touch an
        # ML-owned or manually-owned position. Blocked, audit-logged.
        reason = (
            f"{actor_l} mutation of {action} blocked: position owned by "
            f"{controller.value} (ML control plane, spec §2/§18)"
        )
        return self._record(
            OwnershipDecision(
                allowed=False,
                controller=controller,
                action=action,
                ticket=int(ticket),
                reason=reason,
            )
        )

    def authorize_or_raise(self, *, ticket: int, action: str, actor: str) -> OwnershipDecision:
        """authorize() + raise OwnershipViolation when blocked."""
        d = self.authorize(ticket=ticket, action=action, actor=actor)
        if not d.allowed:
            raise OwnershipViolation(d.reason)
        return d

    def is_legacy_mutation(self, action: str) -> bool:
        return str(action).strip().upper() in _LEGACY_MUTATION_ACTIONS

    # ------------------------------------------------------------- audit

    def _record(self, d: OwnershipDecision) -> OwnershipDecision:
        with self._lock:
            self._audit.append(d)
            if len(self._audit) > self._audit_max:
                self._audit = self._audit[-self._audit_max :]
        return d

    def audit_tail(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [d.to_dict() for d in self._audit[-limit:]]

    def status(self) -> dict[str, Any]:
        with self._lock:
            blocked = sum(1 for d in self._audit if not d.allowed)
            return {
                "ml_active": self._ml_active,
                "ml_healthy": self._ml_healthy,
                "manual_tickets": sorted(self._manual_tickets),
                "audit_size": len(self._audit),
                "audit_blocked": blocked,
            }
