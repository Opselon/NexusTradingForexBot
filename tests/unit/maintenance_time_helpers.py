"""Maintenance-window-safe timestamp pins for dispatch tests (BUG-264).

Tests that exercise gates AFTER the MAINTENANCE_WINDOW entry guard must pin
``generated_at`` outside the buffered nightly window. The naive pattern
(``now + 180min``, retry once with another ``+ 180min``) collides with the
INCLUSIVE window edges: the buffered window spans 181 minutes
(server 22:30..01:30 inclusive == UTC 19:30..22:30 inclusive at the canonical
GMT+3 offset), so a 180-minute step can land on 19:30:xx and — after the
single retry — exactly on 22:30:xx, both in-window. ``now`` values in
[16:30:00, 16:30:59] UTC (CI run 1085, windows-latest, 16:30:01.944Z) hit
that collision and failed ``test_capital_protection_a15.py::
TestDispatchIdempotency::test_primary_path_duplicate_request_id_blocked``.

``outside_maintenance_utc`` fixes the class: it advances in 180-minute steps
until the timestamp is PROVABLY outside the window (termination is
guaranteed: the orbit has period 8 and the window can hold at most two
consecutive orbit points), so no wall-clock minute of any day can re-open
the collision.

The predicate mirrors the production guard EXACTLY
(execution/lifecycle/dispatch.py): canonical
``BROKER_SERVER_UTC_OFFSET_MINUTES`` constant +
``research.economics.in_maintenance_window``. If the guard ever switches to
the env-overridable accessor, switch this helper with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from nexus_scalp.adapters.mt5.providers import BROKER_SERVER_UTC_OFFSET_MINUTES
from nexus_scalp.research.economics import in_maintenance_window

_STEP_MINUTES = 180  # == canonical window width (23:00..01:00 server)
_MAX_PROBES = 6  # window holds <=2 consecutive orbit points; 6 is generous


def outside_maintenance_utc(now: datetime) -> datetime:
    """Return `now` advanced in 180-minute steps until provably OUTSIDE the
    buffered nightly maintenance window under the canonical broker offset.

    `now` itself is returned untouched when it already clears the window, so
    the pin stays as fresh as possible (tick-domain realism preserved).
    Raises AssertionError if no probe clears the window within `_MAX_PROBES`
    (would mean the window geometry changed — fail loud, never flake).
    """
    offset_hours = BROKER_SERVER_UTC_OFFSET_MINUTES / 60.0
    candidate = now
    for _ in range(_MAX_PROBES):
        if not in_maintenance_window(candidate, server_utc_offset_hours=offset_hours):
            return candidate
        candidate = candidate + timedelta(minutes=_STEP_MINUTES)
    raise AssertionError(
        f"no 180-minute probe from {now.isoformat()} cleared the maintenance "
        "window — window geometry or offset assumption changed"
    )


__all__ = ["outside_maintenance_utc"]
