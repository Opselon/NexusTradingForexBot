"""BUG-264 regression: maintenance-window test pins must be provably
outside the buffered window at EVERY wall-clock minute, not just the ones
where CI happened to run.

Root cause (CI run 1085, tests (OS Matrix) / Py Tests windows-latest,
2026-09-12 16:30:01.944Z):
  tests/unit/test_capital_protection_a15.py::
  TestDispatchIdempotency::test_primary_path_duplicate_request_id_blocked
  pinned `generated_at = now + 180min`, and on collision `+ 180min` again.
  The buffered window is server 22:30..01:30 INCLUSIVE (research/economics
  .py:142-148: 23:00-30m .. 01:00+30m, `start <= minutes <= end`) == UTC
  19:30..22:30 inclusive at the canonical +180min offset — 181 minutes wide.
  A 180-minute step is SMALLER than the window: from `now` in
  [16:30:00, 16:30:59] UTC the first probe lands on 19:30:xx (in-window,
  inclusive edge) and the single retry lands on 22:30:xx (in-window,
  inclusive edge) -> the pinned timestamp is inside the guard window, the
  first dispatch returns False, and `assert ... is True` fails.

This suite sweeps the FULL day at second-resolution around the historical
failure instants and minute resolution everywhere, asserting the fixed
helper always lands outside the window (proving RED on the old shape as
well).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.mt5.providers import BROKER_SERVER_UTC_OFFSET_MINUTES
from nexus_scalp.research.economics import in_maintenance_window
from tests.unit.maintenance_time_helpers import outside_maintenance_utc

OFF = BROKER_SERVER_UTC_OFFSET_MINUTES / 60.0


def _legacy_pin(now: datetime) -> datetime:
    """The BROKEN shape (pre-BUG-264): +180, retry +180 once."""
    candidate = now + timedelta(minutes=180)
    if in_maintenance_window(candidate, server_utc_offset_hours=OFF):
        candidate = candidate + timedelta(minutes=180)
    return candidate


def test_the_exact_ci_failure_instant_is_red_on_the_legacy_pin() -> None:
    """The CI 1085 windows-latest instant (16:30:01.944Z): legacy pin lands
    exactly on the inclusive 22:30 UTC edge -> in-window -> test would fail.
    Pinned here so the geometry can never be 'fixed' by moving one constant
    without this suite noticing."""
    now = datetime(2026, 9, 12, 16, 30, 1, 944000, tzinfo=UTC)
    legacy = _legacy_pin(now)
    assert in_maintenance_window(legacy, server_utc_offset_hours=OFF), (
        "legacy shape must STILL demonstrate the collision (if this flipped, "
        "the window geometry changed — re-derive the pin contract)"
    )
    fixed = outside_maintenance_utc(now)
    assert not in_maintenance_window(fixed, server_utc_offset_hours=OFF)


def test_legacy_collision_band_is_exactly_one_minute_per_day() -> None:
    """Second-resolution day sweep: the ONLY wall-clock seconds where the
    legacy '+180, retry +180' shape returns an in-window pin are the 60
    seconds of 16:30:00..16:30:59 UTC (both probes on the inclusive edges
    19:30 and 22:30). Neighbouring instants (e.g. 19:30:30, where the first
    probe lands on the inclusive 22:30 edge) still escape via the retry —
    pinned so the RED band is precisely characterized, not guessed."""
    day = datetime(2026, 9, 12, tzinfo=UTC)
    red = [
        s
        for s in range(86400)
        if in_maintenance_window(
            _legacy_pin(day + timedelta(seconds=s)), server_utc_offset_hours=OFF
        )
    ]
    assert red == list(range(16 * 3600 + 30 * 60, 16 * 3600 + 31 * 60))
    # first probe at 19:30:30 lands ON the inclusive edge (retry saves it)
    probe1 = datetime(2026, 9, 12, 19, 30, 30, tzinfo=UTC) + timedelta(minutes=180)
    assert in_maintenance_window(probe1, server_utc_offset_hours=OFF)
    assert not in_maintenance_window(
        _legacy_pin(datetime(2026, 9, 12, 19, 30, 30, tzinfo=UTC)), server_utc_offset_hours=OFF
    )


def test_full_day_minute_sweep_helper_always_clears() -> None:
    """Every minute of a full UTC day (the +180min orbit has period 8, so a
    day sweep covers all distinct phase classes): the helper must return a
    provably-outside timestamp."""
    day = datetime(2026, 9, 12, tzinfo=UTC)
    for m in range(0, 24 * 60):
        now = day + timedelta(minutes=m)
        pinned = outside_maintenance_utc(now)
        assert not in_maintenance_window(pinned, server_utc_offset_hours=OFF), (
            f"helper returned an in-window pin for {now.isoformat()}"
        )


def test_second_resolution_around_every_edge_instant() -> None:
    """The inclusive edges (server 22:30:00/01:30:00 == UTC 19:30:00 and
    22:30:00) plus the historical collision instants, swept at 1-second
    resolution +-90s."""
    anchors = [
        datetime(2026, 9, 12, 16, 30, tzinfo=UTC),  # CI 1085 collision
        datetime(2026, 9, 12, 19, 30, tzinfo=UTC),  # UTC 19:30 window start
        datetime(2026, 9, 12, 22, 30, tzinfo=UTC),  # UTC 22:30 window end
    ]
    for anchor in anchors:
        for sec in range(-90, 91):
            now = anchor + timedelta(seconds=sec)
            pinned = outside_maintenance_utc(now)
            assert not in_maintenance_window(pinned, server_utc_offset_hours=OFF), (
                f"helper returned an in-window pin for {now.isoformat()}"
            )


def test_helper_returns_now_untouched_when_already_outside() -> None:
    mid_session = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)  # server 15:00
    assert outside_maintenance_utc(mid_session) == mid_session


def test_helper_fails_loud_on_impossible_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the predicate claims EVERY probe is in-window (geometry change /
    bug), the helper must raise, not return a silently-bad pin."""

    def _always_in(ts: datetime, **kw: object) -> bool:
        return True

    monkeypatch.setattr("tests.unit.maintenance_time_helpers.in_maintenance_window", _always_in)
    with pytest.raises(AssertionError):
        outside_maintenance_utc(datetime(2026, 9, 12, 12, 0, tzinfo=UTC))
