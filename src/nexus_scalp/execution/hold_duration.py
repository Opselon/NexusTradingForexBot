"""Monotonic holding-duration clock (TASK-HOLD-CLOCK).

Problem this solves: time-based exits computed as ``(tick.timestamp -
entry_time)`` mix two different clock domains — the broker-stamped anchor
(``pos.time_setup``, broker server-local epoch) and host-stamped fallbacks
(``datetime.now(UTC)``). Any clock skew, DST transition, or NTP step between
those domains silently corrupts every holding-duration computation
(production observed ``Age: -10781.6s``).

Design: the PRIMARY duration is anchored to ``time.monotonic()`` at ticket
registration and read back from the same process-local, non-decreasing
clock — immune to wall-clock jumps by construction. Wall/broker anchors are
retained ONLY for forensic ``sanity()`` divergence reporting, never for the
primary duration.

All absolute timestamps persisted or exposed by this module are
timezone-aware UTC (``datetime`` objects with ``tzinfo=timezone.utc`` or
normalized to it). Naive datetimes are rejected; aware datetimes in other
offsets are normalized to UTC. Monotonic readings are never exposed as
epoch-like absolute times — only as elapsed durations in seconds.

Injection surface for tests: monkeypatch module-level ``monotonic_now`` and
``_utc_now``.

NOTE (follow-up handoff): this module is NOT yet wired into
``order_manager.py`` — wiring is the designated follow-up mission
(TASK-HOLD-CLOCK wiring handoff on agents/taskboard.md).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

__all__ = ["HoldDurationClock", "monotonic_now"]

# Documented sanity tolerance: divergence between the monotonic primary and
# a wall/broker comparison duration is acceptable when it is within
# max(60.0, 2% of the larger duration). A +/-1h DST jump (~3600s) or a
# coarse NTP step exceeds this on any realistic holding duration and flags
# SUSPECT, while sub-minute scheduler jitter does not.
_SANITY_ABS_TOLERANCE_SEC: float = 60.0
_SANITY_REL_TOLERANCE: float = 0.02

_DEFAULT_MAX_PLAUSIBLE_SEC: float = 7 * 24 * 3600.0


def monotonic_now() -> float:
    """Process-local monotonic seconds (injection seam; wraps time.monotonic)."""
    return time.monotonic()


def _utc_now() -> datetime:
    """Timezone-aware UTC wall clock (injection seam; wraps datetime.now)."""
    return datetime.now(UTC)


def _require_aware_utc(name: str, value: datetime) -> datetime:
    """Validate a tz-aware datetime and normalize it to UTC.

    Naive datetimes are rejected: storing them would silently re-introduce
    the mixed-clock-domain bug this module exists to fix.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{name} must be timezone-aware (got naive datetime {value!r}); "
            "absolute timestamps in HoldDurationClock are always tz-aware UTC"
        )
    return value.astimezone(UTC)


class HoldDurationClock:
    """Per-ticket holding-duration anchors with a monotonic primary duration.

    ``register`` records an anchor triple per ticket: the monotonic reading
    at registration (primary), the broker-stamped entry time and the
    host-stamped wall entry time (forensic only). ``elapsed_sec`` returns
    the monotonic-based duration; ``sanity`` reports divergence between the
    monotonic duration and the wall/broker clock domains so a skew, DST
    jump, or NTP step is surfaced instead of silently corrupting exits.
    """

    def __init__(self, *, max_plausible_sec: float = _DEFAULT_MAX_PLAUSIBLE_SEC) -> None:
        if max_plausible_sec <= 0.0:
            raise ValueError(f"max_plausible_sec must be positive, got {max_plausible_sec!r}")
        self._max_plausible_sec: float = max_plausible_sec
        # ticket -> (monotonic_at_registration, broker_entry_utc | None, wall_entry_utc)
        self._anchors: dict[int, tuple[float, datetime | None, datetime]] = {}

    def register(
        self,
        ticket: int,
        *,
        broker_entry_utc: datetime | None,
        wall_entry_utc: datetime | None = None,
    ) -> float:
        """Record the per-ticket anchor pair and return 0.0 elapsed.

        ``broker_entry_utc`` (may be None when the broker epoch is unknown)
        and ``wall_entry_utc`` (defaults to the host UTC clock at
        registration) are retained for sanity forensics only. Re-registering
        an existing ticket replaces its anchors and restarts the duration
        at 0.0.
        """
        normalized_broker = (
            _require_aware_utc("broker_entry_utc", broker_entry_utc)
            if broker_entry_utc is not None
            else None
        )
        normalized_wall = (
            _require_aware_utc("wall_entry_utc", wall_entry_utc)
            if wall_entry_utc is not None
            else _utc_now()
        )
        self._anchors[ticket] = (monotonic_now(), normalized_broker, normalized_wall)
        return 0.0

    def elapsed_sec(self, ticket: int, *, broker_now_utc: datetime | None = None) -> float | None:
        """PRIMARY holding duration from the monotonic clock, or None if unknown.

        Immune to wall-clock jumps (DST/NTP/skew) by construction: both
        anchor and now come from the same process-local non-decreasing
        clock. A defensive clamp returns 0.0 if a monotonic regression is
        ever observed (monotonic clocks are non-decreasing; this guards
        against exotic platform behaviour rather than trusting it).
        """
        del broker_now_utc  # primary duration never uses foreign clock domains
        anchor = self._anchors.get(ticket)
        if anchor is None:
            return None
        monotonic_at_registration, _broker_entry, _wall_entry = anchor
        elapsed = monotonic_now() - monotonic_at_registration
        if elapsed < 0.0:
            return 0.0
        return elapsed

    def sanity(self, ticket: int, *, broker_now_utc: datetime | None = None) -> dict[str, Any]:
        """Forensic divergence report between the monotonic and wall/broker domains.

        Returns ``{'status': 'OK'|'SUSPECT'|'MISSING', 'monotonic_sec': ...,
        'wallclock_sec': ..., 'broker_clock_sec': ..., 'divergence_sec': ...}``.
        Status is SUSPECT when |monotonic - wallclock| or (when a broker now
        is supplied) |monotonic - broker| exceeds
        ``max(60.0, 0.02 * max(monotonic, comparison))`` — sized so a +/−1h
        DST jump or an NTP step flags SUSPECT while sub-minute jitter does
        not. ``divergence_sec`` is the worst offending divergence. Numeric
        fields are None where a domain is unavailable; MISSING carries no
        fabricated numbers.
        """
        anchor = self._anchors.get(ticket)
        if anchor is None:
            return {
                "status": "MISSING",
                "monotonic_sec": None,
                "wallclock_sec": None,
                "broker_clock_sec": None,
                "divergence_sec": None,
            }
        monotonic_at_registration, broker_entry, wall_entry = anchor

        monotonic_sec = max(0.0, monotonic_now() - monotonic_at_registration)
        wallclock_sec: float | None = max(0.0, (_utc_now() - wall_entry).total_seconds())
        broker_clock_sec: float | None = None
        if broker_entry is not None and broker_now_utc is not None:
            broker_now = _require_aware_utc("broker_now_utc", broker_now_utc)
            broker_clock_sec = max(0.0, (broker_now - broker_entry).total_seconds())

        divergence_sec = 0.0
        suspect = False
        if wallclock_sec is not None:
            wall_divergence = abs(monotonic_sec - wallclock_sec)
            divergence_sec = max(divergence_sec, wall_divergence)
            if wall_divergence > self._tolerance_sec(monotonic_sec, wallclock_sec):
                suspect = True
        if broker_clock_sec is not None:
            broker_divergence = abs(monotonic_sec - broker_clock_sec)
            divergence_sec = max(divergence_sec, broker_divergence)
            if broker_divergence > self._tolerance_sec(monotonic_sec, broker_clock_sec):
                suspect = True
        # Defensive plausibility gate: a monotonic duration beyond
        # max_plausible_sec means the process has been up longer than any
        # plausible holding period or the anchor was corrupted.
        if monotonic_sec > self._max_plausible_sec:
            suspect = True

        return {
            "status": "SUSPECT" if suspect else "OK",
            "monotonic_sec": monotonic_sec,
            "wallclock_sec": wallclock_sec,
            "broker_clock_sec": broker_clock_sec,
            "divergence_sec": divergence_sec,
        }

    def _tolerance_sec(self, primary_sec: float, comparison_sec: float) -> float:
        return max(
            _SANITY_ABS_TOLERANCE_SEC,
            _SANITY_REL_TOLERANCE * max(primary_sec, comparison_sec),
        )

    def expire(self, ticket: int) -> None:
        """Drop the ticket's anchors (cleanup once the position is closed)."""
        self._anchors.pop(ticket, None)
